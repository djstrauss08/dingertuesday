#!/usr/bin/env python3
"""
Test script to verify deployment readiness
"""
import sys
import os

def test_api_import():
    """Test if the API can be imported successfully"""
    try:
        # Set Vercel environment
        os.environ['VERCEL'] = '1'
        os.environ['DATABASE_PATH'] = '/tmp/daily_mlb_data.sqlite'
        
        sys.path.append('.')
        from api.index import app
        
        print("✅ API import successful")
        
        # Test basic routes
        with app.test_client() as client:
            response = client.get('/')
            print(f"✅ Root route status: {response.status_code}")
            
            response = client.get('/api/daily_status')
            print(f"✅ Daily status API: {response.status_code}")
            
        return True
    except Exception as e:
        print(f"❌ API import failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_database():
    """Test database initialization"""
    try:
        os.environ['VERCEL'] = '1'
        os.environ['DATABASE_PATH'] = '/tmp/test_daily_mlb_data.sqlite'
        
        sys.path.append('.')
        from app import init_database, DATABASE_PATH
        
        print(f"✅ Database path: {DATABASE_PATH}")
        
        # Ensure directory exists
        db_dir = os.path.dirname(DATABASE_PATH)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
            
        init_database()
        print("✅ Database initialization successful")
        
        # Clean up test database
        if os.path.exists(DATABASE_PATH):
            os.remove(DATABASE_PATH)
            
        return True
    except Exception as e:
        print(f"❌ Database initialization failed: {e}")
        return False

if __name__ == "__main__":
    print("🧪 Testing deployment readiness...")
    
    db_test = test_database()
    api_test = test_api_import()
    
    if db_test and api_test:
        print("\n🎉 All tests passed! Ready for deployment.")
        sys.exit(0)
    else:
        print("\n💥 Some tests failed. Please fix the issues before deploying.")
        sys.exit(1) 