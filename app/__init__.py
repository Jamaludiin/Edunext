import os
from flask import Flask
from flask_bootstrap import Bootstrap5
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from dotenv import load_dotenv
from datetime import timedelta
from app.models import db
from app.utils.auth import initialize_firebase

load_dotenv()


def create_app(test_config=None):
    """Create and configure the Flask application using an application factory."""

    # Create app instance
    app = Flask(__name__, instance_relative_config=True)

    # Configure app
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", "dev_key"),
        UPLOAD_FOLDER=os.path.join(app.static_folder, "uploads"),
        MAX_CONTENT_LENGTH=100
        * 1024
        * 1024,  # 100MB max upload size to prevent "Request Entity Too Large" errors
        PERMANENT_SESSION_LIFETIME=timedelta(days=1),  # Session persists for 1 day
        # Database configuration
        SQLALCHEMY_DATABASE_URI=os.getenv(
            "DATABASE_URI", "mysql://root:1234@localhost:3306/learning_assistance"
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        # Firebase configuration
        FIREBASE_ADMIN_SDK_PATH=os.path.join(
            os.getcwd(),
            "learning-assistance-d4c04-firebase-adminsdk-fbsvc-7cfc7f4619.json",
        ),
    )

    # Override config with test config if provided
    if test_config:
        app.config.update(test_config)

    # Initialize extensions
    bootstrap = Bootstrap5(app)
    csrf = CSRFProtect(app)

    # Initialize database
    db.init_app(app)
    migrate = Migrate(app, db)

    # Create upload folders if they don't exist
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(
        os.path.join(app.static_folder, "uploads", "knowledge_base"), exist_ok=True
    )

    # Initialize Firebase Admin SDK
    with app.app_context():
        initialize_firebase()
        # Create database tables if they don't exist
        db.create_all()

    # Set session to be permanent by default
    @app.before_request
    def make_session_permanent():
        from flask import session

        session.permanent = True

    # Register blueprints
    from app.routes import main_bp

    app.register_blueprint(main_bp)

    # Register auth blueprint
    from app.auth.routes import auth_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")

    # Register admin blueprint
    from app.admin.routes import admin_bp

    app.register_blueprint(admin_bp, url_prefix="/admin")

    # Register chat blueprint
    from app.chat.routes import chat_bp

    app.register_blueprint(chat_bp, url_prefix="/chat")

    # Register dashboard blueprint
    from app.dashboard.routes import dashboard_bp

    app.register_blueprint(dashboard_bp, url_prefix="/dashboard")

    # Context processor for template variables
    @app.context_processor
    def inject_template_vars():
        from flask import session
        from app.models import User, Subject

        # Default values
        context = {"has_registered_subjects": False, "registered_subjects": []}

        # Get current user
        user_id = session.get("user_id")
        if user_id:
            user = User.query.filter_by(firebase_uid=user_id).first()
            if user and user.role == "student":
                # Get subjects student is enrolled in
                enrolled_subjects = [
                    enrollment.subject for enrollment in user.enrolled_subjects
                ]
                context["has_registered_subjects"] = len(enrolled_subjects) > 0
                context["registered_subjects"] = enrolled_subjects

        return context

    return app
