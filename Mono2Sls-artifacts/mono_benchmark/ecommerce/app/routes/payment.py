"""
Payment Routes
Handles payment validation related API endpoints
"""
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from app.services import payment_service

bp = Blueprint('payment', __name__, url_prefix='/api/payment')


@bp.route('/validate', methods=['POST'])
@jwt_required()
def validate_payment():
    """
    Validate payment token
    
    Request Body:
        {
            "paymentToken": "uuid",
            "total": 10000
        }
    
    Returns:
        {
            "ok": true
        }
    """
    data = request.get_json()
    
    if not data or 'paymentToken' not in data or 'total' not in data:
        return jsonify({'message': 'Missing paymentToken or total'}), 400
    
    valid = payment_service.validate_payment(data['paymentToken'], data['total'])
    
    return jsonify({'ok': valid}), 200
