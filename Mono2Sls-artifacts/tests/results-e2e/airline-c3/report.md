# airline: monolith vs v0 vs optimized (AWS us-east-1)

| variant | commit | arch | tests passed | invocations | Lambda GB-s | RRU | WRU | est. cost USD |
|---|---|---|---|---|---|---|---|---|
| monolith | - | - | 25/32 | - | - | - | - | - |
| v0 | 1ac2ee9 | x86_64 | 24/32 | 70 | 3.227 | 50.0 | 58.0 | 0.00035528 |
| opt | 4d1ddb4 | x86_64 | 23/32 (1 flaky) | 71 | 2.927 | 48.0 | 58.0 | 0.00035373 |

## Per function (warm p50 / p90 ms, cold starts)

| function | v0 | opt |
|---|---|---|
| BookingsCancelFunction | 37.8 / 42.9 (1 cold) | 35.5 / 40.4 (1 cold) |
| BookingsCreateFunction | 32.6 / 42.5 (1 cold) | 31.2 / 35.4 (1 cold) |
| BookingsGetFunction | 7.9 / 8.0 (1 cold) | 7.4 / 8.1 (1 cold) |
| CustomersListBookingsFunction | 13.7 / 13.7 (1 cold) | 56.1 / 56.1 (1 cold) |
| FlightsGetFlightFunction | 6.0 / 6.2 (1 cold) | 6.3 / 17.2 (1 cold) |
| FlightsReleaseSeatFunction | 14.7 / 14.7 (1 cold) | 26.3 / 26.3 (1 cold) |
| FlightsReserveSeatFunction | 63.6 / 63.6 (1 cold) | 49.6 / 49.6 (1 cold) |
| FlightsSearchFunction | 11.1 / 12.7 (1 cold) | 9.2 / 10.5 (1 cold) |
| LoyaltyGetCustomerFunction | 11.4 / 15.8 (1 cold) | 12.3 / 13.5 (1 cold) |
| PaymentsCollectFunction | 1.7 / 1.7 (1 cold) | 1.5 / 1.6 (1 cold) |
| PaymentsGetFunction | None / None (1 cold) | 1.9 / 1.9 (1 cold) |
| PaymentsRefundFunction | 1.6 / 1.6 (1 cold) | 2.1 / 2.1 (1 cold) |

## Authenticated workload, client-side latency (p50 / p90 ms, statuses)

Monolith runs locally (no network hop); the AWS variants include the client-to-us-east-1 round trip.

| call | monolith | v0 | opt |
|---|---|---|---|
| GET /customers/{sub}/bookings | 35.9 / 39.4 ['200'] | - | - |
| GET /flights/FL001 | 30.1 / 31.7 ['200'] | - | - |
| GET /flights/search?departureCode=LAX&arrivalCode=SFO&departureDate=2025-11-10 | 26.6 / 29.1 ['200'] | - | - |
| GET /loyalty/{sub} | 27.7 / 33.0 ['200'] | - | - |

## Behaviour differences vs the monolith (1)

- Payments Contract > TC-18a: should expose payment lookup semantics for a collected charge id: monolith=passed, v0=failed, opt=failed

## Flaky (outcome differed between the two suite runs on one deployment) (1)

- Robustness > ROB-04a: flight search should be deterministic for the fixture query: monolith=passed, v0=passed, opt=flaky

## Tests failing everywhere (suite contract vs the monolith, not a migration difference)

None.
