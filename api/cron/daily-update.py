import sys
import os

# Add the parent directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from flask import Flask, jsonify
from datetime import date
import logging

# Import the data update function from your main app
try:
    from app import daily_data_update, logger
except ImportError:
    # Fallback logging if main app import fails
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/api/cron/daily-update', methods=['GET', 'POST'])
def cron_daily_update():
    """Endpoint for Vercel cron to trigger daily data updates"""
    try:
        logger.info("Cron job triggered: Starting daily data update...")
        
        # Run the daily data update
        daily_data_update()
        
        return jsonify({
            'status': 'success',
            'message': 'Daily data update completed successfully',
            'date': date.today().strftime("%Y-%m-%d")
        }), 200
        
    except Exception as e:
        logger.error(f"Cron job failed: {e}")
        return jsonify({
            'status': 'error',
            'message': f'Daily data update failed: {str(e)}',
            'date': date.today().strftime("%Y-%m-%d")
        }), 500

# For Vercel serverless functions
if __name__ == "__main__":
    app.run(debug=False) 