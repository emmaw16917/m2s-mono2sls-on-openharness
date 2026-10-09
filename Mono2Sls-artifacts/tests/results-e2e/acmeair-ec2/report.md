# acmeair: official JMeter workload, same region (us-east-1)

Load generator: EC2 t3.small in us-east-1a running acmeair-driver AcmeAir.jmx (10 threads x 20 loops), two passes per variant (pass 1 cold, pass 2 warm). Monolith: EC2 t3.small, same AZ. Serverless: API Gateway + Lambda + DynamoDB.


## End-to-end (client = load generator)

| variant | pass | samples | errors | req/s | p50 ms | p90 ms | p99 ms |
|---|---|---|---|---|---|---|---|
| monolith | 1 | 1866 | 0 | 187.93 | 3.0 | 9.0 | 30.0 |
| monolith | 2 | 1860 | 0 | 198.29 | 2.0 | 5.0 | 10.0 |
| v0 | 1 | 1830 | 0 | 108.01 | 42.0 | 69.0 | 340.0 |
| v0 | 2 | 1838 | 0 | 109.88 | 39.0 | 57.0 | 132.0 |
| opt | 1 | 1839 | 0 | 107.4 | 43.0 | 71.0 | 568.0 |
| opt | 2 | 1846 | 0 | 115.42 | 40.0 | 58.0 | 107.0 |

## Per request, warm pass (p50 / p90 ms, errors/samples)

| request | monolith | v0 | opt |
|---|---|---|---|
| BookFlight | 2.0 / 4.0 (0/160) | 33.0 / 43.0 (0/138) | 37.0 / 45.0 (0/146) |
| List Bookings | 2.0 / 3.0 (0/200) | 32.0 / 40.0 (0/200) | 34.0 / 44.0 (0/200) |
| Login | 2.0 / 3.0 (0/200) | 34.0 / 46.0 (0/200) | 34.0 / 43.0 (0/200) |
| QueryFlight | 3.0 / 6.0 (0/1000) | 45.0 / 62.0 (0/1000) | 45.0 / 64.0 (0/1000) |
| Update Customer | 2.0 / 3.0 (0/50) | 32.0 / 38.0 (0/50) | 33.0 / 44.0 (0/50) |
| View Profile Information | 2.0 / 3.0 (0/50) | 30.0 / 37.0 (0/50) | 30.0 / 43.0 (0/50) |
| logout | 2.0 / 3.0 (0/200) | 30.0 / 38.0 (0/200) | 29.0 / 36.0 (0/200) |

## Server-side time

Monolith (morgan response time, both passes):

| route | n | p50 ms | p90 ms |
|---|---|---|---|
| GET /rest/api/bookings/byuser/:user | 400 | 1.41 | 2.79 |
| GET /rest/api/customer/byid/:user | 100 | 1.32 | 3.71 |
| GET /rest/api/login/logout | 400 | 0.95 | 1.82 |
| POST /rest/api/bookings/bookflights | 326 | 1.6 | 3.18 |
| POST /rest/api/customer/byid/:user | 100 | 1.47 | 2.58 |
| POST /rest/api/flights/queryflights | 2000 | 2.9 | 5.84 |
| POST /rest/api/login | 400 | 1.6 | 3.49 |

v0 (Lambda REPORT, warm pass; cold starts counted separately):

| function | invocations | cold | warm p50 ms | warm p90 ms | init p50 ms |
|---|---|---|---|---|---|
| BookingsByCustomerFunction | 200 | 0 | 9.3 | 18.6 | - |
| BookingsCreateFunction | 138 | 0 | 11.0 | 20.9 | - |
| ConfigCountBookingsFunction | 1 | 1 | - | - | 264.8 |
| ConfigCountSessionsFunction | 1 | 1 | - | - | 302.0 |
| CustomerGetFunction | 50 | 0 | 9.3 | 21.4 | - |
| CustomerUpdateFunction | 50 | 0 | 10.6 | 12.9 | - |
| FlightsQueryFunction | 1000 | 0 | 24.0 | 43.1 | - |
| LoginCreateSessionFunction | 200 | 0 | 11.5 | 19.8 | - |
| SessionLogoutFunction | 200 | 0 | 7.2 | 8.2 | - |

Cold starts in pass 1: 28.

opt (Lambda REPORT, warm pass; cold starts counted separately):

| function | invocations | cold | warm p50 ms | warm p90 ms | init p50 ms |
|---|---|---|---|---|---|
| BookingsByCustomerFunction | 200 | 0 | 10.6 | 22.3 | - |
| BookingsCreateFunction | 146 | 0 | 11.3 | 16.6 | - |
| ConfigCountBookingsFunction | 1 | 1 | - | - | 250.2 |
| ConfigCountSessionsFunction | 1 | 1 | - | - | 307.0 |
| CustomerGetFunction | 50 | 0 | 9.4 | 19.6 | - |
| CustomerUpdateFunction | 50 | 0 | 10.5 | 20.1 | - |
| FlightsQueryFunction | 1000 | 0 | 24.3 | 42.5 | - |
| LoginCreateSessionFunction | 200 | 0 | 11.1 | 19.6 | - |
| SessionLogoutFunction | 200 | 0 | 6.5 | 7.5 | - |

Cold starts in pass 1: 29.

## Cost and data

| variant | invocations | Lambda GB-s | RRU | WRU | cost USD (both passes) | per 1k requests |
|---|---|---|---|---|---|---|
| monolith | - | - | - | - | 0.000287 (EC2 time during the passes; $0.0208/h whether busy or idle) | 7.7e-05 |
| v0 | 5514 | 69.694 | 4660.0 | 27092.0 | 0.038846 | 0.010591 |
| opt | 5539 | 70.444 | 4362.0 | 14748.0 | 0.031196 | 0.008466 |

- monolith: loader `Database Finished Loading`; counts {'Customers': 10000, 'Flights': 1970, 'FlightSegments': 394, 'Airports': 14, '_': 0}; after the runs {'Bookings': '326', 'Sessions': '0'}
- v0: loader `200 6.575602s
Database Finished Loading`; counts {'Customers': 10000, 'Flights': 1970, 'FlightSegments': 394, 'Airports': 14, '_': 0}; after the runs {'Bookings': '268', 'Sessions': '0'}
- opt: loader `200 6.884054s
Database Finished Loading`; counts {'Customers': 10000, 'Flights': 1970, 'FlightSegments': 394, 'Airports': 14, '_': 0}; after the runs {'Bookings': '285', 'Sessions': '0'}
