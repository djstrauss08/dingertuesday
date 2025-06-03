# Admin Access Setup for Dinger Tuesday

This document explains how to set up admin access for the Dinger Tuesday application.

## Overview

The application now has role-based access control with two user roles:
- **user** (default) - Can view all content, navigate the site
- **admin** - Can access admin dashboard, write/edit articles, manage content

## Setting Up Your First Admin

### Step 1: Create Your Account
1. Start the application: `python app.py`
2. Navigate to `http://localhost:8080/login`
3. Click "Sign Up" and create your account with your email and password
4. Sign in to verify your account works

### Step 2: Make Yourself Admin

#### Option A: Using the Admin Setup Script (Recommended)
1. Run the admin setup script:
   ```bash
   python admin_setup.py your-email@example.com
   ```
   
   Or run it interactively:
   ```bash
   python admin_setup.py
   ```
   Then enter your email when prompted.

#### Option B: Using the API Endpoint
1. Make a POST request to the admin setup endpoint:
   ```bash
   curl -X POST http://localhost:8080/api/admin/make-admin \
        -H "Content-Type: application/json" \
        -d '{"email": "your-email@example.com", "secret_key": "initial_admin_setup_2025"}'
   ```

### Step 3: Verify Admin Access
1. Sign out and sign back in to refresh your session
2. You should now see "(Admin)" next to your name in the navigation
3. The "Admin" link should be visible in the navigation menu
4. You can access:
   - `/admin` - Admin dashboard
   - `/admin/articles` - Article management
   - `/admin/articles/new` - Create new articles

## Admin Features

### What Admins Can Do
- Access the admin dashboard (`/admin`)
- View all articles (published and drafts)
- Create new articles (`/admin/articles/new`)
- Edit existing articles (`/admin/articles/{id}/edit`)
- Delete articles
- Upload images for articles
- Manage daily data cache and updates
- See edit/delete buttons on the blog page

### What Regular Users Can Do
- View published articles
- Navigate all public pages (pitchers, hitters, articles)
- Create accounts and sign in
- Cannot access admin pages or functions

## Security Features

1. **Server-side Role Verification**: All admin routes check user role on the server
2. **UI Hiding**: Admin links and buttons are hidden from non-admin users
3. **Access Control**: Non-admin users get 403 Forbidden when trying to access admin pages
4. **Database-backed Roles**: User roles are stored in SQLite database, not just client-side

## Making Additional Admins

Once you're an admin, you can make other users admins:

1. Have the new user create an account first
2. Use the admin setup script:
   ```bash
   python admin_setup.py their-email@example.com
   ```

Or use the API (requires you to be signed in as admin):
```bash
curl -X POST http://localhost:8080/api/admin/make-admin \
     -H "Content-Type: application/json" \
     -d '{"email": "their-email@example.com"}'
```

## Troubleshooting

### "User not found" Error
- Make sure the user has created an account through the web interface first
- Check that you're using the exact email address they signed up with

### Admin Links Not Showing
- Sign out and sign back in to refresh your session
- Check browser console for any JavaScript errors
- Verify the user role was updated in the database

### 403 Forbidden Errors
- Verify the user has admin role in the database
- Clear browser cache and cookies
- Check that Firebase authentication is working properly

## Database Schema

The user table structure:
```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    uid TEXT UNIQUE NOT NULL,        -- Firebase UID
    email TEXT UNIQUE NOT NULL,      -- User email
    role TEXT DEFAULT 'user',        -- 'user' or 'admin'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

You can also directly query the database to check user roles:
```bash
sqlite3 daily_mlb_data.sqlite "SELECT email, role FROM users;"
``` 