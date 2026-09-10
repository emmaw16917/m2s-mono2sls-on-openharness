"""
Decorators
Used for permission validation, etc.
"""
from functools import wraps
from flask import jsonify
from flask_jwt_extended import get_jwt, verify_jwt_in_request


def admin_required(fn):
    """
    Admin permission decorator
    Ensures user is an admin to access
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        claims = get_jwt()
        if claims.get('role') != 'admin':
            return jsonify({'message': 'Admin access required'}), 403
        return fn(*args, **kwargs)
    return wrapper


def warehouse_required(fn):
    """
    Warehouse personnel permission decorator
    Ensures user is warehouse staff or admin to access
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        claims = get_jwt()
        if claims.get('role') not in ['admin', 'warehouse']:
            return jsonify({'message': 'Warehouse access required'}), 403
        return fn(*args, **kwargs)
    return wrapper


def delivery_required(fn):
    """
    Delivery personnel permission decorator
    Ensures user is delivery staff or admin to access
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        claims = get_jwt()
        if claims.get('role') not in ['admin', 'delivery']:
            return jsonify({'message': 'Delivery access required'}), 403
        return fn(*args, **kwargs)
    return wrapper
