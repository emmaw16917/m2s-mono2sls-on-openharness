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
import csv
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
sed -i 's/"MAX_DAYS_TO_SCHEDULE_FLIGHTS" *: *[0-9]*/"MAX_DAYS_TO_SCHEDULE_FLIGHTS" : {flight_days}/' loader/loader-settings.json
grep MAX_DAYS loader/loader-settings.json
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


def minute_gap() -> None:
    """Sleep into the next wall-clock minute (+2 s). DynamoDB capacity metrics
    only exist per minute, so the loader, the count checks and each pass must
    sit in minutes of their own for e2e_aws.collect_metrics to separate them."""
    time.sleep(62 - time.time() % 60)


def jmeter_passes(infra: dict, variant: str, host: str, port: int, ctx: str, protocol: str, threads: int, loops: int,
                  out_dir: Path, log: Path) -> list[dict]:
    passes = []
    for n in (1, 2):
        key = f"{variant}.pass{n}"
        minute_gap()
        started = time.time()
        run(infra["LoadGenId"], JMETER.format(host=host, port=port, ctx=ctx, protocol=protocol, threads=threads, loops=loops,
                                              bucket=infra["Bucket"], key=key), log)
        ended = time.time()
        jtl = fetch(infra["Bucket"], f"{key}.jtl", out_dir / f"{key}.jtl")
        fetch(infra["Bucket"], f"{key}.jmeter.log", out_dir / f"{key}.jmeter.log")
        passes.append({"pass": n, "started": started, "ended": ended, **e2e_jmeter.summarize_jtl(jtl)})
    minute_gap()  # keep the after-run count checks out of the last pass's minute
    return passes


_MORGAN = re.compile(r"(GET|POST|PUT|DELETE) (\S+) \S*?(\d{3})\S* (\d+(?:\.\d+)?) ms")


def monolith_server_times(text: str) -> dict:
    by: dict[str, list[float]] = {}
    for m in _MORGAN.finditer(re.sub(r"\x1b\[[0-9;]*m", "", text)):
        path = re.sub(r"/(byid|byuser)/[^/\s?]+", r"/\g<1>/:user", m.group(2))
        by.setdefault(f"{m.group(1)} {path.split('?')[0]}", []).append(float(m.group(4)))
    return {k: {"n": len(v), "p50_ms": round(statistics.median(v), 2), "p90_ms": round(sorted(v)[int(0.9 * (len(v) - 1))], 2)}
            for k, v in sorted(by.items())}


def run_monolith(infra: dict, out_dir: Path, threads: int, loops: int, customers: int, flight_days: int) -> dict:
    log = out_dir / "monolith.log"
    out = run(infra["MonolithId"], SETUP_MONOLITH.format(repo=REPO_URL, commit=REPO_COMMIT, flight_days=flight_days), log, timeout=1800)
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


ACCESS_LOG_FORMAT = "$context.requestId $context.status $context.responseLatency $context.integrationLatency"


def prepare_variant(src: Path, variant: str, flight_days: int) -> None:
    """Edits to the exported copy (never the workspace): the loader's flight
    days (the driver books return dates 6-12 days out, so fewer than 13 days
    makes every booking one-way), an access log that records the gateway's
    own latencies, and for v0-http the REST API swapped for an HTTP API with
    payload format 1.0 (the REST proxy event shape, so handlers are unchanged)."""
    for f in src.glob("lambdas/*/loader-load/loader/loader-settings.json"):
        f.write_text(re.sub(r'("MAX_DAYS_TO_SCHEDULE_FLIGHTS"\s*:\s*)\d+', rf"\g<1>{flight_days}", f.read_text(encoding="utf-8")), encoding="utf-8")
    t = (src / "template.yaml").read_text(encoding="utf-8")
    # A workspace whose blueprint chose api_type "http" (gate AT1/AT2) already
    # deploys an HTTP API (payload 1.0): only its access log format is set.
    native_http = "AWS::Serverless::HttpApi" in t
    if native_http and variant.endswith("-http"):
        raise SystemExit(f"{variant}: the workspace already deploys an HTTP API; run v0/opt instead")
    key = "AccessLogSettings" if native_http else "AccessLogSetting"
    t, n = re.subn(rf"({key}:\n\s+DestinationArn: !GetAtt ApiLogGroup.Arn\n\s+Format: ).*", rf"\g<1>'{ACCESS_LOG_FORMAT}'", t)
    if n != 1:
        print(f"WARNING {variant}: no {key} -> ApiLogGroup with a Format; the gateway/Lambda split will be missing from the report")
    if variant.endswith("-http"):
        a, b = t.index("  AcmeAirApi:\n"), t.index("\n  # Companion for the Api's AccessLogSetting")
        t = t[:a] + f"""  AcmeAirApi:
    Type: AWS::Serverless::HttpApi
    Properties:
      # e2e A/B variant (e2e_ec2.prepare_variant): HTTP API, $default stage,
      # same CORS answer as the REST API's Cors block.
      CorsConfiguration:
        AllowOrigins: ['*']
        AllowHeaders: ['*']
        AllowMethods: [GET, POST, PUT, DELETE, OPTIONS]
      AccessLogSettings:
        DestinationArn: !GetAtt ApiLogGroup.Arn
        Format: '{ACCESS_LOG_FORMAT}'
""" + t[b:]
        t, n1 = re.subn(r"Type: Api\n", "Type: HttpApi\n", t)
        t, n2 = re.subn(r"( +)RestApiId: !Ref AcmeAirApi\n", r"\1ApiId: !Ref AcmeAirApi\n\1PayloadFormatVersion: '1.0'\n", t)
        t, n3 = re.subn(r" +Auth:\n +Authorizer: NONE\n", "", t)
        t, n4 = re.subn(r"(execute-api\.\$\{AWS::Region\}\.amazonaws\.com)/\$\{Stage\}", r"\1", t)
        assert n1 == n2 == n3 and n1 > 0 and n4 == 1, (n1, n2, n3, n4)
    (src / "template.yaml").write_text(t, encoding="utf-8")


def api_price(template: Path) -> float:
    """Per-request API Gateway price of the deployed (prepared) template: the
    -http variant and a workspace whose blueprint chose api_type http both
    deploy an HTTP API."""
    return e2e_aws.API_GW_HTTP_PER_REQUEST if "AWS::Serverless::HttpApi" in template.read_text(encoding="utf-8") else e2e_aws.API_GW_REST_PER_REQUEST


def gateway_latency(access_log: Path) -> dict | None:
    """p50/p90 of the access log's responseLatency (time inside API Gateway,
    request in to response out) and integrationLatency (the Lambda call as
    the gateway sees it, invoke overhead included) -- see ACCESS_LOG_FORMAT."""
    if not access_log.exists():
        return None
    resp, integ = [], []
    for line in access_log.read_text(encoding="utf-8").splitlines():
        f = line.split("\t", 1)[-1].split()
        if len(f) >= 4 and f[2].isdigit() and f[3].isdigit():
            resp.append(int(f[2]))
            integ.append(int(f[3]))
    if not resp:
        return None
    q = lambda xs, p: sorted(xs)[int(p * (len(xs) - 1))]  # noqa: E731
    gw = [a - b for a, b in zip(resp, integ)]
    return {"n": len(resp), "response_p50": q(resp, .5), "response_p90": q(resp, .9), "integration_p50": q(integ, .5),
            "integration_p90": q(integ, .9), "gateway_own_p50": q(gw, .5), "gateway_own_p90": q(gw, .9)}


def run_serverless(infra: dict, variant: str, ref: str, workspace: str, out_dir: Path, threads: int, loops: int,
                   customers: int, keep: bool, flight_days: int = 13) -> dict:
    log = out_dir / f"{variant}.log"
    src = e2e_aws.export_variant(RUNS / workspace, ref, e2e_aws.WORK / f"{APP}-{variant}")
    prepare_variant(src, variant, flight_days)
    stack = f"m2s-e2e-{APP}-{variant}"
    try:
        stack, outputs, resources = e2e_aws.deploy(APP, variant, src, log)
        api = next(v for k, v in outputs.items() if re.search(r"api.*(url|endpoint)", k, re.I))
        u = urlparse(api.rstrip("/"))
        stage = u.path.rstrip("/")
        if stage.endswith("/rest/api"):
            stage = stage[: -len("/rest/api")]
        base = f"https://{u.netloc}{stage}/rest/api"
        # An edge-optimized REST API is served through CloudFront; until a new API
        # has reached every edge node, some answer 403 ForbiddenException
        # ("x-cache: Error from cloudfront") -- seen from the us-east-1 load
        # generator for minutes after deploy (acmeair opt, 2026-10-09). One 200
        # is not enough: require 30 in a row.
        run(infra["LoadGenId"], f"ok=0; for i in $(seq 1 450); do if [ \"$(curl -s -o /dev/null -w '%{{http_code}}' {base}/checkstatus)\" = 200 ]; "
            "then ok=$((ok+1)); [ $ok -ge 30 ] && echo UP after $i && exit 0; else ok=0; sleep 2; fi; done; exit 1", log, timeout=1200)
        # API Gateway ends a request after 29 s while the loader Lambda (300 s)
        # keeps writing: record what the client saw, then wait for the data
        # retried only on the CloudFront 403 (the request never reached the app)
        loaded = run(infra["LoadGenId"], "for i in $(seq 1 20); do "
                     f"s=$(curl -s -m 900 -o /tmp/load.out -w '%{{http_code}} %{{time_total}}s' '{base}/loader/load?numCustomers={customers}'); "
                     "case \"$s\" in 403*) sleep 15;; *) break;; esac; done; echo \"$s\"; head -c 200 /tmp/load.out", log, timeout=1800)
        run(infra["LoadGenId"], f"for i in $(seq 1 120); do c=$(curl -s {base}/config/countCustomers); f=$(curl -s {base}/config/countFlights); "
            f"[ \"$c\" = \"{customers}\" ] && [ \"$f\" -gt 0 ] 2>/dev/null && [ \"$f\" = \"$prev\" ] && echo $c $f && exit 0; prev=$f; sleep 20; done; exit 1", log, timeout=1500)
        counts = json.loads(run(infra["LoadGenId"], "printf '{' ; for k in Customers Flights FlightSegments Airports; do "
                                f"printf '\"%s\":%s,' $k $(curl -sf {base}/config/count$k); done; printf '\"_\":0}}'", log))
        passes = jmeter_passes(infra, variant, u.netloc, 443, stage, "https", threads, loops, out_dir, log)
        after = run(infra["LoadGenId"], f"echo $(curl -sf {base}/config/countBookings) $(curl -sf {base}/config/countSessions)", log).split()
        raw_dir = out_dir / f"{variant}-aws"
        raw_dir.mkdir(exist_ok=True)
        (raw_dir / "stack.json").write_text(json.dumps({"stack": stack, "outputs": outputs, "resources": resources}, indent=2), encoding="utf-8")
        per_pass = []
        for i, p in enumerate(passes):
            # REPORT lines up to the next pass's start (or 60 s on), never into it
            until = passes[i + 1]["started"] if i + 1 < len(passes) else p["ended"] + 60
            m = e2e_aws.collect_metrics(resources, p["started"], p["ended"], raw_dir / f"pass{p['pass']}", until=until)
            per_pass.append(e2e_aws.summarize_metrics(m))
        arch = "arm64" if re.search(r"Architectures:\s*\n\s*-\s*arm64", (src / "template.yaml").read_text(encoding="utf-8")) else "x86_64"
        total = {"invocations": sum(s["invocations"] for s in per_pass),
                 "lambda_gb_seconds": round(sum(s["lambda_gb_seconds"] for s in per_pass), 3),
                 "rru": sum(s["rru"] for s in per_pass), "wru": sum(s["wru"] for s in per_pass)}
        return {"variant": variant, "ref": ref, "commit": sh(["git", "-C", str(RUNS / workspace), "rev-parse", "--short", ref]).stdout.strip(),
                "where": "API Gateway + Lambda + DynamoDB, us-east-1", "architecture": arch, "loader": loaded.strip()[:200],
                "counts": counts, "passes": passes, "metrics_per_pass": per_pass, "metrics": total,
                "counts_after": {"Bookings": after[0] if after else None, "Sessions": after[1] if len(after) > 1 else None},
                "cost_usd": e2e_aws.cost(total, arch, api_requests=sum(p["samples"] for p in passes),
                                         api_price=api_price(src / "template.yaml")),
                "gateway_latency": gateway_latency(raw_dir / f"pass{passes[-1]['pass']}" / "logs" / "ApiLogGroup.log")}
    finally:
        if not keep and e2e_aws.stack_exists(stack):
            e2e_aws.delete(stack, log)


def _fmt(x):
    return "-" if x is None else x


MIN_N = 10  # fewer samples than this: shown, but flagged as not comparable
_REPORT = re.compile(r"Duration: ([\d.]+) ms\s+Billed Duration: (\d+) ms\s+Memory Size: (\d+) MB\s+Max Memory Used: (\d+) MB"
                     r"(?:\s+Init Duration: ([\d.]+) ms)?")


def _lambda_logs(out_dir: Path, variant: str, n: int) -> list[Path]:
    d = out_dir / f"{variant}-aws" / f"pass{n}" / "logs"
    return sorted(f for f in d.glob("*.log") if f.name != "ApiLogGroup.log") if d.is_dir() else []


def cold_windows(out_dir: Path, variant: str, n: int) -> list[tuple[int, int]]:
    """(INIT_START ms, REPORT ms) of every cold invocation in the saved logs:
    the request that waited for it is the one whose client window contains it."""
    wins = []
    for f in _lambda_logs(out_dir, variant, n):
        init: dict[str, int] = {}
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split("\t", 2)
            if len(parts) < 3 or not parts[0].isdigit():
                continue
            ts, stream, msg = int(parts[0]), parts[1], parts[2]
            if msg.startswith("INIT_START"):
                init[stream] = ts
            elif msg.startswith("REPORT") and "Init Duration" in msg and stream in init:
                wins.append((init.pop(stream), ts))
    return wins


def function_stats(out_dir: Path, variant: str, n: int) -> dict | None:
    """Per function from the saved REPORT lines, one percentile rule
    (e2e_jmeter._pct) for every column, warm sample count included."""
    files = _lambda_logs(out_dir, variant, n)
    if not files:
        return None
    out = {}
    for f in files:
        warm, init = [], []
        for m in _REPORT.finditer(f.read_text(encoding="utf-8", errors="replace")):
            (init.append(float(m.group(5))) if m.group(5) else warm.append(float(m.group(1))))
        if warm or init:
            out[f.stem] = {"invocations": len(warm) + len(init), "cold": len(init), "warm_n": len(warm),
                           "warm_p50_ms": e2e_jmeter._pct(warm, 50), "warm_p90_ms": e2e_jmeter._pct(warm, 90),
                           "init_p50_ms": e2e_jmeter._pct(init, 50)}
    return out


def warm_labels(out_dir: Path, variant: str, n: int) -> dict | None:
    """Per request label of pass n with cold-start samples taken out: a sample
    is cold when a cold invocation (INIT_START..REPORT, 100 ms clock slack)
    lies inside its client window."""
    jtl = out_dir / f"{variant}.pass{n}.jtl"
    if not jtl.exists():
        return None
    wins = cold_windows(out_dir, variant, n)
    by: dict[str, dict] = {}
    with jtl.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            t0, el = int(r["timeStamp"]), int(r["elapsed"])
            x = by.setdefault(r["label"], {"n": 0, "cold": 0, "errors": 0, "warm": []})
            x["n"] += 1
            x["errors"] += r["success"] != "true"
            if any(t0 - 100 <= a and b <= t0 + el + 100 for a, b in wins):
                x["cold"] += 1
            else:
                x["warm"].append(el)
    return {k: {"n": x["n"], "cold": x["cold"], "errors": x["errors"], "warm_n": len(x["warm"]),
                "p50": e2e_jmeter._pct(x["warm"], 50), "p90": e2e_jmeter._pct(x["warm"], 90)} for k, x in by.items()}


def report(results: dict, out_dir: Path, threads: int, loops: int) -> None:
    (out_dir / "report.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    vs = [v for v in ("monolith", "v0", "v0-http", "opt") if v in results and not results[v].get("error")]
    md = ["# acmeair: official JMeter workload, same region (us-east-1)", "",
          f"Load generator: EC2 t3.small in us-east-1a running acmeair-driver AcmeAir.jmx ({threads} threads x {loops} loops), "
          "two passes per variant (pass 1 cold, pass 2 warm). Monolith: EC2 t3.small, same AZ. Serverless: API Gateway + Lambda + DynamoDB.", ""]
    for v in ("monolith", "v0", "v0-http", "opt"):
        if results.get(v, {}).get("error"):
            md.append(f"- **{v}: error** {results[v]['error'][:300]}")
    md += ["", "## End-to-end (client = load generator)", "",
           "| variant | pass | samples | errors | req/s | p50 ms | p90 ms | p99 ms |", "|---|---|---|---|---|---|---|---|"]
    for v in vs:
        for p in results[v]["passes"]:
            md.append(f"| {v} | {p['pass']} | {p['samples']} | {p['errors']} | {p['throughput_per_s']} | "
                      f"{p['all']['p50']} | {p['all']['p90']} | {p['all']['p99']} |")
    labels = sorted({k for v in vs for p in results[v]["passes"] for k in p["labels"]})
    warm = {v: warm_labels(out_dir, v, results[v]["passes"][-1]["pass"]) for v in vs}
    md += ["", "## Per request, warm pass (p50 / p90 ms over warm samples; errors/samples, cold)", "",
           "A sample counts as cold when a Lambda cold start (INIT_START to its REPORT) falls inside its client window; "
           "cold samples are left out of p50/p90 and counted after the comma. *few* = under "
           f"{MIN_N} warm samples, not comparable. `-` = the request never ran in this pass (e.g. the driver cancels only "
           "for a customer holding more than 2 bookings, AcmeAir.jmx).", "", "| request | " + " | ".join(vs) + " |",
           "|---|" + "---|" * len(vs)]
    for k in labels:
        cells = []
        for v in vs:
            x = (warm[v] or {}).get(k)
            if x is None:
                y = results[v]["passes"][-1]["labels"].get(k)  # no saved jtl: the summary as recorded
                cells.append(f"{y['p50']} / {y['p90']} ({y['errors']}/{y['n']})" if y else "-")
                continue
            few = " *few*" if x["warm_n"] < MIN_N else ""
            cold = f", cold {x['cold']}" if x["cold"] else ""
            cells.append(f"{_fmt(x['p50'])} / {_fmt(x['p90'])} ({x['errors']}/{x['n']}{cold}){few}")
        md.append(f"| {k} | " + " | ".join(cells) + " |")
    md += ["", "## Server-side time", ""]
    if "monolith" in vs:
        md += ["Monolith (morgan response time, both passes):", "", "| route | n | p50 ms | p90 ms |", "|---|---|---|---|"]
        md += [f"| {k} | {x['n']} | {x['p50_ms']} | {x['p90_ms']} |" for k, x in results["monolith"]["server_time"].items()]
    for v in vs:
        if v == "monolith":
            continue
        last = results[v]["metrics_per_pass"][-1]
        fns = function_stats(out_dir, v, len(results[v]["metrics_per_pass"])) or last["functions"]
        md += ["", f"{v} (Lambda REPORT, warm pass; cold starts counted separately):", "",
               "`-` under warm p50/p90: every invocation in this pass was a cold start (e.g. the count checks the harness "
               "calls once before/after the passes). `-` under init: no cold start in this pass (pass 1 already warmed it). "
               f"*few* = under {MIN_N} warm invocations.", "",
               "| function | invocations | cold | warm p50 ms | warm p90 ms | init p50 ms |", "|---|---|---|---|---|---|"]
        for fn, x in fns.items():
            few = " *few*" if 0 < x.get("warm_n", MIN_N) < MIN_N else ""
            md.append(f"| {fn} | {x['invocations']} | {x['cold']} | {_fmt(x['warm_p50_ms'])}{few} | {_fmt(x['warm_p90_ms'])} | {_fmt(x['init_p50_ms'])} |")
        c1 = sum(x["cold"] for x in results[v]["metrics_per_pass"][0]["functions"].values())
        md.append(f"\nCold starts in pass 1: {c1}.")
    gl = [(v, results[v].get("gateway_latency")) for v in vs if v != "monolith" and results[v].get("gateway_latency")]
    if gl:
        md += ["", "## Where the time goes, warm pass (p50 ms)", "",
               "End-to-end from the load generator; API Gateway response latency (request in to response out) and integration latency "
               "(the Lambda call, invoke overhead included) from the access log; Lambda Duration from REPORT.", "",
               "| variant | end-to-end | in API Gateway | of which integration | gateway's own (response - integration) | outside the gateway (e2e - response) |",
               "|---|---|---|---|---|---|"]
        for v, g in gl:
            e2e = results[v]["passes"][-1]["all"]["p50"]
            md.append(f"| {v} | {e2e} | {g['response_p50']} | {g['integration_p50']} | {g['gateway_own_p50']} | {round(e2e - g['response_p50'], 1)} |")
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
    ap.add_argument("--only", choices=list(("monolith", "v0", "v0-http", "opt")), action="append")
    ap.add_argument("--workspace", default="acmeair-a1")
    ap.add_argument("--v0-ref", default="v0")
    ap.add_argument("--threads", type=int, default=10)
    ap.add_argument("--loops", type=int, default=20)
    ap.add_argument("--customers", type=int, default=10000)
    ap.add_argument("--keep-infra", action="store_true")
    ap.add_argument("--flight-days", type=int, default=13, help="loader MAX_DAYS_TO_SCHEDULE_FLIGHTS (>=13 for round trips)")
    ap.add_argument("--out-name", default="acmeair-ec2", help="results-e2e/<name>")
    ap.add_argument("--report-only", action="store_true", help="rebuild report.md from results.json and the saved jtl/logs; no AWS")
    args = ap.parse_args()
    out_dir = TESTS / "results-e2e" / args.out_name
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / "infra.log"
    path = out_dir / "results.json"
    results = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if args.report_only:
        report({v: results[v] for v in ("monolith", "v0", "v0-http", "opt") if v in results}, out_dir, args.threads, args.loops)
        return
    try:
        infra = infra_up(log)
        if "READY" not in run(infra["LoadGenId"], SETUP_LOADGEN.format(repo=REPO_URL, commit=REPO_COMMIT), log, timeout=1800):
            raise RuntimeError("load generator setup failed")
        for v in args.only or list(("monolith", "v0", "v0-http", "opt")):
            print(f"== {v}", flush=True)
            try:
                results[v] = run_monolith(infra, out_dir, args.threads, args.loops, args.customers, args.flight_days) if v == "monolith" else \
                    run_serverless(infra, v, args.v0_ref if v.startswith("v0") else "HEAD", args.workspace, out_dir, args.threads,
                                   args.loops, args.customers, False, args.flight_days)
            except Exception as exc:  # noqa: BLE001
                results[v] = {"variant": v, "error": str(exc)[:2000]}
            path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
            print(json.dumps({k: results[v].get(k) for k in ("error", "cost_usd")}), flush=True)
    finally:
        if not args.keep_infra:
            infra_down(log)
    report({v: results[v] for v in ("monolith", "v0", "v0-http", "opt") if v in results}, out_dir, args.threads, args.loops)


if __name__ == "__main__":
    main()
