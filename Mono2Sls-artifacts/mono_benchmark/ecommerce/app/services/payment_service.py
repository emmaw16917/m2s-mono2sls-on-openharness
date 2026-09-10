"""
Payment Service
Handles payment validation and interaction with third-party payment systems
"""
import uuid
from decimal import Decimal
from typing import Dict, Tuple
from app.models import PaymentToken
from app.utils.validators import validate_card_number


def preauth_payment(card_number: str, amount: int) -> Tuple[bool, str, str]:
    """
    Pre-authorize payment (simulating third-party payment system)
    
    Args:
        card_number: Credit card number (16 digits)
        amount: Amount (in cents)
    
    Returns:
        (success, message, payment_token)
    """
    # Validate card number
    if not validate_card_number(card_number):
        return False, "Invalid card number format", ""
    
    # Validate amount
    if amount < 0:
        return False, "Amount must be non-negative", ""
    
    # Generate payment token
    payment_token = str(uuid.uuid4())
    
    # Save to database
    token = PaymentToken(
        payment_token=payment_token,
        amount=amount,
        status='AUTHORIZED',
        card_number_last4=card_number[-4:]
    )
    
    try:
        token.save()
        return True, "Payment pre-authorized", payment_token
    except Exception as e:
        return False, f"Failed to create payment token: {str(e)}", ""


def check_payment(payment_token: str, amount: int) -> Tuple[bool, str]:
    """
    Check if payment token is valid
    
    Args:
        payment_token: Payment token
        amount: Amount (in cents)
    
    Returns:
        (is_valid, message)
    """
    token = PaymentToken.get_by_token(payment_token)
    
    if not token:
        return False, "Payment token not found"
    
    if token.status != 'AUTHORIZED':
        return False, f"Payment token is in status '{token.status}'"
    
    # Ensure type consistency when comparing amounts
    token_amount = int(token.amount) if isinstance(token.amount, (Decimal, str)) else token.amount
    amount = int(amount) if isinstance(amount, (Decimal, str)) else amount
    
    if token_amount < amount:
        return False, f"Insufficient authorized amount: {token_amount} < {amount}"
    
    return True, "Payment token is valid"


def validate_payment(payment_token: str, total: int) -> bool:
    """
    Validate payment token (for order service)
    
    Args:
        payment_token: Payment token
        total: Order total
    
    Returns:
        Is valid
    """
    valid, _ = check_payment(payment_token, total)
    return valid


def process_payment(payment_token: str) -> Tuple[bool, str]:
    """
    Process payment (capture)
    
    Args:
        payment_token: Payment token
    
    Returns:
        (success, message)
    """
    token = PaymentToken.get_by_token(payment_token)
    
    if not token:
        return False, "Payment token not found"
    
    if token.status != 'AUTHORIZED':
        return False, f"Payment token is in status '{token.status}', cannot process"
    
    token.status = 'PROCESSED'
    
    try:
        token.save()
        return True, "Payment processed"
    except Exception as e:
        return False, f"Failed to process payment: {str(e)}"


def cancel_payment(payment_token: str) -> Tuple[bool, str]:
    """
    Cancel payment (refund/void)
    
    Args:
        payment_token: Payment token
    
    Returns:
        (success, message)
    """
    token = PaymentToken.get_by_token(payment_token)
    
    if not token:
        return False, "Payment token not found"
    
    if token.status == 'CANCELLED':
        return True, "Payment already cancelled"
    
    token.status = 'CANCELLED'
    
    try:
        token.save()
        return True, "Payment cancelled"
    except Exception as e:
        return False, f"Failed to cancel payment: {str(e)}"


def update_payment_amount(payment_token: str, new_amount: int) -> Tuple[bool, str]:
    """
    Update payment amount (can only decrease)
    
    Args:
        payment_token: Payment token
        new_amount: New amount
    
    Returns:
        (success, message)
    """
    token = PaymentToken.get_by_token(payment_token)
    
    if not token:
        return False, "Payment token not found"
    
    if token.status != 'AUTHORIZED':
        return False, f"Payment token is in status '{token.status}', cannot update"
    
    # Ensure type consistency when comparing amounts
    token_amount = int(token.amount) if isinstance(token.amount, (Decimal, str)) else token.amount
    new_amount = int(new_amount) if isinstance(new_amount, (Decimal, str)) else new_amount
    
    if new_amount > token_amount:
        return False, f"New amount {new_amount} exceeds authorized amount {token_amount}"
    
    token.amount = new_amount
    
    try:
        token.save()
        return True, "Payment amount updated"
    except Exception as e:
        return False, f"Failed to update payment amount: {str(e)}"
