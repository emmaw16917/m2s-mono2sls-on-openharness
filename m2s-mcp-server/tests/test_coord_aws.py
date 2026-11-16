"""Unit tests for coord/aws.py (v3 M4/W4.7), with a fake AWS backend."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord import aws  # noqa: E402


class FakeBackend:
    def __init__(self, samples: dict[str, list[float]], invocations: dict[str, int] | None = None) -> None:
        self.samples = samples
        self.counts = invocations or {"fn1": 50}
        self.deployed: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self.loads: list[tuple[str, str]] = []
        self.exists = False

    def deploy(self, ws, ref, stack, log, variant="opt") -> None:
        self.deployed.append((stack, ref, variant))

    def outputs(self, stack):
        return {"ApiEndpoint": f"https://{stack}.example"}

    def delete(self, stack, log) -> None:
        self.deleted.append(stack)

    def stack_exists(self, stack) -> bool:
        return self.exists

    def physical(self, stack, logical) -> str:
        return f"{stack}-{logical}"

    def run_load(self, cmd, env, out, timeout_s):
        side = env["M2S_AWS_SIDE"]
        self.loads.append((side, env["M2S_AWS_TARGET"]))
        return self.samples[side]

    def invocations(self, functions, start, end):
        return {logical: self.counts.get(logical, 0) for logical in functions}


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setenv("M2S_AWS", "1")
    monkeypatch.setattr(aws, "changed_functions", lambda ws, b, r: ["fn1"])
    monkeypatch.setattr(aws, "logical_names", lambda ws, ref, dirs: {"fn1": "Fn1"})


def test_disabled_by_default_and_touches_nothing(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("M2S_AWS", raising=False)
    backend = FakeBackend({})
    result = aws.measure_aws(tmp_path, "cand", load_cmd="true", backend=backend)
    assert result["status"] == "DISABLED" and backend.deployed == [] and backend.loads == []


def test_measure_runs_abab_judges_and_deletes_the_candidate(tmp_path: Path, wired):
    backend = FakeBackend({"b0": [100.0] * 20, "candidate": [80.0] * 20})
    result = aws.measure_aws(tmp_path, "cand", load_cmd="true", repeats=2, backend=backend)
    assert result["status"] == "PASS" and result["verdict"]["verdict"] == "better"
    assert [(p["pass"], p["side"]) for p in result["passes"] if "side" in p] == [
        (1, "b0"), (1, "candidate"), (2, "candidate"), (2, "b0")]  # ABAB alternates
    assert backend.deleted and backend.deleted[0].endswith("-cand")
    assert result["stacks"]["candidate_deleted"] is True
    assert (tmp_path / "perf" / "aws" / "cand" / "result.json").is_file()
    assert (tmp_path / "perf" / "aws_costs.json").is_file()


def test_measure_marks_an_unreached_function_uncovered(tmp_path: Path, wired):
    backend = FakeBackend({"b0": [100.0] * 20, "candidate": [80.0] * 20}, invocations={"Fn1": 0})
    result = aws.measure_aws(tmp_path, "cand", load_cmd="true", backend=backend)
    assert result["uncovered"] == ["fn1"] and result["verdict"]["verdict"] == "uncovered"
    assert result["status"] == "INCONCLUSIVE"


def test_measure_keeps_the_candidate_when_asked(tmp_path: Path, wired):
    backend = FakeBackend({"b0": [100.0] * 20, "candidate": [100.0] * 20})
    result = aws.measure_aws(tmp_path, "cand", load_cmd="true", keep=True, backend=backend)
    assert backend.deleted == [] and result["stacks"]["candidate_deleted"] is False
    assert result["verdict"]["verdict"] == "no_change"


def test_measure_needs_a_load_command(tmp_path: Path, wired):
    result = aws.measure_aws(tmp_path, "cand", backend=FakeBackend({}))
    assert result["status"] == "ERROR" and "load_cmd" in result["error"]


def test_measure_without_a_change_does_not_touch_aws(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("M2S_AWS", "1")
    monkeypatch.setattr(aws, "changed_functions", lambda ws, b, r: [])
    backend = FakeBackend({})
    result = aws.measure_aws(tmp_path, "cand", load_cmd="true", backend=backend)
    assert result["status"] == "NO_CHANGES" and backend.deployed == []


def test_compare_drops_cold_samples_and_uses_the_threshold():
    base = [500.0] + [100.0] * 20  # the 500 is the cold start
    cand = [500.0] + [95.0] * 20   # 5% better: below the 10% floor
    out = aws.compare(aws._warm(base), aws._warm(cand), 0.10)
    assert out["base"]["n"] == 20 and out["base"]["p50"] == 100.0
    assert out["verdict"] == "no_change"
    out = aws.compare(aws._warm(base), aws._warm([500.0] + [80.0] * 20), 0.10)
    assert out["verdict"] == "better" and out["relative_delta"] < -0.1
    out = aws.compare([100.0] * 5, [200.0] * 5, 0.10)
    assert out["small_samples"] is True and out["verdict"] == "worse"


def test_calibrate_writes_the_noise_floor(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("M2S_AWS", "1")
    backend = FakeBackend({"b0": [100.0] * 20})
    # three B0 runs spread by ~4%: threshold is max(0.05, spread*1.5)
    runs = iter([[100.0] * 20, [104.0] * 20, [102.0] * 20])
    backend.run_load = lambda cmd, env, out, timeout_s: next(runs)
    result = aws.calibrate_noise(tmp_path, load_cmd="true", repeats=3, backend=backend)
    assert result["status"] == "PASS"
    data = json.loads((tmp_path / "perf" / "aws_noise.json").read_text(encoding="utf-8"))
    assert data["relative_threshold"] > 0.05 and data["b0_runs"] == [100.0, 104.0, 102.0]
