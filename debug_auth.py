#!/usr/bin/env python3
"""
Debug Authentication Script

This script helps debug authentication issues by testing the user creation flow.
"""

import sqlite3
from app import get_or_create_user, set_user_role

DATABASE_PATH = 'daily_mlb_data.sqlite'

def test_user_creation():
    """Test the user creation function"""
    print("Testing user creation function...")
    
    # Test with sample data
    test_uid = "test-uid-12345"
    test_email = "test@example.com"
    
    print(f"Creating user with UID: {test_uid}, Email: {test_email}")
    
    user_info = get_or_create_user(test_uid, test_email)
    if user_info:
        print("✓ User creation successful!")
        print(f"  User info: {user_info}")
        
        # Make them admin
        success = set_user_role(test_email, 'admin')
        if success:
            print("✓ Admin role set successfully!")
        else:
            print("❌ Failed to set admin role")
    else:
        print("❌ User creation failed")

def check_database_structure():
    """Check if the users table exists and has the right structure"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        # Check if users table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users';")
        table_exists = cursor.fetchone()
        
        if table_exists:
            print("✓ Users table exists")
            
            # Get table schema
            cursor.execute("PRAGMA table_info(users);")
            columns = cursor.fetchall()
            print("Table structure:")
            for col in columns:
                print(f"  {col[1]} {col[2]} {'NOT NULL' if col[3] else ''}")
        else:
            print("❌ Users table does not exist")
            
    except Exception as e:
        print(f"Error checking database: {e}")
    finally:
        conn.close()

def main():
    print("Dinger Tuesday Authentication Debug")
    print("=" * 40)
    
    print("\n1. Checking database structure...")
    check_database_structure()
    
    print("\n2. Testing user creation function...")
    test_user_creation()
    
    print("\n3. Current users in database:")
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT * FROM users')
        users = cursor.fetchall()
        if users:
            for user in users:
                print(f"  ID: {user[0]}, UID: {user[1]}, Email: {user[2]}, Role: {user[3]}")
        else:
            print("  No users found")
    except Exception as e:
        print(f"  Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    main() 