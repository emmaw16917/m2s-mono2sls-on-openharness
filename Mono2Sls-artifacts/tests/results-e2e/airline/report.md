# airline: monolith vs v0 vs optimized (AWS us-east-1)

| variant | commit | arch | tests passed | invocations | Lambda GB-s | RRU | WRU | est. cost USD |
|---|---|---|---|---|---|---|---|---|
| monolith | - | - | 25/32 | - | - | - | - | - |
| v0 | b037819 | x86_64 | 23/32 (1 flaky) | 79 | 6.034 | 64.0 | 104.0 | 0.00046587 |
| opt | 9574f0b | arm64 | 23/32 (1 flaky) | 78 | 5.808 | 41.0 | 104.0 | 0.00043617 |

## Per function (warm p50 / p90 ms, cold starts)

| function | v0 | opt |
|---|---|---|
| BookingsCancelFunction | 47.3 / 57.9 (1 cold) | 37.3 / 59.0 (1 cold) |
| BookingsCreateFunction | 39.0 / 49.0 (1 cold) | 41.5 / 59.7 (1 cold) |
| BookingsGetDetailFunction | 7.7 / 8.4 (1 cold) | 10.7 / 10.8 (1 cold) |
| CustomersListBookingsFunction | 1.7 / 1.7 (1 cold) | 7.3 / 7.3 (1 cold) |
| FlightsGetDetailFunction | 6.3 / 7.4 (1 cold) | 5.9 / 6.3 (1 cold) |
| FlightsReleaseSeatFunction | 66.4 / 66.4 (1 cold) | 12.3 / 12.3 (1 cold) |
| FlightsReserveSeatFunction | 72.3 / 72.3 (1 cold) | 9.4 / 9.4 (1 cold) |
| FlightsSearchFunction | 10.9 / 14.1 (1 cold) | 11.4 / 12.8 (1 cold) |
| LoyaltyAccruePointsFunction | 23.0 / 23.7 (1 cold) | 26.1 / 28.9 (1 cold) |
| LoyaltyGetSummaryFunction | 9.8 / 13.1 (1 cold) | 6.0 / 7.1 (1 cold) |
| PaymentsCollectFunction | 6.2 / 6.5 (1 cold) | 6.2 / 7.0 (1 cold) |
| PaymentsGetDetailFunction | 6.7 / 6.7 (1 cold) | 13.4 / 13.4 (1 cold) |
| PaymentsRefundFunction | 2.2 / 2.2 (1 cold) | 1.6 / 1.6 (1 cold) |

## Authenticated workload, client-side latency (p50 / p90 ms, statuses)

Monolith runs locally (no network hop); the AWS variants include the client-to-us-east-1 round trip.

| call | monolith | v0 | opt |
|---|---|---|---|
| GET /customers/{sub}/bookings | 35.9 / 39.4 ['200'] | - | - |
| GET /flights/FL001 | 30.1 / 31.7 ['200'] | - | - |
| GET /flights/search?departureCode=LAX&arrivalCode=SFO&departureDate=2025-11-10 | 26.6 / 29.1 ['200'] | - | - |
| GET /loyalty/{sub} | 27.7 / 33.0 ['200'] | - | - |

## Behaviour differences vs the monolith (1)

- Robustness > ROB-01b: key endpoints should expose CORS headers: monolith=passed, v0=failed, opt=failed

## Flaky (outcome differed between the two suite runs on one deployment) (2)

- Booking Contract > Authenticated booking flow > TC-08a: should fetch the owner booking details: monolith=passed, v0=flaky, opt=passed
- Robustness > ROB-01a: key endpoints should return JSON responses: monolith=passed, v0=passed, opt=flaky

## Tests failing everywhere (suite contract vs the monolith, not a migration difference)

None.
