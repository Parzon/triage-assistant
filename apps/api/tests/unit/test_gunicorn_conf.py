import importlib.util
import os
from pathlib import Path
from types import ModuleType

import pytest

CONF = Path(__file__).resolve().parents[2] / "gunicorn.conf.py"


def load_conf() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gunicorn_conf", CONF)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("cpu_max", "expected"),
    [
        ("200000 100000", 2),  # --cpus 2
        ("250000 100000", 3),  # --cpus 2.5 rounds up
        ("50000 100000", 1),  # --cpus 0.5 still needs one worker
    ],
)
def test_worker_count_follows_the_cgroup_cpu_limit(
    tmp_path: Path, cpu_max: str, expected: int
) -> None:
    (tmp_path / "cpu.max").write_text(cpu_max + "\n")
    assert load_conf().available_cpus(tmp_path / "cpu.max") == expected


@pytest.mark.parametrize("content", ["max 100000", "garbage", None])
def test_without_a_limit_falls_back_to_cpu_affinity(tmp_path: Path, content: str | None) -> None:
    path = tmp_path / "cpu.max"
    if content is not None:
        path.write_text(content)
    assert load_conf().available_cpus(path) == len(os.sched_getaffinity(0))


def test_web_concurrency_overrides_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "7")
    assert load_conf().workers == 7
