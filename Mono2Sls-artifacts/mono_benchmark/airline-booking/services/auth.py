"""
Authentication Service
JWT-based authentication for user management and authorization
"""
import jwt
import datetime
import secrets
import os
import hashlib
from typing import Optional
from functools import wraps
from flask import request, jsonify
from data.storage import storage


class AuthService:
    """Service for JWT authentication and authorization"""
    
    SECRET_KEY = os.environ.get('JWT_SECRET_KEY', secrets.token_urlsafe(32))
    ALGORITHM = 'HS256'
    ACCESS_TOKEN_EXPIRE_MINUTES = 60
    
    @staticmethod
    def _hash_password(password: str) -> str:
        """Hash password using SHA-256"""
        return hashlib.sha256(password.encode()).hexdigest()
    
    @staticmethod
    def _init_default_users():
        """Initialize some default users for testing"""
        try:
            existing = storage.get_all_users()
            if existing:
                return
            
            AuthService.register_user('user@example.com', 'password123', 'user123')
            AuthService.register_user('admin@example.com', 'admin123', 'admin123')
            AuthService.add_to_admin_group('admin123')
        except Exception as e:
            print(f"Warning: Could not initialize default users: {str(e)}")
    
    @staticmethod
    def register_user(email: str, password: str, user_id: Optional[str] = None) -> dict:
        """
        Register a new user.
        
        Raises ValueError if user already exists.
        """
        existing = storage.get_user_by_email(email)
        if existing:
            raise ValueError(f"User with email {email} already exists")
        
        if user_id is None:
            user_id = secrets.token_urlsafe(16)
        
        user = {
            'sub': user_id,
            'email': email,
            'password_hash': AuthService._hash_password(password),
            'groups': [],
            'created_at': datetime.datetime.utcnow().isoformat()
        }
        
        storage.create_user(user)
        
        return {
            'sub': user['sub'],
            'email': user['email'],
            'message': 'User registered successfully'
        }
    
    @staticmethod
    def authenticate_user(email: str, password: str) -> Optional[dict]:
        """Authenticate user credentials. Returns user dict if valid, None otherwise."""
        user = storage.get_user_by_email(email)
        if not user:
            return None
        
        if user.get('password_hash') == AuthService._hash_password(password):
            return user
        
        return None
    
    @staticmethod
    def create_access_token(user: dict) -> str:
        """Create JWT access token"""
        expire = datetime.datetime.utcnow() + datetime.timedelta(
            minutes=AuthService.ACCESS_TOKEN_EXPIRE_MINUTES
        )
        
        payload = {
            'sub': user['sub'],
            'email': user['email'],
            'groups': user.get('groups', []),
            'exp': expire,
            'iat': datetime.datetime.utcnow(),
            'token_use': 'id'
        }
        
        return jwt.encode(payload, AuthService.SECRET_KEY, algorithm=AuthService.ALGORITHM)
    
    @staticmethod
    def decode_token(token: str) -> Optional[dict]:
        """Decode and validate JWT token. Returns payload or None."""
        try:
            return jwt.decode(
                token,
                AuthService.SECRET_KEY,
                algorithms=[AuthService.ALGORITHM]
            )
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
            return None
    
    @staticmethod
    def get_user_from_token(token: str) -> Optional[dict]:
        """Get user record from database using token's sub claim."""
        payload = AuthService.decode_token(token)
        if not payload:
            return None
        return storage.get_user(payload['sub'])
    
    @staticmethod
    def add_to_admin_group(user_id: str):
        """Add user to Admin group"""
        user = storage.get_user(user_id)
        if not user:
            return
        
        groups = user.get('groups', [])
        if 'Admin' not in groups:
            groups.append('Admin')
            storage.update_user_groups(user_id, groups)
    
    @staticmethod
    def is_admin(user: dict) -> bool:
        """Check if user is in Admin group"""
        return 'Admin' in user.get('groups', [])
    
    @staticmethod
    def get_all_users() -> list:
        """Get all registered users (returns sanitized list without password hashes)"""
        users = storage.get_all_users()
        return [
            {
                'sub': user['sub'],
                'email': user['email'],
                'groups': user.get('groups', [])
            }
            for user in users
        ]


# --- Shared auth helper ---

def _get_authenticated_user():
    """
    Extract and validate JWT from the Authorization header.
    Returns (user_dict, None) on success, or (None, error_response_tuple) on failure.
    """
    if 'Authorization' not in request.headers:
        return None, (jsonify({'error': 'Authentication required. Please provide a valid token.'}), 401)
    
    auth_header = request.headers['Authorization']
    try:
        token = auth_header.split(' ')[1]
    except IndexError:
        return None, (jsonify({'error': 'Invalid token format. Use: Bearer <token>'}), 401)
    
    current_user = AuthService.get_user_from_token(token)
    if not current_user:
        return None, (jsonify({'error': 'Invalid or expired token'}), 401)
    
    return current_user, None


# --- Route decorators ---

def login_required(f):
    """Decorator to require authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        current_user, error = _get_authenticated_user()
        if error:
            return error
        request.current_user = current_user
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Decorator to require Admin group membership"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        current_user, error = _get_authenticated_user()
        if error:
            return error
        if not AuthService.is_admin(current_user):
            return jsonify({'error': 'Admin privileges required'}), 403
        request.current_user = current_user
        return f(*args, **kwargs)
    return decorated_function


def owner_or_admin_required(f):
    """Decorator to require ownership (matching customer_id URL param) or Admin group"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        current_user, error = _get_authenticated_user()
        if error:
            return error
        
        customer_id = kwargs.get('customer_id')
        is_owner = current_user['sub'] == customer_id
        
        if not (AuthService.is_admin(current_user) or is_owner):
            return jsonify({'error': 'Access denied. You can only access your own resources.'}), 403
        
        request.current_user = current_user
        return f(*args, **kwargs)
    return decorated_function


def booking_owner_or_admin_required(f):
    """Decorator to require booking ownership or Admin group"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        current_user, error = _get_authenticated_user()
        if error:
            return error
        
        booking_id = kwargs.get('booking_id')
        if booking_id:
            booking = storage.get_booking(booking_id)
            if not booking:
                return jsonify({'error': 'Booking not found'}), 404
            
            is_owner = current_user['sub'] == booking['customer']
            if not (AuthService.is_admin(current_user) or is_owner):
                return jsonify({'error': 'Access denied. You can only access your own bookings.'}), 403
        
        request.current_user = current_user
        return f(*args, **kwargs)
    return decorated_function


# Initialize default users when module is imported
AuthService._init_default_users()
