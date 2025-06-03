import sys
import os
from werkzeug.middleware.proxy_fix import ProxyFix

# Add the parent directory to the Python path so we can import app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Patch the scheduler to avoid issues in serverless
import sqlite3
def mock_scheduler(*args, **kwargs):
    """Mock scheduler for serverless environment"""
    class MockScheduler:
        def __init__(self):
            self.running = False
        def add_job(self, *args, **kwargs):
            pass
        def start(self):
            self.running = True
        def shutdown(self):
            self.running = False
    return MockScheduler()

# Import the Flask app with error handling
try:
    # Mock the scheduler before importing
    sys.modules['apscheduler.schedulers.background'] = type('MockModule', (), {
        'BackgroundScheduler': mock_scheduler
    })()
    
    from app import app
    
    # Configure for production/serverless environment
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    
    # Update template and static folder paths for serverless
    base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app.template_folder = os.path.join(base_path, 'templates')
    app.static_folder = os.path.join(base_path, 'static')
    
    # Ensure database is initialized for each cold start
    try:
        from app import init_database, DATABASE_PATH
        
        # Ensure the directory exists for the database
        db_dir = os.path.dirname(DATABASE_PATH)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
            
        init_database()
        print(f"Database initialized at: {DATABASE_PATH}")
    except Exception as e:
        print(f"Database initialization warning: {e}")
        import traceback
        traceback.print_exc()
    
except Exception as e:
    print(f"Error importing app: {e}")
    # Create a minimal Flask app as fallback
    from flask import Flask, jsonify
    app = Flask(__name__)
    
    @app.route('/')
    def fallback():
        return jsonify({'error': 'App initialization failed', 'details': str(e)})

# Export the app for Vercel
if __name__ == "__main__":
    app.run(debug=False) 