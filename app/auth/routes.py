from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    current_app,
    jsonify,
)
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, SelectField
from wtforms.validators import DataRequired, Email, Length, EqualTo, ValidationError
from app.models import db, User
from app.utils.auth import (
    verify_firebase_token,
    create_user_session,
    set_user_session,
    verify_token,
    initialize_firebase,
)
from datetime import datetime
import os
import firebase_admin
from firebase_admin import auth as firebase_auth
import json

auth_bp = Blueprint("auth", __name__, template_folder="templates")


class LoginForm(FlaskForm):
    """Form for user login."""

    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField(
        "Password",
        validators=[DataRequired()],
        render_kw={"autocomplete": "current-password"},
    )
    submit = SubmitField("Login")


class SignupForm(FlaskForm):
    """Form for user registration."""

    email = StringField(
        "Email",
        validators=[DataRequired(), Email()],
        render_kw={"autocomplete": "email"},
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired(), Length(min=6)],
        render_kw={"autocomplete": "new-password"},
    )
    confirm_password = PasswordField(
        "Confirm Password",
        validators=[
            DataRequired(),
            EqualTo("password", message="Passwords must match"),
        ],
        render_kw={"autocomplete": "new-password"},
    )
    name = StringField(
        "Name", validators=[DataRequired()], render_kw={"autocomplete": "name"}
    )
    submit = SubmitField("Sign Up")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Handle user login."""
    # Clear any existing session
    if "user_id" in session:
        session.clear()

    form = LoginForm()

    if form.validate_on_submit():
        # This is handled by Firebase on the client-side
        # We don't actually process the form data here
        pass

    return render_template("auth/login.html", form=form)


@auth_bp.route("/forgot-password")
def forgot_password():
    """Handle password reset requests."""
    return render_template("auth/forgot_password.html")

@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    """Handle user registration."""
    # Clear any existing session
    if "user_id" in session:
        session.clear()

    form = SignupForm()

    if form.validate_on_submit():
        # This is handled by Firebase on the client-side
        # We don't actually process the form data here
        pass

    return render_template("auth/signup.html", form=form)


@auth_bp.route("/verify-token", methods=["POST"])
def verify_token():
    """Verify Firebase token after email/password login."""
    try:
        # Get the ID token from request
        id_token = request.json.get("idToken")

        current_app.logger.info("Received email/password login request")

        if not id_token:
            current_app.logger.error("No ID token provided")
            return jsonify({"error": "No ID token provided"}), 400

        # Additional user data from signup
        role = request.json.get("role", "student")  # Default to student
        name = request.json.get("name")

        current_app.logger.info(f"Processing login with role: {role}, name: {name}")

        # For signup, always force role to be student
        if request.json.get("is_signup"):
            role = "student"
            current_app.logger.info("Signup detected, forcing role to student")

        # Verify the token
        decoded_token = verify_firebase_token(id_token)

        if not decoded_token:
            current_app.logger.error("Failed to verify Firebase token")
            return jsonify({"error": "Invalid ID token"}), 401

        current_app.logger.info(f"Token verified successfully: {decoded_token}")

        # Get user info from the token
        firebase_uid = decoded_token.get("uid")
        email = decoded_token.get("email")

        if not email:
            current_app.logger.error("Email not found in token")
            return jsonify({"error": "Email not found in token"}), 400

        current_app.logger.info(
            f"User info from token - UID: {firebase_uid}, Email: {email}"
        )

        # Check if user exists in our database
        user = User.query.filter_by(firebase_uid=firebase_uid).first()

        if user:
            current_app.logger.info(f"Existing user found: {user.email}")
            # If this is a signup request with role info, update the user's role
            if role and role != user.role:
                user.role = role
                current_app.logger.info(f"Updated user role to: {role}")

            # If name was provided, update it
            if name and name != user.name:
                user.name = name
                current_app.logger.info(f"Updated user name to: {name}")
        else:
            current_app.logger.info("Creating new user")
            # First-time login, create the user
            # Use name from token, parameter, or fall back to email prefix
            display_name = name or decoded_token.get("name") or email.split("@")[0]
            user_role = role or "student"  # Default role

            user = User(
                firebase_uid=firebase_uid,
                email=email,
                name=display_name,
                role=user_role,
            )
            db.session.add(user)
            try:
                db.session.commit()
                current_app.logger.info(
                    f"New user created: {user.email}, Role: {user.role}"
                )
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Failed to create user: {e}")
                return jsonify({"error": f"Database error: {str(e)}"}), 500

        # Update last login time
        user.last_login = datetime.utcnow()
        db.session.commit()

        # Create user session
        user_data = {
            "uid": user.firebase_uid,
            "email": user.email,
            "name": user.name,
            "role": user.role,
        }
        create_user_session(user_data)
        current_app.logger.info(f"Session created for user: {user.email}")

        # Return success response with next URL
        if user.role == "admin":
            next_url = url_for("admin.dashboard")
        else:
            next_url = url_for("dashboard.student_dashboard")

        return jsonify(
            {
                "success": True,
                "user": {"name": user.name, "role": user.role},
                "next_url": next_url,
            }
        )
    except Exception as e:
        current_app.logger.error(f"Unexpected error in verify_token: {e}")
        return jsonify({"error": f"Authentication error: {str(e)}"}), 500


@auth_bp.route("/verify-social", methods=["POST"])
def verify_social_login():
    """Verify Firebase token after social sign-in (Google/Apple)."""
    try:
        # Get the auth header (Bearer token)
        auth_header = request.headers.get("Authorization")

        current_app.logger.info("Received social login request")

        if not auth_header or not auth_header.startswith("Bearer "):
            current_app.logger.error("No bearer token provided in request")
            return jsonify({"error": "No bearer token provided"}), 400

        # Extract the token
        id_token = auth_header.split(" ")[1]
        current_app.logger.info(
            f"Extracted token of length {len(id_token)} chars, attempting verification"
        )

        # Ensure Firebase is properly initialized
        initialize_firebase()

        # Verify the token with more detailed error handling
        try:
            decoded_token = verify_firebase_token(id_token)
            if not decoded_token:
                current_app.logger.error(
                    "Firebase returned null for token verification"
                )
                return (
                    jsonify({"error": "Invalid ID token - verification returned null"}),
                    401,
                )
        except Exception as e:
            current_app.logger.error(f"Token verification exception: {str(e)}")
            return jsonify({"error": f"Token verification failed: {str(e)}"}), 401

        current_app.logger.info(
            f"Token verified successfully. Token contains uid: {decoded_token.get('uid', 'no-uid')}"
        )

        # Get user info from the token
        firebase_uid = decoded_token.get("uid")
        email = decoded_token.get("email")
        name = decoded_token.get("name") or decoded_token.get("display_name")

        current_app.logger.info(
            f"User info from token - UID: {firebase_uid}, Email: {email}, Name: {name}"
        )

        if not email:
            current_app.logger.error("Email not found in token")
            return jsonify({"error": "Email not found in token"}), 400

        # Check if user exists in our database
        user = User.query.filter_by(firebase_uid=firebase_uid).first()

        if user:
            current_app.logger.info(f"Existing user found: {user.email}")
            # Update user information if needed
            if name and name != user.name:
                user.name = name
                current_app.logger.info(f"Updated user name to: {name}")
        else:
            current_app.logger.info("Creating new user from social login")
            # For social login signups, always set role to student
            role = "student"

            # For social logins, extract name from token or use email prefix
            display_name = name if name else email.split("@")[0]

            # Create the user
            user = User(
                firebase_uid=firebase_uid,
                email=email,
                name=display_name,
                role=role,
            )
            db.session.add(user)
            try:
                db.session.commit()
                current_app.logger.info(f"New user created: {user.email}")
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Failed to create user: {e}")
                return jsonify({"error": f"Database error: {str(e)}"}), 500

        # Update last login time
        user.last_login = datetime.utcnow()
        db.session.commit()

        # Set user session directly
        set_user_session(user)
        current_app.logger.info(f"Session created for user: {user.email}")

        # Return success response with next URL
        if user.role == "admin":
            next_url = url_for("admin.dashboard")
        else:
            next_url = url_for("dashboard.student_dashboard")

        return jsonify(
            {
                "success": True,
                "user": {"name": user.name, "role": user.role},
                "next_url": next_url,
            }
        )
    except Exception as e:
        current_app.logger.error(f"Unexpected error in verify_social_login: {str(e)}")
        return jsonify({"error": f"Authentication error: {str(e)}"}), 500


@auth_bp.route("/logout")
def logout():
    """Handle user logout."""
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/debug")
def debug_auth():
    """Debug route to check Firebase configuration."""
    debug_info = {}

    # Get server time
    debug_info["server_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Check Firebase Admin SDK path
    firebase_admin_sdk_path = current_app.config.get("FIREBASE_ADMIN_SDK_PATH")
    debug_info["firebase_admin_sdk_path"] = firebase_admin_sdk_path

    # Check if file exists
    file_exists = False
    credentials_valid = False
    credentials_content = {}

    if firebase_admin_sdk_path:
        file_exists = os.path.isfile(firebase_admin_sdk_path)

        if file_exists:
            try:
                # Try to read and parse the credentials file
                with open(firebase_admin_sdk_path, "r") as f:
                    credentials_json = json.load(f)

                # Check if essential fields exist
                required_fields = [
                    "type",
                    "project_id",
                    "private_key_id",
                    "private_key",
                    "client_email",
                    "client_id",
                ]
                credentials_valid = all(
                    field in credentials_json for field in required_fields
                )

                # For security, only share partial content
                credentials_content = {
                    "type": credentials_json.get("type", "Not found"),
                    "project_id": credentials_json.get("project_id", "Not found"),
                    "private_key_id": (
                        credentials_json.get("private_key_id", "Not found")[:8] + "..."
                        if credentials_json.get("private_key_id")
                        else "Not found"
                    ),
                    "client_email": credentials_json.get("client_email", "Not found"),
                    "auth_uri": credentials_json.get("auth_uri", "Not found"),
                    "token_uri": credentials_json.get("token_uri", "Not found"),
                }
            except Exception as e:
                current_app.logger.error(f"Error reading credentials file: {str(e)}")
                credentials_content = {"error": str(e)}

    debug_info["file_exists"] = file_exists
    debug_info["credentials_valid"] = credentials_valid
    debug_info["credentials_content"] = credentials_content

    # Check if Firebase is initialized
    firebase_initialized = False
    firebase_apps_count = 0

    try:
        # Check if Firebase Admin has any apps initialized
        firebase_apps_count = len(firebase_admin._apps)
        firebase_initialized = firebase_apps_count > 0
    except Exception as e:
        current_app.logger.error(f"Error checking Firebase initialization: {str(e)}")

    debug_info["firebase_initialized"] = firebase_initialized
    debug_info["firebase_apps_count"] = firebase_apps_count

    # Try to list users from Firebase
    firebase_users = []
    auth_error = None

    if firebase_initialized:
        try:
            # List users from Firebase
            page = firebase_auth.list_users()
            for user in page.users:
                firebase_users.append(
                    {
                        "uid": user.uid,
                        "email": user.email,
                        "provider_id": [p.provider_id for p in user.provider_data],
                    }
                )
                if len(firebase_users) >= 5:  # Limit to 5 users
                    break
        except Exception as e:
            auth_error = str(e)
            current_app.logger.error(f"Error listing Firebase users: {str(e)}")

    debug_info["firebase_users"] = firebase_users
    debug_info["auth_error"] = auth_error

    # Get OAuth configuration
    debug_info["oauth_config"] = {
        "web_client_id": current_app.config.get("FIREBASE_WEB_CLIENT_ID", "Not set"),
        "web_api_key": current_app.config.get("FIREBASE_WEB_API_KEY", "Not set"),
    }

    # Get app environment
    debug_info["app_environment"] = {
        "debug": current_app.debug,
        "env": current_app.env,
        "testing": current_app.testing,
        "host": request.host,
        "url_root": request.url_root,
    }

    # Get database user count
    users_count = 0
    try:
        users_count = User.query.count()
    except Exception as e:
        current_app.logger.error(f"Error counting users: {str(e)}")

    debug_info["users_count"] = users_count

    return render_template("auth/debug.html", debug_info=debug_info)


@auth_bp.route("/email_login", methods=["POST"])
def email_login():
    """Handle email/password login"""
    try:
        # Get the ID token sent by the client
        id_token = request.json.get("idToken")
        if not id_token:
            return jsonify({"success": False, "error": "No ID token provided"}), 400

        # Verify the ID token
        current_app.logger.info(f"Verifying ID token (length {len(id_token)})")
        decoded_token = verify_firebase_token(id_token)

        # Get user data from decoded token
        firebase_uid = decoded_token.get("uid")
        email = decoded_token.get("email")
        name = decoded_token.get("name", "User")

        current_app.logger.info(f"User authenticated: {email} ({firebase_uid})")

        # Check if user exists in database
        user = User.query.filter_by(firebase_uid=firebase_uid).first()

        if not user:
            # Create new user
            current_app.logger.info(f"Creating new user in database: {email}")
            user = User(
                firebase_uid=firebase_uid, email=email, name=name, role="student"
            )
            db.session.add(user)
            db.session.commit()

        # Set session
        set_user_session(user)

        return jsonify({"success": True, "redirect": url_for("main.index")})

    except Exception as e:
        current_app.logger.error(f"Login error: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


@auth_bp.route("/api/login", methods=["POST"])
def api_login():
    """API endpoint for handling Firebase authentication."""
    # Add CORS headers
    if request.method == "OPTIONS":
        response = jsonify({"status": "success"})
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Methods", "POST")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type")
        return response

    try:
        # Get request data
        request_data = request.get_json()

        if not request_data or "idToken" not in request_data:
            response = jsonify({"success": False, "error": "No ID token provided"})
            response.headers.add("Access-Control-Allow-Origin", "*")
            return response, 400

        id_token = request_data.get("idToken")

        # Verify the Firebase token using the correct function
        try:
            # Ensure Firebase is initialized
            if not initialize_firebase():
                current_app.logger.error(
                    "API Login: Firebase SDK initialization failed"
                )
                response = jsonify(
                    {"success": False, "error": "Firebase SDK initialization failed"}
                )
                response.headers.add("Access-Control-Allow-Origin", "*")
                return response, 500

            decoded_token = verify_firebase_token(id_token)
            if not decoded_token:
                current_app.logger.error("API Login: Token verification failed")
                response = jsonify({"success": False, "error": "Invalid token"})
                response.headers.add("Access-Control-Allow-Origin", "*")
                return response, 401

            firebase_uid = decoded_token["uid"]
            email = decoded_token.get("email", "")
            name = decoded_token.get(
                "name", decoded_token.get("email", "").split("@")[0]
            )

            # Create or update user in database and set session
            user = User.query.filter_by(firebase_uid=firebase_uid).first()

            if user:
                # Update existing user
                user.email = email if email else user.email
                user.name = name if name else user.name
                user.last_login = db.func.now()
                db.session.commit()
                set_user_session(user)
                current_app.logger.info(f"User logged in: {user.email} (ID: {user.id})")
            else:
                # Create new user
                user = User(
                    firebase_uid=firebase_uid,
                    email=email,
                    name=name,
                    role="student",  # Default role
                )
                db.session.add(user)
                db.session.commit()
                set_user_session(user)
                current_app.logger.info(
                    f"New user created and logged in: {user.email} (ID: {user.id})"
                )

            response = jsonify(
                {
                    "success": True,
                    "user": {
                        "id": user.id,
                        "email": user.email,
                        "name": user.name,
                        "role": user.role,
                    },
                }
            )
            response.headers.add("Access-Control-Allow-Origin", "*")
            return response

        except Exception as e:
            current_app.logger.error(f"API Login: Token verification failed: {str(e)}")
            response = jsonify({"success": False, "error": f"Invalid token: {str(e)}"})
            response.headers.add("Access-Control-Allow-Origin", "*")
            return response, 401

    except Exception as e:
        current_app.logger.error(f"API login error: {str(e)}")
        response = jsonify({"success": False, "error": str(e)})
        response.headers.add("Access-Control-Allow-Origin", "*")
        return response, 500


@auth_bp.route("/api/logout", methods=["POST", "OPTIONS"])
def api_logout():
    """API endpoint for logging out users."""
    # Add CORS headers
    if request.method == "OPTIONS":
        response = jsonify({"status": "success"})
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Methods", "POST")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type")
        return response

    try:
        # Clear user session
        session.clear()
        current_app.logger.info("User logged out via API")
        response = jsonify({"success": True, "message": "Successfully logged out"})
        response.headers.add("Access-Control-Allow-Origin", "*")
        return response
    except Exception as e:
        current_app.logger.error(f"API logout error: {str(e)}")
        response = jsonify({"success": False, "error": str(e)})
        response.headers.add("Access-Control-Allow-Origin", "*")
        return response, 500


@auth_bp.route("/token-debug", methods=["POST"])
def token_debug():
    """Debug endpoint to save and analyze token without full verification."""
    try:
        token_data = request.get_json()

        if not token_data or "idToken" not in token_data:
            return jsonify({"success": False, "error": "No token provided"}), 400

        token = token_data["idToken"]

        # Store token length and prefix for logging
        token_info = {
            "received_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "token_length": len(token),
            "token_prefix": token[:20] + "..." if len(token) > 20 else token,
        }

        # Check if Firebase is initialized and credentials exist
        cred_path = current_app.config.get("FIREBASE_ADMIN_SDK_PATH")
        token_info["credentials_path"] = cred_path
        token_info["credentials_exist"] = (
            os.path.isfile(cred_path) if cred_path else False
        )

        # Try to look at the token header (without verifying signature)
        try:
            import base64
            import json

            # JWT structure is header.payload.signature
            header_encoded = token.split(".")[0]
            # Add padding if needed
            padding = "=" * (4 - len(header_encoded) % 4)
            header_decoded = base64.b64decode(header_encoded + padding)
            header_json = json.loads(header_decoded)
            token_info["header"] = header_json
        except Exception as e:
            token_info["header_parse_error"] = str(e)

        # Try to initialize Firebase
        try:
            init_result = initialize_firebase()
            token_info["firebase_initialized"] = init_result
        except Exception as e:
            token_info["firebase_init_error"] = str(e)

        # Try token verification if Firebase is initialized
        if token_info.get("firebase_initialized"):
            try:
                from firebase_admin import auth

                decoded_token = auth.verify_id_token(token)
                token_info["verification"] = "success"
                token_info["decoded"] = {
                    "uid": decoded_token.get("uid"),
                    "email": decoded_token.get("email"),
                    "name": decoded_token.get("name"),
                    "issued_at": decoded_token.get("iat"),
                    "expires_at": decoded_token.get("exp"),
                    "issuer": decoded_token.get("iss"),
                    "audience": decoded_token.get("aud"),
                }
            except Exception as e:
                token_info["verification"] = "failed"
                token_info["verification_error"] = str(e)

        # Log all the information for debugging
        current_app.logger.info(f"Token debug information: {token_info}")

        return jsonify({"success": True, "token_analysis": token_info})

    except Exception as e:
        current_app.logger.error(f"Token debug error: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


@auth_bp.route("/debug-firebase", methods=["GET"])
def debug_firebase():
    """Debug route to check Firebase initialization and token verification."""
    debug_info = {
        "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "firebase_admin_sdk_path": current_app.config.get(
            "FIREBASE_ADMIN_SDK_PATH", "Not set"
        ),
    }

    # Check if Firebase Admin SDK exists
    if debug_info["firebase_admin_sdk_path"]:
        debug_info["sdk_file_exists"] = os.path.isfile(
            debug_info["firebase_admin_sdk_path"]
        )
    else:
        debug_info["sdk_file_exists"] = False

    # Check Firebase initialization
    try:
        from app.utils.auth import initialize_firebase

        init_result = initialize_firebase()
        debug_info["firebase_initialized"] = init_result
    except Exception as e:
        debug_info["firebase_initialized"] = False
        debug_info["init_error"] = str(e)

    # Check if Firebase Admin apps exist
    try:
        apps_count = len(firebase_admin._apps)
        debug_info["firebase_apps_count"] = apps_count
        if apps_count > 0:
            debug_info["app_names"] = list(firebase_admin._apps.keys())
    except Exception as e:
        debug_info["firebase_apps_error"] = str(e)

    # Display test ID token verification info
    if request.args.get("id_token"):
        try:
            from app.utils.auth import verify_firebase_token

            token = request.args.get("id_token")
            debug_info["token_length"] = len(token)
            decoded = verify_firebase_token(token)
            if decoded:
                debug_info["token_verified"] = True
                debug_info["token_info"] = {
                    "uid": decoded.get("uid"),
                    "email": decoded.get("email"),
                    "name": decoded.get("name"),
                    "expires": decoded.get("exp"),
                    "issued": decoded.get("iat"),
                }
            else:
                debug_info["token_verified"] = False
        except Exception as e:
            debug_info["token_verification_error"] = str(e)

    return jsonify(debug_info)
