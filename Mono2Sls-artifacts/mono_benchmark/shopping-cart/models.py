"""
Data models and database operations
Provides CRUD operations for users, cart items, and product aggregates.
Uses DynamoDB as the data store.
"""
import json
from uuid import uuid4
from datetime import datetime, timedelta
from decimal import Decimal
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError

from db import get_table
from auth import hash_password


# ==================== Helper Functions ====================

def _python_obj_to_dynamodb(obj):
    """
    Convert Python objects to DynamoDB-compatible structures.
    Mainly handles float -> Decimal conversion.
    """
    if isinstance(obj, dict):
        return {k: _python_obj_to_dynamodb(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_python_obj_to_dynamodb(item) for item in obj]
    elif isinstance(obj, float):
        return Decimal(str(obj))
    else:
        return obj


def _dynamodb_to_python_obj(obj):
    """
    Convert DynamoDB objects to standard Python objects.
    Mainly handles Decimal -> int/float conversion.
    """
    if isinstance(obj, dict):
        return {k: _dynamodb_to_python_obj(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_dynamodb_to_python_obj(item) for item in obj]
    elif isinstance(obj, Decimal):
        # If it's an integer, return int; otherwise return float
        if obj % 1 == 0:
            return int(obj)
        else:
            return float(obj)
    else:
        return obj


# ==================== User Operations ====================

def create_user(username, password, email=None):
    """
    Create a new user.
    
    Args:
        username: Username
        password: Plaintext password
        email: Email (optional)
    
    Returns:
        User ID
    """
    table = get_table()
    user_id = str(uuid4())
    password_hash = hash_password(password)
    
    # User record: pk='USER#{username}', sk='PROFILE'
    item = {
        'pk': f'USER#{username}',
        'sk': 'PROFILE',
        'id': user_id,
        'username': username,
        'password_hash': password_hash,
        'email': email,
        'created_at': datetime.now().isoformat()
    }
    
    try:
        # Use a conditional expression to ensure username uniqueness
        table.put_item(
            Item=item,
            ConditionExpression='attribute_not_exists(pk)'
        )
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            raise Exception(f'Username {username} already exists')
        raise
    
    return user_id


def get_user_by_username(username):
    """
    Get user info by username.
    
    Args:
        username: Username
    
    Returns:
        User dict or None
    """
    table = get_table()
    
    try:
        response = table.get_item(
            Key={
                'pk': f'USER#{username}',
                'sk': 'PROFILE'
            }
        )
        
        if 'Item' in response:
            return _dynamodb_to_python_obj(response['Item'])
        return None
    except Exception as e:
        print(f"Failed to get user: {e}")
        return None


def get_user_by_id(user_id):
    """
    Get user info by user ID.
    
    Args:
        user_id: User ID
    
    Returns:
        User dict or None
    """
    table = get_table()
    
    # Because username is part of the primary key, we need to scan the table.
    # In production, consider adding a GSI to optimize this query.
    try:
        response = table.scan(
            FilterExpression=Attr('id').eq(user_id) & Attr('sk').eq('PROFILE')
        )
        
        if response['Items']:
            return _dynamodb_to_python_obj(response['Items'][0])
        return None
    except Exception as e:
        print(f"Failed to get user: {e}")
        return None


# ==================== Product Operations ====================

def get_all_products():
    """
    Get all products from DynamoDB.

    Returns:
        List of product dicts
    """
    table = get_table()
    products = []

    try:
        scan_kwargs = {
            'FilterExpression': Attr('pk').begins_with('PRODUCT#') & Attr('sk').eq('DETAIL')
        }

        while True:
            response = table.scan(**scan_kwargs)
            for item in response.get('Items', []):
                product = _dynamodb_to_python_obj(item)
                product.pop('pk', None)
                product.pop('sk', None)
                products.append(product)

            last_key = response.get('LastEvaluatedKey')
            if not last_key:
                break
            scan_kwargs['ExclusiveStartKey'] = last_key

        return products
    except Exception as e:
        print(f"Failed to get products: {e}")
        return []


def get_product_by_id(product_id):
    """
    Get product detail by product ID from DynamoDB.

    Args:
        product_id: Product ID

    Returns:
        Product dict or None
    """
    table = get_table()

    try:
        response = table.get_item(
            Key={
                'pk': f'PRODUCT#{product_id}',
                'sk': 'DETAIL'
            }
        )
        item = response.get('Item')
        if not item:
            return None

        product = _dynamodb_to_python_obj(item)
        product.pop('pk', None)
        product.pop('sk', None)
        return product
    except Exception as e:
        print(f"Failed to get product {product_id}: {e}")
        return None


# ==================== Cart Operations ====================

def get_cart_items(pk):
    """
    Get all items in a cart.
    
    Args:
        pk: Partition key (user#xxx or cart#xxx)
    
    Returns:
        List of items
    """
    table = get_table()
    
    try:
        # Query cart items: only items with sk beginning with product#
        response = table.query(
            KeyConditionExpression=Key('pk').eq(pk) & Key('sk').begins_with('product#')
        )
        
        items = []
        current_time = datetime.now()
        
        for item in response['Items']:
            # Filter out zero-quantity and expired items
            quantity = item.get('quantity', 0)
            if quantity <= 0:
                continue
            
            # Check expiration
            expiration_time = item.get('expirationTime')
            if expiration_time:
                # DynamoDB TTL is a Unix timestamp (seconds)
                # Convert Decimal to int if needed
                if isinstance(expiration_time, Decimal):
                    expiration_time = int(expiration_time)
                exp_datetime = datetime.fromtimestamp(expiration_time)
                if exp_datetime < current_time:
                    continue
            
            # Parse product_detail
            product_detail = item.get('product_detail')
            if product_detail and isinstance(product_detail, str):
                product_detail = json.loads(product_detail)
            
            items.append({
                'sk': item['sk'],
                'quantity': int(quantity) if isinstance(quantity, Decimal) else quantity,
                'productDetail': _dynamodb_to_python_obj(product_detail)
            })
        
        return items
    except Exception as e:
        print(f"Failed to get cart: {e}")
        return []


def add_cart_item(pk, product_id, quantity, product_detail, expiration_time):
    """
    Add or update a cart item (increment quantity).
    
    Args:
        pk: Partition key (user#xxx or cart#xxx)
        product_id: Product ID
        quantity: Quantity change (can be negative)
        product_detail: Product detail dict
        expiration_time: Expiration time (datetime)
    """
    table = get_table()
    sk = f"product#{product_id}"
    
    # Convert expiration time to Unix timestamp (DynamoDB TTL format)
    ttl = int(expiration_time.timestamp()) if expiration_time else None
    
    # Serialize product_detail as JSON string
    product_detail_json = json.dumps(_python_obj_to_dynamodb(product_detail))
    
    try:
        # Use UpdateItem for atomic increment
        response = table.update_item(
            Key={'pk': pk, 'sk': sk},
            UpdateExpression='ADD quantity :qty SET product_detail = :detail, expirationTime = :ttl, updated_at = :now',
            ExpressionAttributeValues={
                ':qty': quantity,
                ':detail': product_detail_json,
                ':ttl': ttl,
                ':now': datetime.now().isoformat()
            },
            ReturnValues='ALL_NEW'
        )
        
        # If the quantity becomes <= 0 after update, delete the item
        new_quantity = response['Attributes'].get('quantity', 0)
        if new_quantity <= 0:
            table.delete_item(Key={'pk': pk, 'sk': sk})
            
    except Exception as e:
        print(f"Failed to add cart item: {e}")
        raise


def update_cart_item_quantity(pk, product_id, quantity, product_detail, expiration_time):
    """
    Update cart item quantity (idempotent, set to an absolute value).
    
    Args:
        pk: Partition key (user#xxx or cart#xxx)
        product_id: Product ID
        quantity: Quantity
        product_detail: Product detail dict
        expiration_time: Expiration time (datetime)
    """
    table = get_table()
    sk = f"product#{product_id}"
    
    # Convert expiration time to Unix timestamp
    ttl = int(expiration_time.timestamp()) if expiration_time else None
    
    # Serialize product_detail as JSON string
    product_detail_json = json.dumps(_python_obj_to_dynamodb(product_detail))
    
    try:
        if quantity <= 0:
            # Quantity is 0 or negative: delete the item
            table.delete_item(Key={'pk': pk, 'sk': sk})
        else:
            # Set absolute quantity
            table.put_item(
                Item={
                    'pk': pk,
                    'sk': sk,
                    'quantity': quantity,
                    'product_detail': product_detail_json,
                    'expirationTime': ttl,
                    'updated_at': datetime.now().isoformat()
                }
            )
    except Exception as e:
        print(f"Failed to update cart item: {e}")
        raise


def delete_cart_items(pk):
    """
    Delete all items for a given user/cart.
    
    Args:
        pk: Partition key (user#xxx or cart#xxx)
    """
    table = get_table()
    
    try:
        # Query all cart items first
        response = table.query(
            KeyConditionExpression=Key('pk').eq(pk) & Key('sk').begins_with('product#')
        )
        
        # Batch delete
        with table.batch_writer() as batch:
            for item in response['Items']:
                batch.delete_item(
                    Key={
                        'pk': item['pk'],
                        'sk': item['sk']
                    }
                )
    except Exception as e:
        print(f"Failed to delete cart items: {e}")
        raise


def migrate_cart_items(anonymous_pk, user_pk):
    """
    Migrate an anonymous cart to the user account.
    
    Args:
        anonymous_pk: Anonymous cart partition key (cart#xxx)
        user_pk: User partition key (user#xxx)
    
    Returns:
        List of migrated items
    """
    table = get_table()
    
    try:
        # Fetch all items in the anonymous cart
        response = table.query(
            KeyConditionExpression=Key('pk').eq(anonymous_pk) & Key('sk').begins_with('product#')
        )
        
        anonymous_items = response['Items']
        migrated_items = []
        
        # Migrate to the user cart
        for item in anonymous_items:
            sk = item['sk']
            quantity = item.get('quantity', 0)
            # Handle Decimal type
            if isinstance(quantity, Decimal):
                quantity = int(quantity)
            product_detail = item.get('product_detail')
            
            if quantity <= 0:
                continue
            
            # Set new expiration time (authenticated users keep items for 30 days)
            expiration_time = datetime.now() + timedelta(days=30)
            ttl = int(expiration_time.timestamp())
            
            # Check if the user cart already has this product
            try:
                user_response = table.get_item(Key={'pk': user_pk, 'sk': sk})
                
                if 'Item' in user_response:
                    # Exists: increment quantity (atomic ADD)
                    table.update_item(
                        Key={'pk': user_pk, 'sk': sk},
                        UpdateExpression='ADD quantity :qty SET product_detail = :detail, expirationTime = :ttl, updated_at = :now',
                        ExpressionAttributeValues={
                            ':qty': quantity,
                            ':detail': product_detail,
                            ':ttl': ttl,
                            ':now': datetime.now().isoformat()
                        }
                    )
                else:
                    # Not exists: insert
                    table.put_item(
                        Item={
                            'pk': user_pk,
                            'sk': sk,
                            'quantity': quantity,
                            'product_detail': product_detail,
                            'expirationTime': ttl,
                            'updated_at': datetime.now().isoformat()
                        }
                    )
            except Exception as e:
                print(f"Failed to migrate item {sk}: {e}")
                continue
            
            migrated_items.append({
                'sk': sk,
                'quantity': quantity,
                'product_detail': product_detail
            })
        
        # Delete items from the anonymous cart
        delete_cart_items(anonymous_pk)
        
        return migrated_items
        
    except Exception as e:
        print(f"Failed to migrate cart: {e}")
        raise


# ==================== Product Aggregate Operations ====================

def get_product_total_quantity(product_id):
    """
    Get total quantity of a product across all carts.
    
    Args:
        product_id: Product ID
    
    Returns:
        Total quantity
    """
    table = get_table()
    
    try:
        response = table.get_item(
            Key={
                'pk': f'PRODUCT#{product_id}',
                'sk': 'TOTAL'
            }
        )
        
        if 'Item' in response:
            total = response['Item'].get('total_quantity', 0)
            return int(total) if isinstance(total, Decimal) else total
        return 0
    except Exception as e:
        print(f"Failed to get product aggregate: {e}")
        return 0


def update_product_total_quantity(product_id, quantity_change):
    """
    Update product total quantity (incremental update).
    
    Args:
        product_id: Product ID
        quantity_change: Quantity delta (can be positive or negative)
    """
    table = get_table()
    pk = f'PRODUCT#{product_id}'
    sk = 'TOTAL'
    
    try:
        # Use atomic ADD
        response = table.update_item(
            Key={'pk': pk, 'sk': sk},
            UpdateExpression='ADD total_quantity :change SET updated_at = :now',
            ExpressionAttributeValues={
                ':change': quantity_change,
                ':now': datetime.now().isoformat()
            },
            ReturnValues='ALL_NEW'
        )
        
        # If the total becomes < 0, reset to 0
        new_total = response['Attributes'].get('total_quantity', 0)
        if new_total < 0:
            table.update_item(
                Key={'pk': pk, 'sk': sk},
                UpdateExpression='SET total_quantity = :zero',
                ExpressionAttributeValues={':zero': 0}
            )
    except ClientError as e:
        if e.response['Error']['Code'] == 'ValidationException':
            # If the record doesn't exist, create it
            initial_total = max(0, quantity_change)
            table.put_item(
                Item={
                    'pk': pk,
                    'sk': sk,
                    'total_quantity': initial_total,
                    'updated_at': datetime.now().isoformat()
                }
            )
        else:
            print(f"Failed to update product aggregate: {e}")
            raise
    except Exception as e:
        print(f"Failed to update product aggregate: {e}")
        raise


# ==================== Data Cleanup ====================

def cleanup_expired_items():
    """
    Clean up expired cart items.
    Note: DynamoDB TTL will automatically remove expired items; this is a fallback manual cleanup.
    """
    table = get_table()
    current_timestamp = int(datetime.now().timestamp())
    
    try:
        # Scan all expired cart items
        response = table.scan(
            FilterExpression=Attr('expirationTime').exists() & Attr('expirationTime').lt(current_timestamp)
        )
        
        deleted_count = 0
        with table.batch_writer() as batch:
            for item in response['Items']:
                batch.delete_item(
                    Key={
                        'pk': item['pk'],
                        'sk': item['sk']
                    }
                )
                deleted_count += 1
        
        return deleted_count
    except Exception as e:
        print(f"Failed to clean up expired items: {e}")
        return 0
