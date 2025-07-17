// Firebase Authentication Module
// Handles email/password authentication with Firebase

/**
 * Initialize Firebase Authentication
 */
function initFirebaseAuth() {
    // Firebase auth state observer
    firebase.auth().onAuthStateChanged(function (user) {
        if (user) {
            // User is signed in
            console.log('User signed in:', user.email);
            // You can update UI elements here
            document.querySelectorAll('.logged-in').forEach(el => el.style.display = 'block');
            document.querySelectorAll('.logged-out').forEach(el => el.style.display = 'none');
        } else {
            // User is signed out
            console.log('User signed out');
            document.querySelectorAll('.logged-in').forEach(el => el.style.display = 'none');
            document.querySelectorAll('.logged-out').forEach(el => el.style.display = 'block');
        }
    });
}

/**
 * Sign in with email and password
 * @param {string} email - User email
 * @param {string} password - User password
 * @returns {Promise} - Authentication promise
 */
async function signInWithEmail(email, password) {
    try {
        const userCredential = await firebase.auth().signInWithEmailAndPassword(email, password);
        const user = userCredential.user;
        console.log('User signed in successfully:', user.email);

        // Get ID token to pass to backend
        const idToken = await user.getIdToken();
        return await sendTokenToBackend(idToken);
    } catch (error) {
        console.error('Error signing in:', error.code, error.message);
        throw error;
    }
}

/**
 * Create a new user with email and password
 * @param {string} email - User email
 * @param {string} password - User password
 * @param {string} name - User display name
 * @returns {Promise} - Authentication promise
 */
async function createUserWithEmail(email, password, name) {
    try {
        const userCredential = await firebase.auth().createUserWithEmailAndPassword(email, password);
        const user = userCredential.user;

        // Update user profile with name
        await user.updateProfile({
            displayName: name
        });

        console.log('User created successfully:', user.email);

        // Get ID token to pass to backend
        const idToken = await user.getIdToken();
        return await sendTokenToBackend(idToken);
    } catch (error) {
        console.error('Error creating user:', error.code, error.message);
        throw error;
    }
}

/**
 * Sign out the current user
 * @returns {Promise} - Sign out promise
 */
async function signOut() {
    try {
        await firebase.auth().signOut();
        console.log('User signed out successfully');
        window.location.href = '/';
    } catch (error) {
        console.error('Error signing out:', error.code, error.message);
        throw error;
    }
}

/**
 * Send the Firebase ID token to the backend
 * @param {string} idToken - Firebase ID token
 * @returns {Promise} - Response from backend
 */
async function sendTokenToBackend(idToken) {
    try {
        const response = await fetch('/auth/email_login', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ id_token: idToken }),
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.message || 'Failed to authenticate with server');
        }

        const data = await response.json();
        console.log('Authentication successful:', data);

        // Redirect according to response data
        if (data.redirect) {
            window.location.href = data.redirect;
        }

        return data;
    } catch (error) {
        console.error('Backend authentication error:', error);
        throw error;
    }
}

/**
 * Reset password for user email
 * @param {string} email - User email
 * @returns {Promise} - Password reset promise
 */
async function resetPassword(email) {
    try {
        await firebase.auth().sendPasswordResetEmail(email);
        console.log('Password reset email sent to:', email);
        return true;
    } catch (error) {
        console.error('Error sending password reset:', error.code, error.message);
        throw error;
    }
}

// Initialize Firebase Auth when DOM is loaded
document.addEventListener('DOMContentLoaded', initFirebaseAuth); 