"""
Product Routes
Handles product related API endpoints
"""
from flask import Blueprint, request, jsonify
from app.services import product_service

bp = Blueprint('products', __name__, url_prefix='/api/products')


@bp.route('', methods=['GET'])
def get_products():
    """
    Get product list
    
    Query Parameters:
        limit: Limit on number of returned items (default 100)
    
    Returns:
        {
            "products": [...]
        }
    """
    limit = request.args.get('limit', 100, type=int)
    products = product_service.get_products(limit=limit)
    
    return jsonify({'products': products}), 200


@bp.route('/<product_id>', methods=['GET'])
def get_product(product_id):
    """
    Get single product
    
    Returns:
        {
            "product": {...}
        }
    """
    product = product_service.get_product(product_id)
    
    if not product:
        return jsonify({'message': 'Product not found'}), 404
    
    return jsonify({'product': product}), 200


@bp.route('/category/<category>', methods=['GET'])
def get_products_by_category(category):
    """
    Get products by category
    
    Query Parameters:
        limit: Limit on number of returned items (default 100)
    
    Returns:
        {
            "products": [...]
        }
    """
    limit = request.args.get('limit', 100, type=int)
    products = product_service.get_products_by_category(category, limit=limit)
    
    return jsonify({'products': products}), 200
