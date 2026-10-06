# NOTE

This run was limited by the account Lambda concurrency quota (10). JMeter 10 threads exceeded it; throttled calls surfaced as API Gateway 500 "Internal server error" (no function errors). Serverless error rates here are not representative; latencies of successful requests are. Rerun after the quota increase -> results-e2e/acmeair-ec2/.
