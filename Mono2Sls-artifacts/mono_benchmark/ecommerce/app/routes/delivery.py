"""
Delivery Routes
Handles delivery related API endpoints
"""
from flask import Blueprint, request, jsonify
from app.utils.decorators import delivery_required
from app.services import delivery_service

bp = Blueprint('delivery', __name__, url_prefix='/api/delivery')


@bp.route('/deliveries', methods=['GET'])
@delivery_required
def get_new_deliveries():
    """
    Get list of new delivery requests
    
    Query Parameters:
        limit: Limit on number of returned items (default 50)
    
    Returns:
        {
            "deliveries": [...]
        }
    """
    limit = request.args.get('limit', 50, type=int)
    deliveries = delivery_service.get_new_deliveries(limit=limit)
    
    return jsonify({'deliveries': deliveries}), 200


@bp.route('/deliveries/<order_id>', methods=['GET'])
@delivery_required
def get_delivery(order_id):
    """
    Get delivery details
    
    Returns:
        {
            "delivery": {...}
        }
    """
    delivery = delivery_service.get_delivery(order_id)
    
    if not delivery:
        return jsonify({'message': 'Delivery not found'}), 404
    
    return jsonify({'delivery': delivery}), 200


@bp.route('/deliveries/<order_id>/start', methods=['POST'])
@delivery_required
def start_delivery(order_id):
    """
    Start delivery
    
    Returns:
        {
            "success": true,
            "message": "Delivery started"
        }
    """
    success, message = delivery_service.start_delivery(order_id)
    
    return jsonify({'success': success, 'message': message}), 200 if success else 400


@bp.route('/deliveries/<order_id>/complete', methods=['POST'])
@delivery_required
def complete_delivery(order_id):
    """
    Complete delivery
    
    Returns:
        {
            "success": true,
            "message": "Delivery completed"
        }
    """
    success, message = delivery_service.complete_delivery(order_id)
    
    return jsonify({'success': success, 'message': message}), 200 if success else 400


@bp.route('/deliveries/<order_id>/fail', methods=['POST'])
@delivery_required
def fail_delivery(order_id):
    """
    Mark delivery as failed
    
    Request Body:
        {
            "reason": "Wrong address"  // Optional
        }
    
    Returns:
        {
            "success": true,
            "message": "Delivery marked as failed and refund initiated"
        }
    """
    data = request.get_json() or {}
    reason = data.get('reason', 'Delivery failed')
    
    success, message = delivery_service.fail_delivery(order_id, reason)
    
    return jsonify({'success': success, 'message': message}), 200 if success else 400
