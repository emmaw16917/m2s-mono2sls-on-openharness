# acmeair: official JMeter workload, same region (us-east-1)

Load generator: EC2 t3.small in us-east-1a running acmeair-driver AcmeAir.jmx (10 threads x 20 loops), two passes per variant (pass 1 cold, pass 2 warm). Monolith: EC2 t3.small, same AZ. Serverless: API Gateway + Lambda + DynamoDB.


## End-to-end (client = load generator)

| variant | pass | samples | errors | req/s | p50 ms | p90 ms | p99 ms |
|---|---|---|---|---|---|---|---|
| monolith | 1 | 1892 | 0 | 200.93 | 3.0 | 5.0 | 10.0 |
| monolith | 2 | 1895 | 0 | 202.67 | 2.0 | 5.0 | 10.0 |
| v0 | 1 | 1866 | 0 | 106.79 | 44.0 | 75.0 | 633.0 |
| v0 | 2 | 1872 | 0 | 111.47 | 41.0 | 61.0 | 138.0 |
| v0-http | 1 | 1861 | 0 | 115.71 | 39.0 | 69.0 | 563.0 |
| v0-http | 2 | 1874 | 0 | 115.47 | 36.0 | 55.0 | 102.0 |
| opt | 1 | 1861 | 0 | 107.58 | 45.0 | 73.0 | 623.0 |
| opt | 2 | 1865 | 0 | 107.96 | 41.0 | 60.0 | 142.0 |

## Per request, warm pass (p50 / p90 ms, errors/samples)

| request | monolith | v0 | v0-http | opt |
|---|---|---|---|---|
| BookFlight | 2.0 / 4.0 (0/192) | 39.0 / 51.0 (0/169) | 34.0 / 46.0 (0/172) | 37.0 / 51.0 (0/165) |
| Cancel Booking | 1.0 / 2.0 (0/3) | 62.0 / 844.0 (0/3) | 40.0 / 824.0 (0/2) | - |
| List Bookings | 2.0 / 3.0 (0/200) | 33.0 / 47.0 (0/200) | 26.0 / 38.0 (0/200) | 33.0 / 44.0 (0/200) |
| Login | 2.0 / 3.0 (0/200) | 34.0 / 61.0 (0/200) | 29.0 / 46.0 (0/200) | 37.0 / 54.0 (0/200) |
| QueryFlight | 3.0 / 6.0 (0/1000) | 47.0 / 67.0 (0/1000) | 42.0 / 60.0 (0/1000) | 47.0 / 65.0 (0/1000) |
| Update Customer | 2.0 / 3.0 (0/50) | 35.0 / 41.0 (0/50) | 29.0 / 35.0 (0/50) | 33.0 / 45.0 (0/50) |
| View Profile Information | 2.0 / 3.0 (0/50) | 31.0 / 39.0 (0/50) | 26.0 / 39.0 (0/50) | 34.0 / 50.0 (0/50) |
| logout | 2.0 / 2.0 (0/200) | 30.0 / 38.0 (0/200) | 25.0 / 32.0 (0/200) | 29.0 / 36.0 (0/200) |

## Server-side time

Monolith (morgan response time, both passes):

| route | n | p50 ms | p90 ms |
|---|---|---|---|
| GET /rest/api/bookings/byuser/:user | 400 | 1.13 | 1.55 |
| GET /rest/api/customer/byid/:user | 100 | 1.06 | 1.69 |
| GET /rest/api/login/logout | 400 | 0.76 | 1.08 |
| POST /rest/api/bookings/bookflights | 383 | 1.61 | 2.16 |
| POST /rest/api/bookings/cancelbooking | 4 | 0.99 | 1.02 |
| POST /rest/api/customer/byid/:user | 100 | 1.22 | 1.9 |
| POST /rest/api/flights/queryflights | 2000 | 2.49 | 4.29 |
| POST /rest/api/login | 400 | 1.33 | 2.02 |

v0 (Lambda REPORT, warm pass; cold starts counted separately):

| function | invocations | cold | warm p50 ms | warm p90 ms | init p50 ms |
|---|---|---|---|---|---|
| BookingsByCustomerFunction | 200 | 1 | 10.1 | 22.4 | 342.3 |
| BookingsCancelFunction | 3 | 1 | 20.2 | 17.0 | 324.4 |
| BookingsCreateFunction | 169 | 0 | 17.4 | 28.3 | - |
| ConfigCountBookingsFunction | 1 | 1 | - | - | 328.1 |
| ConfigCountSessionsFunction | 1 | 1 | - | - | 336.3 |
| CustomerGetFunction | 50 | 0 | 9.3 | 19.3 | - |
| CustomerUpdateFunction | 50 | 0 | 11.5 | 18.6 | - |
| FlightsQueryFunction | 1000 | 0 | 24.9 | 45.9 | - |
| LoginCreateSessionFunction | 200 | 0 | 10.4 | 20.8 | - |
| SessionLogoutFunction | 200 | 0 | 7.8 | 8.8 | - |

Cold starts in pass 1: 25.

v0-http (Lambda REPORT, warm pass; cold starts counted separately):

| function | invocations | cold | warm p50 ms | warm p90 ms | init p50 ms |
|---|---|---|---|---|---|
| BookingsByCustomerFunction | 200 | 0 | 9.1 | 19.1 | - |
| BookingsCancelFunction | 2 | 1 | 24.6 | 24.6 | 316.5 |
| BookingsCreateFunction | 172 | 0 | 17.8 | 30.4 | - |
| ConfigCountBookingsFunction | 1 | 1 | - | - | 328.5 |
| ConfigCountSessionsFunction | 1 | 1 | - | - | 321.7 |
| CustomerGetFunction | 50 | 0 | 8.8 | 19.4 | - |
| CustomerUpdateFunction | 50 | 0 | 11.7 | 17.2 | - |
| FlightsQueryFunction | 1000 | 0 | 25.1 | 43.5 | - |
| LoginCreateSessionFunction | 200 | 0 | 11.4 | 23.1 | - |
| SessionLogoutFunction | 200 | 0 | 7.5 | 9.0 | - |

Cold starts in pass 1: 27.

opt (Lambda REPORT, warm pass; cold starts counted separately):

| function | invocations | cold | warm p50 ms | warm p90 ms | init p50 ms |
|---|---|---|---|---|---|
| BookingsByCustomerFunction | 200 | 0 | 10.2 | 21.7 | - |
| BookingsCreateFunction | 165 | 0 | 13.4 | 23.6 | - |
| ConfigCountBookingsFunction | 1 | 1 | - | - | 204.8 |
| ConfigCountSessionsFunction | 1 | 1 | - | - | 257.6 |
| CustomerGetFunction | 50 | 0 | 10.2 | 21.1 | - |
| CustomerUpdateFunction | 50 | 0 | 11.6 | 18.9 | - |
| FlightsQueryFunction | 1000 | 0 | 24.7 | 44.2 | - |
| LoginCreateSessionFunction | 200 | 1 | 12.2 | 22.8 | 309.1 |
| SessionLogoutFunction | 200 | 0 | 7.0 | 8.4 | - |

Cold starts in pass 1: 27.

## Where the time goes, warm pass (p50 ms)

End-to-end from the load generator; API Gateway response latency (request in to response out) and integration latency (the Lambda call, invoke overhead included) from the access log; Lambda Duration from REPORT.

| variant | end-to-end | in API Gateway | of which integration | gateway's own (response - integration) | outside the gateway (e2e - response) |
|---|---|---|---|---|---|
| v0 | 41.0 | 35 | 31 | 4 | 6.0 |
| v0-http | 36.0 | 32 | 31 | 1 | 4.0 |
| opt | 41.0 | 35 | 31 | 4 | 6.0 |

## Cost and data

| variant | invocations | Lambda GB-s | RRU | WRU | cost USD (both passes) | per 1k requests |
|---|---|---|---|---|---|---|
| monolith | - | - | - | - | 0.000476 (EC2 time during the passes; $0.0208/h whether busy or idle) | 0.000126 |
| v0 | 3740 | 53.458 | 5490.0 | 2096.0 | 0.01654 | 0.004425 |
| v0-http | 3737 | 53.037 | 5487.5 | 2060.0 | 0.007163 | 0.001918 |
| opt | 3728 | 52.952 | 5484.0 | 2056.0 | 0.016463 | 0.004418 |

- monolith: loader `Database Finished Loading`; counts {'Customers': 10000, 'Flights': 5122, 'FlightSegments': 394, 'Airports': 14, '_': 0}; after the runs {'Bookings': '592', 'Sessions': '0'}
- v0: loader `200 7.521857s
Database Finished Loading`; counts {'Customers': 10000, 'Flights': 5122, 'FlightSegments': 394, 'Airports': 14, '_': 0}; after the runs {'Bookings': '592', 'Sessions': '0'}
- v0-http: loader `200 8.163836s
Database Finished Loading`; counts {'Customers': 10000, 'Flights': 5122, 'FlightSegments': 394, 'Airports': 14, '_': 0}; after the runs {'Bookings': '576', 'Sessions': '0'}
- opt: loader `200 8.761168s
Database Finished Loading`; counts {'Customers': 10000, 'Flights': 5122, 'FlightSegments': 394, 'Airports': 14, '_': 0}; after the runs {'Bookings': '574', 'Sessions': '0'}
