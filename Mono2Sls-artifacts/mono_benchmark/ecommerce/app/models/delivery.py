"""
Delivery Model
Manages delivery information and status
"""
from datetime import datetime
from app.db import get_delivery_table
from boto3.dynamodb.conditions import Attr


class Delivery:
    """Delivery Model"""
    
    def __init__(self, order_id=None, status='NEW', address=None,
                 created_date=None, modified_date=None):
        """Initialize delivery object"""
        self.order_id = order_id
        self.status = status
        
        # Address information
        if address:
            self.address_name = address.get('name')
            self.address_company_name = address.get('companyName')
            self.address_street = address.get('streetAddress')
            self.address_post_code = address.get('postCode')
            self.address_city = address.get('city')
            self.address_state = address.get('state')
            self.address_country = address.get('country')
            self.address_phone = address.get('phoneNumber')
        else:
            self.address_name = None
            self.address_company_name = None
            self.address_street = None
            self.address_post_code = None
            self.address_city = None
            self.address_state = None
            self.address_country = None
            self.address_phone = None
        
        self.created_date = created_date or datetime.utcnow().isoformat()
        self.modified_date = modified_date or datetime.utcnow().isoformat()
    
    def get_address(self):
        """Get address info (dictionary format)"""
        return {
            'name': self.address_name,
            'companyName': self.address_company_name,
            'streetAddress': self.address_street,
            'postCode': self.address_post_code,
            'city': self.address_city,
            'state': self.address_state,
            'country': self.address_country,
            'phoneNumber': self.address_phone
        }
    
    def set_address(self, address_dict):
        """Set address info"""
        self.address_name = address_dict.get('name')
        self.address_company_name = address_dict.get('companyName')
        self.address_street = address_dict.get('streetAddress')
        self.address_post_code = address_dict.get('postCode')
        self.address_city = address_dict.get('city')
        self.address_state = address_dict.get('state')
        self.address_country = address_dict.get('country')
        self.address_phone = address_dict.get('phoneNumber')
    
    def to_dict(self):
        """Convert to dictionary format"""
        return {
            'orderId': self.order_id,
            'status': self.status,
            'address': self.get_address(),
            'createdDate': self.created_date,
            'modifiedDate': self.modified_date
        }
    
    def save(self):
        """Save delivery info to DynamoDB"""
        table = get_delivery_table()
        self.modified_date = datetime.utcnow().isoformat()
        
        item = {
            'orderId': self.order_id,
            'status': self.status,
            'address': self.get_address(),
            'createdDate': self.created_date,
            'modifiedDate': self.modified_date
        }
        
        # If status is NEW, add isNew field for GSI
        if self.status == 'NEW':
            item['isNew'] = 'true'
        
        table.put_item(Item=item)
        return self
    
    @staticmethod
    def get_by_order_id(order_id):
        """Get delivery info by order ID"""
        table = get_delivery_table()
        
        response = table.get_item(Key={'orderId': order_id})
        
        if 'Item' not in response:
            return None
        
        item = response['Item']
        return Delivery._from_dynamodb_item(item)
    
    @staticmethod
    def get_new_deliveries(limit=100):
        """Get all new delivery requests"""
        table = get_delivery_table()
        
        response = table.scan(
            FilterExpression=Attr('status').eq('NEW'),
            Limit=limit
        )
        
        deliveries = [Delivery._from_dynamodb_item(item) for item in response.get('Items', [])]
        return deliveries
    
    def update_status(self, new_status):
        """Update delivery status"""
        table = get_delivery_table()
        self.status = new_status
        self.modified_date = datetime.utcnow().isoformat()
        
        update_expression = "SET #status = :status, modifiedDate = :modified_date"
        expression_attribute_names = {'#status': 'status'}
        expression_attribute_values = {
            ':status': new_status,
            ':modified_date': self.modified_date
        }
        
        # If status changes, remove isNew field
        if new_status != 'NEW':
            update_expression += " REMOVE isNew"
        
        table.update_item(
            Key={'orderId': self.order_id},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values
        )
    
    @staticmethod
    def _from_dynamodb_item(item):
        """Create Delivery object from DynamoDB item"""
        address = item.get('address', {})
        return Delivery(
            order_id=item.get('orderId'),
            status=item.get('status', 'NEW'),
            address=address,
            created_date=item.get('createdDate'),
            modified_date=item.get('modifiedDate')
        )
    
    def __repr__(self):
        return f'<Delivery {self.order_id}>'
