"""
Application entry point
Used to start the Flask development server
"""
import os
from app import create_app

# Get configuration name from environment variable, default is development
config_name = os.environ.get('FLASK_CONFIG', 'development')

# Create application instance
app = create_app(config_name)

if __name__ == '__main__':
    # Read host and port from environment variables
    host = os.environ.get('FLASK_HOST', '0.0.0.0')
    port = int(os.environ.get('FLASK_PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'
    
    print(f"Starting ecommerce monolith application...")
    print(f"Environment: {config_name}")
    print(f"Server running on http://{host}:{port}")
    
    # Start the application
    app.run(host=host, port=port, debug=debug)
