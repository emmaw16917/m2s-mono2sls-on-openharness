"""
Warehouse Routes
Handles packaging request related API endpoints
"""
from flask import Blueprint, request, jsonify
from app.utils.decorators import warehouse_required
from app.services import warehouse_service

bp = Blueprint('warehouse', __name__, url_prefix='/api/warehouse')


@bp.route('/packaging-requests', methods=['GET'])
@warehouse_required
def get_new_packaging_requests():
    """
    Get list of new packaging request IDs
    
    Query Parameters:
        limit: Limit on number of returned items (default 50)
    
    Returns:
        {
            "packagingRequestIds": [...]
        }
    """
    limit = request.args.get('limit', 50, type=int)
    request_ids = warehouse_service.get_new_packaging_requests(limit=limit)
    
    return jsonify({'packagingRequestIds': request_ids}), 200


@bp.route('/packaging-requests/<order_id>', methods=['GET'])
@warehouse_required
def get_packaging_request(order_id):
    """
    Get packaging request details
    
    Returns:
        {
            "packagingRequest": {...}
        }
    """
    request_data = warehouse_service.get_packaging_request(order_id)
    
    if not request_data:
        return jsonify({'message': 'Packaging request not found'}), 404
    
    return jsonify({'packagingRequest': request_data}), 200


@bp.route('/packaging-requests/<order_id>/start', methods=['POST'])
@warehouse_required
def start_packaging(order_id):
    """
    Start packaging
    
    Returns:
        {
            "success": true,
            "message": "Packaging started"
        }
    """
    success, message = warehouse_service.start_packaging(order_id)
    
    return jsonify({'success': success, 'message': message}), 200 if success else 400


@bp.route('/packaging-requests/<order_id>/products', methods=['PUT'])
@warehouse_required
def update_packaging_product(order_id):
    """
    Update packaging product quantity
    
    Request Body:
        {
            "productId": "uuid",
            "quantity": 5
        }
    
    Returns:
        {
            "success": true,
            "message": "Product quantity updated"
        }
    """
    data = request.get_json()
    
    if not data or 'productId' not in data or 'quantity' not in data:
        return jsonify({'success': False, 'message': 'Missing productId or quantity'}), 400
    
    success, message = warehouse_service.update_packaging_product(
        order_id,
        data['productId'],
        data['quantity']
    )
    
    return jsonify({'success': success, 'message': message}), 200 if success else 400


@bp.route('/packaging-requests/<order_id>/complete', methods=['POST'])
@warehouse_required
def complete_packaging(order_id):
    """
    Complete packaging
    
    Returns:
        {
            "success": true,
            "message": "Packaging completed"
        }
    """
    success, message = warehouse_service.complete_packaging(order_id)
    
    return jsonify({'success': success, 'message': message}), 200 if success else 400


@bp.route('/packaging-requests/<order_id>/fail', methods=['POST'])
@warehouse_required
def fail_packaging(order_id):
    """
    Mark packaging as failed
    
    Request Body:
        {
            "reason": "Out of stock"  // Optional
        }
    
    Returns:
        {
            "success": true,
            "message": "Packaging marked as failed and refund initiated"
        }
    """
    data = request.get_json() or {}
    reason = data.get('reason', 'Packaging failed')
    
    success, message = warehouse_service.fail_packaging(order_id, reason)
    
    return jsonify({'success': success, 'message': message}), 200 if success else 400
