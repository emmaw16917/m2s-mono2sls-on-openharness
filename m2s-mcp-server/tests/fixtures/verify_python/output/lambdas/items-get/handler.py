import os

import boto3

from shared_utils import response

table = boto3.resource("dynamodb").Table(os.environ["ITEMS_TABLE"])


def lambda_handler(event, context):
    item_id = (event.get("pathParameters") or {}).get("id")
    if not item_id:
        return response(400, {"error": "id is required"})
    item = table.get_item(Key={"id": item_id}).get("Item")
    if item is None:
        return response(404, {"error": "not found"})
    table.update_item(Key={"id": item_id}, UpdateExpression="ADD #v :one", ExpressionAttributeNames={"#v": "views"},
                      ExpressionAttributeValues={":one": 1})
    return response(200, {"id": item["id"], "name": item.get("name")})
