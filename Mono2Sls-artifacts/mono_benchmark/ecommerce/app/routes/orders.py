"""
Order Routes
Handles order related API endpoints
"""
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.services import order_service
from app.services.delivery_pricing import calculate_delivery_price

bp = Blueprint('orders', __name__, url_prefix='/api/orders')


@bp.route('', methods=['POST'])
@jwt_required()
def create_order():
    """
    Create order
    
    Request Body:
        {
            "products": [...],
            "address": {...},
            "deliveryPrice": 1000,
            "paymentToken": "uuid"
        }
    
    Returns:
        {
            "success": true,
            "message": "Order created",
            "order": {...}
        }
    """
    user_id = get_jwt_identity()
    data = request.get_json()
    
    if not data:
        return jsonify({'success': False, 'message': 'Missing request body'}), 400
    
    success, message, result = order_service.create_order(user_id, data)
    
    if success:
        return jsonify({
            'success': True,
            'message': message,
            'order': result
        }), 201
    else:
        return jsonify({
            'success': False,
            'message': message,
            **result
        }), 400


@bp.route('', methods=['GET'])
@jwt_required()
def get_orders():
    """
    Get current user's order list
    
    Query Parameters:
        limit: Limit on number of returned items (default 50)
    
    Returns:
        {
            "orders": [...]
        }
    """
    user_id = get_jwt_identity()
    limit = request.args.get('limit', 50, type=int)
    
    orders = order_service.get_user_orders(user_id, limit=limit)
    
    return jsonify({'orders': orders}), 200


@bp.route('/<order_id>', methods=['GET'])
@jwt_required()
def get_order(order_id):
    """
    Get order details
    
    Returns:
        {
            "order": {...}
        }
    """
    user_id = get_jwt_identity()
    order = order_service.get_order(order_id)
    
    if not order:
        return jsonify({'message': 'Order not found'}), 404
    
    # Validate order ownership
    if order['userId'] != user_id:
        return jsonify({'message': 'Access denied'}), 403
    
    return jsonify({'order': order}), 200


@bp.route('/<order_id>', methods=['PUT'])
@jwt_required()
def update_order(order_id):
    """
    Update order (only in NEW status)
    
    Request Body:
        {
            "products": [...],  # Optional
            "deliveryPrice": 1000  # Optional
        }
    
    Returns:
        {
            "success": true,
            "message": "Order updated",
            "order": {...}
        }
    """
    user_id = get_jwt_identity()
    
    # Validate order ownership first
    order = order_service.get_order(order_id)
    if not order:
        return jsonify({'success': False, 'message': 'Order not found'}), 404
    
    if order['userId'] != user_id:
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': 'Missing request body'}), 400
    
    success, message, result = order_service.update_order(order_id, data)
    
    if success:
        return jsonify({
            'success': True,
            'message': message,
            'order': result
        }), 200
    else:
        return jsonify({
            'success': False,
            'message': message,
            **result
        }), 400


@bp.route('/<order_id>', methods=['DELETE'])
@jwt_required()
def delete_order(order_id):
    """
    Delete order (only in NEW status)
    
    Returns:
        {
            "success": true,
            "message": "Order deleted"
        }
    """
    user_id = get_jwt_identity()
    
    # Validate order ownership first
    order = order_service.get_order(order_id)
    if not order:
        return jsonify({'success': False, 'message': 'Order not found'}), 404
    
    if order['userId'] != user_id:
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    success, message = order_service.delete_order(order_id)
    
    if success:
        return jsonify({
            'success': True,
            'message': message
        }), 200
    else:
        return jsonify({
            'success': False,
            'message': message
        }), 400


@bp.route('/delivery-pricing', methods=['POST'])
@jwt_required()
def get_delivery_pricing():
    """
    Calculate delivery price
    
    Request Body:
        {
            "products": [...],
            "address": {...}
        }
    
    Returns:
        {
            "pricing": 1000
        }
    """
    data = request.get_json()
    
    if not data or 'products' not in data or 'address' not in data:
        return jsonify({'message': 'Missing products or address'}), 400
    
    pricing = calculate_delivery_price(data['products'], data['address'])
    
    return jsonify({'pricing': pricing}), 200
