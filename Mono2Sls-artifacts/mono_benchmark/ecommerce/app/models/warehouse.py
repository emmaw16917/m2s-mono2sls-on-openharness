"""
Warehouse Model
Manages packaging requests and inventory
"""
from datetime import datetime
from app.db import get_warehouse_table
from boto3.dynamodb.conditions import Key, Attr


class PackagingRequest:
    """Packaging Request Model"""
    
    def __init__(self, order_id=None, status='NEW', products=None,
                 created_date=None, modified_date=None):
        """Initialize packaging request object"""
        self.order_id = order_id
        self.status = status
        self.products = products or []
        self.created_date = created_date or datetime.utcnow().isoformat()
        self.modified_date = modified_date or datetime.utcnow().isoformat()
    
    def to_dict(self):
        """Convert to dictionary format"""
        return {
            'orderId': self.order_id,
            'status': self.status,
            'products': self.products,
            'createdDate': self.created_date,
            'modifiedDate': self.modified_date
        }
    
    def save(self):
        """Save packaging request to DynamoDB"""
        table = get_warehouse_table()
        self.modified_date = datetime.utcnow().isoformat()
        
        # In Warehouse table, use composite primary key (orderId, productId)
        # To keep request-level metadata, use special productId: "__metadata"
        metadata_item = {
            'orderId': self.order_id,
            'productId': '__metadata',
            'status': self.status,
            'createdDate': self.created_date,
            'modifiedDate': self.modified_date
        }
        
        # If status is NEW, add newDate field for GSI
        if self.status == 'NEW':
            metadata_item['newDate'] = self.created_date
        
        table.put_item(Item=metadata_item)
        
        # Save each product
        for product in self.products:
            product_item = {
                'orderId': self.order_id,
                'productId': product['productId'],
                'quantity': int(product.get('quantity', 1)),
                'createdDate': self.created_date,
                'modifiedDate': self.modified_date
            }
            
            if self.status == 'NEW':
                product_item['newDate'] = self.created_date
            
            table.put_item(Item=product_item)
        
        return self
    
    @staticmethod
    def get_by_order_id(order_id):
        """Get packaging request by order ID"""
        table = get_warehouse_table()
        
        # Query all items for this order
        response = table.query(
            KeyConditionExpression=Key('orderId').eq(order_id)
        )
        
        items = response.get('Items', [])
        if not items:
            return None
        
        # Separate metadata and products
        metadata = None
        products = []
        
        for item in items:
            if item.get('productId') == '__metadata':
                metadata = item
            else:
                products.append({
                    'productId': item.get('productId'),
                    'quantity': int(item.get('quantity', 1))
                })
        
        if not metadata:
            return None
        
        return PackagingRequest(
            order_id=order_id,
            status=metadata.get('status', 'NEW'),
            products=products,
            created_date=metadata.get('createdDate'),
            modified_date=metadata.get('modifiedDate')
        )
    
    @staticmethod
    def get_new_requests(limit=100):
        """Get all new packaging requests"""
        table = get_warehouse_table()
        
        # Scan metadata items with newDate field
        response = table.scan(
            FilterExpression=Attr('productId').eq('__metadata') & Attr('status').eq('NEW'),
            Limit=limit
        )
        
        order_ids = [item['orderId'] for item in response.get('Items', [])]
        
        # For each order ID, get full packaging request
        requests = []
        for order_id in order_ids:
            request = PackagingRequest.get_by_order_id(order_id)
            if request:
                requests.append(request)
        
        return requests
    
    def update_status(self, new_status):
        """Update packaging request status"""
        table = get_warehouse_table()
        self.status = new_status
        self.modified_date = datetime.utcnow().isoformat()
        
        update_expression = "SET #status = :status, modifiedDate = :modified_date"
        expression_attribute_names = {'#status': 'status'}
        expression_attribute_values = {
            ':status': new_status,
            ':modified_date': self.modified_date
        }
        
        # If status changes, remove newDate field
        if new_status != 'NEW':
            update_expression += " REMOVE newDate"
        
        # Update metadata item
        table.update_item(
            Key={'orderId': self.order_id, 'productId': '__metadata'},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values
        )
        
        # Update newDate for all product items (if exists)
        if new_status != 'NEW':
            for product in self.products:
                try:
                    table.update_item(
                        Key={'orderId': self.order_id, 'productId': product['productId']},
                        UpdateExpression="REMOVE newDate",
                        ConditionExpression=Attr('newDate').exists()
                    )
                except Exception:
                    # Ignore error if newDate does not exist
                    pass
    
    def __repr__(self):
        return f'<PackagingRequest {self.order_id}>'


# Alias for backward compatibility
class PackagingProduct:
    """Product in packaging request (for compatibility)"""
    
    def __init__(self, product_id, quantity=1):
        self.product_id = product_id
        self.quantity = quantity
    
    def to_dict(self):
        return {
            'productId': self.product_id,
            'quantity': self.quantity
        }
    
    def __repr__(self):
        return f'<PackagingProduct {self.product_id}>'
