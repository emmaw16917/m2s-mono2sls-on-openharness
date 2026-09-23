"""
Configuration file
Contains all configuration parameters for the application
"""
import os
from datetime import timedelta


class Config:
    """Base configuration class"""
    
    # Flask configuration
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    
    # AWS configuration
    AWS_REGION = os.environ.get('AWS_REGION') or 'us-east-1'
    AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
    AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
    
    # DynamoDB table names configuration
    TABLE_USERS_NAME = os.environ.get('TABLE_USERS_NAME') or 'ecommerce-users'
    TABLE_PRODUCTS_NAME = os.environ.get('TABLE_PRODUCTS_NAME') or 'ecommerce-products'
    TABLE_ORDERS_NAME = os.environ.get('TABLE_ORDERS_NAME') or 'ecommerce-orders'
    TABLE_DELIVERY_NAME = os.environ.get('TABLE_DELIVERY_NAME') or 'ecommerce-delivery'
    TABLE_WAREHOUSE_NAME = os.environ.get('TABLE_WAREHOUSE_NAME') or 'ecommerce-warehouse'
    TABLE_PAYMENT_3P_NAME = os.environ.get('TABLE_PAYMENT_3P_NAME') or 'ecommerce-payment-3p'
    
    # JWT configuration
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY') or 'jwt-secret-key-change-in-production'
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=1)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=30)
    
    # Application configuration
    DEBUG = False
    TESTING = False
    
    # Log configuration
    LOG_LEVEL = os.environ.get('LOG_LEVEL') or 'INFO'
    
    # CORS configuration
    CORS_ORIGINS = os.environ.get('CORS_ORIGINS') or '*'


class DevelopmentConfig(Config):
    """Development environment configuration"""
    DEBUG = True


class ProductionConfig(Config):
    """Production environment configuration"""
    DEBUG = False
    # Production environment should read sensitive information from environment variables
    

class TestingConfig(Config):
    """Testing environment configuration"""
    TESTING = True


# Configuration dictionary
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}
