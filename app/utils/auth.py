import os
import firebase_admin
from firebase_admin import credentials, auth, initialize_app
from flask import request, session, current_app, redirect, url_for, flash
from functools import wraps


def initialize_firebase():
    """Initialize Firebase Admin SDK if not already initialized."""
    if (
        not hasattr(initialize_firebase, "initialized")
        or not initialize_firebase.initialized
    ):
        try:
            cred_path = current_app.config.get("FIREBASE_ADMIN_SDK_PATH")
            if not cred_path or not os.path.exists(cred_path):
                current_app.logger.error(
                    f"Firebase credentials file not found at {cred_path}"
                )
                return False

            cred = credentials.Certificate(cred_path)
            initialize_app(cred)
            initialize_firebase.initialized = True
            current_app.logger.info("Firebase Admin SDK initialized successfully")
            return True
        except Exception as e:
            current_app.logger.error(f"Failed to initialize Firebase: {str(e)}")
            initialize_firebase.initialized = False
            return False
    return True


def verify_firebase_token(id_token):
    """Verify the Firebase ID token."""
    try:
        # Ensure Firebase is initialized
        if not initialize_firebase():
            current_app.logger.error("Failed to initialize Firebase SDK")
            return None

        # Log token length for debugging
        current_app.logger.info(f"Verifying token of length: {len(id_token)}")

        # Verify token with clock tolerance (5 seconds) to handle clock skew
        decoded_token = auth.verify_id_token(
            id_token, check_revoked=True, clock_skew_seconds=5
        )
        current_app.logger.info(
            f"Token verified successfully. Token UID: {decoded_token.get('uid', 'No UID')}"
        )
        return decoded_token
    except ValueError as e:
        current_app.logger.error(f"Token verification failed (ValueError): {str(e)}")
        return None
    except auth.InvalidIdTokenError as e:
        current_app.logger.error(f"Invalid ID token: {str(e)}")
        return None
    except auth.ExpiredIdTokenError as e:
        current_app.logger.error(f"Expired ID token: {str(e)}")
        return None
    except auth.RevokedIdTokenError as e:
        current_app.logger.error(f"Revoked ID token: {str(e)}")
        return None
    except Exception as e:
        current_app.logger.error(f"Unexpected token verification error: {str(e)}")
        return None


def verify_token(id_token):
    """Verify Firebase ID token and return decoded token."""
    try:
        # Ensure Firebase is initialized
        if not initialize_firebase():
            raise Exception("Firebase SDK not initialized")

        # Verify the token with clock tolerance
        decoded_token = auth.verify_id_token(
            id_token, check_revoked=True, clock_skew_seconds=5
        )
        return decoded_token
    except Exception as e:
        current_app.logger.error(f"Token verification failed: {str(e)}")
        raise Exception(f"Invalid authentication token: {str(e)}")


def set_user_session(user):
    """Set user session data."""
    session["user_id"] = user.firebase_uid
    session["firebase_uid"] = user.firebase_uid
    session["user_db_id"] = user.id
    session["email"] = user.email
    session["user_display_name"] = user.name
    session["name"] = user.name
    session["role"] = user.role
    session["authenticated"] = True
    session.permanent = True

    current_app.logger.info(
        f"User session set for {user.email} (ID: {user.id}, Firebase UID: {user.firebase_uid}, Role: {user.role})"
    )


def create_user_session(user_data):
    """Create a session for the authenticated user."""
    from app.models import User, db

    # Extract firebase_uid from user_data
    firebase_uid = user_data.get("uid")

    if not firebase_uid:
        current_app.logger.error("Missing uid in user_data")
        return None

    # Check if user exists in database
    user = User.query.filter_by(firebase_uid=firebase_uid).first()

    if not user:
        # Create new user in database
        user = User(
            firebase_uid=firebase_uid,
            email=user_data.get("email"),
            name=user_data.get("name"),
            role=user_data.get("role", "student"),  # Default role
        )
        db.session.add(user)
        db.session.commit()
        current_app.logger.info(f"Created new user: {user.email} (ID: {user.id})")

    # Set session data
    set_user_session(user)

    return user


def login_required(f=None, role=None):
    """Decorator to require login for routes with optional role restriction.

    Can be used in two ways:
    @login_required  # Just requires authentication
    @login_required(role="admin")  # Requires authentication and specific role
    """

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get("authenticated"):
                flash("Please log in to access this page.", "warning")
                return redirect(url_for("auth.login"))

            # If role is specified, check if user has that role
            if role and session.get("role") != role:
                flash("You do not have permission to access this page.", "danger")
                return redirect(url_for("main.index"))

            return f(*args, **kwargs)

        return decorated_function

    # Handle both @login_required and @login_required(role="admin") syntax
    if f is None:
        return decorator
    return decorator(f)
