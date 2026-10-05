# acmeair: official JMeter workload, monolith vs v0 vs optimized

acmeair-driver AcmeAir.jmx, 10 threads x 20 loops. Monolith local (Node + MongoDB 4.4 in Docker, no network hop); serverless variants on AWS us-east-1 (includes the client-to-us-east-1 round trip).

| variant | commit | arch | samples | errors | req/s | p50 ms | p90 ms | p99 ms | invocations | Lambda GB-s | RRU | WRU | est. cost USD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| monolith | - | - | 1860 | 0 | 191.99 | 3.0 | 5.0 | 7.0 | - | - | - | - | - |

## Per request (p50 / p90 ms, errors/samples, status codes)

| request | monolith |
|---|---|
| BookFlight | 3.0 / 4.0 (0/160) {'200': 160} |
| List Bookings | 3.0 / 4.0 (0/200) {'200': 200} |
| Login | 3.0 / 5.0 (0/200) {'200': 200} |
| QueryFlight | 4.0 / 6.0 (0/1000) {'200': 1000} |
| Update Customer | 3.0 / 4.0 (0/50) {'200': 50} |
| View Profile Information | 3.0 / 3.0 (0/50) {'200': 50} |
| logout | 2.0 / 3.0 (0/200) {'200': 200} |

## Data

- monolith: loader `Database Finished Loading`; counts {'Customers': '10000', 'Flights': '1970', 'FlightSegments': '394', 'Airports': '14'}; after the run {'Bookings': '160', 'Sessions': '0'}
