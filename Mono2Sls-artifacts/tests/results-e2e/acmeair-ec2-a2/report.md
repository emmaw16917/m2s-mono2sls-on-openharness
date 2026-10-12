# acmeair: official JMeter workload, same region (us-east-1)

Load generator: EC2 t3.small in us-east-1a running acmeair-driver AcmeAir.jmx (10 threads x 20 loops), two passes per variant (pass 1 cold, pass 2 warm). Monolith: EC2 t3.small, same AZ. Serverless: API Gateway + Lambda + DynamoDB.


## End-to-end (client = load generator)

| variant | pass | samples | errors | req/s | p50 ms | p90 ms | p99 ms |
|---|---|---|---|---|---|---|---|
| monolith | 1 | 1892 | 0 | 202.96 | 3.0 | 5.0 | 8.0 |
| monolith | 2 | 1892 | 0 | 202.85 | 2.0 | 5.0 | 8.0 |
| v0 | 1 | 1866 | 0 | 116.65 | 38.0 | 64.0 | 592.0 |
| v0 | 2 | 1873 | 0 | 123.24 | 34.0 | 51.0 | 112.0 |
| opt | 1 | 1864 | 0 | 122.77 | 32.0 | 60.0 | 552.0 |
| opt | 2 | 1867 | 0 | 130.39 | 28.0 | 44.0 | 85.0 |

## Per request, warm pass (p50 / p90 ms over warm samples; errors/samples, cold)

A sample counts as cold when a Lambda cold start (INIT_START to its REPORT) falls inside its client window; cold samples are left out of p50/p90 and counted after the comma. *few* = under 10 warm samples, not comparable. `-` = the request never ran in this pass (e.g. the driver cancels only for a customer holding more than 2 bookings, AcmeAir.jmx).

| request | monolith | v0 | opt |
|---|---|---|---|
| BookFlight | 2.0 / 4.0 (0/192) | 29.0 / 43.0 (0/171) | 28.0 / 39.0 (0/163) |
| Cancel Booking | - | 46.0 / 46.0 (0/2, cold 1) *few* | 38.0 / 40.0 (0/4, cold 2) *few* |
| List Bookings | 2.0 / 2.0 (0/200) | 27.0 / 38.0 (0/200) | 25.0 / 34.0 (0/200) |
| Login | 2.0 / 3.0 (0/200) | 27.0 / 40.0 (0/200) | 28.0 / 39.0 (0/200) |
| QueryFlight | 3.0 / 6.0 (0/1000) | 40.0 / 58.0 (0/1000) | 30.0 / 49.0 (0/1000) |
| Update Customer | 2.0 / 3.0 (0/50) | 29.0 / 38.0 (0/50) | 28.0 / 35.0 (0/50) |
| View Profile Information | 2.0 / 2.0 (0/50) | 26.0 / 34.0 (0/50) | 25.0 / 34.0 (0/50) |
| logout | 1.0 / 2.0 (0/200) | 24.0 / 33.0 (0/200) | 23.0 / 27.0 (0/200) |

## Server-side time

Monolith (morgan response time, both passes):

| route | n | p50 ms | p90 ms |
|---|---|---|---|
| GET /rest/api/bookings/byuser/:user | 400 | 1.1 | 1.47 |
| GET /rest/api/customer/byid/:user | 100 | 1.0 | 1.36 |
| GET /rest/api/login/logout | 400 | 0.71 | 1.03 |
| POST /rest/api/bookings/bookflights | 384 | 1.56 | 2.18 |
| POST /rest/api/customer/byid/:user | 100 | 1.22 | 1.79 |
| POST /rest/api/flights/queryflights | 2000 | 2.5 | 4.21 |
| POST /rest/api/login | 400 | 1.29 | 1.92 |

v0 (Lambda REPORT, warm pass; cold starts counted separately):

`-` under warm p50/p90: every invocation in this pass was a cold start (e.g. the count checks the harness calls once before/after the passes). `-` under init: no cold start in this pass (pass 1 already warmed it). *few* = under 10 warm invocations.

| function | invocations | cold | warm p50 ms | warm p90 ms | init p50 ms |
|---|---|---|---|---|---|
| BookingsByCustomerFunction | 200 | 0 | 9.54 | 21.66 | - |
| BookingsCancelFunction | 2 | 1 | 26.72 *few* | 26.72 | 304.47 |
| BookingsCreateFunction | 171 | 0 | 11.32 | 23.29 | - |
| ConfigCountBookingsFunction | 1 | 1 | - | - | 306.12 |
| ConfigCountSessionsFunction | 1 | 1 | - | - | 259.68 |
| CustomerGetFunction | 50 | 0 | 9.23 | 21.41 | - |
| CustomerUpdateFunction | 50 | 0 | 10.35 | 18.48 | - |
| FlightsQueryFunction | 1000 | 0 | 23.69 | 43.45 | - |
| LoginCreateSessionFunction | 200 | 0 | 9.86 | 20.38 | - |
| SessionLogoutFunction | 200 | 0 | 6.91 | 9.02 | - |

Cold starts in pass 1: 25.

opt (Lambda REPORT, warm pass; cold starts counted separately):

`-` under warm p50/p90: every invocation in this pass was a cold start (e.g. the count checks the harness calls once before/after the passes). `-` under init: no cold start in this pass (pass 1 already warmed it). *few* = under 10 warm invocations.

| function | invocations | cold | warm p50 ms | warm p90 ms | init p50 ms |
|---|---|---|---|---|---|
| BookingsByCustomerFunction | 200 | 0 | 9.74 | 17.37 | - |
| BookingsCancelFunction | 4 | 2 | 15.51 *few* | 19.24 | 237.81 |
| BookingsCreateFunction | 163 | 0 | 13.68 | 29.21 | - |
| ConfigCountBookingsFunction | 1 | 1 | - | - | 326.52 |
| ConfigCountSessionsFunction | 1 | 1 | - | - | 249.88 |
| CustomerGetFunction | 50 | 0 | 10.07 | 19.28 | - |
| CustomerUpdateFunction | 50 | 0 | 11.39 | 20.68 | - |
| FlightsQueryFunction | 1000 | 0 | 13.89 | 35.18 | - |
| LoginCreateSessionFunction | 200 | 0 | 11.84 | 20.68 | - |
| SessionLogoutFunction | 200 | 0 | 6.71 | 8.03 | - |

Cold starts in pass 1: 27.

## Where the time goes, warm pass (p50 ms)

End-to-end from the load generator; API Gateway response latency (request in to response out) and integration latency (the Lambda call, invoke overhead included) from the access log; Lambda Duration from REPORT.

| variant | end-to-end | in API Gateway | of which integration | gateway's own (response - integration) | outside the gateway (e2e - response) |
|---|---|---|---|---|---|
| v0 | 34.0 | 31 | 29 | 1 | 3.0 |
| opt | 28.0 | 25 | 24 | 1 | 3.0 |

## Cost and data

| variant | invocations | Lambda GB-s | RRU | WRU | cost USD (both passes) | per 1k requests |
|---|---|---|---|---|---|---|
| monolith | - | - | - | - | 0.000445 (EC2 time during the passes; $0.0208/h whether busy or idle) | 0.000118 |
| v0 | 3741 | 50.859 | 5488.5 | 2112.0 | 0.007171 | 0.001918 |
| opt | 3733 | 43.975 | 4242.5 | 2072.0 | 0.006889 | 0.001846 |

- monolith: loader `Database Finished Loading`; counts {'Customers': 10000, 'Flights': 5122, 'FlightSegments': 394, 'Airports': 14, '_': 0}; after the runs {'Bookings': '597', 'Sessions': '0'}
- v0: loader `200 7.709610s
Database Finished Loading`; counts {'Customers': 10000, 'Flights': 5122, 'FlightSegments': 394, 'Airports': 14, '_': 0}; after the runs {'Bookings': '602', 'Sessions': '0'}
- opt: loader `200 7.305068s
Database Finished Loading`; counts {'Customers': 10000, 'Flights': 5122, 'FlightSegments': 394, 'Airports': 14, '_': 0}; after the runs {'Bookings': '578', 'Sessions': '0'}
