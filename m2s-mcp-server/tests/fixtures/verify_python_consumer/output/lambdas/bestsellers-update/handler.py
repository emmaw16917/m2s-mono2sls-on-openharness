"""SQS consumer: adds each order's book quantities to BestsellersTable.

Idempotent (Pattern A, m2s-async-patterns-kb): one TransactWriteItems per
batch commits the batch's dedupe marker together with its counter updates,
so a redelivered message can never be counted twice.
"""

import json
import os
import random
import time

import boto3
from botocore.exceptions import ClientError

# A client, not a Table resource: transactions are a client operation, and
# clients (unlike resources) are thread-safe.
ddb = boto3.client("dynamodb")
BESTSELLERS_TABLE = os.environ["BESTSELLERS_TABLE"]
IDEMPOTENCY_TABLE = os.environ["IDEMPOTENCY_TABLE"]

CONSUMER = "bestsellers-update"
MARKER_TTL_SECONDS = 7 * 24 * 3600  # longer than SQS retention + retries
MAX_EFFECTS_PER_TRANSACTION = 99   # 100 items per transaction, one is the batch's own marker
MAX_RETRIES = 3                    # after the first attempt
RETRYABLE_CODES = {"ProvisionedThroughputExceededException", "ThrottlingException", "RequestLimitExceeded",
                   "InternalServerError", "ServiceUnavailable", "TransactionConflictException"}


def merge_by_key(books):
    """One entry per bookId (a transaction may not touch an item twice); dicts
    keep first-appearance order, which makes the batches deterministic."""
    merged = {}
    for book in books:
        merged[book["bookId"]] = merged.get(book["bookId"], 0) + int(book.get("quantity") or 0)
    return merged


def transaction_batches(order_id, merged):
    """Chunks of at most 99 updates, each with its own marker: one batch keeps
    the plain key <consumer>#<orderId>, several use <consumer>#<orderId>#<i>."""
    updates = [{"Update": {"TableName": BESTSELLERS_TABLE, "Key": {"bookId": {"S": book_id}},
                           "UpdateExpression": "ADD salesCount :q", "ExpressionAttributeValues": {":q": {"N": str(qty)}}}}
               for book_id, qty in merged.items()]
    chunks = [updates[i:i + MAX_EFFECTS_PER_TRANSACTION] for i in range(0, len(updates), MAX_EFFECTS_PER_TRANSACTION)]
    expires_at = str(int(time.time()) + MARKER_TTL_SECONDS)
    batches = []
    for index, chunk in enumerate(chunks):
        pk = f"{CONSUMER}#{order_id}#{index}" if len(chunks) > 1 else f"{CONSUMER}#{order_id}"
        marker = {"Put": {"TableName": IDEMPOTENCY_TABLE, "Item": {"pk": {"S": pk}, "expiresAt": {"N": expires_at}},
                          "ConditionExpression": "attribute_not_exists(pk)"}}
        batches.append([marker, *chunk])  # the marker must stay first: duplicates are read from reason [0]
    return batches


def classify(error):
    """'duplicate' | 'retryable' | 'permanent'."""
    code = error.response.get("Error", {}).get("Code")
    if code == "TransactionCanceledException":
        reasons = error.response.get("CancellationReasons") or []
        if reasons and reasons[0].get("Code") == "ConditionalCheckFailed":
            return "duplicate"  # this batch was committed by an earlier delivery
        if any(r.get("Code") == "TransactionConflict" for r in reasons):
            return "retryable"
        return "permanent"
    return "retryable" if code in RETRYABLE_CODES else "permanent"


def apply_message(order_id, books):
    """'applied' | 'duplicate' | 'failed_retryable' | 'failed_permanent'."""
    batches = transaction_batches(order_id, merge_by_key(books))
    duplicates = 0
    for items in batches:
        for attempt in range(MAX_RETRIES + 1):
            if attempt:
                time.sleep((min(50 * 2 ** (attempt - 1), 800) + random.randint(0, 50)) / 1000)
            try:
                ddb.transact_write_items(TransactItems=items)
                break
            except ClientError as error:
                kind = classify(error)
                if kind == "duplicate":
                    duplicates += 1
                    break
                if kind == "permanent":
                    print(f"Permanent failure for order {order_id}, dropping the rest of the message: {error}")
                    return "failed_permanent"
        else:
            # Never swallow a conflict: report the message, SQS redelivers it
            # and the markers skip the batches that were already committed.
            print(f"Retries exhausted for order {order_id}, reporting it for redelivery")
            return "failed_retryable"
    return "duplicate" if duplicates == len(batches) else "applied"


def lambda_handler(event, context):
    records = event.get("Records") or []
    batch_item_failures = []
    for record in records:
        try:
            body = json.loads(record["body"])
            order_id, books = body["orderId"], body["books"]
        except (ValueError, KeyError, TypeError):
            print(f"Skipping malformed message {record.get('messageId')}")
            continue
        if apply_message(order_id, books) == "failed_retryable":
            batch_item_failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": batch_item_failures}
