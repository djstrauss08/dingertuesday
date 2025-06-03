// Page-specific authentication script
// This script should be included on pages that require authentication

import authManager from './auth.js';

class PageAuth {
    constructor() {
        this.init();
    }

    init() {
        // Wait for auth state to be determined
        authManager.onAuthStateChange((user) => {
            if (!user && this.isProtectedPage()) {
                // User is not authenticated and this is a protected page
                this.redirectToLogin();
            } else if (user && this.isAdminPage() && !authManager.isAdmin()) {
                // User is authenticated but not admin and this is an admin page
                this.redirectToForbidden();
            }
        });

        // Also check immediately in case auth state is already known
        setTimeout(() => {
            if (!authManager.isAuthenticated() && this.isProtectedPage()) {
                this.redirectToLogin();
            } else if (authManager.isAuthenticated() && this.isAdminPage() && !authManager.isAdmin()) {
                this.redirectToForbidden();
            }
        }, 1500); // Give more time for user info to load
    }

    isProtectedPage() {
        const protectedPaths = ['/admin', '/admin/articles', '/admin/articles/new', '/profile'];
        const currentPath = window.location.pathname;
        
        // Check if current path starts with any protected path
        return protectedPaths.some(path => 
            currentPath === path || 
            currentPath.startsWith(path + '/') ||
            currentPath.startsWith('/admin/articles/') && currentPath.endsWith('/edit')
        );
    }

    isAdminPage() {
        const adminPaths = ['/admin', '/admin/articles', '/admin/articles/new'];
        const currentPath = window.location.pathname;
        
        // Check if current path is an admin path
        return adminPaths.some(path => 
            currentPath === path || 
            currentPath.startsWith(path + '/') ||
            currentPath.startsWith('/admin/articles/') && currentPath.endsWith('/edit')
        );
    }

    redirectToLogin() {
        const returnUrl = encodeURIComponent(window.location.href);
        window.location.href = `/login?return_url=${returnUrl}`;
    }

    redirectToForbidden() {
        // Show a message and redirect to home or show 403 page
        alert('Access denied: Admin privileges required for this page.');
        window.location.href = '/';
    }
}

// Initialize page authentication
document.addEventListener('DOMContentLoaded', () => {
    new PageAuth();
});

// Export for module usage
export default PageAuth; 