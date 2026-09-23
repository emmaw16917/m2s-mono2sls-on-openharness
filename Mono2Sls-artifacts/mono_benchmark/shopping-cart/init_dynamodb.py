"""
DynamoDB table initialization script
Creates the DynamoDB table required by the shopping cart application.

Usage:
  python init_dynamodb.py
"""
import os
import sys
import boto3
from botocore.exceptions import ClientError


# DynamoDB configuration
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', 'shopping-cart-monolith')


def get_dynamodb_resource():
    """Get a DynamoDB resource object."""
    return boto3.resource(
        'dynamodb',
        region_name=AWS_REGION
    )


def init_dynamodb():
    """
    Initialize the DynamoDB table schema.
    Creates a single-table design to store users, cart items, and product aggregate data.
    """
    dynamodb = get_dynamodb_resource()
    
    try:
        # Check if the table already exists
        existing_tables = dynamodb.meta.client.list_tables()['TableNames']
        if TABLE_NAME in existing_tables:
            print(f"Table {TABLE_NAME} already exists; skipping creation.")
            return
        
        # Create the main table - single-table design
        # pk (Partition Key) and sk (Sort Key) are used to distinguish different item types:
        # - User: pk='USER#{username}', sk='PROFILE'
        # - Cart item: pk='user#{userId}' or 'cart#{cartId}', sk='product#{productId}'
        # - Product aggregate: pk='PRODUCT#{productId}', sk='TOTAL'
        table = dynamodb.create_table(
            TableName=TABLE_NAME,
            KeySchema=[
                {'AttributeName': 'pk', 'KeyType': 'HASH'},   # Partition key
                {'AttributeName': 'sk', 'KeyType': 'RANGE'}   # Sort key
            ],
            AttributeDefinitions=[
                {'AttributeName': 'pk', 'AttributeType': 'S'},
                {'AttributeName': 'sk', 'AttributeType': 'S'},
                {'AttributeName': 'username', 'AttributeType': 'S'}  # GSI
            ],
            # Add a global secondary index to query users by username
            GlobalSecondaryIndexes=[
                {
                    'IndexName': 'username-index',
                    'KeySchema': [
                        {'AttributeName': 'username', 'KeyType': 'HASH'}
                    ],
                    'Projection': {'ProjectionType': 'ALL'},
                    'ProvisionedThroughput': {
                        'ReadCapacityUnits': 5,
                        'WriteCapacityUnits': 5
                    }
                }
            ],
            BillingMode='PROVISIONED',
            ProvisionedThroughput={
                'ReadCapacityUnits': 5,
                'WriteCapacityUnits': 5
            }
        )
        
        # Wait for table creation to complete
        print(f"Creating table {TABLE_NAME}...")
        table.meta.client.get_waiter('table_exists').wait(TableName=TABLE_NAME)
        print(f"Table {TABLE_NAME} created successfully!")
        
        # Enable TTL (auto cleanup for expired cart items)
        try:
            dynamodb.meta.client.update_time_to_live(
                TableName=TABLE_NAME,
                TimeToLiveSpecification={
                    'Enabled': True,
                    'AttributeName': 'expirationTime'
                }
            )
            print(f"TTL enabled for table {TABLE_NAME}.")
        except Exception as e:
            print(f"Error enabling TTL (can be ignored): {e}")
            
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceInUseException':
            print(f"Table {TABLE_NAME} already exists.")
        else:
            print(f"Error creating table: {e}")
            raise
    except Exception as e:
        print(f"Error initializing DynamoDB: {e}")
        raise


def main():
    """Main entry point."""
    print('========================================')
    print('Shopping Cart App - DynamoDB Table Initialization')
    print('========================================')
    print(f'Region: {AWS_REGION}')
    print('')
    
    try:
        init_dynamodb()
        
        print('')
        print('========================================')
        print('✓ Database initialization complete!')
        print('========================================')
        print('')
        print('Created table:')
        print(f'  1. {TABLE_NAME} - Shopping cart main table (single-table design)')
        print('')
        print('You can now start the app:')
        print('  python app.py')
        print('')
        
    except Exception as e:
        print('')
        print('========================================')
        print('✗ Initialization failed!')
        print('========================================')
        print(f'Error: {e}')
        print('')
        sys.exit(1)


if __name__ == '__main__':
    main()
