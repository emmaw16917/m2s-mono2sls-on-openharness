"""Acme Air, same-region comparison: the official JMeter workload runs on a
load-generator EC2 in us-east-1 against (a) the monolith on its own t3.small
EC2 (Node + MongoDB 4.4, the app's usual single-box deployment) and (b) the
migrated v0 / optimized serverless stacks (API Gateway + Lambda + DynamoDB),
so both sides see the same client and an in-region network hop.

    python e2e_ec2.py [--only monolith|v0|opt ...] [--threads 10] [--loops 20] [--keep-infra]

Everything EC2-side is one CloudFormation stack (m2s-e2e-acmeair-infra:
2 instances, security group, SSM instance role, results bucket) driven over
SSM Run Command -- no SSH, no key pair -- and deleted at the end. Each
variant gets two JMeter passes: pass 1 includes cold starts / cold caches,
pass 2 is warm. Server-side time: Lambda REPORT durations (CloudWatch) for
the serverless variants, morgan's per-request response time for the monolith.

Outputs: Mono2Sls-artifacts/tests/results-e2e/acmeair-ec2/ (report.md,
report.json, <variant>.pass<N>.jtl, logs).
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from pathlib import Path
from urllib.parse import urlparse

import boto3

import e2e_aws
import e2e_jmeter
from e2e_aws import REGION, RUNS, TESTS, sh

APP = "acmeair"
INFRA = "m2s-e2e-acmeair-infra"
REPO_URL = "https://github.com/acmeair/acmeair-nodejs.git"
REPO_COMMIT = "11fa31ab5cd21ca184f760680e8aecc52ef4286a"  # = mono_benchmark/acmeair
EC2_PRICE_T3_SMALL = 0.0208  # USD/hour, us-east-1 on-demand Linux

TEMPLATE = """
AWSTemplateFormatVersion: '2010-09-09'
Parameters:
  Ami:
    Type: AWS::SSM::Parameter::Value<AWS::EC2::Image::Id>
    Default: /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64
  Subnet:
    Type: AWS::EC2::Subnet::Id
  Vpc:
    Type: AWS::EC2::VPC::Id
Resources:
  Results:
    Type: AWS::S3::Bucket
  Role:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement: [{Effect: Allow, Principal: {Service: ec2.amazonaws.com}, Action: sts:AssumeRole}]
      ManagedPolicyArns: [arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore]
      Policies:
        - PolicyName: results
          PolicyDocument:
            Version: '2012-10-17'
            Statement: [{Effect: Allow, Action: [s3:PutObject, s3:GetObject], Resource: !Sub '${Results.Arn}/*'}]
  Profile:
    Type: AWS::IAM::InstanceProfile
    Properties: {Roles: [!Ref Role]}
  Sg:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupDescription: m2s e2e acmeair - no inbound from outside, loadgen to monolith 9080 only
      VpcId: !Ref Vpc
  SgSelf:
    Type: AWS::EC2::SecurityGroupIngress
    Properties: {GroupId: !Ref Sg, IpProtocol: tcp, FromPort: 9080, ToPort: 9080, SourceSecurityGroupId: !Ref Sg}
  Monolith:
    Type: AWS::EC2::Instance
    Properties:
      ImageId: !Ref Ami
      InstanceType: t3.small
      SubnetId: !Ref Subnet
      SecurityGroupIds: [!Ref Sg]
      IamInstanceProfile: !Ref Profile
      BlockDeviceMappings: [{DeviceName: /dev/xvda, Ebs: {VolumeSize: 20, VolumeType: gp3}}]
      Tags: [{Key: Name, Value: m2s-e2e-acmeair-monolith}]
  LoadGen:
    Type: AWS::EC2::Instance
    Properties:
      ImageId: !Ref Ami
      InstanceType: t3.small
      SubnetId: !Ref Subnet
      SecurityGroupIds: [!Ref Sg]
      IamInstanceProfile: !Ref Profile
      BlockDeviceMappings: [{DeviceName: /dev/xvda, Ebs: {VolumeSize: 20, VolumeType: gp3}}]
      Tags: [{Key: Name, Value: m2s-e2e-acmeair-loadgen}]
Outputs:
  Bucket: {Value: !Ref Results}
  MonolithId: {Value: !Ref Monolith}
  MonolithIp: {Value: !GetAtt Monolith.PrivateIp}
  LoadGenId: {Value: !Ref LoadGen}
"""

SETUP_COMMON = """set -e
dnf install -y -q docker git >/dev/null
systemctl enable --now docker
cd /opt && rm -rf acmeair && git clone -q {repo} acmeair && cd acmeair && git checkout -q {commit}
"""

SETUP_MONOLITH = SETUP_COMMON + """
docker rm -f mongo app >/dev/null 2>&1 || true
docker run -d --name mongo --network host mongo:4.4 >/dev/null
# the app reads settings.json: port 9080, contextRoot /rest/api, mongo 127.0.0.1, useDevLogger (morgan dev) true
docker run -d --name app --network host -v /opt/acmeair:/app -w /app node:22 sh -c 'npm install --no-audit --no-fund --omit=optional >/tmp/npm.log 2>&1 && node app.js' >/dev/null
for i in $(seq 1 90); do curl -sf http://127.0.0.1:9080/rest/api/checkstatus >/dev/null && echo READY && exit 0; sleep 5; done
docker logs app | tail -20; exit 1
"""

SETUP_LOADGEN = SETUP_COMMON + """
docker pull -q justb4/jmeter:5.5 >/dev/null
J=/opt/jm && rm -rf $J && mkdir -p $J && S=/opt/acmeair/document/workload/jmeter
cp $S/Airports.csv $S/Airports2.csv $S/lib/acmeair-driver-1.0-SNAPSHOT.jar $J/
sed 's#<stringProp name="HTTPSampler.protocol"></stringProp>#<stringProp name="HTTPSampler.protocol">${{__P(PROTOCOL,http)}}</stringProp>#' $S/AcmeAir.jmx > $J/AcmeAir.jmx
curl -sfL -o $J/json-simple-1.1.1.jar https://repo1.maven.org/maven2/com/googlecode/json-simple/json-simple/1.1.1/json-simple-1.1.1.jar
echo READY
"""

JMETER = """set -e
J=/opt/jm; echo "{host},{port},{ctx}" > $J/hosts.csv; rm -f $J/result.jtl $J/jmeter.log
# TZ=UTC: the driver's dates in the image's default zone (CEST) are unparseable by JavaScript
docker run --rm -e TZ=UTC -v $J:/test -w /test --entrypoint sh justb4/jmeter:5.5 -c \
  'cp /test/*.jar $JMETER_HOME/lib/ext/ && jmeter -n -t AcmeAir.jmx -JNUM_THREAD={threads} -JLOOP_COUNT={loops} -JPROTOCOL={protocol} -DusePureIDs=true -l /test/result.jtl -j /test/jmeter.log' | tail -3
aws s3 cp --quiet $J/result.jtl s3://{bucket}/{key}.jtl
aws s3 cp --quiet $J/jmeter.log s3://{bucket}/{key}.jmeter.log
"""


def _cf():
    return boto3.client("cloudformation", region_name=REGION)


def infra_up(log: Path) -> dict:
    ec2 = boto3.client("ec2", region_name=REGION)
    vpc = ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    subnet = next(s["SubnetId"] for s in ec2.describe_subnets(Filters=[{"Name": "default-for-az", "Values": ["true"]}])["Subnets"]
                  if s["AvailabilityZone"] == f"{REGION}a")
    if not e2e_aws.stack_exists(INFRA):
        _cf().create_stack(StackName=INFRA, TemplateBody=TEMPLATE, Capabilities=["CAPABILITY_IAM"],
                           Parameters=[{"ParameterKey": "Vpc", "ParameterValue": vpc},
                                       {"ParameterKey": "Subnet", "ParameterValue": subnet}])
        _cf().get_waiter("stack_create_complete").wait(StackName=INFRA, WaiterConfig={"Delay": 15, "MaxAttempts": 80})
    out = {o["OutputKey"]: o["OutputValue"] for o in _cf().describe_stacks(StackName=INFRA)["Stacks"][0]["Outputs"]}
    ssm = boto3.client("ssm", region_name=REGION)
    for _ in range(60):  # SSM agent registration
        ids = {i["InstanceId"] for i in ssm.describe_instance_information()["InstanceInformationList"]}
        if {out["MonolithId"], out["LoadGenId"]} <= ids:
            break
        time.sleep(10)
    with log.open("a", encoding="utf-8") as f:
        f.write(f"infra up: {json.dumps(out)}\n")
    return out


def infra_down(log: Path) -> None:
    if not e2e_aws.stack_exists(INFRA):
        return
    out = {o["OutputKey"]: o["OutputValue"] for o in _cf().describe_stacks(StackName=INFRA)["Stacks"][0].get("Outputs", [])}
    if out.get("Bucket"):
        b = boto3.resource("s3", region_name=REGION).Bucket(out["Bucket"])
        b.object_versions.delete()
        b.objects.all().delete()
    _cf().delete_stack(StackName=INFRA)
    _cf().get_waiter("stack_delete_complete").wait(StackName=INFRA, WaiterConfig={"Delay": 15, "MaxAttempts": 80})
    with log.open("a", encoding="utf-8") as f:
        f.write("infra deleted\n")


def run(instance: str, script: str, log: Path, timeout: int = 3600) -> str:
    """Run a shell script on an instance over SSM; returns stdout (tail)."""
    ssm = boto3.client("ssm", region_name=REGION)
    cid = e2e_aws._retry(lambda: ssm.send_command(InstanceIds=[instance], DocumentName="AWS-RunShellScript",
                                                  Parameters={"commands": [script], "executionTimeout": [str(timeout)]},
                                                  TimeoutSeconds=600)["Command"]["CommandId"])
    deadline = time.time() + timeout + 120
    while time.time() < deadline:
        time.sleep(5)
        try:
            inv = ssm.get_command_invocation(CommandId=cid, InstanceId=instance)
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        except Exception:  # noqa: BLE001 -- flaky control-plane TLS from this machine
            continue
        if inv["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
            text = inv.get("StandardOutputContent", "") + inv.get("StandardErrorContent", "")
            with log.open("a", encoding="utf-8") as f:
                f.write(f"--- {instance} {inv['Status']}\n{text}\n")
            if inv["Status"] != "Success":
                raise RuntimeError(f"SSM command on {instance} {inv['Status']}: {text[-1500:]}")
            return inv.get("StandardOutputContent", "")
    raise RuntimeError(f"SSM command {cid} on {instance} did not finish")


def fetch(bucket: str, key: str, dest: Path) -> Path:
    e2e_aws._retry(lambda: boto3.client("s3", region_name=REGION).download_file(bucket, key, str(dest)))
    return dest


def jmeter_passes(infra: dict, variant: str, host: str, port: int, ctx: str, protocol: str, threads: int, loops: int,
                  out_dir: Path, log: Path) -> list[dict]:
    passes = []
    for n in (1, 2):
        key = f"{variant}.pass{n}"
        started = time.time()
        run(infra["LoadGenId"], JMETER.format(host=host, port=port, ctx=ctx, protocol=protocol, threads=threads, loops=loops,
                                              bucket=infra["Bucket"], key=key), log)
        ended = time.time()
        jtl = fetch(infra["Bucket"], f"{key}.jtl", out_dir / f"{key}.jtl")
        fetch(infra["Bucket"], f"{key}.jmeter.log", out_dir / f"{key}.jmeter.log")
        passes.append({"pass": n, "started": started, "ended": ended, **e2e_jmeter.summarize_jtl(jtl)})
    return passes


_MORGAN = re.compile(r"(GET|POST|PUT|DELETE) (\S+) \S*?(\d{3})\S* (\d+(?:\.\d+)?) ms")


def monolith_server_times(text: str) -> dict:
    by: dict[str, list[float]] = {}
    for m in _MORGAN.finditer(re.sub(r"\x1b\[[0-9;]*m", "", text)):
        path = re.sub(r"/(byid|byuser)/[^/\s?]+", r"/\g<1>/:user", m.group(2))
        by.setdefault(f"{m.group(1)} {path.split('?')[0]}", []).append(float(m.group(4)))
    return {k: {"n": len(v), "p50_ms": round(statistics.median(v), 2), "p90_ms": round(sorted(v)[int(0.9 * (len(v) - 1))], 2)}
            for k, v in sorted(by.items())}


def run_monolith(infra: dict, out_dir: Path, threads: int, loops: int, customers: int) -> dict:
    log = out_dir / "monolith.log"
    out = run(infra["MonolithId"], SETUP_MONOLITH.format(repo=REPO_URL, commit=REPO_COMMIT), log, timeout=1800)
    if "READY" not in out:
        raise RuntimeError("monolith did not start")
    base = f"http://{infra['MonolithIp']}:9080/rest/api"
    loaded = run(infra["LoadGenId"], f"curl -sf '{base}/loader/load?numCustomers={customers}'", log, timeout=1800)
    counts = json.loads(run(infra["LoadGenId"], "printf '{' ; for k in Customers Flights FlightSegments Airports; do "
                            f"printf '\"%s\":%s,' $k $(curl -sf {base}/config/count$k); done; printf '\"_\":0}}'", log))
    run(infra["MonolithId"], "docker logs app > /tmp/before.log 2>&1; wc -l < /tmp/before.log", log)
    passes = jmeter_passes(infra, "monolith", infra["MonolithIp"], 9080, "", "http", threads, loops, out_dir, log)
    # morgan lines written during the passes (after the loader)
    tail = run(infra["MonolithId"], "docker logs app 2>&1 | tail -n +$(( $(wc -l < /tmp/before.log) + 1 )) > /tmp/run.log; "
               f"aws s3 cp --quiet /tmp/run.log s3://{infra['Bucket']}/monolith.app.log; wc -l < /tmp/run.log", log)
    app_log = fetch(infra["Bucket"], "monolith.app.log", out_dir / "monolith.app.log").read_text(encoding="utf-8", errors="replace")
    after = run(infra["LoadGenId"], f"echo $(curl -sf {base}/config/countBookings) $(curl -sf {base}/config/countSessions)", log).split()
    hours = (passes[-1]["ended"] - passes[0]["started"]) / 3600
    return {"variant": "monolith", "where": "EC2 t3.small us-east-1a (Node 22 + MongoDB 4.4, same box)", "loader": loaded.strip(),
            "counts": counts, "passes": passes, "server_time": monolith_server_times(app_log), "morgan_lines": tail.strip(),
            "counts_after": {"Bookings": after[0] if after else None, "Sessions": after[1] if len(after) > 1 else None},
            "cost_usd": {"ec2_during_passes": round(hours * EC2_PRICE_T3_SMALL, 6), "ec2_per_hour": EC2_PRICE_T3_SMALL}}


def run_serverless(infra: dict, variant: str, ref: str, workspace: str, out_dir: Path, threads: int, loops: int,
                   customers: int, keep: bool) -> dict:
    log = out_dir / f"{variant}.log"
    src = e2e_aws.export_variant(RUNS / workspace, ref, e2e_aws.WORK / f"{APP}-{variant}")
    stack = f"m2s-e2e-{APP}-{variant}"
    try:
        stack, outputs, resources = e2e_aws.deploy(APP, variant, src, log)
        api = next(v for k, v in outputs.items() if re.search(r"api.*(url|endpoint)", k, re.I))
        u = urlparse(api.rstrip("/"))
        stage = u.path.rstrip("/")
        if stage.endswith("/rest/api"):
            stage = stage[: -len("/rest/api")]
        base = f"https://{u.netloc}{stage}/rest/api"
        # API Gateway ends a request after 29 s while the loader Lambda (300 s)
        # keeps writing: record what the client saw, then wait for the data
        loaded = run(infra["LoadGenId"], f"curl -s -m 900 -o /tmp/load.out -w '%{{http_code}} %{{time_total}}s' "
                     f"'{base}/loader/load?numCustomers={customers}'; echo; head -c 200 /tmp/load.out", log, timeout=1800)
        run(infra["LoadGenId"], f"for i in $(seq 1 120); do c=$(curl -s {base}/config/countCustomers); f=$(curl -s {base}/config/countFlights); "
            f"[ \"$c\" = \"{customers}\" ] && [ \"$f\" -gt 0 ] 2>/dev/null && sleep 20 && echo $c $f && exit 0; sleep 10; done; exit 1", log, timeout=1500)
        counts = json.loads(run(infra["LoadGenId"], "printf '{' ; for k in Customers Flights FlightSegments Airports; do "
                                f"printf '\"%s\":%s,' $k $(curl -sf {base}/config/count$k); done; printf '\"_\":0}}'", log))
        passes = jmeter_passes(infra, variant, u.netloc, 443, stage, "https", threads, loops, out_dir, log)
        after = run(infra["LoadGenId"], f"echo $(curl -sf {base}/config/countBookings) $(curl -sf {base}/config/countSessions)", log).split()
        raw_dir = out_dir / f"{variant}-aws"
        raw_dir.mkdir(exist_ok=True)
        (raw_dir / "stack.json").write_text(json.dumps({"stack": stack, "outputs": outputs, "resources": resources}, indent=2), encoding="utf-8")
        per_pass = []
        for p in passes:
            m = e2e_aws.collect_metrics(resources, p["started"], p["ended"], raw_dir / f"pass{p['pass']}")
            per_pass.append(e2e_aws.summarize_metrics(m))
        arch = "arm64" if re.search(r"Architectures:\s*\n\s*-\s*arm64", (src / "template.yaml").read_text(encoding="utf-8")) else "x86_64"
        total = {"invocations": sum(s["invocations"] for s in per_pass),
                 "lambda_gb_seconds": round(sum(s["lambda_gb_seconds"] for s in per_pass), 3),
                 "rru": sum(s["rru"] for s in per_pass), "wru": sum(s["wru"] for s in per_pass)}
        return {"variant": variant, "ref": ref, "commit": sh(["git", "-C", str(RUNS / workspace), "rev-parse", "--short", ref]).stdout.strip(),
                "where": "API Gateway + Lambda + DynamoDB, us-east-1", "architecture": arch, "loader": loaded.strip()[:200],
                "counts": counts, "passes": passes, "metrics_per_pass": per_pass, "metrics": total,
                "counts_after": {"Bookings": after[0] if after else None, "Sessions": after[1] if len(after) > 1 else None},
                "cost_usd": e2e_aws.cost(total, arch)}
    finally:
        if not keep and e2e_aws.stack_exists(stack):
            e2e_aws.delete(stack, log)


def _fmt(x):
    return "-" if x is None else x


def report(results: dict, out_dir: Path, threads: int, loops: int) -> None:
    (out_dir / "report.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    vs = [v for v in ("monolith", "v0", "opt") if v in results and not results[v].get("error")]
    md = ["# acmeair: official JMeter workload, same region (us-east-1)", "",
          f"Load generator: EC2 t3.small in us-east-1a running acmeair-driver AcmeAir.jmx ({threads} threads x {loops} loops), "
          "two passes per variant (pass 1 cold, pass 2 warm). Monolith: EC2 t3.small, same AZ. Serverless: API Gateway + Lambda + DynamoDB.", ""]
    for v in ("monolith", "v0", "opt"):
        if results.get(v, {}).get("error"):
            md.append(f"- **{v}: error** {results[v]['error'][:300]}")
    md += ["", "## End-to-end (client = load generator)", "",
           "| variant | pass | samples | errors | req/s | p50 ms | p90 ms | p99 ms |", "|---|---|---|---|---|---|---|---|"]
    for v in vs:
        for p in results[v]["passes"]:
            md.append(f"| {v} | {p['pass']} | {p['samples']} | {p['errors']} | {p['throughput_per_s']} | "
                      f"{p['all']['p50']} | {p['all']['p90']} | {p['all']['p99']} |")
    labels = sorted({k for v in vs for p in results[v]["passes"] for k in p["labels"]})
    md += ["", "## Per request, warm pass (p50 / p90 ms, errors/samples)", "", "| request | " + " | ".join(vs) + " |",
           "|---|" + "---|" * len(vs)]
    for k in labels:
        cells = []
        for v in vs:
            x = results[v]["passes"][-1]["labels"].get(k)
            cells.append(f"{x['p50']} / {x['p90']} ({x['errors']}/{x['n']})" if x else "-")
        md.append(f"| {k} | " + " | ".join(cells) + " |")
    md += ["", "## Server-side time", ""]
    if "monolith" in vs:
        md += ["Monolith (morgan response time, both passes):", "", "| route | n | p50 ms | p90 ms |", "|---|---|---|---|"]
        md += [f"| {k} | {x['n']} | {x['p50_ms']} | {x['p90_ms']} |" for k, x in results["monolith"]["server_time"].items()]
    for v in vs:
        if v == "monolith":
            continue
        md += ["", f"{v} (Lambda REPORT, warm pass; cold starts counted separately):", "",
               "| function | invocations | cold | warm p50 ms | warm p90 ms | init p50 ms |", "|---|---|---|---|---|---|"]
        for fn, x in results[v]["metrics_per_pass"][-1]["functions"].items():
            md.append(f"| {fn} | {x['invocations']} | {x['cold']} | {_fmt(x['warm_p50_ms'])} | {_fmt(x['warm_p90_ms'])} | {_fmt(x['init_p50_ms'])} |")
        c1 = sum(x["cold"] for x in results[v]["metrics_per_pass"][0]["functions"].values())
        md.append(f"\nCold starts in pass 1: {c1}.")
    md += ["", "## Cost and data", "", "| variant | invocations | Lambda GB-s | RRU | WRU | cost USD (both passes) | per 1k requests |",
           "|---|---|---|---|---|---|---|"]
    for v in vs:
        r = results[v]
        n = sum(p["samples"] for p in r["passes"])
        if v == "monolith":
            c = r["cost_usd"]["ec2_during_passes"]
            md.append(f"| monolith | - | - | - | - | {c} (EC2 time during the passes; ${EC2_PRICE_T3_SMALL}/h whether busy or idle) | {round(c / n * 1000, 6)} |")
        else:
            m, c = r["metrics"], r["cost_usd"]["total"]
            md.append(f"| {v} | {m['invocations']} | {m['lambda_gb_seconds']} | {m['rru']} | {m['wru']} | {round(c, 6)} | {round(c / n * 1000, 6)} |")
    md += [""] + [f"- {v}: loader `{results[v].get('loader')}`; counts {results[v].get('counts')}; after the runs {results[v].get('counts_after')}"
                  for v in vs]
    (out_dir / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", choices=["monolith", "v0", "opt"], action="append")
    ap.add_argument("--workspace", default="acmeair-a1")
    ap.add_argument("--v0-ref", default="v0")
    ap.add_argument("--threads", type=int, default=10)
    ap.add_argument("--loops", type=int, default=20)
    ap.add_argument("--customers", type=int, default=10000)
    ap.add_argument("--keep-infra", action="store_true")
    args = ap.parse_args()
    out_dir = TESTS / "results-e2e" / "acmeair-ec2"
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / "infra.log"
    path = out_dir / "results.json"
    results = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    try:
        infra = infra_up(log)
        if "READY" not in run(infra["LoadGenId"], SETUP_LOADGEN.format(repo=REPO_URL, commit=REPO_COMMIT), log, timeout=1800):
            raise RuntimeError("load generator setup failed")
        for v in args.only or ["monolith", "v0", "opt"]:
            print(f"== {v}", flush=True)
            try:
                results[v] = run_monolith(infra, out_dir, args.threads, args.loops, args.customers) if v == "monolith" else \
                    run_serverless(infra, v, args.v0_ref if v == "v0" else "HEAD", args.workspace, out_dir, args.threads,
                                   args.loops, args.customers, False)
            except Exception as exc:  # noqa: BLE001
                results[v] = {"variant": v, "error": str(exc)[:2000]}
            path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
            print(json.dumps({k: results[v].get(k) for k in ("error", "cost_usd")}), flush=True)
    finally:
        if not args.keep_infra:
            infra_down(log)
    report({v: results[v] for v in ("monolith", "v0", "opt") if v in results}, out_dir, args.threads, args.loops)


if __name__ == "__main__":
    main()
