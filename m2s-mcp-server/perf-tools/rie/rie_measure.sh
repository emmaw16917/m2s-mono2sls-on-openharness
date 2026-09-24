#!/usr/bin/env bash
# Usage: [BUILD_ROOT=<dir>] [SHIM=<http-shim.js>] [VALIDATE=1] \
#        rie_measure.sh <Function> <event.json> <cpus|0> [extra docker -e args...]
#
# Runs the Lambda RIE image on a `sam build` artifact (<BUILD_ROOT>/<Function>)
# with ../http-shim.js preloaded via NODE_OPTIONS. The code is docker-cp'd into
# the container rather than bind-mounted (Windows bind mounts make init ~5-15x
# slower). Invokes go to 127.0.0.1 with the proxy bypassed (with HTTP_PROXY set
# and no NO_PROXY, requests get routed to the proxy: 502 after ~3.7s).
#
# VALIDATE=1: one invoke with SHIM_LOG=1; prints the status code and how many
# SDK calls the handler made. A non-2xx status or 0 calls means the event did
# not reach the handler's real code path -- fix the event before measuring.
#
# Otherwise: 1 cold + 30 warm invokes; prints in-container timings:
#   init_ms  = rapid "INIT REPORT(durationMs)"  (RIE's own "Init Duration" is a stub)
#   warm_*   = REPORT "Duration" of invokes 2..31
# RIE's "Max Memory Used" just echoes MemorySize, so it is not reported.
export PATH="$PATH:/c/Program Files/Docker/Docker/resources/bin"; export MSYS_NO_PATHCONV=1
fn=$1; ev=$2; cpus=$3; shift 3
BUILD_ROOT=${BUILD_ROOT:-.aws-sam/build}
SHIM=${SHIM:-$(dirname "$0")/../http-shim.js}
PORT=${PORT:-9002}
name=rie-$(echo "$fn" | tr 'A-Z' 'a-z'); docker rm -f "$name" >/dev/null 2>&1
cpuarg=(); [ "$cpus" != "0" ] && cpuarg=(--cpus "$cpus")
logarg=(); [ -n "$VALIDATE" ] && logarg=(-e SHIM_LOG=1)
docker create --name "$name" "${cpuarg[@]}" -p 127.0.0.1:$PORT:8080 \
  -e NODE_OPTIONS="--require /var/task/http-shim.js" -e AWS_REGION=us-east-1 -e AWS_LAMBDA_FUNCTION_MEMORY_SIZE=512 \
  -e ENVIRONMENT=dev -e LOG_LEVEL=INFO \
  -e BOOKS_TABLE=t -e CART_TABLE=t -e ORDERS_TABLE=t -e BESTSELLERS_TABLE=t \
  -e BESTSELLER_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/123456789012/q "${logarg[@]}" "$@" \
  public.ecr.aws/lambda/nodejs:22-x86_64 handler.lambda_handler >/dev/null
docker cp "$BUILD_ROOT/$fn/." "$name:/var/task" >/dev/null
docker cp "$SHIM" "$name:/var/task/http-shim.js" >/dev/null
docker start "$name" >/dev/null; sleep 1.5
URL=http://127.0.0.1:$PORT/2015-03-31/functions/function/invocations
if [ -n "$VALIDATE" ]; then
  resp=$(curl -s --noproxy '*' -XPOST "$URL" -d @"$ev")
  calls=$(docker logs "$name" 2>&1 | grep -c '\[shim\]')
  status=$(printf '%s' "$resp" | node -e 'let d="";process.stdin.on("data",c=>d+=c).on("end",()=>{try{const j=JSON.parse(d);console.log(j.statusCode===undefined?(j.batchItemFailures!==undefined||j.success!==undefined?"async-ok":"n/a"):j.statusCode)}catch(e){console.log("unparseable")}})')
  echo "{\"fn\":\"$fn\",\"status\":\"$status\",\"sdk_calls\":$calls,\"resp\":$(printf '%s' "$resp" | head -c 120 | node -e 'let d="";process.stdin.on("data",c=>d+=c).on("end",()=>console.log(JSON.stringify(d)))')}"
else
  for i in $(seq 1 31); do curl -s --noproxy '*' -o /dev/null -XPOST "$URL" -d @"$ev"; done
  docker logs "$name" 2>&1 | grep -E "REPORT" | node -e '
let d="";process.stdin.on("data",c=>d+=c).on("end",()=>{const L=d.trim().split("\n");
const init=+(/INIT REPORT\(durationMs: ([\d.]+)/.exec(d)||[0,NaN])[1];
const dur=L.filter(l=>l.startsWith("REPORT")).map(l=>+/\tDuration: ([\d.]+)/.exec(l)[1]);
const warm=dur.slice(1).sort((a,b)=>a-b); const q=(p)=>warm[Math.floor(p*(warm.length-1))];
console.log(JSON.stringify({fn:process.argv[1],cpus:process.argv[2],n_warm:warm.length,init_ms:+init.toFixed(1),warm_median_ms:q(0.5),warm_p10:q(0.1),warm_p90:q(0.9)}))})' "$fn$EXTRA_LABEL" "$cpus"
fi
[ -n "$KEEP" ] || docker rm -f "$name" >/dev/null
