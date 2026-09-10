"""
Loyalty Service
Handles customer loyalty points and tier management
"""
from data.storage import storage


class LoyaltyService:
    """Service for managing loyalty points and tiers"""
    
    TIER_GOLD = 100000
    TIER_SILVER = 50000
    TIER_BRONZE = 1
    
    @staticmethod
    def get_loyalty_level(points: int) -> str:
        """Calculate loyalty level based on total points"""
        if points >= LoyaltyService.TIER_GOLD:
            return 'gold'
        elif points >= LoyaltyService.TIER_SILVER:
            return 'silver'
        else:
            return 'bronze'
    
    @staticmethod
    def get_remaining_points_to_next_tier(points: int, level: str) -> int:
        """Calculate points needed to reach the next tier"""
        if level == 'bronze':
            return LoyaltyService.TIER_SILVER - points
        elif level == 'silver':
            return LoyaltyService.TIER_GOLD - points
        else:
            return 0
    
    @staticmethod
    def get_customer_loyalty(customer_id: str) -> dict:
        """
        Get loyalty information for a customer
        
        Returns dict with points, level, and remainingPoints.
        """
        total_points = storage.get_loyalty_points(customer_id)
        level = LoyaltyService.get_loyalty_level(total_points)
        remaining_points = LoyaltyService.get_remaining_points_to_next_tier(total_points, level)
        
        return {
            'points': total_points,
            'level': level,
            'remainingPoints': remaining_points
        }
    
    @staticmethod
    def add_loyalty_points(customer_id: str, points: int) -> dict:
        """
        Add loyalty points for a customer.
        
        Raises ValueError if points is not a positive number.
        """
        if not isinstance(points, (int, float)) or points <= 0:
            raise ValueError("Points must be a positive number")
        
        storage.add_loyalty_points(customer_id, int(points))
        
        return {
            'message': 'Loyalty points added successfully',
            'customerId': customer_id,
            'pointsAdded': int(points)
        }
    
    @staticmethod
    def process_booking_loyalty(customer_id: str, price: float) -> dict:
        """Process loyalty points from a confirmed booking (1:1 price-to-points ratio)."""
        points = int(price)
        return LoyaltyService.add_loyalty_points(customer_id, points)

