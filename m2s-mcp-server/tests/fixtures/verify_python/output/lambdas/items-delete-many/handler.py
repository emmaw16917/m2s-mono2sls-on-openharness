import json
import os

import boto3
from botocore.exceptions import ClientError

from shared_utils import response

table = boto3.resource("dynamodb").Table(os.environ["ITEMS_TABLE"])


def lambda_handler(event, context):
    ids = json.loads(event.get("body") or "{}").get("ids") or []
    deleted = 0
    missing = []
    failed = []
    for item_id in ids:
        try:
            table.delete_item(Key={"id": item_id}, ConditionExpression="attribute_exists(id)")
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                print(f"item {item_id} does not exist")
                missing.append(item_id)
            else:
                print(f"could not delete {item_id}: {error}")
                failed.append(item_id)
            continue
        deleted += 1
    return response(200, {"deleted": deleted, "missing": missing, "failed": failed})
