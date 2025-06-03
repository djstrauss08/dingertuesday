#!/usr/bin/env python3
"""
Render entry point for the MLB Stats Flask application
"""
import os
import sys

# Set environment variables for Render
os.environ['DATABASE_PATH'] = '/tmp/daily_mlb_data.sqlite'
os.environ['FLASK_ENV'] = 'production'

# Import the Flask app
from app import app, init_database

# Initialize database
init_database()

if __name__ == '__main__':
    # Render provides the PORT environment variable
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False) 