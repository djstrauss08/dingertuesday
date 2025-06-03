#!/usr/bin/env python3
"""
Admin Setup Script for Dinger Tuesday

This script helps you set up the initial admin user for your Dinger Tuesday application.
Run this after you've created your account through the web interface.
"""

import sqlite3
import sys

DATABASE_PATH = 'daily_mlb_data.sqlite'

def list_users():
    """List all users in the database"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT id, email, role, created_at FROM users ORDER BY created_at')
        users = cursor.fetchall()
        
        if not users:
            print("No users found in the database.")
            print("Please sign up through the web interface first at /login")
            return
        
        print("\nCurrent users:")
        print("-" * 80)
        print(f"{'ID':<5} {'Email':<30} {'Role':<10} {'Created'}")
        print("-" * 80)
        
        for user in users:
            print(f"{user[0]:<5} {user[1]:<30} {user[2]:<10} {user[3]}")
        
    except Exception as e:
        print(f"Error listing users: {e}")
    finally:
        conn.close()

def make_admin(email):
    """Make a user admin by email"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        # Check if user exists
        cursor.execute('SELECT id, email, role FROM users WHERE email = ?', (email,))
        user = cursor.fetchone()
        
        if not user:
            print(f"User with email '{email}' not found.")
            print("Please sign up through the web interface first at /login")
            return False
        
        if user[2] == 'admin':
            print(f"User '{email}' is already an admin.")
            return True
        
        # Update user role to admin
        cursor.execute('UPDATE users SET role = ? WHERE email = ?', ('admin', email))
        conn.commit()
        
        if cursor.rowcount > 0:
            print(f"✓ Successfully made '{email}' an admin!")
            return True
        else:
            print(f"Failed to update user '{email}'.")
            return False
            
    except Exception as e:
        print(f"Error making user admin: {e}")
        return False
    finally:
        conn.close()

def main():
    print("Dinger Tuesday Admin Setup")
    print("=" * 40)
    
    # List current users
    list_users()
    
    if len(sys.argv) > 1:
        # Email provided as command line argument
        email = sys.argv[1]
        make_admin(email)
    else:
        # Interactive mode
        print("\nEnter the email address of the user you want to make an admin:")
        email = input("Email: ").strip()
        
        if not email:
            print("No email provided. Exiting.")
            return
        
        if make_admin(email):
            print("\nAdmin setup complete!")
            print(f"User '{email}' now has admin access to:")
            print("- /admin - Admin dashboard")
            print("- /admin/articles - Article management")
            print("- Article creation and editing")
            print("- Image uploads")
        else:
            print("\nAdmin setup failed. Please try again.")

if __name__ == "__main__":
    main() 