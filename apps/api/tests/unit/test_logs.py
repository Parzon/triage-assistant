import json
import logging

from app.logs import JsonFormatter, request_id_var


def record(msg: str, **extra: object) -> logging.LogRecord:
    rec = logging.makeLogRecord({"name": "app.test", "levelname": "INFO", "msg": msg})
    for key, value in extra.items():
        setattr(rec, key, value)
    return rec


def test_one_json_object_with_request_id_and_extra_fields() -> None:
    token = request_id_var.set("abc123def456")
    try:
        line = JsonFormatter().format(record("request", route="/alerts", status=201))
    finally:
        request_id_var.reset(token)
    payload = json.loads(line)
    assert payload["msg"] == "request"
    assert payload["request_id"] == "abc123def456"
    assert payload["route"] == "/alerts"
    assert payload["status"] == 201
    assert payload["level"] == "info"


def test_exceptions_are_included_as_text() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        rec = record("failed")
        rec.exc_info = sys.exc_info()
    payload = json.loads(JsonFormatter().format(rec))
    assert "ValueError: boom" in payload["exc"]
