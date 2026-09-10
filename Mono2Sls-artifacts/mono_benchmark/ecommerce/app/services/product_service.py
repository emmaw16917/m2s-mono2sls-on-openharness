"""
Product Service
Handles product-related business logic
"""
from typing import Dict, List, Optional
from app.models import Product


def get_products(limit: int = 100) -> List[Dict]:
    """
    Get list of products
    
    Args:
        limit: Limit on number of returned items
    
    Returns:
        List of products
    """
    products = Product.get_all(limit=limit)
    return [product.to_dict() for product in products]


def get_product(product_id: str) -> Optional[Dict]:
    """
    Get single product
    
    Args:
        product_id: Product ID
    
    Returns:
        Product dictionary or None
    """
    product = Product.get_by_id(product_id)
    return product.to_dict() if product else None


def get_products_by_category(category: str, limit: int = 100) -> List[Dict]:
    """
    Get products by category
    
    Args:
        category: Product category
        limit: Limit on number of returned items
    
    Returns:
        List of products
    """
    products = Product.get_by_category(category, limit=limit)
    return [product.to_dict() for product in products]
