import { auth, signInWithEmailAndPassword, createUserWithEmailAndPassword, signOut, onAuthStateChanged } from './firebase-config.js';

class AuthManager {
    constructor() {
        this.currentUser = null;
        this.userInfo = null;
        this.authCallbacks = [];
        this.init();
    }

    init() {
        onAuthStateChanged(auth, async (user) => {
            this.currentUser = user;
            
            if (user) {
                // Get Firebase ID token and send to server
                try {
                    const idToken = await user.getIdToken();
                    await this.verifyTokenWithServer(idToken);
                    // Fetch user info including role
                    await this.fetchUserInfo();
                } catch (error) {
                    console.error('Error getting ID token:', error);
                }
            } else {
                // User signed out, clear server session
                this.userInfo = null;
                await this.clearServerSession();
            }
            
            this.updateUI();
            this.notifyAuthCallbacks(user);
        });
    }

    async verifyTokenWithServer(idToken) {
        try {
            const response = await fetch('/api/auth/verify', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ idToken })
            });
            
            if (!response.ok) {
                throw new Error('Failed to verify token with server');
            }
        } catch (error) {
            console.error('Server token verification failed:', error);
        }
    }

    async clearServerSession() {
        try {
            await fetch('/api/auth/logout', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                }
            });
        } catch (error) {
            console.error('Error clearing server session:', error);
        }
    }

    async fetchUserInfo() {
        if (!this.currentUser) return;
        
        try {
            const response = await fetch('/api/auth/user-info', {
                method: 'GET',
                headers: {
                    'Content-Type': 'application/json',
                }
            });
            
            if (response.ok) {
                this.userInfo = await response.json();
                // Update admin navigation after user info is fetched
                this.updateAdminNavigation();
            } else {
                this.userInfo = null;
            }
        } catch (error) {
            console.error('Error fetching user info:', error);
            this.userInfo = null;
        }
    }

    async signIn(email, password) {
        try {
            const userCredential = await signInWithEmailAndPassword(auth, email, password);
            console.log('User signed in:', userCredential.user.email);
            return { success: true, user: userCredential.user };
        } catch (error) {
            console.error('Sign in error:', error);
            return { success: false, error: error.message };
        }
    }

    async signUp(email, password) {
        try {
            const userCredential = await createUserWithEmailAndPassword(auth, email, password);
            console.log('User created:', userCredential.user.email);
            return { success: true, user: userCredential.user };
        } catch (error) {
            console.error('Sign up error:', error);
            return { success: false, error: error.message };
        }
    }

    async logout() {
        try {
            await signOut(auth);
            console.log('User signed out');
            return { success: true };
        } catch (error) {
            console.error('Sign out error:', error);
            return { success: false, error: error.message };
        }
    }

    isAuthenticated() {
        return this.currentUser !== null;
    }

    isAdmin() {
        return this.userInfo && this.userInfo.isAdmin === true;
    }

    getUserRole() {
        return this.userInfo ? this.userInfo.role : 'user';
    }

    getCurrentUser() {
        return this.currentUser;
    }

    getUserInfo() {
        return this.userInfo;
    }

    updateUI() {
        const authContainer = document.getElementById('auth-container');
        const userInfo = document.getElementById('user-info');
        const loginButton = document.getElementById('login-btn');
        const logoutButton = document.getElementById('logout-btn');
        const accountButton = document.getElementById('account-btn');

        if (this.currentUser) {
            if (authContainer) authContainer.style.display = 'none';
            if (userInfo) {
                userInfo.style.display = 'block';
                const roleText = this.isAdmin() ? ' (Admin)' : '';
                userInfo.textContent = `Welcome, ${this.currentUser.email}${roleText}`;
            }
            if (loginButton) loginButton.style.display = 'none';
            if (logoutButton) logoutButton.style.display = 'block';
            if (accountButton) accountButton.style.display = 'block';
        } else {
            if (authContainer) authContainer.style.display = 'block';
            if (userInfo) userInfo.style.display = 'none';
            if (loginButton) loginButton.style.display = 'block';
            if (logoutButton) logoutButton.style.display = 'none';
            if (accountButton) accountButton.style.display = 'none';
        }

        // Update admin navigation
        this.updateAdminNavigation();
    }

    updateAdminNavigation() {
        const adminLinks = document.querySelectorAll('.admin-nav-link');
        const editButtons = document.querySelectorAll('.admin-edit-btn');
        
        console.log(`Updating admin navigation. Is admin: ${this.isAdmin()}`);
        console.log(`Found ${adminLinks.length} admin links and ${editButtons.length} edit buttons`);
        
        adminLinks.forEach(link => {
            if (this.isAdmin()) {
                link.style.display = 'block';
            } else {
                link.style.display = 'none';
            }
        });

        editButtons.forEach(button => {
            if (this.isAdmin()) {
                button.style.display = 'inline-block';
            } else {
                button.style.display = 'none';
            }
        });
        
        // Also retry after a short delay to catch any buttons that load later
        if (this.isAdmin()) {
            setTimeout(() => {
                const newEditButtons = document.querySelectorAll('.admin-edit-btn');
                newEditButtons.forEach(button => {
                    button.style.display = 'inline-block';
                });
            }, 500);
        }
    }

    onAuthStateChange(callback) {
        this.authCallbacks.push(callback);
    }

    notifyAuthCallbacks(user) {
        this.authCallbacks.forEach(callback => callback(user));
    }

    requireAuth() {
        if (!this.isAuthenticated()) {
            this.showLoginModal();
            return false;
        }
        return true;
    }

    showLoginModal() {
        const modal = document.getElementById('auth-modal');
        if (modal) {
            modal.style.display = 'block';
        }
    }

    hideLoginModal() {
        const modal = document.getElementById('auth-modal');
        if (modal) {
            modal.style.display = 'none';
        }
    }
}

// Create global auth manager instance
window.authManager = new AuthManager();

// Export for module usage
export default window.authManager; 