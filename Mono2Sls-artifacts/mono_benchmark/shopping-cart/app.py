"""
Shopping Cart Monolith - Main application
Provides RESTful APIs for product browsing and cart management.
"""
import os
from datetime import datetime, timedelta
from functools import wraps
from uuid import uuid4

from flask import Flask, request, jsonify, make_response, send_from_directory
from flask_cors import CORS

from db import close_connection
from init_dynamodb import init_dynamodb
from auth import create_token, verify_token, hash_password, verify_password
from models import (
    get_cart_items,
    add_cart_item,
    update_cart_item_quantity,
    delete_cart_items,
    get_all_products,
    get_product_by_id,
    get_product_total_quantity,
    update_product_total_quantity,
    migrate_cart_items,
    create_user,
    get_user_by_username
)

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
CORS(app, supports_credentials=True)

# Initialize DynamoDB
with app.app_context():
    init_dynamodb()

# Register teardown hook to clean up resources
app.teardown_appcontext(close_connection)


def get_cart_id_from_cookie():
    """Get cart ID from cookie; generate a new one if missing."""
    cart_id = request.cookies.get('cartId')
    generated = False
    if not cart_id:
        cart_id = str(uuid4())
        generated = True
    return cart_id, generated


def set_cart_cookie(response, cart_id):
    """Set cart cookie."""
    response.set_cookie(
        'cartId',
        cart_id,
        max_age=60 * 60 * 24,  # 1 day
        secure=False,  # False in dev; should be True in production
        httponly=True,
        samesite='Lax',
        path='/'
    )
    return response


def login_required(f):
    """Auth decorator - requires the user to be logged in."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            return jsonify({'message': 'Missing authorization header'}), 401
        
        try:
            # Support "Bearer <token>" or raw "<token>"
            token = auth_header.replace('Bearer ', '')
            payload = verify_token(token)
            request.user_id = payload['sub']
            request.username = payload['username']
        except Exception as e:
            return jsonify({'message': 'Invalid or expired token'}), 401
        
        return f(*args, **kwargs)
    
    return decorated_function


def get_user_identifier():
    """Get user identifier (user_id for authenticated users or cart_id for anonymous users)."""
    auth_header = request.headers.get('Authorization')
    if auth_header:
        try:
            token = auth_header.replace('Bearer ', '')
            payload = verify_token(token)
            return f"user#{payload['sub']}", True
        except:
            pass
    
    cart_id, _ = get_cart_id_from_cookie()
    return f"cart#{cart_id}", False


# ==================== User Authentication APIs ====================

@app.route('/auth/register', methods=['POST'])
def register():
    """User registration."""
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    email = data.get('email')
    
    if not username or not password:
        return jsonify({'message': 'Username and password required'}), 400
    
    # Check if the user already exists
    if get_user_by_username(username):
        return jsonify({'message': 'Username already exists'}), 400
    
    # Create user
    user_id = create_user(username, password, email)
    
    return jsonify({
        'message': 'User registered successfully',
        'userId': user_id
    }), 201


@app.route('/auth/login', methods=['POST'])
def login():
    """User login."""
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'message': 'Username and password required'}), 400
    
    # Validate user
    user = get_user_by_username(username)
    if not user or not verify_password(password, user['password_hash']):
        return jsonify({'message': 'Invalid credentials'}), 401
    
    # Generate JWT token
    token = create_token(user['id'], username)
    
    return jsonify({
        'message': 'Login successful',
        'token': token,
        'userId': user['id'],
        'username': username
    }), 200


# ==================== Product APIs ====================

@app.route('/product', methods=['GET'])
def get_products():
    """Get all products."""
    return jsonify({'products': get_all_products()}), 200


@app.route('/product/<product_id>', methods=['GET'])
def get_product(product_id):
    """Get product details by product_id."""
    product = get_product_by_id(product_id)
    
    if not product:
        return jsonify({'message': 'Product not found'}), 404
    
    return jsonify({'product': product}), 200


# ==================== Cart APIs ====================

@app.route('/cart', methods=['GET'])
def list_cart():
    """Get the current user's cart contents."""
    cart_id, generated = get_cart_id_from_cookie()
    pk, is_authenticated = get_user_identifier()
    
    # If cart_id was just generated for an anonymous user, skip DB query
    if generated and not is_authenticated:
        product_list = []
    else:
        product_list = get_cart_items(pk)
    
    # Normalize response data
    for product in product_list:
        if 'sk' in product:
            product['sk'] = product['sk'].replace('product#', '')
    
    response = make_response(jsonify({'products': product_list}))
    response = set_cart_cookie(response, cart_id)
    return response, 200


@app.route('/cart', methods=['POST'])
def add_to_cart():
    """Add a product to the cart."""
    data = request.get_json()
    if not data:
        return jsonify({'message': 'No request payload'}), 400
    
    product_id = data.get('productId')
    quantity = data.get('quantity', 1)
    
    if not product_id:
        return jsonify({'message': 'Product ID required'}), 400
    
    # Validate that the product exists
    product = get_product_by_id(product_id)
    if not product:
        return jsonify({'message': 'Product not found'}), 404
    
    cart_id, _ = get_cart_id_from_cookie()
    pk, is_authenticated = get_user_identifier()
    
    # Set expiration time
    if is_authenticated:
        ttl = datetime.now() + timedelta(days=7)
    else:
        ttl = datetime.now() + timedelta(days=1)
    
    # Add or update cart item
    try:
        add_cart_item(pk, product_id, quantity, product, ttl)
        # Update product aggregate counters
        update_product_total_quantity(product_id, quantity)
    except Exception as e:
        return jsonify({'message': f'Failed to add to cart: {str(e)}'}), 500
    
    response = make_response(jsonify({
        'productId': product_id,
        'message': 'product added to cart'
    }))
    response = set_cart_cookie(response, cart_id)
    return response, 200


@app.route('/cart/<product_id>', methods=['PUT'])
def update_cart(product_id):
    """Update quantity for a product in the cart (idempotent)."""
    data = request.get_json()
    if not data:
        return jsonify({'message': 'No request payload'}), 400
    
    quantity = int(data.get('quantity', 0))
    
    if quantity < 0:
        return jsonify({
            'productId': product_id,
            'message': 'Quantity must not be lower than 0'
        }), 400
    
    # Validate that the product exists
    product = get_product_by_id(product_id)
    if not product:
        return jsonify({'message': 'Product not found'}), 404
    
    cart_id, _ = get_cart_id_from_cookie()
    pk, is_authenticated = get_user_identifier()
    
    # Set expiration time
    if is_authenticated:
        ttl = datetime.now() + timedelta(days=7)
    else:
        ttl = datetime.now() + timedelta(days=1)
    
    try:
        # Get old quantity to compute delta
        old_quantity = 0
        items = get_cart_items(pk)
        for item in items:
            if item['sk'] == f"product#{product_id}":
                old_quantity = item['quantity']
                break
        
        # Update cart item
        update_cart_item_quantity(pk, product_id, quantity, product, ttl)
        
        # Update product aggregate counters (delta)
        quantity_diff = quantity - old_quantity
        if quantity_diff != 0:
            update_product_total_quantity(product_id, quantity_diff)
    except Exception as e:
        return jsonify({'message': f'Failed to update cart: {str(e)}'}), 500
    
    response = make_response(jsonify({
        'productId': product_id,
        'quantity': quantity,
        'message': 'cart updated'
    }))
    response = set_cart_cookie(response, cart_id)
    return response, 200


@app.route('/cart/migrate', methods=['POST'])
@login_required
def migrate_cart():
    """Migrate an anonymous cart to the authenticated user account."""
    cart_id, _ = get_cart_id_from_cookie()
    user_id = request.user_id
    
    anonymous_pk = f"cart#{cart_id}"
    user_pk = f"user#{user_id}"
    
    try:
        # Migrate cart items
        migrated_items = migrate_cart_items(anonymous_pk, user_pk)
    except Exception as e:
        return jsonify({'message': f'Failed to migrate cart: {str(e)}'}), 500
    
    # Fetch migrated cart
    product_list = get_cart_items(user_pk)
    
    # Normalize response data
    for product in product_list:
        if 'sk' in product:
            product['sk'] = product['sk'].replace('product#', '')
    
    response = make_response(jsonify({'products': product_list}))
    response = set_cart_cookie(response, cart_id)
    return response, 200


@app.route('/cart/checkout', methods=['POST'])
@login_required
def checkout_cart():
    """Checkout the cart (clears the cart)."""
    cart_id, _ = get_cart_id_from_cookie()
    user_id = request.user_id
    user_pk = f"user#{user_id}"
    
    # Fetch cart items
    cart_items = get_cart_items(user_pk)
    
    try:
        # Before clearing, update product aggregate counters (decrease quantities)
        for item in cart_items:
            product_id = item['sk'].replace('product#', '')
            quantity = -item['quantity']  # Negative means decrement
            update_product_total_quantity(product_id, quantity)
        
        # Delete cart items
        delete_cart_items(user_pk)
    except Exception as e:
        return jsonify({'message': f'Failed to checkout: {str(e)}'}), 500
    
    response = make_response(jsonify({
        'products': cart_items,
        'message': 'Checkout successful'
    }))
    response = set_cart_cookie(response, cart_id)
    return response, 200


@app.route('/cart/<product_id>/total', methods=['GET'])
def get_cart_total(product_id):
    """Get total quantity of a product across all carts."""
    try:
        total = get_product_total_quantity(product_id)
    except Exception as e:
        return jsonify({'message': f'Failed to get total: {str(e)}'}), 500
    
    return jsonify({
        'product': product_id,
        'quantity': total
    }), 200


# ==================== Error Handling ====================

@app.errorhandler(500)
def internal_error(error):
    """500 error handler."""
    return jsonify({'message': 'Internal server error'}), 500


# ==================== Application Entry Point ====================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('DEBUG', 'True').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)

