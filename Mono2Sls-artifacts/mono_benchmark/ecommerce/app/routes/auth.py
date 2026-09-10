"""
Authentication Routes
Handles user registration, login, and JWT token management
"""
import uuid
from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, create_refresh_token, jwt_required, get_jwt_identity
from app.models import User
from app.utils.validators import validate_email

bp = Blueprint('auth', __name__, url_prefix='/api/auth')


@bp.route('/register', methods=['POST'])
def register():
    """
    User registration
    
    Request Body:
        {
            "email": "user@example.com",
            "password": "password123",
            "role": "user"  # Optional, default is user
        }
    
    Returns:
        {
            "success": true,
            "message": "User registered successfully",
            "userId": "uuid"
        }
    """
    data = request.get_json()
    
    # Validate required fields
    if not data or 'email' not in data or 'password' not in data:
        return jsonify({'success': False, 'message': 'Missing email or password'}), 400
    
    email = data['email']
    password = data['password']
    role = data.get('role', 'user')
    
    # Validate email format
    if not validate_email(email):
        return jsonify({'success': False, 'message': 'Invalid email format'}), 400
    
    # Check if user already exists
    if User.get_by_email(email):
        return jsonify({'success': False, 'message': 'Email already registered'}), 400
    
    # Create new user
    user = User(
        user_id=str(uuid.uuid4()),
        email=email,
        role=role
    )
    user.set_password(password)
    
    try:
        user.save()
        
        return jsonify({
            'success': True,
            'message': 'User registered successfully',
            'userId': user.user_id
        }), 201
    except Exception as e:
        return jsonify({'success': False, 'message': f'Registration failed: {str(e)}'}), 500


@bp.route('/login', methods=['POST'])
def login():
    """
    User login
    
    Request Body:
        {
            "email": "user@example.com",
            "password": "password123"
        }
    
    Returns:
        {
            "success": true,
            "accessToken": "jwt_token",
            "refreshToken": "refresh_token",
            "user": {...}
        }
    """
    data = request.get_json()
    
    # Validate required fields
    if not data or 'email' not in data or 'password' not in data:
        return jsonify({'success': False, 'message': 'Missing email or password'}), 400
    
    email = data['email']
    password = data['password']
    
    # Find user
    user = User.get_by_email(email)
    
    # Validate password
    if not user or not user.check_password(password):
        return jsonify({'success': False, 'message': 'Invalid email or password'}), 401
    
    # Create JWT tokens
    additional_claims = {
        'role': user.role,
        'email': user.email
    }
    access_token = create_access_token(identity=user.user_id, additional_claims=additional_claims)
    refresh_token = create_refresh_token(identity=user.user_id, additional_claims=additional_claims)
    
    return jsonify({
        'success': True,
        'accessToken': access_token,
        'refreshToken': refresh_token,
        'user': user.to_dict()
    }), 200


@bp.route('/refresh', methods=['POST'])
@jwt_required(refresh=True)
def refresh():
    """
    Refresh access token
    
    Returns:
        {
            "accessToken": "new_jwt_token"
        }
    """
    identity = get_jwt_identity()
    user = User.get_by_id(identity)
    
    if not user:
        return jsonify({'message': 'User not found'}), 404
    
    additional_claims = {
        'role': user.role,
        'email': user.email
    }
    access_token = create_access_token(identity=identity, additional_claims=additional_claims)
    
    return jsonify({'accessToken': access_token}), 200


@bp.route('/me', methods=['GET'])
@jwt_required()
def get_current_user():
    """
    Get current logged-in user info
    
    Returns:
        {
            "user": {...}
        }
    """
    user_id = get_jwt_identity()
    user = User.get_by_id(user_id)
    
    if not user:
        return jsonify({'message': 'User not found'}), 404
    
    return jsonify({'user': user.to_dict()}), 200
