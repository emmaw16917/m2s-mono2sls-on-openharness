"""
Warehouse Service
Handles packaging requests and inventory management
"""
from typing import Dict, List, Optional
from app.models import PackagingRequest, Delivery, Order


def get_new_packaging_requests(limit: int = 50) -> List[str]:
    """
    Get list of new packaging request IDs
    
    Args:
        limit: Limit on number of returned items
    
    Returns:
        List of order IDs
    """
    requests = PackagingRequest.get_new_requests(limit=limit)
    return [req.order_id for req in requests]


def get_packaging_request(order_id: str) -> Optional[Dict]:
    """
    Get packaging request details
    
    Args:
        order_id: Order ID
    
    Returns:
        Packaging request dictionary or None
    """
    request = PackagingRequest.get_by_order_id(order_id)
    return request.to_dict() if request else None


def start_packaging(order_id: str) -> tuple[bool, str]:
    """
    Start packaging process
    
    Args:
        order_id: Order ID
    
    Returns:
        (success, message)
    """
    request = PackagingRequest.get_by_order_id(order_id)
    
    if not request:
        return False, f"Packaging request for order {order_id} not found"
    
    if request.status != 'NEW':
        return False, f"Packaging request is in status '{request.status}', cannot start"
    
    try:
        request.update_status('IN_PROGRESS')
        return True, "Packaging started"
    except Exception as e:
        return False, f"Failed to start packaging: {str(e)}"


def update_packaging_product(order_id: str, product_id: str, quantity: int) -> tuple[bool, str]:
    """
    Update packaging product quantity
    
    Args:
        order_id: Order ID
        product_id: Product ID
        quantity: New quantity
    
    Returns:
        (success, message)
    """
    request = PackagingRequest.get_by_order_id(order_id)
    
    if not request:
        return False, f"Packaging request for order {order_id} not found"
    
    if request.status != 'IN_PROGRESS':
        return False, f"Packaging request is in status '{request.status}', cannot update"
    
    # Find and update product
    found = False
    for product in request.products:
        if product['productId'] == product_id:
            product['quantity'] = int(quantity)
            found = True
            break
    
    if not found:
        return False, f"Product {product_id} not found in packaging request"
    
    try:
        request.save()
        return True, "Product quantity updated"
    except Exception as e:
        return False, f"Failed to update product: {str(e)}"


def complete_packaging(order_id: str) -> tuple[bool, str]:
    """
    Complete packaging process
    
    Args:
        order_id: Order ID
    
    Returns:
        (success, message)
    """
    request = PackagingRequest.get_by_order_id(order_id)
    
    if not request:
        return False, f"Packaging request for order {order_id} not found"
    
    if request.status != 'IN_PROGRESS':
        return False, f"Packaging request is in status '{request.status}', cannot complete"
    
    try:
        request.update_status('COMPLETED')
        
        # Trigger delivery process
        _trigger_delivery(order_id)
        
        return True, "Packaging completed"
    except Exception as e:
        return False, f"Failed to complete packaging: {str(e)}"


def fail_packaging(order_id: str, reason: str = "Packaging failed") -> tuple[bool, str]:
    """
    Mark packaging as failed
    
    Args:
        order_id: Order ID
        reason: Failure reason
    
    Returns:
        (success, message)
    """
    request = PackagingRequest.get_by_order_id(order_id)
    
    if not request:
        return False, f"Packaging request for order {order_id} not found"
    
    try:
        request.update_status('FAILED')
        
        # Update order status to packaging failed
        order = Order.get_by_id(order_id)
        if order:
            order.update_status('PACKAGING_FAILED')
        
        # Trigger refund process
        _trigger_payment_cancellation(order_id, reason)
        
        return True, "Packaging marked as failed and refund initiated"
    except Exception as e:
        return False, f"Failed to mark packaging as failed: {str(e)}"


def _trigger_payment_cancellation(order_id: str, reason: str):
    """
    Trigger payment cancellation process
    
    Args:
        order_id: Order ID
        reason: Cancellation reason
    """
    from app.services import payment_service
    
    # Get order info
    order = Order.get_by_id(order_id)
    if not order or not order.payment_token:
        return
    
    # Execute refund
    payment_service.cancel_payment(order.payment_token)


def _trigger_delivery(order_id: str):
    """
    Trigger delivery process
    
    Args:
        order_id: Order ID
    """
    # Get order info
    order = Order.get_by_id(order_id)
    if not order:
        return
    
    # Create delivery record
    delivery = Delivery(
        order_id=order_id,
        status='NEW',
        address=order.get_address()
    )
    
    delivery.save()
