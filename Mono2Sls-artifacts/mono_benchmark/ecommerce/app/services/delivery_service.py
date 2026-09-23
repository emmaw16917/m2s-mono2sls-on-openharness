"""
Delivery Service
Handles delivery-related business logic
"""
from typing import Dict, List, Optional
from app.models import Delivery, Order


def get_new_deliveries(limit: int = 50) -> List[Dict]:
    """
    Get list of new delivery requests
    
    Args:
        limit: Limit on number of returned items
    
    Returns:
        List of deliveries
    """
    deliveries = Delivery.get_new_deliveries(limit=limit)
    return [delivery.to_dict() for delivery in deliveries]


def get_delivery(order_id: str) -> Optional[Dict]:
    """
    Get delivery details
    
    Args:
        order_id: Order ID
    
    Returns:
        Delivery dictionary or None
    """
    delivery = Delivery.get_by_order_id(order_id)
    return delivery.to_dict() if delivery else None


def start_delivery(order_id: str) -> tuple[bool, str]:
    """
    Start delivery
    
    Args:
        order_id: Order ID
    
    Returns:
        (success, message)
    """
    delivery = Delivery.get_by_order_id(order_id)
    
    if not delivery:
        return False, f"Delivery for order {order_id} not found"
    
    if delivery.status != 'NEW':
        return False, f"Delivery is in status '{delivery.status}', cannot start"
    
    try:
        delivery.update_status('IN_PROGRESS')
        
        # Update order status
        order = Order.get_by_id(order_id)
        if order:
            order.update_status('IN_TRANSIT')
        
        return True, "Delivery started"
    except Exception as e:
        return False, f"Failed to start delivery: {str(e)}"


def complete_delivery(order_id: str) -> tuple[bool, str]:
    """
    Complete delivery
    
    Args:
        order_id: Order ID
    
    Returns:
        (success, message)
    """
    delivery = Delivery.get_by_order_id(order_id)
    
    if not delivery:
        return False, f"Delivery for order {order_id} not found"
    
    if delivery.status != 'IN_PROGRESS':
        return False, f"Delivery is in status '{delivery.status}', cannot complete"
    
    try:
        delivery.update_status('COMPLETED')
        
        # Update order status
        order = Order.get_by_id(order_id)
        if order:
            order.update_status('COMPLETED')
        
        # Trigger final payment processing
        _trigger_payment_processing(order_id)
        
        return True, "Delivery completed and payment processed"
    except Exception as e:
        return False, f"Failed to complete delivery: {str(e)}"


def _trigger_payment_processing(order_id: str):
    """
    Trigger payment processing (capture)
    
    Args:
        order_id: Order ID
    """
    from app.services import payment_service
    
    # Get order info
    order = Order.get_by_id(order_id)
    if not order or not order.payment_token:
        return
    
    # Execute payment processing
    payment_service.process_payment(order.payment_token)


def fail_delivery(order_id: str, reason: str = "Delivery failed") -> tuple[bool, str]:
    """
    Mark delivery as failed
    
    Args:
        order_id: Order ID
        reason: Failure reason
    
    Returns:
        (success, message)
    """
    delivery = Delivery.get_by_order_id(order_id)
    
    if not delivery:
        return False, f"Delivery for order {order_id} not found"
    
    try:
        delivery.update_status('FAILED')
        
        # Update order status
        order = Order.get_by_id(order_id)
        if order:
            order.update_status('DELIVERY_FAILED')
        
        # Trigger refund process
        _trigger_payment_cancellation(order_id, reason)
        
        return True, "Delivery marked as failed and refund initiated"
    except Exception as e:
        return False, f"Failed to mark delivery as failed: {str(e)}"


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
