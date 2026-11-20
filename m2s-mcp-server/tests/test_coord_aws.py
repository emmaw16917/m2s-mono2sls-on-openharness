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

    def resource_tables(self, stack):
        return ["stack-table"]

    def pass_metrics(self, functions, tables, queues, start, end, until):
        counts = {logical: self.counts.get(logical, 0) for logical in functions}
        lam = {logical: {"invocations": n, "duration_sum_ms": 0.0, "warm_count": n, "warm_duration_sum_ms": 0.0,
                         "duration_mean_ms": 0.0, "billed_ms": 0, "init_count": 0, "init_mean_ms": 0.0}
               for logical, n in counts.items()}
        return {"lambda": lam, "units": {"rru": 0.0, "wru": 0.0, "sqs_requests": 0}, "invocations": counts}


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setenv("M2S_AWS", "1")
    monkeypatch.setattr(aws, "changed_functions", lambda ws, b, r: ["fn1"])
    monkeypatch.setattr(aws, "logical_names", lambda ws, ref, dirs: {"fn1": "Fn1"})
    monkeypatch.setattr(aws, "_minute_gap", lambda: None)
    monkeypatch.setattr(aws, "METRICS_DELAY_S", 0)


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


def test_the_baseline_stack_follows_head(tmp_path, wired, monkeypatch):
    """The rolling baseline must be redeployed when HEAD moved since it was
    deployed; reusing a stale stack would compare the change against old code."""
    class RollingBackend(FakeBackend):
        def __init__(self, tag):
            super().__init__({"b0": [100.0] * 20, "candidate": [80.0] * 20})
            self.tag = tag
            self.tags_seen = {}

        def deploy(self, ws, ref, stack, log, variant="opt", tags=None):
            super().deploy(ws, ref, stack, log, variant)
            self.tags_seen[stack] = dict(tags or {})

        def stack_tags(self, stack):
            return {"M2SBaseline": self.tag} if stack in self.tags_seen else {}

    monkeypatch.setattr(aws, "_commit_sha", lambda ws, ref: "sha-new")
    stale = RollingBackend("sha-old")
    stale.exists = True
    aws.measure_aws(tmp_path, "cand", load_cmd="true", repeats=1, backend=stale)
    baseline_stack = [s for s, _, _ in stale.deployed if s.endswith("-b0")]
    assert baseline_stack and stale.tags_seen[baseline_stack[0]]["M2SBaseline"] == "sha-new"

    fresh = RollingBackend("sha-new")
    fresh.exists = True
    # The baseline stack was already deployed and tagged at the current HEAD.
    fresh.stack_tags = lambda stack: {"M2SBaseline": "sha-new"} if stack.endswith("-b0") else {}
    aws.measure_aws(tmp_path, "cand", load_cmd="true", repeats=1, backend=fresh)
    assert not any(s.endswith("-b0") for s, _, _ in fresh.deployed)


def test_measure_aggregates_lambda_duration_units_and_api_type(tmp_path, wired):
    """The verdict needs more than client samples: Duration/Init from Lambda
    REPORT lines, DynamoDB units and the API type are aggregated per side."""
    class MetricsBackend(FakeBackend):
        def pass_metrics(self, functions, tables, queues, start, end, until):
            return {"lambda": {"Fn1": {"invocations": 100, "duration_sum_ms": 400.0, "warm_count": 98,
                                       "warm_duration_sum_ms": 392.0, "duration_mean_ms": 4.0, "billed_ms": 400,
                                       "init_count": 2, "init_mean_ms": 300.0}},
                    "units": {"rru": 10.0, "wru": 5.0, "sqs_requests": 3},
                    "invocations": {"Fn1": 100}}

        def api_type(self, ws, ref):
            return "http"

    backend = MetricsBackend({"b0": [100.0] * 20, "candidate": [80.0] * 20})
    result = aws.measure_aws(tmp_path, "cand", load_cmd="true", repeats=2, backend=backend)
    side = result["sides"]["b0"]
    assert side["invocations"] == 200 and side["duration_sum_ms"] == 800.0 and side["billed_ms"] == 800.0
    assert side["warm_count"] == 196 and side["duration_mean_ms"] == 4.0
    assert side["init_count"] == 4 and side["init_mean_ms"] == 300.0
    assert side["rru"] == 20.0 and side["wru"] == 10.0 and side["sqs_requests"] == 6
    assert result["api_type"] == "http"
    assert result["baseline_sha"] == ""


def test_endpoint_finds_an_app_prefixed_api_url():
    """Bookstore/SAM templates name the output BookstoreApiUrl, not ApiUrl."""
    assert aws._endpoint({"BookstoreApiUrl": "https://x/staging/", "CognitoUserPoolId": "p"}) == "https://x/staging"
    assert aws._endpoint({"ApiUrl": "https://y"}) == "https://y"
    assert aws._endpoint({"Other": "https://z"}) == ""


def test_logical_names_and_api_type_parse_cfn_tags(tmp_path):
    """SAM templates carry !Sub / !Ref / !GetAtt; plain yaml.safe_load dies on
    them (found by the first real AWS smoke)."""
    from test_verify import TEMPLATE

    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "template.yaml").write_text(TEMPLATE, encoding="utf-8")
    assert aws.logical_names(tmp_path, None, ["books-get"]) == {"books-get": "GetBook"}
    assert aws.AwsBackend().api_type(tmp_path, None) == "rest"


def test_changed_functions_sees_an_untracked_candidate_file(tmp_path):
    """A change may only add a new file; git diff would miss it, git ls-files
    --others catches it (otherwise measure_aws says NO_CHANGES)."""
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True)

    git("init")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    fn = tmp_path / "output" / "lambdas" / "bookstore" / "books-get"
    fn.mkdir(parents=True)
    (fn / "handler.js").write_text("exports.handler = () => {};\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "b0")
    git("tag", "B0")
    (fn / "cache.js").write_text("module.exports = {};\n", encoding="utf-8")
    assert aws.changed_functions(tmp_path.resolve(), "B0", None) == ["books-get"]


def test_measure_seeds_each_side_and_carries_the_benchmark_env(tmp_path, wired, monkeypatch):
    """The load command needs the stack's outputs/resources and the benchmark
    path; the seed command runs once per side, before the passes."""
    import sys

    script = tmp_path / "seed.py"
    script.write_text("import json, os\n"
                      "open(os.environ['SEED_OUT'], 'a', encoding='utf-8').write(json.dumps({k: os.environ.get(k) for k in "
                      "('M2S_AWS_SIDE', 'M2S_AWS_TARGET', 'M2S_AWS_STACK', 'M2S_AWS_BENCHMARK', 'M2S_AWS_OUTPUTS', 'M2S_AWS_RESOURCES')}) + '\\n')\n",
                      encoding="utf-8")
    seed_out = tmp_path / "seed.log"
    monkeypatch.setenv("SEED_OUT", str(seed_out))

    class EnvBackend(FakeBackend):
        def outputs(self, stack):
            return {"ApiEndpoint": f"https://{stack}.example", "UserPoolId": "pool-1"}

        def stack_resources(self, stack):
            return {"CartTable": {"type": "AWS::DynamoDB::Table", "physical": "tbl-1"}}

    backend = EnvBackend({"b0": [100.0] * 20, "candidate": [80.0] * 20})
    seen = {}

    def run_load(cmd, env, out, timeout_s):
        seen.update(env)
        return backend.samples[env["M2S_AWS_SIDE"]]

    backend.run_load = run_load
    seed_cmd = f'"{sys.executable}" "{script}"'
    aws.measure_aws(tmp_path, "cand", load_cmd="true", repeats=1, seed_cmd=seed_cmd,
                    load_env={"M2S_AWS_BENCHMARK": "perf/benchmarks/orders-create/order.json"}, backend=backend)
    lines = [json.loads(line) for line in seed_out.read_text(encoding="utf-8").splitlines()]
    assert [line["M2S_AWS_SIDE"] for line in lines] == ["b0", "candidate"]
    assert lines[0]["M2S_AWS_BENCHMARK"] == "perf/benchmarks/orders-create/order.json"
    assert lines[0]["M2S_AWS_STACK"].startswith("m2s-")
    assert json.loads(lines[0]["M2S_AWS_OUTPUTS"])["UserPoolId"] == "pool-1"
    assert json.loads(lines[0]["M2S_AWS_RESOURCES"])["CartTable"]["physical"] == "tbl-1"
    assert seen["M2S_AWS_BENCHMARK"] == "perf/benchmarks/orders-create/order.json"
    assert json.loads(seen["M2S_AWS_RESOURCES"])["CartTable"]["physical"] == "tbl-1"


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
