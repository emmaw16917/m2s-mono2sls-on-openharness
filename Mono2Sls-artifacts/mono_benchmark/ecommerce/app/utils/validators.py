"""
Validators
Used to validate input data
"""
import re


def validate_email(email):
    """
    Validate email format
    
    Args:
        email: Email address
    
    Returns:
        Boolean indicating validity
    """
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


def validate_product(product):
    """
    Validate product data
    
    Args:
        product: Product dictionary
    
    Returns:
        (is_valid, error_message)
    """
    required_fields = ['productId', 'name', 'package', 'price']
    
    for field in required_fields:
        if field not in product:
            return False, f"Missing required field: {field}"
    
    # Validate price
    if not isinstance(product['price'], int) or product['price'] < 0:
        return False, "Price must be a non-negative integer"
    
    # Validate package info
    package = product['package']
    if not isinstance(package, dict):
        return False, "Package must be an object"
    
    return True, None


def validate_address(address):
    """
    Validate address data
    
    Args:
        address: Address dictionary
    
    Returns:
        (is_valid, error_message)
    """
    required_fields = ['name', 'streetAddress', 'city', 'country', 'phoneNumber']
    
    for field in required_fields:
        if field not in address:
            return False, f"Missing required field: {field}"
    
    # Validate country code (2 letters)
    if not re.match(r'^[A-Za-z]{2}$', address['country']):
        return False, "Country code must be 2 letters"
    
    return True, None


def validate_payment_token(payment_token):
    """
    Validate payment token format
    
    Args:
        payment_token: Payment token
    
    Returns:
        Boolean indicating validity
    """
    # UUID format validation
    pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    return re.match(pattern, payment_token.lower()) is not None


def validate_card_number(card_number):
    """
    Validate credit card number format
    
    Args:
        card_number: Credit card number
    
    Returns:
        Boolean indicating validity
    """
    # Simple validation: 16 digits
    return re.match(r'^\d{16}$', card_number) is not None
