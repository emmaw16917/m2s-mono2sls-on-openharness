# acmeair: official JMeter workload, same region (us-east-1)

Load generator: EC2 t3.small in us-east-1a running acmeair-driver AcmeAir.jmx (10 threads x 20 loops), two passes per variant (pass 1 cold, pass 2 warm). Monolith: EC2 t3.small, same AZ. Serverless: API Gateway + Lambda + DynamoDB.


## End-to-end (client = load generator)

| variant | pass | samples | errors | req/s | p50 ms | p90 ms | p99 ms |
|---|---|---|---|---|---|---|---|
| monolith | 1 | 1856 | 0 | 199.68 | 2.0 | 4.0 | 6.0 |
| monolith | 2 | 1856 | 0 | 201.28 | 2.0 | 4.0 | 7.0 |
| v0 | 1 | 907 | 242 | 54.77 | 37.0 | 67.0 | 776.0 |
| v0 | 2 | 1339 | 141 | 80.11 | 38.0 | 57.0 | 114.0 |
| opt | 1 | 867 | 85 | 59.76 | 40.0 | 69.0 | 697.0 |
| opt | 2 | 1317 | 101 | 82.64 | 37.0 | 56.0 | 121.0 |

## Per request, warm pass (p50 / p90 ms, errors/samples)

| request | monolith | v0 | opt |
|---|---|---|---|
| BookFlight | 2.0 / 3.0 (0/156) | 33.0 / 41.0 (9/101) | 34.0 / 48.0 (6/95) |
| List Bookings | 2.0 / 2.0 (0/200) | 31.0 / 39.0 (9/141) | 32.0 / 44.0 (7/140) |
| Login | 2.0 / 3.0 (0/200) | 34.0 / 42.0 (12/147) | 32.0 / 48.0 (8/145) |
| QueryFlight | 2.0 / 5.0 (0/1000) | 44.0 / 63.0 (97/735) | 42.0 / 61.0 (65/725) |
| Update Customer | 2.0 / 2.0 (0/50) | 33.0 / 36.0 (1/37) | 30.0 / 38.0 (3/36) |
| View Profile Information | 2.0 / 2.0 (0/50) | 32.0 / 36.0 (0/37) | 30.0 / 37.0 (3/36) |
| logout | 1.0 / 2.0 (0/200) | 33.0 / 39.0 (13/141) | 30.0 / 38.0 (9/140) |

## Server-side time

Monolith (morgan response time, both passes):

| route | n | p50 ms | p90 ms |
|---|---|---|---|
| GET /rest/api/bookings/byuser/:user | 400 | 1.0 | 1.22 |
| GET /rest/api/customer/byid/:user | 100 | 0.95 | 1.16 |
| GET /rest/api/login/logout | 400 | 0.67 | 0.84 |
| POST /rest/api/bookings/bookflights | 312 | 1.23 | 1.49 |
| POST /rest/api/customer/byid/:user | 100 | 1.13 | 1.37 |
| POST /rest/api/flights/queryflights | 2000 | 2.16 | 3.44 |
| POST /rest/api/login | 400 | 1.19 | 1.52 |

v0 (Lambda REPORT, warm pass; cold starts counted separately):

| function | invocations | cold | warm p50 ms | warm p90 ms | init p50 ms |
|---|---|---|---|---|---|
| BookingsByCustomerFunction | 132 | 0 | 9.2 | 20.0 | - |
| BookingsCreateFunction | 92 | 0 | 11.1 | 17.8 | - |
| ConfigCountBookingsFunction | 1 | 1 | - | - | 320.5 |
| ConfigCountSessionsFunction | 1 | 1 | - | - | 313.1 |
| CustomerGetFunction | 37 | 0 | 9.4 | 16.5 | - |
| CustomerUpdateFunction | 36 | 0 | 11.2 | 14.9 | - |
| FlightsQueryFunction | 661 | 0 | 24.5 | 44.7 | - |
| LoginCreateSessionFunction | 135 | 0 | 11.7 | 19.4 | - |
| SessionLogoutFunction | 128 | 0 | 7.3 | 9.4 | - |

Cold starts in pass 1: 27.

opt (Lambda REPORT, warm pass; cold starts counted separately):

| function | invocations | cold | warm p50 ms | warm p90 ms | init p50 ms |
|---|---|---|---|---|---|
| BookingsByCustomerFunction | 133 | 0 | 10.7 | 21.8 | - |
| BookingsCreateFunction | 89 | 0 | 12.0 | 20.5 | - |
| ConfigCountBookingsFunction | 1 | 1 | - | - | 302.8 |
| ConfigCountSessionsFunction | 1 | 1 | - | - | 317.2 |
| CustomerGetFunction | 33 | 0 | 9.3 | 10.0 | - |
| CustomerUpdateFunction | 33 | 0 | 9.7 | 10.4 | - |
| FlightsQueryFunction | 671 | 0 | 21.9 | 42.2 | - |
| LoginCreateSessionFunction | 137 | 0 | 9.9 | 20.4 | - |
| SessionLogoutFunction | 131 | 0 | 5.9 | 7.2 | - |

Cold starts in pass 1: 20.

## Cost and data

| variant | invocations | Lambda GB-s | RRU | WRU | cost USD (both passes) | per 1k requests |
|---|---|---|---|---|---|---|
| monolith | - | - | - | - | 0.000328 (EC2 time during the passes; $0.0208/h whether busy or idle) | 8.8e-05 |
| v0 | 3139 | 42.669 | 3013.0 | 25964.0 | 0.028787 | 0.012817 |
| opt | 3278 | 40.666 | 3122.0 | 26022.0 | 0.029325 | 0.013427 |

- monolith: loader `Database Finished Loading`; counts {'Customers': 10000, 'Flights': 1970, 'FlightSegments': 394, 'Airports': 14, '_': 0}; after the runs {'Bookings': '312', 'Sessions': '0'}
- v0: loader `200 6.947600s
Database Finished Loading`; counts {'Customers': 10000, 'Flights': 1970, 'FlightSegments': 394, 'Airports': 14, '_': 0}; after the runs {'Bookings': '144', 'Sessions': '29'}
- opt: loader `200 7.136121s
Database Finished Loading`; counts {'Customers': 10000, 'Flights': 1970, 'FlightSegments': 394, 'Airports': 14, '_': 0}; after the runs {'Bookings': '141', 'Sessions': '11'}
