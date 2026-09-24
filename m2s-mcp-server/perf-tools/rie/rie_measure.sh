#!/usr/bin/env bash
# Usage: rie_measure.sh <BuildDir> <event.json> <cpus|0> [extra docker -e args...]
# Runs the Lambda RIE image on a `sam build` artifact (code docker-cp'd into the
# container, not bind-mounted: Windows bind mounts make module loading ~5x
# slower), does 1 cold + 30 warm invokes over 127.0.0.1 (with HTTP_PROXY set and no NO_PROXY, localhost gets routed to the proxy: 502 after ~3.7s
# per request on this Windows box), and summarises in-container timings:
#   init_ms  = rapid "INIT REPORT(durationMs)"  (RIE's own "Init Duration" field is a stub)
#   warm_*   = REPORT "Duration" of invokes 2..31
# RIE's "Max Memory Used" just echoes MemorySize, so it is not reported.
export PATH="$PATH:/c/Program Files/Docker/Docker/resources/bin"; export MSYS_NO_PATHCONV=1
fn=$1; ev=$2; cpus=$3; shift 3
name=rie-$(echo $fn | tr 'A-Z' 'a-z'); docker rm -f $name >/dev/null 2>&1
cpuarg=(); [ "$cpus" != "0" ] && cpuarg=(--cpus "$cpus")
docker create --name $name "${cpuarg[@]}" -p 9002:8080 \
  -e NODE_OPTIONS="--require /var/task/http-shim.js" -e AWS_REGION=us-east-1 -e AWS_LAMBDA_FUNCTION_MEMORY_SIZE=512 \
  -e BOOKS_TABLE=t -e CART_TABLE=t -e ORDERS_TABLE=t -e BESTSELLERS_TABLE=t \
  -e BESTSELLER_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/123456789012/q "$@" \
  public.ecr.aws/lambda/nodejs:22-x86_64 handler.lambda_handler >/dev/null
docker cp "D:/tmp/m2s-samfine/.aws-sam/build/$fn/." $name:/var/task >/dev/null
docker start $name >/dev/null; sleep 1.5
for i in $(seq 1 31); do curl -s -o /dev/null -XPOST http://127.0.0.1:9002/2015-03-31/functions/function/invocations -d @$ev; done
docker logs $name 2>&1 | grep -E "REPORT" | node -e '
let d="";process.stdin.on("data",c=>d+=c).on("end",()=>{const L=d.trim().split("\n");
const init=+(/INIT REPORT\(durationMs: ([\d.]+)/.exec(d)||[0,NaN])[1];
const dur=L.filter(l=>l.startsWith("REPORT")).map(l=>+/\tDuration: ([\d.]+)/.exec(l)[1]);
const warm=dur.slice(1).sort((a,b)=>a-b); const q=(p)=>warm[Math.floor(p*(warm.length-1))];
console.log(JSON.stringify({fn:process.argv[1],cpus:process.argv[2],n_warm:warm.length,init_ms:+init.toFixed(1),warm_median_ms:q(0.5),warm_p10:q(0.1),warm_p90:q(0.9)}))})' "$fn$EXTRA_LABEL" $cpus
[ -n "$KEEP" ] || docker rm -f $name >/dev/null
