"""Tests for perf.py (run_proxy_metrics / run_rie_verdict helpers / update_ledger).
The measurement paths themselves need sam + Docker and were exercised by
hand (see CHANGELOG step 2); these cover the judging logic and bookkeeping."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import perf  # noqa: E402
import verify  # noqa: E402
from test_verify import TEMPLATE, BLUEPRINT, ANALYSIS  # noqa: E402


@pytest.fixture()
def ws(tmp_path):
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "template.yaml").write_text(TEMPLATE, encoding="utf-8")
    (tmp_path / "storage").mkdir()
    (tmp_path / "storage" / "blueprint.json").write_text(json.dumps({**BLUEPRINT, "metadata": {"application_name": "demo"}}), encoding="utf-8")
    (tmp_path / "storage" / "analysis_report.json").write_text(json.dumps(ANALYSIS), encoding="utf-8")
    return tmp_path


def test_fill_placeholders():
    t = {"id": "book-{i}", "n": "{i}", "cat": "cat-{i % 3}", "m": "{i % 4}", "keep": 7}
    assert perf._fill(t, 5) == {"id": "book-5", "n": 5, "cat": "cat-2", "m": 1, "keep": 7}


def test_direction_needs_non_overlapping_ranges():
    assert perf._direction([350, 356, 358], [80, 90, 93]) == "better"
    assert perf._direction([80, 90], [350, 356]) == "worse"
    assert perf._direction([100, 120], [110, 130]) == "no_diff"
    assert perf._direction([], [1]) == "unknown"


def test_proxy_direction():
    base = {"calls": 30, "depth": 30, "rru": 0, "wru": 30, "sqs_requests": 0, "warm_cpu_ms": {"median": 5.0, "p90": 6.0}}
    cand = {"calls": 30, "depth": 1, "rru": 0, "wru": 30, "sqs_requests": 0, "warm_cpu_ms": {"median": 5.5, "p90": 6.5}}
    d = perf._proxy_direction(base, cand)
    assert d["depth"] == "better" and d["calls"] == "same" and d["warm_cpu"] == "same"
    cand["warm_cpu_ms"] = {"median": 7.0, "p90": 8.0}
    assert perf._proxy_direction(base, cand)["warm_cpu"] == "worse"


def test_validity():
    assert perf._valid({"calls": 3, "status": 200}) is None
    assert "status 404" in perf._valid({"calls": 1, "status": 404})
    assert "no SDK calls" in perf._valid({"calls": 0, "status": 200})
    assert "threw" in perf._valid({"calls": 0, "error": {"type": "E", "message": "x"}})


def test_check_benchmark_and_fixtures(ws, tmp_path):
    m = verify.Model(ws)
    b = {"id": "b1", "function": "books-get", "data_volume": "1 book",
         "request": {"method": "GET", "path": "/api/books/b1", "identity": {"sub": "u"}},
         "generate": {"Books": {"count": 3, "template": {"id": "book-{i}"}}}, "fixtures": {"Books": [{"id": "b1"}]}}
    assert perf.check_benchmark(m, b) == []
    fx = json.loads(perf._fixture_file(m, b, tmp_path / "fx.json").read_text(encoding="utf-8"))
    assert fx["tables"]["BooksTable"]["key"] == ["id"]
    assert [i["id"] for i in fx["tables"]["BooksTable"]["items"]] == ["b1", "book-0", "book-1", "book-2"]
    errs = perf.check_benchmark(m, {**b, "data_volume": ""})
    assert any("data_volume" in e for e in errs)
    with pytest.raises(verify.VerifyError):
        perf._event(m, {**b, "request": {"method": "GET", "path": "/api/books/b1", "identity": None}})


def test_cost_uses_prices():
    p = perf._prices()
    c = perf.cost({"rru": 2, "wru": 3, "sqs_requests": 1}, 100.0, 512)
    assert c["units_usd"] == pytest.approx(2 * p["dynamodb_read_request_unit"] + 3 * p["dynamodb_write_request_unit"] + p["sqs_request"])
    assert c["duration_usd"] == pytest.approx(0.1 * 0.5 * p["lambda_gb_second"])


def test_update_ledger(ws, tmp_path, monkeypatch):
    monkeypatch.setattr(perf, "LEDGER", tmp_path / "ledger.json")
    report = {"confidence": "normal",
              "rule_scan": {"R1": {"hits": 1}, "R2": {"hits": 0}, "R3": {"hits": 0}},
              "changes": [{"id": "C1", "source": "rule:R1", "verdict": "kept"},
                          {"id": "C2", "source": "analysis", "verdict": "kept_inconclusive", "request": "REQ-2", "functions": ["f"]},
                          {"id": "C3", "source": "rule:R2", "verdict": "revert"}]}
    (ws / "output" / "perf_report.json").write_text(json.dumps(report), encoding="utf-8")
    r = perf.update_ledger(str(ws))
    assert r["app"] == "demo" and r["rules"]["R1"]["kept"] == 1 and r["rules"]["R2"]["reverted"] == 1 and r["candidates"] == 1
    perf.update_ledger(str(ws))  # re-run replaces, not duplicates
    led = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert len(led["runs"]) == 1 and led["totals"]["R1"]["hits"] == 1 and len(led["candidate_rules"]) == 1
    (ws / "output" / "perf_report.json").write_text(json.dumps({"changes": []}), encoding="utf-8")
    with pytest.raises(verify.VerifyError):
        perf.update_ledger(str(ws))


def test_update_ledger_counts_fixes_and_reviews(ws, tmp_path, monkeypatch):
    """§3.6: first-try reliability stays measurable -- a change kept only
    after its one fix attempt, or after the verification line's test review,
    is counted separately."""
    monkeypatch.setattr(perf, "LEDGER", tmp_path / "ledger.json")
    report = {"rule_scan": {"R1": {"hits": 2}, "R4": {"hits": 2}},
              "changes": [{"id": "C1", "source": "rule:R1", "verdict": "kept", "fix_attempts": 1},
                          {"id": "C2", "source": "rule:R1", "verdict": "revert", "fix_attempts": 1},
                          {"id": "C3", "source": "rule:R4", "verdict": "kept", "review": {"blocked": "test", "outcome": "tests_fixed"}},
                          {"id": "C4", "source": "rule:R4", "verdict": "blocked_by_coverage"}]}
    (ws / "output" / "perf_report.json").write_text(json.dumps(report), encoding="utf-8")
    r = perf.update_ledger(str(ws))["rules"]
    assert (r["R1"]["kept"], r["R1"]["reverted"], r["R1"]["fix_attempted"], r["R1"]["kept_after_fix"]) == (1, 1, 2, 1)
    assert (r["R4"]["kept"], r["R4"]["reviewed"], r["R4"]["blocked_by_coverage"], r["R4"]["kept_after_fix"]) == (1, 1, 1, 0)


def test_ledger_app_name_is_the_monolith_directory(ws, tmp_path, monkeypatch):
    """The architect's application_name is free text ("presso (coffee order
    management)"); the ledger keys apps by the monolith's directory name."""
    monkeypatch.setattr(perf, "LEDGER", tmp_path / "ledger.json")
    (ws / "storage" / "analysis_report.json").write_text(json.dumps({"spec_meta": {"monolith_root": "D:/bench/coffee"}}), encoding="utf-8")
    (ws / "output" / "perf_report.json").write_text(json.dumps({"rule_scan": {"R1": {"hits": 0}}, "changes": []}), encoding="utf-8")
    assert perf.update_ledger(str(ws))["app"] == "coffee"


def test_fill_time_relative_placeholders():
    """Buckets around the time of the run: a benchmark whose data depends on
    the current time never expires (coffee's verify-token benchmark seeded
    fixed buckets and would have turned INVALID on 2026-10-01)."""
    now = 1_790_571_600_000
    tpl = {"PK": "evt-7-{now_ms // 300000 - 432 + i}", "last_id": "{now_ms // 300000 - 432 + i}",
           "start_ts": "{(now_ms // 300000 - 432 + i) * 300000}", "note": "keep {literal} braces", "n": "{i % 3}"}
    item = perf._fill(tpl, 432, now)
    assert item["PK"] == f"evt-7-{now // 300000}" and item["last_id"] == now // 300000
    assert item["start_ts"] == (now // 300000) * 300000 and item["n"] == 0
    assert item["note"] == "keep {literal} braces"
    assert perf._fill("{__import__('os')}", 0, now) == "{__import__('os')}"  # not arithmetic: left alone
