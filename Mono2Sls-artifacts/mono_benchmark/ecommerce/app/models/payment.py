"""
Payment Model
Manages payment tokens and transaction information
"""
from datetime import datetime
from app.db import get_payment_3p_table


class PaymentToken:
    """Payment Token Model (Simulates third-party payment system)"""
    
    def __init__(self, payment_token=None, amount=None, status='AUTHORIZED',
                 card_number_last4=None, created_date=None, modified_date=None):
        """Initialize payment token object"""
        self.payment_token = payment_token
        self.amount = amount
        self.status = status
        self.card_number_last4 = card_number_last4
        self.created_date = created_date or datetime.utcnow().isoformat()
        self.modified_date = modified_date or datetime.utcnow().isoformat()
    
    def to_dict(self):
        """Convert to dictionary format"""
        return {
            'paymentToken': self.payment_token,
            'amount': self.amount,
            'status': self.status,
            'createdDate': self.created_date,
            'modifiedDate': self.modified_date
        }
    
    def save(self):
        """Save payment token to DynamoDB"""
        table = get_payment_3p_table()
        self.modified_date = datetime.utcnow().isoformat()
        
        item = {
            'paymentToken': self.payment_token,
            'amount': self.amount,
            'status': self.status,
            'createdDate': self.created_date,
            'modifiedDate': self.modified_date
        }
        
        if self.card_number_last4:
            item['cardNumberLast4'] = self.card_number_last4
        
        table.put_item(Item=item)
        return self
    
    @staticmethod
    def get_by_token(payment_token):
        """Get payment info by token"""
        table = get_payment_3p_table()
        
        response = table.get_item(Key={'paymentToken': payment_token})
        
        if 'Item' not in response:
            return None
        
        item = response['Item']
        return PaymentToken._from_dynamodb_item(item)
    
    @staticmethod
    def delete_by_token(payment_token):
        """Delete payment token"""
        table = get_payment_3p_table()
        table.delete_item(Key={'paymentToken': payment_token})
    
    @staticmethod
    def _from_dynamodb_item(item):
        """Create PaymentToken object from DynamoDB item"""
        return PaymentToken(
            payment_token=item.get('paymentToken'),
            amount=item.get('amount'),
            status=item.get('status', 'AUTHORIZED'),
            card_number_last4=item.get('cardNumberLast4'),
            created_date=item.get('createdDate'),
            modified_date=item.get('modifiedDate')
        )
    
    def __repr__(self):
        return f'<PaymentToken {self.payment_token}>'
