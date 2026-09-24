#!/usr/bin/env python3
"""Every setting of the api (apps/api/app/config.py) is listed in the api's
`environment:` in compose.yaml.

A variable compose does not list never reaches the container: setting it in
.env changes nothing, and nothing says so. Found in v0.5.0, where 18
documented settings could not be changed from .env. Run by `make lint`.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def settings_fields() -> set[str]:
    tree = ast.parse((ROOT / "apps/api/app/config.py").read_text())
    (settings,) = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Settings"]
    return {
        node.target.id.upper()
        for node in settings.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        and node.target.id != "model_config"
    }


def compose_api_environment() -> set[str]:
    # No YAML parser on a bare host: the api block's environment keys are
    # the lines indented six spaces between "  api:" and the next service.
    names, in_api, in_env = set(), False, False
    for line in (ROOT / "compose.yaml").read_text().splitlines():
        if line.startswith("  ") and not line.startswith("   ") and line.strip().endswith(":"):
            in_api, in_env = line.strip() == "api:", False
        elif in_api and line.startswith("    ") and not line.startswith("     "):
            in_env = line.strip() == "environment:"
        elif in_api and in_env and line.startswith("      ") and not line.startswith("       "):
            key = line.strip().split(":", 1)[0]
            if key and not key.startswith("#"):
                names.add(key)
    return names


def main() -> int:
    missing = sorted(settings_fields() - compose_api_environment())
    if missing:
        print(
            "compose.yaml's api environment does not pass these settings, so .env cannot set "
            f"them: {', '.join(missing)}. Add each as `NAME:` (no value: unset keeps the code's "
            "default).",
            file=sys.stderr,
        )
        return 1
    print(f"settings: all {len(settings_fields())} reach the api from .env")
    return 0


if __name__ == "__main__":
    sys.exit(main())
