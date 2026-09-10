"""
User Model
Manages user account information and authentication
"""
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from app.db import get_users_table
from boto3.dynamodb.conditions import Key


class User:
    """User Model"""
    
    def __init__(self, user_id=None, email=None, password_hash=None, role='user',
                 created_date=None, modified_date=None):
        """Initialize user object"""
        self.user_id = user_id
        self.email = email
        self.password_hash = password_hash
        self.role = role
        self.created_date = created_date or datetime.utcnow().isoformat()
        self.modified_date = modified_date or datetime.utcnow().isoformat()
    
    def set_password(self, password):
        """Set password (encrypted storage)"""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """Verify password"""
        return check_password_hash(self.password_hash, password)
    
    def to_dict(self):
        """Convert to dictionary format"""
        return {
            'userId': self.user_id,
            'email': self.email,
            'createdDate': self.created_date,
            'modifiedDate': self.modified_date,
            'role': self.role
        }
    
    def save(self):
        """Save user to DynamoDB"""
        table = get_users_table()
        self.modified_date = datetime.utcnow().isoformat()
        
        item = {
            'userId': self.user_id,
            'email': self.email,
            'passwordHash': self.password_hash,
            'role': self.role,
            'createdDate': self.created_date,
            'modifiedDate': self.modified_date
        }
        
        table.put_item(Item=item)
        return self
    
    @staticmethod
    def get_by_id(user_id):
        """Get user by user ID"""
        table = get_users_table()
        
        response = table.get_item(Key={'userId': user_id})
        
        if 'Item' not in response:
            return None
        
        item = response['Item']
        return User._from_dynamodb_item(item)
    
    @staticmethod
    def get_by_email(email):
        """Get user by email"""
        table = get_users_table()
        
        response = table.query(
            IndexName='email-index',
            KeyConditionExpression=Key('email').eq(email)
        )
        
        if not response['Items']:
            return None
        
        item = response['Items'][0]
        return User._from_dynamodb_item(item)
    
    @staticmethod
    def _from_dynamodb_item(item):
        """Create User object from DynamoDB item"""
        return User(
            user_id=item.get('userId'),
            email=item.get('email'),
            password_hash=item.get('passwordHash'),
            role=item.get('role', 'user'),
            created_date=item.get('createdDate'),
            modified_date=item.get('modifiedDate')
        )
    
    def __repr__(self):
        return f'<User {self.email}>'
