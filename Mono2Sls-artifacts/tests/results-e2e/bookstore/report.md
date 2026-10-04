# bookstore: monolith vs v0 vs optimized (AWS us-east-1)

| variant | commit | arch | tests passed | invocations | Lambda GB-s | RRU | WRU | est. cost USD |
|---|---|---|---|---|---|---|---|---|
| monolith | - | - | 9/21 | - | - | - | - | - |
| v0 | e098e0f | x86_64 | 9/21 | 226 | 4.936 | 303.0 | 161.0 | 0.00105697 |
| opt | 078b6fd | arm64 | 9/21 | 226 | 5.314 | 243.0 | 161.0 | 0.00103805 |

## Per function (warm p50 / p90 ms, cold starts)

| function | v0 | opt |
|---|---|---|
| BestsellersListFunction | 9.8 / 15.6 (1 cold) | 6.6 / 20.7 (1 cold) |
| BestsellersUpdateFunction | 24.6 / 123.3 (1 cold) | 30.5 / 86.0 (1 cold) |
| BooksGetItemFunction | 5.5 / 9.2 (1 cold) | 6.1 / 9.5 (1 cold) |
| BooksListFunction | 10.7 / 14.9 (1 cold) | 9.6 / 19.6 (1 cold) |
| CartAddFunction | 8.8 / 103.5 (1 cold) | 9.4 / 82.3 (1 cold) |
| CartGetFunction | 6.1 / 10.4 (1 cold) | 5.6 / 15.2 (1 cold) |
| CartGetItemFunction | 53.6 / 53.6 (1 cold) | 10.8 / 10.8 (1 cold) |
| CartRemoveFunction | 86.8 / 86.8 (1 cold) | 92.7 / 92.7 (1 cold) |
| CartUpdateFunction | 84.0 / 84.0 (1 cold) | 91.2 / 91.2 (1 cold) |
| OrdersCreateFunction | 27.1 / 164.6 (1 cold) | 37.5 / 146.0 (1 cold) |
| OrdersListFunction | 6.5 / 11.7 (1 cold) | 6.5 / 17.1 (1 cold) |
| SearchBooksFunction | 9.9 / 20.9 (1 cold) | 11.1 / 18.7 (1 cold) |

## Authenticated workload, client-side latency (p50 / p90 ms, statuses)

Monolith runs locally (no network hop); the AWS variants include the client-to-us-east-1 round trip.

| call | monolith | v0 | opt |
|---|---|---|---|
| GET /bestsellers | 5.2 / 6.0 ['200'] | 681.8 / 1211.2 ['200'] | 854.2 / 1063.6 ['200'] |
| GET /books | 5.9 / 7.4 ['200'] | 650.6 / 1265.5 ['200'] | 850.0 / 1229.2 ['200'] |
| GET /books/book-001 | 5.9 / 6.8 ['200'] | 978.3 / 1284.9 ['200'] | 854.8 / 884.0 ['200'] |
| GET /cart | 5.8 / 6.9 ['200'] | 811.3 / 1400.7 ['200'] | 855.7 / 1044.7 ['200'] |
| GET /orders | 6.0 / 7.7 ['200'] | 614.6 / 942.8 ['200'] | 858.6 / 975.9 ['200'] |
| GET /search?q=javascript | 5.3 / 6.2 ['200'] | 862.7 / 1263.5 ['200'] | 867.1 / 1026.6 ['200'] |

## Behaviour differences vs the monolith (0)

None.

## Flaky (outcome differed between the two suite runs on one deployment) (0)

None.

## Tests failing everywhere (suite contract vs the monolith, not a migration difference)

- Async Bestsellers Semantics > ASYNC-01 / TC-10: should eventually expose ordered books in bestsellers
- Cart Negative Cases > TC-NEG-02: should handle update of a non-existent cart item
- Catalog Contract > TC-01: should list seeded books
- Catalog Contract > TC-02: should filter books by category
- Catalog Contract > TC-03: should fetch a known book by id
- Catalog Contract > TC-04: should reject unknown book ids
- Catalog Contract > TC-05: should search books by keyword
- Catalog Contract > TC-06: should reject search without q
- Robustness > ROB-01: catalog responses should return valid JSON with correct Content-Type
- Robustness > ROB-02: should handle concurrent book detail requests
- Robustness > ROB-03: list and get should return consistent data for the same book
- Robustness > ROB-04: search should handle special characters gracefully
