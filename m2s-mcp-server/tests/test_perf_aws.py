"""run_aws_verdict (PERF_OPTIMIZATION_DESIGN.md §2.2): the judging rules on a
fake measurement. The real deployment path is exercised by hand with M2S_AWS=1;
these cover how the numbers become a verdict."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import perf  # noqa: E402
from coord import aws as aws_mod  # noqa: E402
from test_verify import TEMPLATE, BLUEPRINT, ANALYSIS  # noqa: E402


@pytest.fixture()
def ws(tmp_path):
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "template.yaml").write_text(TEMPLATE, encoding="utf-8")
    (tmp_path / "storage").mkdir()
    (tmp_path / "storage" / "blueprint.json").write_text(json.dumps(BLUEPRINT), encoding="utf-8")
    (tmp_path / "storage" / "analysis_report.json").write_text(json.dumps(ANALYSIS), encoding="utf-8")
    benchmark = {"id": "b1", "function": "books-get", "data_volume": "1 book",
                 "request": {"method": "GET", "path": "/api/books/b1", "identity": {"sub": "u"}},
                 "fixtures": {"Books": [{"id": "b1"}]}}
    (tmp_path / "perf" / "benchmarks" / "books-get").mkdir(parents=True)
    (tmp_path / "perf" / "benchmarks" / "books-get" / "b1.json").write_text(json.dumps(benchmark), encoding="utf-8")
    return tmp_path


@pytest.fixture()
def aws_on(monkeypatch):
    monkeypatch.setattr(aws_mod, "enabled", lambda: True)


def measured(*, dur=1000.0, cand_dur=1000.0, init=100.0, cand_init=100.0, wru=0.0, cand_wru=0.0,
             init_count=1, cand_init_count=1, p50=100.0, cand_p50=100.0, p95=200.0, cand_p95=200.0,
             small=False, uncovered=None, api="rest", threshold=0.10, n=20):
    """A measure_aws result with both sides at n warm samples."""
    def side(duration, init_ms, write_units, cold):
        return {"passes": 3, "requests": n, "invocations": n, "warm_count": n - cold,
                "duration_sum_ms": duration * n, "duration_mean_ms": duration,
                "init_count": cold, "init_mean_ms": init_ms, "rru": 0.0, "wru": write_units}

    uncovered = uncovered or []
    return {"status": "INCONCLUSIVE" if (small or uncovered) else "PASS",
            "verdict": {"base": {"n": n, "p50": p50, "p95": p95}, "candidate": {"n": n, "p50": cand_p50, "p95": cand_p95},
                        "small_samples": small},
            "sides": {"b0": side(dur, init, wru, init_count), "candidate": side(cand_dur, cand_init, cand_wru, cand_init_count)},
            "uncovered": uncovered, "noise_threshold": threshold, "api_type": api}


def judge(ws, monkeypatch, result, **kw):
    monkeypatch.setattr(aws_mod, "measure_aws", lambda *a, **k: result)
    return perf.run_aws_verdict(str(ws), "b1", load_cmd="load", **kw)


def test_kept_when_duration_and_marginal_cost_better(ws, aws_on, monkeypatch):
    r = judge(ws, monkeypatch, measured(dur=1000.0, cand_dur=500.0))
    assert r["status"] == "OK" and r["verdict"] == "kept"
    assert r["dimensions"] == {"latency": "better", "cost": "better"}
    assert r["guards"]["p50"] == "no_diff" and r["guards"]["cold_start"] == "no_diff"


def test_revert_when_duration_gets_worse(ws, aws_on, monkeypatch):
    r = judge(ws, monkeypatch, measured(dur=500.0, cand_dur=1000.0))
    assert r["verdict"] == "revert" and r["dimensions"]["latency"] == "worse"


def test_trade_off_between_primary_dimensions_needs_a_person(ws, aws_on, monkeypatch):
    # Faster, but so many more write units that the marginal cost rises.
    r = judge(ws, monkeypatch, measured(dur=1000.0, cand_dur=500.0, wru=0.0, cand_wru=10000.0))
    assert r["dimensions"] == {"latency": "better", "cost": "worse"}
    assert r["verdict"] == "pending_human"


def test_a_p95_guard_that_gets_worse_needs_a_person(ws, aws_on, monkeypatch):
    r = judge(ws, monkeypatch, measured(dur=1000.0, cand_dur=500.0, p95=200.0, cand_p95=400.0))
    assert r["guards"]["p95"] == "worse" and r["verdict"] == "pending_human"


def test_a_p50_guard_that_gets_worse_needs_a_person(ws, aws_on, monkeypatch):
    r = judge(ws, monkeypatch, measured(dur=1000.0, cand_dur=500.0, p50=100.0, cand_p50=200.0))
    assert r["guards"]["p50"] == "worse" and r["verdict"] == "pending_human"


def test_a_cold_start_regression_needs_a_person(ws, aws_on, monkeypatch):
    r = judge(ws, monkeypatch, measured(dur=1000.0, cand_dur=500.0, init=100.0, cand_init=400.0))
    assert r["guards"]["cold_start"] == "worse" and r["verdict"] == "pending_human"


def test_new_cold_starts_are_a_worse_guard(ws, aws_on, monkeypatch):
    r = judge(ws, monkeypatch, measured(dur=1000.0, cand_dur=500.0, init_count=0, cand_init_count=1))
    assert r["guards"]["cold_start"] == "worse" and r["verdict"] == "pending_human"


def test_an_uncovered_function_is_inconclusive(ws, aws_on, monkeypatch):
    r = judge(ws, monkeypatch, measured(dur=1000.0, cand_dur=500.0, uncovered=["consumer"]))
    assert r["status"] == "OK" and r["verdict"] == "INCONCLUSIVE" and "consumer" in r["reason"]


def test_too_few_warm_samples_is_inconclusive(ws, aws_on, monkeypatch):
    r = judge(ws, monkeypatch, measured(dur=1000.0, cand_dur=500.0, small=True))
    assert r["verdict"] == "INCONCLUSIVE"


def test_no_significant_difference_and_better_proxies_is_kept_inconclusive(ws, aws_on, monkeypatch):
    r = judge(ws, monkeypatch, measured(dur=1000.0, cand_dur=1000.0), proxy_better=True)
    assert r["dimensions"] == {"latency": "no_diff", "cost": "no_diff"}
    assert r["verdict"] == "kept_inconclusive"


def test_no_significant_difference_without_proxies_is_reverted(ws, aws_on, monkeypatch):
    assert judge(ws, monkeypatch, measured())["verdict"] == "revert"


def test_marginal_cost_is_judged_and_total_cost_reported_with_api_gateway(ws, aws_on, monkeypatch):
    r = judge(ws, monkeypatch, measured(dur=1000.0, cand_dur=500.0, api="http"))
    p = perf._prices()
    for side in ("b0", "candidate"):
        cost = r["cost"][side]
        assert cost["total_per_request"] == pytest.approx(cost["marginal_per_request"] + p["api_gateway_http_request"])
        assert cost["api_gateway_usd"] == pytest.approx(cost["requests"] * p["api_gateway_http_request"])
    assert r["api_type"] == "http"


def test_a_budget_overrun_needs_a_person(ws, aws_on, monkeypatch):
    r = judge(ws, monkeypatch, measured(dur=1000.0, cand_dur=500.0), budget_usd=1e-9)
    assert r["verdict"] == "pending_human" and "budget" in r["reason"]


def test_disabled_without_the_env_flag(ws, monkeypatch):
    monkeypatch.setattr(aws_mod, "enabled", lambda: False)
    r = perf.run_aws_verdict(str(ws), "b1", load_cmd="x")
    assert r["status"] == "DISABLED"


def test_a_load_command_is_required(ws, aws_on):
    r = perf.run_aws_verdict(str(ws), "b1")
    assert r["status"] == "ERROR" and "load_cmd" in r["error"]


def test_the_result_is_saved_under_perf_verdicts(ws, aws_on, monkeypatch):
    r = judge(ws, monkeypatch, measured(dur=1000.0, cand_dur=500.0))
    saved = ws / r["saved_to"]
    assert saved.is_file() and json.loads(saved.read_text(encoding="utf-8"))["verdict"] == "kept"
