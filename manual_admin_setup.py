#!/usr/bin/env python3
"""
Manual Admin Setup Script for Dinger Tuesday

This script manually adds a user as admin, bypassing the Firebase signup process.
Use this when the normal signup process isn't working properly.
"""

import sqlite3
import uuid

DATABASE_PATH = 'daily_mlb_data.sqlite'

def manual_add_admin(email):
    """Manually add a user as admin to the database"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        # Check if user already exists
        cursor.execute('SELECT id, email, role FROM users WHERE email = ?', (email,))
        existing_user = cursor.fetchone()
        
        if existing_user:
            # Update existing user to admin
            cursor.execute('UPDATE users SET role = ? WHERE email = ?', ('admin', email))
            conn.commit()
            print(f"✓ Updated existing user '{email}' to admin role!")
            return True
        else:
            # Create new admin user with a generated UID
            fake_uid = f"manual-{uuid.uuid4().hex[:8]}"
            cursor.execute('''
                INSERT INTO users (uid, email, role)
                VALUES (?, ?, 'admin')
            ''', (fake_uid, email))
            conn.commit()
            print(f"✓ Created new admin user '{email}' with UID: {fake_uid}")
            return True
            
    except Exception as e:
        print(f"Error adding admin user: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def list_all_users():
    """List all users in the database"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT id, uid, email, role, created_at FROM users ORDER BY created_at')
        users = cursor.fetchall()
        
        print("\nAll users in database:")
        print("-" * 90)
        print(f"{'ID':<5} {'UID':<20} {'Email':<30} {'Role':<10} {'Created'}")
        print("-" * 90)
        
        if not users:
            print("No users found.")
        else:
            for user in users:
                print(f"{user[0]:<5} {user[1]:<20} {user[2]:<30} {user[3]:<10} {user[4]}")
        
    except Exception as e:
        print(f"Error listing users: {e}")
    finally:
        conn.close()

def main():
    print("Manual Admin Setup for Dinger Tuesday")
    print("=" * 45)
    
    # List current users
    list_all_users()
    
    print("\nThis script will manually add you as an admin user.")
    print("Enter your email address:")
    email = input("Email: ").strip()
    
    if not email:
        print("No email provided. Exiting.")
        return
    
    if "@" not in email:
        print("Invalid email format. Exiting.")
        return
    
    print(f"\nAdding '{email}' as admin...")
    
    if manual_add_admin(email):
        print("\n🎉 Success! Admin setup complete!")
        print(f"User '{email}' now has admin access.")
        print("\nNext steps:")
        print("1. Go to http://localhost:8080/login")
        print("2. Try to sign up/login with this email")
        print("3. The system should recognize you as admin")
        print("\nIf login still doesn't work, the issue is with Firebase authentication.")
        print("You can access admin functions by manually visiting:")
        print("- http://localhost:8080/admin")
        print("- http://localhost:8080/admin/articles")
        
        # Show updated user list
        list_all_users()
    else:
        print("\n❌ Failed to set up admin access.")

if __name__ == "__main__":
    main() 