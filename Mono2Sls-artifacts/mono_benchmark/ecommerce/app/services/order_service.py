"""
Order Service
Handles order-related business logic
"""
import uuid
from decimal import Decimal
from typing import Dict, List, Tuple, Any
from app.models import Order, Product, PackagingRequest
from app.services.delivery_pricing import calculate_delivery_price
from app.services import payment_service


def _normalize_value(value: Any) -> Any:
    """
    Normalize value for comparison (handles Decimal, int, str types)
    
    Args:
        value: Value to normalize
    
    Returns:
        Normalized value
    """
    # Handle Decimal type
    if isinstance(value, Decimal):
        return int(value)
    
    # Handle string digits
    if isinstance(value, str) and value.isdigit():
        return int(value)
    
    # Handle dictionary (e.g. package)
    if isinstance(value, dict):
        return {k: _normalize_value(v) for k, v in value.items()}
    
    return value


def _values_equal(val1: Any, val2: Any) -> bool:
    """
    Compare two values for equality (tolerating type differences)
    
    Args:
        val1: First value
        val2: Second value
    
    Returns:
        Whether they are equal
    """
    normalized_val1 = _normalize_value(val1)
    normalized_val2 = _normalize_value(val2)
    
    return normalized_val1 == normalized_val2


def validate_products(products: List[Dict]) -> Tuple[bool, str, List[Dict]]:
    """
    Validate product information
    
    Args:
        products: List of products provided by user
    
    Returns:
        (is_valid, error_message, invalid_products)
    """
    invalid_products = []
    reasons = []
    
    for user_product in products:
        product_id = user_product.get('productId')
        if not product_id:
            invalid_products.append(user_product)
            reasons.append("Missing productId")
            continue
        
        # Query product from database
        db_product = Product.get_by_id(product_id)
        if db_product is None:
            invalid_products.append(user_product)
            reasons.append(f"Product '{product_id}' not found")
            continue
        
        # Validate required fields
        required_fields = ['name', 'package', 'price']
        for field in required_fields:
            if field not in user_product:
                invalid_products.append(user_product)
                reasons.append(f"Missing '{field}' in product '{product_id}'")
                break
            
            # Get value from database for comparison
            if field == 'package':
                db_value = db_product.get_package()
                user_value = user_product[field]
            else:
                db_value = getattr(db_product, field)
                user_value = user_product[field]
            
            # Use smart comparison (tolerate Decimal vs int/str differences)
            if not _values_equal(user_value, db_value):
                invalid_products.append(user_product)
                reasons.append(
                    f"Invalid value for '{field}': want '{db_value}', "
                    f"got '{user_value}' in product '{product_id}'"
                )
                break
    
    if invalid_products:
        return False, ". ".join(reasons), invalid_products
    
    return True, "All products are valid", []


def validate_delivery_price(products: List[Dict], address: Dict, claimed_price: int) -> Tuple[bool, str]:
    """
    Validate delivery price
    
    Args:
        products: List of products
        address: Delivery address
        claimed_price: Delivery price claimed by user
    
    Returns:
        (is_valid, error_message)
    """
    actual_price = calculate_delivery_price(products, address)
    
    if actual_price != claimed_price:
        return False, f"Wrong delivery price: got {claimed_price}, expected {actual_price}"
    
    return True, "The delivery price is valid"


def validate_payment_token(payment_token: str, total: int) -> Tuple[bool, str]:
    """
    Validate payment token
    
    Args:
        payment_token: Payment token
        total: Order total
    
    Returns:
        (is_valid, error_message)
    """
    is_valid = payment_service.validate_payment(payment_token, total)
    
    if not is_valid:
        return False, "Invalid payment token or insufficient authorized amount"
    
    return True, "Payment token is valid"


def cleanup_products(products: List[Dict]) -> List[Dict]:
    """
    Clean up product data, keep only necessary fields, and ensure correct numeric types
    
    Args:
        products: List of products
    
    Returns:
        Cleaned list of products
    """
    cleaned = []
    for product in products:
        # Ensure price and quantity are integers
        price = product["price"]
        if isinstance(price, (str, Decimal)):
            price = int(price)
        
        quantity = product.get("quantity", 1)
        if isinstance(quantity, (str, Decimal)):
            quantity = int(quantity)
        
        cleaned.append({
            "productId": product["productId"],
            "name": product["name"],
            "package": product["package"],
            "price": price,
            "quantity": quantity
        })
    
    return cleaned


def create_order(user_id: str, order_data: Dict) -> Tuple[bool, str, Dict]:
    """
    Create order
    
    Args:
        user_id: User ID
        order_data: Order data
    
    Returns:
        (success, message, order_dict or errors)
    """
    # Extract order info
    products = order_data.get('products', [])
    address = order_data.get('address', {})
    delivery_price = order_data.get('deliveryPrice', 0)
    payment_token = order_data.get('paymentToken', '')
    
    # Ensure delivery_price is integer
    if isinstance(delivery_price, (str, Decimal)):
        delivery_price = int(delivery_price)
    
    # Validate products
    valid, message, invalid_products = validate_products(products)
    if not valid:
        return False, message, {'errors': [message], 'products': invalid_products}
    
    # Validate delivery price
    valid, message = validate_delivery_price(products, address, delivery_price)
    if not valid:
        return False, message, {'errors': [message]}
    
    # Clean up product data
    cleaned_products = cleanup_products(products)
    
    # Calculate total price
    total = sum([p["price"] * p.get("quantity", 1) for p in cleaned_products]) + delivery_price
    
    # Validate payment token
    valid, message = validate_payment_token(payment_token, total)
    if not valid:
        return False, message, {'errors': [message]}
    
    # Create order
    order = Order(
        order_id=str(uuid.uuid4()),
        user_id=user_id,
        status='NEW',
        address=address,
        products=cleaned_products,
        delivery_price=delivery_price,
        total=total,
        payment_token=payment_token
    )
    
    # Save to database
    try:
        order.save()
        
        # Trigger subsequent processes (warehouse and delivery)
        _trigger_warehouse_packaging(order)
        
        return True, "Order created", order.to_dict()
    except Exception as e:
        return False, f"Failed to create order: {str(e)}", {'errors': [str(e)]}


def _trigger_warehouse_packaging(order: Order):
    """
    Trigger warehouse packaging process
    
    Args:
        order: Order object
    """
    # Create packaging request
    products = [{
        'productId': product['productId'],
        'quantity': int(product.get('quantity', 1))
    } for product in order.get_products()]
    
    request = PackagingRequest(
        order_id=order.order_id,
        status='NEW',
        products=products
    )
    
    request.save()


def get_order(order_id: str) -> Dict:
    """
    Get order info
    
    Args:
        order_id: Order ID
    
    Returns:
        Order dictionary or None
    """
    order = Order.get_by_id(order_id)
    return order.to_dict() if order else None


def get_user_orders(user_id: str, limit: int = 50) -> List[Dict]:
    """
    Get user's order list
    
    Args:
        user_id: User ID
        limit: Limit on number of returned items
    
    Returns:
        List of orders
    """
    orders = Order.get_by_user_id(user_id, limit=limit)
    return [order.to_dict() for order in orders]


def update_order(order_id: str, order_data: Dict) -> Tuple[bool, str, Dict]:
    """
    Update order info (only allowed in NEW status)
    
    Args:
        order_id: Order ID
        order_data: New order data
    
    Returns:
        (success, message, order_dict or errors)
    """
    # Get existing order
    order = Order.get_by_id(order_id)
    if not order:
        return False, "Order not found", {'errors': ['Order not found']}
    
    # Only orders in NEW status can be modified
    if order.status != 'NEW':
        return False, f"Cannot modify order in status '{order.status}'", {
            'errors': [f"Cannot modify order in status '{order.status}'"]
        }
    
    # Record old values
    old_total = order.total
    old_products = order.get_products()
    
    # Update products (if provided)
    if 'products' in order_data:
        products = order_data['products']
        
        # Validate products
        valid, message, invalid_products = validate_products(products)
        if not valid:
            return False, message, {'errors': [message], 'products': invalid_products}
        
        # Clean up product data
        cleaned_products = cleanup_products(products)
        order.set_products(cleaned_products)
    
    # Update delivery price (if provided)
    if 'deliveryPrice' in order_data:
        delivery_price = order_data['deliveryPrice']
        
        # Ensure delivery_price is integer
        if isinstance(delivery_price, (str, Decimal)):
            delivery_price = int(delivery_price)
        
        # Validate delivery price
        valid, message = validate_delivery_price(order.get_products(), order.get_address(), delivery_price)
        if not valid:
            return False, message, {'errors': [message]}
        
        order.delivery_price = delivery_price
    
    # Recalculate total price
    new_total = sum([p["price"] * p.get("quantity", 1) for p in order.get_products()]) + order.delivery_price
    order.total = new_total
    
    try:
        # Save order
        order.save()
        
        # If products changed, trigger warehouse update
        if order.get_products() != old_products:
            _handle_order_products_changed(order_id, old_products, order.get_products())
        
        # If total price changed, trigger payment update
        if new_total != old_total:
            _handle_order_total_changed(order_id, order.payment_token, old_total, new_total)
        
        return True, "Order updated", order.to_dict()
    except Exception as e:
        return False, f"Failed to update order: {str(e)}", {'errors': [str(e)]}


def delete_order(order_id: str) -> Tuple[bool, str]:
    """
    Delete order (only allowed in NEW status)
    
    Args:
        order_id: Order ID
    
    Returns:
        (success, message)
    """
    # Get order
    order = Order.get_by_id(order_id)
    if not order:
        return False, "Order not found"
    
    # Only orders in NEW status can be deleted
    if order.status != 'NEW':
        return False, f"Cannot delete order in status '{order.status}'"
    
    try:
        # Trigger cleanup operations
        _handle_order_deleted(order_id, order.payment_token)
        
        # Delete order from database (using DynamoDB delete_item)
        from app.db import get_orders_table
        table = get_orders_table()
        table.delete_item(Key={'orderId': order_id})
        
        return True, "Order deleted"
    except Exception as e:
        return False, f"Failed to delete order: {str(e)}"


def _handle_order_products_changed(order_id: str, old_products: List[Dict], new_products: List[Dict]):
    """
    Handle order products change (originally via OrderModified event)
    
    Args:
        order_id: Order ID
        old_products: Old product list
        new_products: New product list
    """
    # Get packaging request
    packaging_request = PackagingRequest.get_by_order_id(order_id)
    if not packaging_request:
        return
    
    # Only packaging requests in NEW status can be modified
    if packaging_request.status != 'NEW':
        return
    
    # Update products in packaging request
    new_packaging_products = [{
        'productId': product['productId'],
        'quantity': int(product.get('quantity', 1))
    } for product in new_products]
    
    packaging_request.products = new_packaging_products
    packaging_request.save()


def _handle_order_total_changed(order_id: str, payment_token: str, old_total: int, new_total: int):
    """
    Handle order total price change (originally via OrderModified event)
    
    Args:
        order_id: Order ID
        payment_token: Payment token
        old_total: Old total price
        new_total: New total price
    """
    # Update authorized amount of payment token (can only decrease, not increase)
    if new_total < old_total:
        success, message = payment_service.update_payment_amount(payment_token, new_total)
        if not success:
            # Log error but don't block order update
            import logging
            logging.warning(f"Failed to update payment amount for order {order_id}: {message}")


def _handle_order_deleted(order_id: str, payment_token: str):
    """
    Handle order deletion (originally via OrderDeleted event)
    
    Args:
        order_id: Order ID
        payment_token: Payment token
    """
    # Delete packaging request
    packaging_request = PackagingRequest.get_by_order_id(order_id)
    if packaging_request and packaging_request.status == 'NEW':
        # Delete all products and metadata
        from app.db import get_warehouse_table
        table = get_warehouse_table()
        
        # Delete metadata
        table.delete_item(Key={'orderId': order_id, 'productId': '__metadata'})
        
        # Delete all products
        for product in packaging_request.products:
            table.delete_item(Key={'orderId': order_id, 'productId': product['productId']})
    
    # Cancel payment authorization
    if payment_token:
        payment_service.cancel_payment(payment_token)
