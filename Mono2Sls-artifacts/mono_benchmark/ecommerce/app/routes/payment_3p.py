"""
Third-party Payment Routes
Simulates third-party payment system API endpoints
"""
from flask import Blueprint, request, jsonify
from app.services import payment_service

bp = Blueprint('payment_3p', __name__, url_prefix='/api/payment-3p')


@bp.route('/preauth', methods=['POST'])
def preauth():
    """
    Pre-authorize payment
    
    Request Body:
        {
            "cardNumber": "1234567890123456",
            "amount": 10000
        }
    
    Returns:
        {
            "paymentToken": "uuid"
        }
    """
    data = request.get_json()
    
    # Validate request body
    if not data:
        return jsonify({'message': 'Missing body in event'}), 400
    
    if 'cardNumber' not in data:
        return jsonify({'message': "Missing 'cardNumber' in request body"}), 400
    
    if not isinstance(data['cardNumber'], str):
        return jsonify({'message': "'cardNumber' is not a string"}), 400
    
    if len(data['cardNumber']) != 16:
        return jsonify({'message': "'cardNumber' is not 16 characters long"}), 400
    
    if 'amount' not in data:
        return jsonify({'message': "Missing 'amount' in request body"}), 400
    
    if not isinstance(data['amount'], int):
        return jsonify({'message': "'amount' is not a number"}), 400
    
    if data['amount'] < 0:
        return jsonify({'message': "'amount' should be a positive number"}), 400
    
    # Process pre-authorization
    success, message, payment_token = payment_service.preauth_payment(
        data['cardNumber'],
        data['amount']
    )
    
    if success:
        return jsonify({'paymentToken': payment_token}), 200
    else:
        return jsonify({'message': message}), 500


@bp.route('/check', methods=['POST'])
def check():
    """
    Check payment token
    
    Request Body:
        {
            "paymentToken": "uuid",
            "amount": 10000
        }
    
    Returns:
        {
            "ok": true
        }
    """
    data = request.get_json()
    
    # Validate request body
    if not data:
        return jsonify({'message': 'Missing body in event'}), 400
    
    if 'paymentToken' not in data:
        return jsonify({'message': "Missing 'paymentToken' in request body"}), 400
    
    if not isinstance(data['paymentToken'], str):
        return jsonify({'message': "'paymentToken' is not a string"}), 400
    
    if 'amount' not in data:
        return jsonify({'message': "Missing 'amount' in request body"}), 400
    
    if not isinstance(data['amount'], int):
        return jsonify({'message': "'amount' is not a number"}), 400
    
    if data['amount'] < 0:
        return jsonify({'message': "'amount' should be a positive number"}), 400
    
    # Check token
    valid, message = payment_service.check_payment(data['paymentToken'], data['amount'])
    
    if valid:
        return jsonify({'ok': True}), 200
    else:
        return jsonify({'ok': False, 'message': message}), 200


@bp.route('/processPayment', methods=['POST'])
def process_payment():
    """
    Process payment
    
    Request Body:
        {
            "paymentToken": "uuid"
        }
    
    Returns:
        {
            "ok": true
        }
    """
    data = request.get_json()
    
    # Validate request body
    if not data:
        return jsonify({'message': 'Missing body in event'}), 400
    
    if 'paymentToken' not in data:
        return jsonify({'message': "Missing 'paymentToken' in request body"}), 400
    
    if not isinstance(data['paymentToken'], str):
        return jsonify({'message': "'paymentToken' is not a string"}), 400
    
    # Process payment
    success, message = payment_service.process_payment(data['paymentToken'])
    
    if success:
        return jsonify({'ok': True}), 200
    else:
        return jsonify({'ok': False, 'message': message}), 500


@bp.route('/cancelPayment', methods=['POST'])
def cancel_payment():
    """
    Cancel payment
    
    Request Body:
        {
            "paymentToken": "uuid"
        }
    
    Returns:
        {
            "ok": true
        }
    """
    data = request.get_json()
    
    # Validate request body
    if not data:
        return jsonify({'message': 'Missing body in event'}), 400
    
    if 'paymentToken' not in data:
        return jsonify({'message': "Missing 'paymentToken' in request body"}), 400
    
    if not isinstance(data['paymentToken'], str):
        return jsonify({'message': "'paymentToken' is not a string"}), 400
    
    # Cancel payment
    success, message = payment_service.cancel_payment(data['paymentToken'])
    
    if success:
        return jsonify({'ok': True}), 200
    else:
        return jsonify({'ok': False, 'message': message}), 500


@bp.route('/updateAmount', methods=['POST'])
def update_amount():
    """
    Update payment amount
    
    Request Body:
        {
            "paymentToken": "uuid",
            "amount": 8000
        }
    
    Returns:
        {
            "ok": true
        }
    """
    data = request.get_json()
    
    # Validate request body
    if not data:
        return jsonify({'message': 'Missing body in event'}), 400
    
    if 'paymentToken' not in data:
        return jsonify({'message': "Missing 'paymentToken' in request body"}), 400
    
    if not isinstance(data['paymentToken'], str):
        return jsonify({'message': "'paymentToken' is not a string"}), 400
    
    if 'amount' not in data:
        return jsonify({'message': "Missing 'amount' in request body"}), 400
    
    if not isinstance(data['amount'], int):
        return jsonify({'message': "'amount' is not a number"}), 400
    
    if data['amount'] < 0:
        return jsonify({'message': "'amount' should be a positive number"}), 400
    
    # Update amount
    success, message = payment_service.update_payment_amount(
        data['paymentToken'],
        data['amount']
    )
    
    if success:
        return jsonify({'ok': True}), 200
    else:
        return jsonify({'ok': False, 'message': message}), 400
