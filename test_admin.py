#!/usr/bin/env python3
"""
Test Admin Access

This script tests if admin access is working properly.
"""

import requests
import json

BASE_URL = "http://localhost:8080"

def test_admin_endpoints():
    """Test admin endpoints"""
    
    print("Testing Admin Access for danieljstrauss1@gmail.com")
    print("=" * 50)
    
    # Test endpoints
    endpoints_to_test = [
        ("/admin", "Admin Dashboard"),
        ("/admin/articles", "Article Management"),
        ("/admin/articles/new", "New Article Page"),
        ("/debug/auth", "Debug Authentication Info"),
    ]
    
    for endpoint, description in endpoints_to_test:
        try:
            print(f"\nTesting: {description}")
            print(f"URL: {BASE_URL}{endpoint}")
            
            response = requests.get(f"{BASE_URL}{endpoint}", timeout=10)
            
            if response.status_code == 200:
                print(f"✅ SUCCESS - {description} accessible")
            elif response.status_code == 403:
                print(f"❌ ACCESS DENIED - {description} (403 Forbidden)")
            elif response.status_code == 401:
                print(f"🔐 NOT AUTHENTICATED - {description} (401 Unauthorized)")
            elif response.status_code == 302:
                print(f"🔄 REDIRECT - {description} (probably to login)")
                print(f"   Redirect to: {response.headers.get('Location', 'Unknown')}")
            else:
                print(f"⚠️  UNEXPECTED - {description} (Status: {response.status_code})")
                
        except requests.exceptions.ConnectionError:
            print(f"❌ CONNECTION ERROR - Cannot connect to {BASE_URL}")
            print("   Make sure Flask app is running on port 8080")
            break
        except Exception as e:
            print(f"❌ ERROR - {description}: {e}")
    
    # Test debug endpoint specifically
    try:
        print(f"\n" + "="*50)
        print("DEBUG AUTHENTICATION INFO:")
        response = requests.get(f"{BASE_URL}/debug/auth", timeout=10)
        if response.status_code == 200:
            debug_info = response.json()
            print(json.dumps(debug_info, indent=2))
        else:
            print(f"Debug endpoint returned status: {response.status_code}")
    except Exception as e:
        print(f"Error getting debug info: {e}")

def check_server_status():
    """Check if the server is running"""
    try:
        response = requests.get(f"{BASE_URL}/", timeout=5)
        if response.status_code == 200:
            print("✅ Flask server is running")
            return True
        else:
            print(f"⚠️ Flask server returned status: {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ Flask server is not running or not accessible")
        print("   Start it with: python app.py")
        return False
    except Exception as e:
        print(f"❌ Error checking server: {e}")
        return False

if __name__ == "__main__":
    print("Dinger Tuesday Admin Access Test")
    print("=" * 40)
    
    if check_server_status():
        test_admin_endpoints()
        
        print(f"\n" + "="*50)
        print("MANUAL TESTING STEPS:")
        print("1. Open browser and go to http://localhost:8080/login")
        print("2. Sign in with: danieljstrauss1@gmail.com")
        print("3. After login, try: http://localhost:8080/admin")
        print("4. You should see the admin dashboard")
        print("5. Check if 'Admin' link appears in navigation")
        print("6. Look for '(Admin)' next to your name in navigation")
    else:
        print("\nPlease start the Flask server first:")
        print("python app.py") 