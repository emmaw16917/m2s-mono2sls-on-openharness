"""
DynamoDB connection management
Provides access to DynamoDB client/resource objects.
"""
import os
import boto3
from flask import g


# DynamoDB configuration
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', 'shopping-cart-monolith')


def get_dynamodb_client():
    """Get a DynamoDB client (low-level API)."""
    client = getattr(g, '_dynamodb_client', None)
    if client is None:
        client = g._dynamodb_client = boto3.client(
            'dynamodb',
            region_name=AWS_REGION
        )
    return client


def get_dynamodb_resource():
    """Get a DynamoDB resource (high-level API)."""
    resource = getattr(g, '_dynamodb_resource', None)
    if resource is None:
        resource = g._dynamodb_resource = boto3.resource(
            'dynamodb',
            region_name=AWS_REGION
        )
    return resource


def get_table():
    """Get the shopping cart main table."""
    dynamodb = get_dynamodb_resource()
    return dynamodb.Table(TABLE_NAME)


def close_connection(exception):
    """Cleanup hook (DynamoDB SDK manages connection pools; kept for compatibility)."""
    # Remove cached client/resource objects
    g.pop('_dynamodb_client', None)
    g.pop('_dynamodb_resource', None)
