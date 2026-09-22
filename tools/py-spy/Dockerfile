# py-spy: a sampling profiler that reads a running Python process's memory
# from outside - no code changes, no restart, negligible overhead. Runs as
# a sidecar that joins the target container's PID namespace and holds
# CAP_SYS_PTRACE (the target itself keeps cap_drop: ALL).
#   make py-spy-dump    what is every worker doing right now?
#   make py-spy-top     live view of where time goes
#   make py-spy-record  flame graph -> docs/images/api-flame.svg
FROM python:3.13-slim
RUN pip install --no-cache-dir --root-user-action=ignore py-spy==0.4.2
ENTRYPOINT ["py-spy"]
