"""
Flask Application Factory
Used to create and configure Flask application instances
"""
from flask import Flask
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from config import config

# Initialize JWT extension
jwt = JWTManager()


def create_app(config_name='default'):
    """
    Application factory function
    
    Args:
        config_name: Configuration name ('development', 'production', 'testing')
    
    Returns:
        Flask application instance
    """
    app = Flask(__name__)
    
    # Load configuration
    app.config.from_object(config[config_name])
    
    # Initialize extensions
    jwt.init_app(app)
    CORS(app, origins=app.config['CORS_ORIGINS'])
    
    # Register blueprints (routes)
    from app.routes import auth, products, orders, warehouse, delivery, payment, payment_3p
    
    app.register_blueprint(auth.bp)
    app.register_blueprint(products.bp)
    app.register_blueprint(orders.bp)
    app.register_blueprint(warehouse.bp)
    app.register_blueprint(delivery.bp)
    app.register_blueprint(payment.bp)
    app.register_blueprint(payment_3p.bp)
    
    # Register error handlers
    register_error_handlers(app)
    
    return app


def register_error_handlers(app):
    """Register global error handlers"""
    
    @app.errorhandler(404)
    def not_found(error):
        """Handle 404 error"""
        return {'message': 'Resource not found'}, 404
    
    @app.errorhandler(500)
    def internal_error(error):
        """Handle 500 error"""
        return {'message': 'Internal server error'}, 500
    
    @app.errorhandler(Exception)
    def handle_exception(error):
        """Handle uncaught exceptions"""
        app.logger.error(f'Unhandled exception: {str(error)}')
        return {'message': 'An unexpected error occurred'}, 500
