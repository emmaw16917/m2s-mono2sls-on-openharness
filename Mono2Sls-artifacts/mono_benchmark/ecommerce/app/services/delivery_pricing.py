"""
Delivery Pricing Service
Calculates delivery fees
"""
import math
from decimal import Decimal
from typing import List, Dict


# 50*50*50 cm cube
BOX_VOLUME = 500 * 500 * 500
# 12kg per box
BOX_WEIGHT = 12000

# Country shipping fees (in cents)
COUNTRY_SHIPPING_FEES = {
    # Nordic countries
    "DK": 0, "FI": 0, "NO": 0, "SE": 0,
    
    # Other EU countries
    "AT": 1000, "BE": 1000, "BG": 1000, "CY": 1000,
    "CZ": 1000, "DE": 1000, "EE": 1000, "ES": 1000,
    "FR": 1000, "GR": 1000, "HR": 1000, "HU": 1000,
    "IE": 1000, "IT": 1000, "LT": 1000, "LU": 1000,
    "LV": 1000, "MT": 1000, "NL": 1000, "PO": 1000,
    "PT": 1000, "RO": 1000, "SI": 1000, "SK": 1000,
    
    # North America
    "CA": 1500, "US": 1500,
    
    # Rest of the world
    "*": 2500
}


def count_boxes(packages: List[Dict]) -> int:
    """
    Calculate number of boxes based on product packages
    
    Args:
        packages: List of package info
    
    Returns:
        Number of boxes needed
    """
    def to_int(val):
        """Convert Decimal/str/int to int"""
        if isinstance(val, (Decimal, str)):
            return int(val)
        return val
    
    volume = sum([
        to_int(p.get('width', 0)) * to_int(p.get('length', 0)) * to_int(p.get('height', 0)) 
        for p in packages
    ])
    weight = sum([to_int(p.get('weight', 0)) for p in packages])
    
    return max(math.ceil(volume / BOX_VOLUME), math.ceil(weight / BOX_WEIGHT))


def get_shipping_cost(address: Dict) -> int:
    """
    Get shipping cost per box
    
    Args:
        address: Address info
    
    Returns:
        Shipping cost per box (in cents)
    """
    country = address.get('country', '')
    return COUNTRY_SHIPPING_FEES.get(country, COUNTRY_SHIPPING_FEES['*'])


def calculate_delivery_price(products: List[Dict], address: Dict) -> int:
    """
    Calculate delivery price
    
    Args:
        products: List of products
        address: Delivery address
    
    Returns:
        Delivery price (in cents)
    """
    # Extract package info from all products
    packages = []
    for product in products:
        quantity = product.get('quantity', 1)
        # Ensure quantity is integer
        if isinstance(quantity, (Decimal, str)):
            quantity = int(quantity)
        
        package = product.get('package', {})
        # Add package info repeatedly based on quantity
        for _ in range(quantity):
            packages.append(package)
    
    # Calculate number of boxes and shipping cost
    boxes = count_boxes(packages)
    cost_per_box = get_shipping_cost(address)
    
    return boxes * cost_per_box
