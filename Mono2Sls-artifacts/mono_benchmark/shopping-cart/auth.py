"""
Authentication module
Provides JWT token creation/verification and password hashing utilities.
"""
import os
import jwt
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash


# JWT configuration
SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
ALGORITHM = 'HS256'
TOKEN_EXPIRATION_HOURS = 24


def create_token(user_id, username):
    """
    Create a JWT token.
    
    Args:
        user_id: User ID
        username: Username
    
    Returns:
        JWT token string
    """
    payload = {
        'sub': user_id,
        'username': username,
        'exp': datetime.utcnow() + timedelta(hours=TOKEN_EXPIRATION_HOURS),
        'iat': datetime.utcnow()
    }
    
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return token


def verify_token(token):
    """
    Verify a JWT token.
    
    Args:
        token: JWT token string
    
    Returns:
        Decoded payload dict
    
    Raises:
        jwt.ExpiredSignatureError: Token has expired
        jwt.InvalidTokenError: Token is invalid
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise Exception('Token has expired')
    except jwt.InvalidTokenError:
        raise Exception('Invalid token')


def hash_password(password):
    """
    Hash a password.
    
    Args:
        password: Plaintext password
    
    Returns:
        Password hash
    """
    return generate_password_hash(password, method='pbkdf2:sha256')


def verify_password(password, password_hash):
    """
    Verify a password against a hash.
    
    Args:
        password: Plaintext password
        password_hash: Password hash
    
    Returns:
        Boolean indicating whether the password matches
    """
    return check_password_hash(password_hash, password)

