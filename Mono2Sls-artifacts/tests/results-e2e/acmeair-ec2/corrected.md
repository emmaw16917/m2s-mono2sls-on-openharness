# acmeair same-region run: corrected serverless cost and data

Recomputed by `m2s-mcp-server/e2e_ec2_recompute.py` (see its docstring for the three collection bugs). End-to-end latency and the warm per-function Duration table in report.md were not affected.

## Cost, both passes (workload only)

| variant | JMeter requests | Lambda invocations | Lambda GB-s | RRU (incl. GSI) | WRU (incl. GSI) | Lambda $ | DynamoDB $ | API GW $ | total $ | per 1k requests |
|---|---|---|---|---|---|---|---|---|---|---|
| v0 | 3668 | 3668 | 48.277 | 5451.5 | 1436 | 0.001377 | 0.001579 | 0.012838 | 0.015794 | 0.004306 |
| opt | 3685 | 3685 | 49.201 | 5463.5 | 1470 | 0.001393 | 0.001602 | 0.012897 | 0.015892 | 0.004313 |

Old (buggy) figures for comparison:

| variant | invocations | GB-s | RRU | WRU | total $ |
|---|---|---|---|---|---|
| v0 | 5514 | 69.694 | 4660.0 | 27092.0 | 0.038846 |
| opt | 5539 | 70.444 | 4362.0 | 14748.0 | 0.031196 |

## Lambda per pass (workload functions only)

| variant | pass | invocations | cold | GB-s | harness invocations left out |
|---|---|---|---|---|---|
| v0 | 1 | 1830 | 24 | 28.446 | 0 |
| v0 | 2 | 1838 | 0 | 19.831 | 2 |
| opt | 1 | 1839 | 25 | 29.534 | 0 |
| opt | 2 | 1846 | 0 | 19.667 | 2 |

## DynamoDB by table / GSI (both passes)

| variant | target | WRU kept | RRU kept | raw WRU | raw RRU |
|---|---|---|---|---|---|
| v0 | AirportCodeMappingTable | 0 | 0.0 | 14 | 2.0 |
| v0 | BookingTable | 268 | 0.0 | 268 | 5.5 |
| v0 | BookingTable/customerId-index | 268 | 200.0 | 268 | 200.0 |
| v0 | CustomerSessionTable | 800 | 1436.0 | 800 | 1436.0 |
| v0 | CustomerTable | 100 | 250.0 | 10100 | 760.5 |
| v0 | FlightSegmentTable | 0 | 0.0 | 394 | 3.0 |
| v0 | FlightSegmentTable/originPort-destPort-index | 0 | 2000.0 | 394 | 2000.0 |
| v0 | FlightTable | 0 | 0.0 | 1970 | 123.0 |
| v0 | FlightTable/flightSegmentId-scheduledDepartureTime-index | 0 | 1565.5 | 1970 | 1565.5 |
| opt | AirportCodeMappingTable | 0 | 0.0 | 0 | 2.0 |
| opt | BookingTable | 285 | 0.0 | 285 | 6.5 |
| opt | BookingTable/customerId-index | 285 | 200.0 | 285 | 200.0 |
| opt | CustomerSessionTable | 800 | 1444.5 | 800 | 1444.5 |
| opt | CustomerTable | 100 | 250.0 | 100 | 505.0 |
| opt | FlightSegmentTable | 0 | 0.0 | 0 | 3.0 |
| opt | FlightSegmentTable/originPort-destPort-index | 0 | 2000.0 | 0 | 2000.0 |
| opt | FlightTable | 0 | 0.0 | 0 | 61.5 |
| opt | FlightTable/flightSegmentId-scheduledDepartureTime-index | 0 | 1569.0 | 0 | 1569.0 |
