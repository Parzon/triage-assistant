#!/usr/bin/env python3
"""Every accepted vulnerability in .trivyignore.yaml has a reason and an
expiry date no more than 90 days out.

Trivy honours `statement` and `expired_at` but requires neither: without
this check a risk accepted once stays accepted forever, for no stated
reason. Run by `make scan` and `make scan-compose`.
"""

import re
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_DAYS = 90


def entries(text: str) -> list[dict[str, str]]:
    # No YAML parser on a bare host: an entry starts at "- id:", and its
    # keys are the lines indented under it.
    found: list[dict[str, str]] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if match := re.match(r"^\s*- id:\s*(\S+)", line):
            found.append({"id": match[1]})
        elif found and (match := re.match(r"^\s+(statement|expired_at):\s*(.*?)\s*$", line)):
            found[-1][match[1]] = match[2].strip("\"'")
    return found


def main() -> int:
    today = date.today()
    problems = []
    for entry in entries((ROOT / ".trivyignore.yaml").read_text()):
        if not entry.get("statement"):
            problems.append(f"{entry['id']}: no statement (why it is accepted, and by whom)")
        try:
            expiry = date.fromisoformat(entry.get("expired_at", ""))
        except ValueError:
            problems.append(f"{entry['id']}: no expired_at (YYYY-MM-DD)")
            continue
        if expiry > today + timedelta(days=MAX_DAYS):
            problems.append(f"{entry['id']}: expires {expiry}, more than {MAX_DAYS} days out")
    for problem in problems:
        print(f".trivyignore.yaml: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
