"""
DynamoDB Database Connection Module
Provides access to DynamoDB resources and table objects
"""
import boto3
from flask import current_app


_dynamodb_resource = None
_dynamodb_client = None


def get_dynamodb_resource():
    """Get DynamoDB resource object (for high-level operations)"""
    global _dynamodb_resource
    
    if _dynamodb_resource is None:
        kwargs = {
            'region_name': current_app.config['AWS_REGION']
        }
        
        # If access keys are set, add them to configuration
        if current_app.config.get('AWS_ACCESS_KEY_ID') and current_app.config.get('AWS_SECRET_ACCESS_KEY'):
            kwargs['aws_access_key_id'] = current_app.config['AWS_ACCESS_KEY_ID']
            kwargs['aws_secret_access_key'] = current_app.config['AWS_SECRET_ACCESS_KEY']
        
        _dynamodb_resource = boto3.resource('dynamodb', **kwargs)
    
    return _dynamodb_resource


def get_dynamodb_client():
    """Get DynamoDB client object (for low-level operations)"""
    global _dynamodb_client
    
    if _dynamodb_client is None:
        kwargs = {
            'region_name': current_app.config['AWS_REGION']
        }
        
        if current_app.config.get('AWS_ACCESS_KEY_ID') and current_app.config.get('AWS_SECRET_ACCESS_KEY'):
            kwargs['aws_access_key_id'] = current_app.config['AWS_ACCESS_KEY_ID']
            kwargs['aws_secret_access_key'] = current_app.config['AWS_SECRET_ACCESS_KEY']
        
        _dynamodb_client = boto3.client('dynamodb', **kwargs)
    
    return _dynamodb_client


def get_table(table_config_key):
    """
    Get DynamoDB table object
    
    Args:
        table_config_key: Table name key in config (e.g. 'TABLE_USERS_NAME')
    
    Returns:
        DynamoDB Table object
    """
    dynamodb = get_dynamodb_resource()
    table_name = current_app.config[table_config_key]
    return dynamodb.Table(table_name)


def get_users_table():
    """Get Users table"""
    return get_table('TABLE_USERS_NAME')


def get_products_table():
    """Get Products table"""
    return get_table('TABLE_PRODUCTS_NAME')


def get_orders_table():
    """Get Orders table"""
    return get_table('TABLE_ORDERS_NAME')


def get_delivery_table():
    """Get Delivery table"""
    return get_table('TABLE_DELIVERY_NAME')


def get_warehouse_table():
    """Get Warehouse table"""
    return get_table('TABLE_WAREHOUSE_NAME')


def get_payment_3p_table():
    """Get Payment-3P table"""
    return get_table('TABLE_PAYMENT_3P_NAME')
