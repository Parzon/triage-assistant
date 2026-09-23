"""Generates dashboards/service.json: `make dashboard`.

The dashboard is code: change a panel here, regenerate, and review the diff
in the PR. Edits made in the Grafana UI are lost on the next provisioning
reload unless they are brought back into this file. The result must be
committed: Grafana provisions the JSON, it never runs this script.
"""

import json
import sys

DS = {"type": "prometheus", "uid": "prometheus"}
panels = []
next_id = 1
y = 0


def target(expr, legend="", ref="A"):
    return {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": ref, "range": True}


def add(kind, title, targets, w, h, x, *, unit="short", description="", stack=False, decimals=None, thresholds=None):
    global next_id
    field = {"unit": unit, "custom": {}}
    if decimals is not None:
        field["decimals"] = decimals
    if kind == "timeseries":
        field["custom"] = {
            "drawStyle": "line",
            "lineWidth": 1,
            "fillOpacity": 15 if stack else 5,
            "showPoints": "never",
            "spanNulls": True,
            "stacking": {"mode": "normal" if stack else "none", "group": "A"},
        }
    if thresholds:
        field["thresholds"] = {"mode": "absolute", "steps": thresholds}
        field["color"] = {"mode": "thresholds"}
    panel = {
        "id": next_id,
        "type": kind,
        "title": title,
        "description": description,
        "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [target(e, l, chr(65 + i)) for i, (e, l) in enumerate(targets)],
        "fieldConfig": {"defaults": field, "overrides": []},
        "options": (
            {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
             "tooltip": {"mode": "multi", "sort": "desc"}}
            if kind == "timeseries"
            else {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                  "colorMode": "value", "graphMode": "area", "textMode": "value"}
        ),
    }
    panels.append(panel)
    next_id += 1


def row(title):
    global next_id, y
    panels.append({"id": next_id, "type": "row", "title": title, "collapsed": False,
                   "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []})
    next_id += 1
    y += 1


NOT_INTERNAL = 'route!~"/metrics|/health|/ready"'
REQUESTS = f'http_requests_total{{{NOT_INTERNAL}}}'

row("Traffic and errors (RED)")
add("stat", "Requests / s", [(f"sum(rate({REQUESTS}[1m]))", "")], 6, 4, 0, unit="reqps", decimals=1,
    description="Excludes /metrics, /health and /ready (probes, not users).")
# `or vector(0)`: until the first 5xx the series does not exist, and a ratio
# with a missing numerator is "no data", not 0. The `> 0` keeps "no traffic"
# honest (no data) instead of dividing by zero.
add("stat", "5xx errors", [(f'100 * (sum(rate(http_requests_total{{status=~"5.."}}[5m])) or vector(0)) / (sum(rate({REQUESTS}[5m])) > 0)', "")],
    6, 4, 6, unit="percent", decimals=2,
    thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 5}])
add("stat", "Rate limited (429) / s", [('sum(rate(http_requests_total{status="429"}[1m])) or vector(0)', "")], 6, 4, 12,
    unit="reqps", decimals=2)
add("stat", "Answers streaming now", [("sum(llm_active_streams)", "")], 6, 4, 18, decimals=0)
y += 4
add("timeseries", "Requests / s by route and status",
    [(f"sum by (route, status) (rate({REQUESTS}[1m]))", "{{route}} {{status}}")], 12, 8, 0, unit="reqps")
add("timeseries", "Latency of non-streaming routes",
    [(f'histogram_quantile({q}, sum by (le) (rate(http_request_duration_seconds_bucket{{{NOT_INTERNAL},route!="/chat/stream"}}[1m])))', f"p{int(q*100)}")
     for q in (0.5, 0.95, 0.99)], 12, 8, 12, unit="s",
    description="Time until the whole response was sent. /chat/stream is excluded: its duration is the answer length (see the LLM row).")
y += 8
add("timeseries", "Event loop lag (saturation)",
    [(f"histogram_quantile({q}, sum by (le) (rate(event_loop_lag_seconds_bucket[1m])))", f"p{int(q*100)}") for q in (0.5, 0.95, 0.99)],
    12, 7, 0, unit="s",
    description="How late the api's event loop runs a timer. Near 0 when healthy. When it grows, every request waits that long before its code runs, and wall-clock timeouts (DB pool, Redis budget) fire spuriously. First place to look when latency rises everywhere at once.")
add("timeseries", "Requests in progress",
    [("sum(http_requests_in_progress)", "in progress"), ("sum(llm_active_streams)", "streams")], 12, 7, 12, unit="short")
y += 7
add("timeseries", "p95 latency by route",
    [(f'histogram_quantile(0.95, sum by (le, route) (rate(http_request_duration_seconds_bucket{{{NOT_INTERNAL},route!="/chat/stream"}}[5m])))', "{{route}}")],
    24, 7, 0, unit="s",
    description="/chat/stream is excluded: its duration is how long the answer is, and it would flatten every other line.")
y += 7

row("LLM")
add("timeseries", "Time to first token",
    [(f"histogram_quantile({q}, sum by (le) (rate(llm_time_to_first_token_seconds_bucket[5m])))", f"p{int(q*100)}") for q in (0.5, 0.95)],
    8, 8, 0, unit="s", description="What the user waits before any text appears. Includes queueing in the provider.")
add("timeseries", "Answer duration",
    [(f'histogram_quantile({q}, sum by (le) (rate(llm_stream_duration_seconds_bucket{{outcome="ok"}}[5m])))', f"p{int(q*100)}") for q in (0.5, 0.95)],
    8, 8, 8, unit="s")
add("timeseries", "Model calls by outcome",
    [("sum by (outcome) (rate(llm_requests_total[1m]))", "{{outcome}}")], 8, 8, 16, unit="reqps", stack=True,
    description="cancelled = the user left mid-answer and we stopped the provider stream.")
y += 8
add("timeseries", "Output tokens / s",
    [('sum(rate(llm_tokens_total{kind="completion"}[1m]))', "completion"),
     ('sum(rate(llm_tokens_total{kind="prompt"}[1m]))', "prompt")], 8, 7, 0, unit="short")
add("stat", "Estimated model cost / hour",
    [('3600 * (sum(rate(llm_tokens_total{kind="prompt"}[5m])) * $usd_per_1m_input + sum(rate(llm_tokens_total{kind="completion"}[5m])) * $usd_per_1m_output) / 1e6', "")],
    8, 7, 8, unit="currencyUSD", decimals=2,
    description="Token rate x the per-million-token prices in the dashboard variables. Set them to your model's price list.")
add("timeseries", "Cancelled answers / min",
    [('60 * sum(rate(llm_requests_total{outcome="cancelled"}[5m])) or vector(0)', "cancelled")], 8, 7, 16, unit="short")
y += 7

row("Sign-in and access")
add("stat", "Identity provider",
    [("min(identity_provider_up)", "")], 4, 7, 0, unit="none",
    thresholds=[{"color": "red", "value": None}, {"color": "green", "value": 1}],
    description="1 = the provider answered the api's last check (every 30s). 0 = new sign-ins fail; signed-in users are unaffected.")
add("timeseries", "Sign-ins by outcome",
    [("sum by (outcome) (increase(auth_logins_total[5m]))", "{{outcome}}")], 10, 7, 4, unit="short", stack=True,
    description="Callbacks from the provider per 5 min. access_denied: the user cancelled or may not use the app. invalid_state / expired: a sign-in replayed or left too long. login_failed / invalid_token: the exchange or the token was refused - a burst after a change means a client secret, redirect URI or clock problem.")
add("timeseries", "Refused requests by reason",
    [("sum by (reason) (rate(auth_rejections_total[1m]))", "{{reason}}")], 10, 7, 14, unit="reqps", stack=True,
    description="no_session: not signed in (a sign-in page loading). expired: sessions ending. cross_origin: a state-changing request from another site - someone attempting CSRF, or a script without the Origin header.")
y += 7

row("Dependencies")
add("timeseries", "Rate limiter decisions",
    [("sum by (scope, decision) (rate(ratelimit_decisions_total[1m]))", "{{scope}} {{decision}}")], 6, 8, 0, unit="reqps",
    description="fail_open = Redis did not answer within the budget, request allowed (ADR-0004).")
add("timeseries", "App database pools",
    [("sum(db_pool_connections_in_use)", "in use"), ("sum(db_pool_connections_max)", "max"),
     ("sum(http_requests_in_progress)", "requests in progress")], 6, 8, 6, unit="short",
    description="The api's own pools, in front of PgBouncer. In use near max = requests about to wait (pool_timeout) and fail with 503. In use above 0 with no traffic = leaked connections (ADR-0010).")
add("timeseries", "PgBouncer pool",
    [("sum(pgbouncer_pools_client_active_connections)", "clients active"),
     ("sum(pgbouncer_pools_client_waiting_connections)", "clients waiting"),
     ("sum(pgbouncer_pools_server_active_connections)", "servers active"),
     ("sum(pgbouncer_pools_server_idle_connections)", "servers idle")], 6, 8, 12, unit="short",
    description="Clients waiting > 0 for long = the pool is too small for the load, or queries are too slow.")
add("timeseries", "Postgres",
    [('sum(pg_stat_database_numbackends{datname="triage"})', "backends"),
     ('sum(rate(pg_stat_database_xact_commit{datname="triage"}[1m]))', "commits / s")], 6, 8, 18, unit="short")
y += 8

row("Containers and host")
add("timeseries", "CPU by container",
    [('sum by (name) (rate(container_cpu_usage_seconds_total{name=~".+", container_label_com_docker_compose_project="$project"}[1m]))', "{{name}}")],
    12, 8, 0, unit="short", description="In cores: 1 = one full CPU.")
add("timeseries", "Memory by container (working set)",
    [('sum by (name) (container_memory_working_set_bytes{name=~".+", container_label_com_docker_compose_project="$project"})', "{{name}}")],
    12, 8, 12, unit="bytes", description="Working set is what the OOM killer compares with the limit.")
y += 8
add("timeseries", "Host disk free",
    [('100 * node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}', "/ free")],
    8, 7, 0, unit="percent")
add("timeseries", "Redis",
    [("sum(rate(redis_commands_processed_total[1m]))", "commands / s"),
     ("sum(redis_connected_clients)", "clients")], 8, 7, 8, unit="short")
add("timeseries", "OOM kills",
    [('sum by (container_label_com_docker_compose_service) (increase(container_oom_events_total{container_label_com_docker_compose_project="$project"}[5m]))', "{{container_label_com_docker_compose_service}}")],
    8, 7, 16, unit="short", decimals=0,
    description="Processes the kernel killed for exceeding their container's memory limit (last 5 min). A killed gunicorn worker is replaced and the container stays healthy: this panel and the ContainerOOMKilled alert are the only trace.")

dashboard = {
    "uid": "triage-service",
    "title": "triage-assistant / service",
    "tags": ["triage-assistant"],
    "timezone": "browser",
    "schemaVersion": 41,
    "refresh": "10s",
    "time": {"from": "now-30m", "to": "now"},
    "templating": {"list": [
        {"name": "project", "label": "compose project", "type": "query", "datasource": DS,
         "query": {"query": "label_values(container_cpu_usage_seconds_total, container_label_com_docker_compose_project)", "refId": "p"},
         "refresh": 2, "current": {}, "options": [], "includeAll": False, "multi": False, "sort": 1},
        {"name": "usd_per_1m_input", "label": "$ / 1M input tokens", "type": "textbox", "query": "0.15",
         "current": {"text": "0.15", "value": "0.15"}},
        {"name": "usd_per_1m_output", "label": "$ / 1M output tokens", "type": "textbox", "query": "0.60",
         "current": {"text": "0.60", "value": "0.60"}},
    ]},
    "panels": panels,
}
json.dump(dashboard, sys.stdout, indent=2)
print()
