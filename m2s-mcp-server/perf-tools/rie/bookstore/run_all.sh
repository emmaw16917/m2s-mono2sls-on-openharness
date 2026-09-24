# Full bookstore-oh baseline (PERF_OPTIMIZATION.md experiment 7). Run from the
# directory that holds bookstore-oh's template.yaml after `sam build` (short path,
# e.g. D:/tmp/m2s-bs): bash <this>/run_all.sh > results.jsonl; node <this>/aggregate.js results.jsonl
# Validate first: VALIDATE=1 bash ../rie_measure.sh <Fn> <event> 0  for every function.
D=$(dirname "$0"); M=$D/../rie_measure.sh
API="BooksListFunction BooksGetByIdFunction SearchBooksFunction CartListItemsFunction CartGetItemFunction CartAddItemFunction CartUpdateItemFunction CartRemoveItemFunction OrdersListFunction OrdersCreateFunction BestsellersListFunction"
for rep in 1 2 3; do
  for fn in $API; do
    EXTRA_LABEL="|A|r$rep" bash $M $fn $D/api.json 0
    EXTRA_LABEL="|B|r$rep" bash $M $fn $D/api.json 0.289 -e SHIM_LATENCY_MS=5
  done
  EXTRA_LABEL="(scan)|A|r$rep" bash $M BooksListFunction $D/api-noquery.json 0
  EXTRA_LABEL="(scan)|B|r$rep" bash $M BooksListFunction $D/api-noquery.json 0.289 -e SHIM_LATENCY_MS=5
  EXTRA_LABEL="|A|r$rep" bash $M BestsellersUpdateConsumerFunction $D/sqs.json 0
  EXTRA_LABEL="|B|r$rep" bash $M BestsellersUpdateConsumerFunction $D/sqs.json 0.289 -e SHIM_LATENCY_MS=5
done
echo DONE
