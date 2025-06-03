# Firebase Authentication Integration

This document explains how Firebase Authentication has been integrated into the Dinger Tuesday website.

## Overview

Firebase Authentication has been integrated to protect admin pages and functionality. Users must sign in to access:

- `/admin` - Admin dashboard
- `/admin/articles` - Article management
- `/admin/articles/new` - Create new articles
- `/admin/articles/{id}/edit` - Edit existing articles
- All admin API endpoints (`/api/articles/*`, `/api/upload-image`)

## How It Works

### Frontend (JavaScript)
1. **Firebase Configuration** (`static/js/firebase-config.js`)
   - Initializes Firebase with your project configuration
   - Exports authentication functions

2. **Authentication Manager** (`static/js/auth.js`)
   - Handles sign in, sign up, and logout
   - Manages authentication state
   - Automatically syncs with Flask backend
   - Updates UI based on authentication status

3. **Page Protection** (`static/js/page-auth.js`)
   - Automatically redirects unauthenticated users from protected pages
   - Included on admin pages

### Backend (Flask)
1. **Firebase Admin SDK Integration**
   - Verifies ID tokens server-side
   - Uses Firebase Admin SDK for secure token validation

2. **Authentication Decorator** (`@require_auth`)
   - Protects Flask routes
   - Checks for valid Firebase tokens
   - Redirects to login or returns 401 for API calls

3. **Session Management**
   - Stores Firebase tokens in Flask sessions
   - Provides logout functionality

## Usage

### For Users
1. Navigate to `/login` to sign in or create an account
2. Use email and password authentication
3. After successful login, you'll be redirected to the admin area
4. Click "Logout" in the navbar to sign out

### For Developers
1. **Protecting a new route:**
   ```python
   @app.route('/new-admin-route')
   @require_auth
   def new_admin_function():
       return render_template('admin_template.html')
   ```

2. **Protecting an API endpoint:**
   ```python
   @app.route('/api/new-endpoint', methods=['POST'])
   @require_auth
   def new_api_function():
       return jsonify({'success': True})
   ```

3. **Adding page-level protection to templates:**
   ```html
   <!-- At the end of your admin template -->
   <script type="module">
       import '/static/js/page-auth.js';
   </script>
   ```

## Configuration

### Firebase Project Settings
The Firebase configuration is set in `static/js/firebase-config.js`:
- Project ID: `dingertuesday-18a26`
- Auth Domain: `dingertuesday-18a26.firebaseapp.com`

### Environment Variables
- `SECRET_KEY`: Flask session secret (set in production)

## Security Features

1. **Server-side Token Verification**: All tokens are verified using Firebase Admin SDK
2. **Session Management**: Tokens are stored securely in Flask sessions
3. **Automatic Logout**: Sessions are cleared when users sign out
4. **Protected Routes**: Both page-level and API-level protection
5. **Redirect Handling**: Users are redirected back to their intended page after login

## Testing

1. Start the Flask server: `python app.py`
2. Navigate to `http://localhost:8080/admin`
3. You should be redirected to the login page
4. Create an account or sign in
5. You should be redirected back to the admin page

## Troubleshooting

### Common Issues
1. **"Firebase Admin SDK initialization error"**: 
   - Check that the project ID is correct
   - Ensure Firebase Admin SDK is installed: `pip install firebase-admin`

2. **"Authentication required" errors**:
   - Check that the user is signed in
   - Verify that the Firebase token is being sent to the server

3. **Module import errors**:
   - Ensure your browser supports ES modules
   - Check that static files are being served correctly

### Debug Mode
The Flask app runs in debug mode by default. Check the console for detailed error messages. 