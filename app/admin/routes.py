from flask import (
    Blueprint,
    render_template,
    redirect,
    url_for,
    flash,
    request,
    session,
    current_app,
    Response,
    jsonify,
)
from app.utils.auth import login_required
from app.models import (
    db,
    User,
    KnowledgeDocument,
    Subject,
    UserSubject,
    Quiz,
    Question,
    Answer,
)
from app.admin.forms import QuizForm, QuestionForm, AnswerForm
from werkzeug.security import generate_password_hash
import firebase_admin
from firebase_admin import auth as firebase_auth
from datetime import datetime
from app.utils.rag_chain import get_vector_db_status, generate_quiz_questions
import os
import io
import pymysql
from sqlalchemy import text

admin_bp = Blueprint("admin", __name__, template_folder="templates")


def _subject_has_vector_db(subject_id):
    """Check if a subject has an associated vector database."""
    vector_db_status = get_vector_db_status()
    subjects_status = vector_db_status.get("subjects", {})
    return (
        subject_id in subjects_status
        and subjects_status[subject_id].get("status") == "Ready"
    )


@admin_bp.before_request
def ensure_user_db_id():
    """Ensure user_db_id is available in session for all admin routes."""
    if session.get("authenticated") and session.get("user_db_id") is None:
        # Try to recover from firebase_uid
        firebase_uid = session.get("user_id")
        if firebase_uid:
            from app.models import User

            user = User.query.filter_by(firebase_uid=firebase_uid).first()
            if user:
                session["user_db_id"] = user.id
                current_app.logger.info(
                    f"Recovered missing user_db_id ({user.id}) for admin route using firebase_uid: {firebase_uid}"
                )


@admin_bp.route("/")
@login_required(role="admin")
def dashboard():
    """Admin dashboard main page."""
    # Get user and document counts
    user_count = User.query.count()
    document_count = KnowledgeDocument.query.count()
    subject_count = Subject.query.count()

    # Get stats by user role
    student_count = User.query.filter_by(role="student").count()
    admin_count = User.query.filter_by(role="admin").count()

    # Get recent users
    recent_users = User.query.order_by(User.created_at.desc()).limit(5).all()

    # Get recent documents
    recent_documents = (
        KnowledgeDocument.query.order_by(KnowledgeDocument.upload_date.desc())
        .limit(5)
        .all()
    )

    # Get recent subjects
    recent_subjects = Subject.query.order_by(Subject.created_at.desc()).limit(5).all()

    return render_template(
        "admin/dashboard.html",
        user_count=user_count,
        document_count=document_count,
        student_count=student_count,
        admin_count=admin_count,
        subject_count=subject_count,
        recent_users=recent_users,
        recent_documents=recent_documents,
        recent_subjects=recent_subjects,
    )


@admin_bp.route("/users")
@login_required(role="admin")
def users():
    """Admin user management page."""
    users = User.query.all()
    return render_template("admin/users.html", users=users)


@admin_bp.route("/users/<int:user_id>/toggle-role", methods=["POST"])
@login_required(role="admin")
def toggle_user_role(user_id):
    """Toggle a user's role between student and admin."""
    user = User.query.get_or_404(user_id)

    # Don't allow changing your own role
    if user.firebase_uid == session.get("user_id"):
        flash("You cannot change your own role.", "warning")
        return redirect(url_for("admin.users"))

    # Toggle role
    if user.role == "student":
        user.role = "admin"
        flash(f"User {user.email} is now an administrator.", "success")
    else:
        user.role = "student"
        flash(f"User {user.email} is now a student.", "success")

    db.session.commit()
    return redirect(url_for("admin.users"))


@admin_bp.route("/documents")
@login_required(role="admin")
def documents():
    """Admin document management page."""
    documents = KnowledgeDocument.query.all()
    subjects = Subject.query.filter_by(is_active=True).order_by(Subject.code).all()

    # Get vector DB status for general DB and all subjects
    vector_db_status = get_vector_db_status()

    return render_template(
        "admin/documents.html",
        documents=documents,
        subjects=subjects,
        vector_db_status=vector_db_status,
    )


@admin_bp.route("/documents/<int:document_id>/delete", methods=["POST"])
@login_required(role="admin")
def delete_document(document_id):
    """Delete a document as an admin."""
    document = KnowledgeDocument.query.get_or_404(document_id)

    try:
        # Delete the document from database
        db.session.delete(document)
        db.session.commit()

        flash("Document deleted successfully.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting document: {str(e)}")
        flash(f"Error deleting document: {str(e)}", "danger")

    return redirect(url_for("admin.documents"))


@admin_bp.route("/users/<int:user_id>/update", methods=["POST"])
@login_required(role="admin")
def update_user(user_id):
    """Update user information."""
    user = User.query.get_or_404(user_id)

    # Get form data
    name = request.form.get("name")
    email = request.form.get("email")
    role = request.form.get("role")

    if not name or not email or not role:
        flash("All fields are required", "danger")
        return redirect(url_for("admin.users"))

    # Check if trying to change the last admin
    if user.role == "admin" and role == "student":
        admin_count = User.query.filter_by(role="admin").count()
        if admin_count <= 1:
            flash("Cannot change the last administrator to student", "danger")
            return redirect(url_for("admin.users"))

    # Update user information
    user.name = name
    user.email = email
    user.role = role

    try:
        db.session.commit()
        flash(f"User {user.name} updated successfully", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating user: {str(e)}")
        flash(f"Error updating user: {str(e)}", "danger")

    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required(role="admin")
def delete_user(user_id):
    """Delete a user."""
    user = User.query.get_or_404(user_id)

    # Don't allow deleting your own account
    if user.firebase_uid == session.get("user_id"):
        flash("You cannot delete your own account", "danger")
        return redirect(url_for("admin.users"))

    # Check if trying to delete the last admin
    if user.role == "admin":
        admin_count = User.query.filter_by(role="admin").count()
        if admin_count <= 1:
            flash("Cannot delete the last administrator", "danger")
            return redirect(url_for("admin.users"))

    try:
        # Delete user from Firebase if possible
        if user.firebase_uid:
            try:
                firebase_auth.delete_user(user.firebase_uid)
            except Exception as e:
                current_app.logger.warning(f"Could not delete Firebase user: {str(e)}")
                # Continue with local deletion even if Firebase deletion fails
        
        # Delete chat conversations and messages associated with this user
        from app.models import ChatConversation, ChatMessage
        # Get all conversation IDs for this user
        conversations = ChatConversation.query.filter_by(user_id=user.id).all()
        conversation_ids = [conv.id for conv in conversations]
        
        # Delete messages from these conversations first
        if conversation_ids:
            ChatMessage.query.filter(ChatMessage.conversation_id.in_(conversation_ids)).delete(synchronize_session=False)
            # Then delete the conversations
            ChatConversation.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        
        # Delete quiz attempts and related records
        from app.models import QuizAttempt, AttemptAnswer
        # Get all attempt IDs for this user
        attempts = QuizAttempt.query.filter_by(user_id=user.id).all()
        attempt_ids = [attempt.id for attempt in attempts]
        
        # Delete attempt answers first
        if attempt_ids:
            AttemptAnswer.query.filter(AttemptAnswer.attempt_id.in_(attempt_ids)).delete(synchronize_session=False)
            # Then delete the attempts
            QuizAttempt.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        
        # Delete subject enrollments for this user
        UserSubject.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        
        # Delete knowledge documents associated with the user
        KnowledgeDocument.query.filter_by(uploaded_by=user.id).delete(synchronize_session=False)
        
        # Delete from database
        db.session.delete(user)
        db.session.commit()
        flash(f"User {user.name} deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting user: {str(e)}")
        flash(f"Error deleting user: {str(e)}", "danger")

    return redirect(url_for("admin.users"))


@admin_bp.route("/users/create", methods=["POST"])
@login_required(role="admin")
def create_user():
    """Create a new user."""
    name = request.form.get("name")
    email = request.form.get("email")
    password = request.form.get("password")
    role = request.form.get("role", "student")

    if not name or not email or not password:
        flash("All fields are required", "danger")
        return redirect(url_for("admin.users"))

    # Check if user already exists
    if User.query.filter_by(email=email).first():
        flash(f"User with email {email} already exists", "danger")
        return redirect(url_for("admin.users"))

    try:
        # Create user in Firebase
        firebase_user = firebase_auth.create_user(
            email=email, password=password, display_name=name
        )

        # Create user in our database
        user = User(
            firebase_uid=firebase_user.uid,
            email=email,
            name=name,
            role=role,
            created_at=datetime.utcnow(),
        )

        db.session.add(user)
        db.session.commit()
        flash(f"User {name} created successfully", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating user: {str(e)}")
        flash(f"Error creating user: {str(e)}", "danger")

    return redirect(url_for("admin.users"))


@admin_bp.route("/subjects")
@login_required(role="admin")
def subjects():
    """Admin subject management page."""
    subjects = Subject.query.order_by(Subject.code).all()

    # Get count of documents per subject
    subject_stats = {}
    for subject in subjects:
        doc_count = KnowledgeDocument.query.filter_by(subject_id=subject.id).count()
        has_vector_db = _subject_has_vector_db(subject.id)
        subject_stats[subject.id] = {
            "doc_count": doc_count,
            "has_vector_db": has_vector_db,
        }

    return render_template(
        "admin/subjects.html", subjects=subjects, subject_stats=subject_stats
    )


@admin_bp.route("/subjects/create", methods=["POST"])
@login_required(role="admin")
def create_subject():
    """Create a new subject."""
    name = request.form.get("name")
    code = request.form.get("code")
    description = request.form.get("description")

    if not name or not code:
        flash("Subject name and code are required", "danger")
        return redirect(url_for("admin.subjects"))

    # Check if subject with code already exists
    if Subject.query.filter_by(code=code).first():
        flash(f"Subject with code {code} already exists", "danger")
        return redirect(url_for("admin.subjects"))

    try:
        # Get user's database ID from Firebase UID
        firebase_uid = session.get("user_id")
        user = User.query.filter_by(firebase_uid=firebase_uid).first()

        if not user:
            flash("User not found. Please log out and log in again.", "danger")
            return redirect(url_for("admin.subjects"))

        # Create subject in database using user's database ID
        subject = Subject(
            name=name,
            code=code,
            description=description,
            created_by=user.id,
            created_at=datetime.utcnow(),
        )

        db.session.add(subject)
        db.session.commit()
        flash(f"Subject {name} created successfully", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating subject: {str(e)}")
        flash(f"Error creating subject: {str(e)}", "danger")

    return redirect(url_for("admin.subjects"))


@admin_bp.route("/subjects/<int:subject_id>/update", methods=["POST"])
@login_required(role="admin")
def update_subject(subject_id):
    """Update subject information."""
    subject = Subject.query.get_or_404(subject_id)

    # Get form data
    name = request.form.get("name")
    code = request.form.get("code")
    description = request.form.get("description")
    is_active = request.form.get("is_active") == "on"

    if not name or not code:
        flash("Subject name and code are required", "danger")
        return redirect(url_for("admin.subjects"))

    # Check if code is already taken by another subject
    existing_subject = Subject.query.filter_by(code=code).first()
    if existing_subject and existing_subject.id != subject_id:
        flash(f"Subject code {code} is already in use", "danger")
        return redirect(url_for("admin.subjects"))

    # Update subject information
    subject.name = name
    subject.code = code
    subject.description = description
    subject.is_active = is_active

    try:
        db.session.commit()
        flash(f"Subject {subject.name} updated successfully", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating subject: {str(e)}")
        flash(f"Error updating subject: {str(e)}", "danger")

    return redirect(url_for("admin.subjects"))


@admin_bp.route("/subjects/<int:subject_id>/delete", methods=["POST"])
@login_required(role="admin")
def delete_subject(subject_id):
    """Delete a subject and all related records."""
    subject = Subject.query.get_or_404(subject_id)
    subject_name = subject.name
    subject_code = subject.code

    try:
        # Count related records for logging purposes
        student_count = len(subject.enrolled_students)
        document_count = len(subject.documents)
        
        # Delete all student enrollments for this subject
        UserSubject.query.filter_by(subject_id=subject.id).delete()
        
        # Get all documents for this subject for vector db cleanup
        subject_documents = KnowledgeDocument.query.filter_by(subject_id=subject.id).all()
        document_ids = [doc.id for doc in subject_documents]
        
        # Delete all documents for this subject
        KnowledgeDocument.query.filter_by(subject_id=subject.id).delete()
        
        # Handle any quizzes related to this subject
        quizzes = Quiz.query.filter_by(subject_id=subject.id).all()
        for quiz in quizzes:
            # Delete all questions and answers for this quiz
            questions = Question.query.filter_by(quiz_id=quiz.id).all()
            for question in questions:
                Answer.query.filter_by(question_id=question.id).delete()
            Question.query.filter_by(quiz_id=quiz.id).delete()
        # Delete all quizzes for this subject
        Quiz.query.filter_by(subject_id=subject.id).delete()
        
        # Delete subject from database
        db.session.delete(subject)
        db.session.commit()
        
        # Log detailed deletion information
        current_app.logger.info(
            f"Deleted subject {subject_code} with {student_count} students, "
            f"{document_count} documents, and related quiz data"
        )
        
        flash(f"Subject {subject_name} ({subject_code}) deleted successfully with all related data", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting subject: {str(e)}")
        flash(f"Error deleting subject: {str(e)}", "danger")

    return redirect(url_for("admin.subjects"))


@admin_bp.route("/subjects/<int:subject_id>/students", methods=["GET"])
@login_required(role="admin")
def subject_students(subject_id):
    """View and manage students enrolled in a subject."""
    subject = Subject.query.get_or_404(subject_id)

    # Get all students who are not enrolled in this subject
    enrolled_student_ids = [
        enrollment.user_id for enrollment in subject.enrolled_students
    ]
    available_students = (
        User.query.filter_by(role="student")
        .filter(User.id.notin_(enrolled_student_ids))
        .all()
    )

    return render_template(
        "admin/subject_students.html",
        subject=subject,
        available_students=available_students,
    )


@admin_bp.route("/subjects/<int:subject_id>/enroll", methods=["POST"])
@login_required(role="admin")
def enroll_student(subject_id):
    """Enroll a student in a subject."""
    subject = Subject.query.get_or_404(subject_id)
    student_id = request.form.get("student_id")

    if not student_id:
        flash("Student ID is required", "danger")
        return redirect(url_for("admin.subject_students", subject_id=subject_id))

    # Verify student exists and is a student
    student = User.query.get_or_404(student_id)
    if student.role != "student":
        flash("Only students can be enrolled in subjects", "danger")
        return redirect(url_for("admin.subject_students", subject_id=subject_id))

    # Check if student is already enrolled
    existing_enrollment = UserSubject.query.filter_by(
        user_id=student_id, subject_id=subject_id
    ).first()

    if existing_enrollment:
        flash(f"Student {student.name} is already enrolled in this subject", "warning")
        return redirect(url_for("admin.subject_students", subject_id=subject_id))

    try:
        # Create enrollment
        enrollment = UserSubject(
            user_id=student_id, subject_id=subject_id, enrolled_date=datetime.utcnow()
        )

        db.session.add(enrollment)
        db.session.commit()
        flash(f"Student {student.name} enrolled successfully", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error enrolling student: {str(e)}")
        flash(f"Error enrolling student: {str(e)}", "danger")

    return redirect(url_for("admin.subject_students", subject_id=subject_id))


@admin_bp.route("/subjects/<int:subject_id>/unenroll/<int:user_id>", methods=["POST"])
@login_required(role="admin")
def unenroll_student(subject_id, user_id):
    """Remove a student from a subject."""
    enrollment = UserSubject.query.filter_by(
        user_id=user_id, subject_id=subject_id
    ).first_or_404()

    student_name = enrollment.user.name

    try:
        db.session.delete(enrollment)
        db.session.commit()
        flash(f"Student {student_name} unenrolled successfully", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error unenrolling student: {str(e)}")
        flash(f"Error unenrolling student: {str(e)}", "danger")

    return redirect(url_for("admin.subject_students", subject_id=subject_id))


@admin_bp.route("/subjects/<int:subject_id>/documents")
@login_required(role="admin")
def subject_documents(subject_id):
    """View and manage documents for a subject."""
    subject = Subject.query.get_or_404(subject_id)
    return render_template("admin/subject_documents.html", subject=subject)


@admin_bp.route("/subjects/<int:subject_id>/test-chat")
@login_required(role="admin")
def subject_test_chat(subject_id):
    """Test chat interface for a specific subject."""
    subject = Subject.query.get_or_404(subject_id)

    # Check if subject has documents
    if not subject.documents:
        flash(
            "This subject has no documents. Add documents before testing the chat.",
            "warning",
        )
        return redirect(url_for("admin.subject_documents", subject_id=subject_id))

    # Get vector DB status for the subject
    vector_db_status = get_vector_db_status()
    subject_status = vector_db_status.get("subjects", {}).get(
        subject.id,
        {
            "status": "Not initialized",
            "document_count": len(subject.documents),
            "chunk_count": 0,
        },
    )

    return render_template(
        "admin/subject_test_chat.html", subject=subject, subject_status=subject_status
    )


@admin_bp.route("/subjects/<int:subject_id>/test-chat/download", methods=["POST"])
@login_required(role="admin")
def download_subject_test_chat(subject_id):
    """Download the test chat conversation as a text file."""
    import io
    from datetime import datetime

    # Get the subject details
    subject = Subject.query.get_or_404(subject_id)

    # Get chat logs from the request (this will be sent from the frontend)
    chat_data = request.json
    if not chat_data or "messages" not in chat_data:
        return Response(
            "No chat data available to download", status=400, mimetype="text/plain"
        )

    # Create a formatted text content
    buffer = io.StringIO()
    buffer.write(f"Chat Logs for {subject.code}: {subject.name}\n")
    buffer.write(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    buffer.write("-" * 80 + "\n\n")

    # Write each message
    for message in chat_data["messages"]:
        sender = "AI Assistant" if message["sender"] == "bot" else "Admin"
        timestamp = message.get("timestamp", "Unknown time")
        # Format the timestamp if it's in ISO format
        if "T" in timestamp and "Z" in timestamp:
            try:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                timestamp = dt.strftime("%Y-%m-%d %H:%M:%S")
            except:
                pass  # Keep original timestamp if parsing fails

        buffer.write(f"{sender} ({timestamp}):\n")
        buffer.write(f"{message['content']}\n\n")

    # If context is available, include it
    if "context" in chat_data and chat_data["context"]:
        buffer.write("\n" + "=" * 30 + " SOURCE CONTEXT " + "=" * 30 + "\n\n")
        for ctx in chat_data["context"]:
            buffer.write(f"Source {ctx.get('index', '')}:\n")
            buffer.write(f"{ctx.get('content', '')}\n\n")

    # Create a download response
    buffer.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{subject.code}_chat_log_{timestamp}.txt"

    return Response(
        buffer.getvalue(),
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment;filename={filename}"},
    )


@admin_bp.route("/base-documents")
@login_required(role="admin")
def base_documents():
    """View and manage base vector documents (university-wide knowledge)."""
    # Get base documents (not associated with any specific subject and marked as public)
    base_documents = (
        KnowledgeDocument.query.filter_by(subject_id=None, is_public=True)
        .order_by(KnowledgeDocument.upload_date.desc())
        .all()
    )

    # Get vector DB status for base DB
    vector_db_status = get_vector_db_status()
    base_status = vector_db_status.get("base", {})

    return render_template(
        "admin/base_documents.html", documents=base_documents, base_status=base_status
    )


@admin_bp.route("/base-documents/upload", methods=["POST"])
@login_required(role="admin")
def upload_base_document():
    """Upload a new base vector document (university-wide knowledge)."""
    import uuid
    from werkzeug.utils import secure_filename

    if "documents" not in request.files:
        flash("No files were selected for upload.", "warning")
        return redirect(url_for("admin.base_documents"))

    files = request.files.getlist("documents")
    description = request.form.get("description", "")

    if not files or files[0].filename == "":
        flash("No files selected", "danger")
        return redirect(url_for("admin.base_documents"))

    # Get the current user
    user_id = session.get("user_id")
    user = User.query.filter_by(firebase_uid=user_id).first()

    if not user:
        flash("User not found. Please log out and log in again.", "danger")
        return redirect(url_for("auth.logout"))

    # List to track uploaded files for vector DB creation
    uploaded_paths = []
    uploaded_count = 0

    for file in files:
        # Check if file type is allowed (PDF only for now)
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)

            # Generate a unique filename to prevent overwriting
            unique_filename = f"{uuid.uuid4().hex}_{filename}"

            # Path to save the file
            knowledge_base_path = os.path.join(
                current_app.config["UPLOAD_FOLDER"], "knowledge_base"
            )
            file_path = os.path.join(knowledge_base_path, unique_filename)

            # Create directory if it doesn't exist
            os.makedirs(knowledge_base_path, exist_ok=True)

            # Save the file
            file.save(file_path)

            # Add the path to our list
            uploaded_paths.append(file_path)

            # Create a record in the database
            document = KnowledgeDocument(
                original_filename=filename,
                stored_filename=unique_filename,
                file_path=file_path,
                file_size=os.path.getsize(file_path),
                uploaded_by=user.id,
                description=description,
                subject_id=None,  # No subject for base documents
                is_public=True,  # Mark as public/university-wide
            )
            db.session.add(document)
            uploaded_count += 1
        else:
            flash(
                f"File {file.filename} is not allowed. Only PDF files are supported.",
                "warning",
            )

    # If we have files to process, create the vector database
    if uploaded_paths:
        try:
            # Save changes to the database
            db.session.commit()

            # Create/update the base vector DB
            from app.utils.rag_chain import create_vector_db

            chunk_count = create_vector_db(uploaded_paths, is_base=True)

            flash(
                f"Successfully uploaded {uploaded_count} base documents with {chunk_count} knowledge chunks.",
                "success",
            )
        except Exception as e:
            current_app.logger.error(f"Error processing base documents: {str(e)}")
            db.session.rollback()
            flash(f"Error processing base documents: {str(e)}", "danger")
    else:
        flash("No valid files were uploaded.", "warning")

    return redirect(url_for("admin.base_documents"))


@admin_bp.route("/base-documents/delete/<int:document_id>", methods=["POST"])
@login_required(role="admin")
def delete_base_document(document_id):
    """Delete a base vector document."""
    document = KnowledgeDocument.query.get_or_404(document_id)

    # Verify this is actually a base document
    if document.subject_id is not None or not document.is_public:
        flash("The specified document is not a base document.", "danger")
        return redirect(url_for("admin.base_documents"))

    try:
        # Delete the file from disk
        if os.path.exists(document.file_path):
            os.remove(document.file_path)

        # Delete from database
        db.session.delete(document)
        db.session.commit()

        flash("Base document deleted successfully.", "success")

        # Reinitialize the base vector DB
        # This is a simplistic approach - in a production environment,
        # you might want to schedule this as a background task
        try:
            from app.utils.rag_chain import initialize_from_existing_documents

            # Get all remaining base documents
            base_docs = KnowledgeDocument.query.filter_by(
                subject_id=None, is_public=True
            ).all()

            if base_docs:
                chunk_count = initialize_from_existing_documents(
                    base_docs, is_base=True
                )
                flash(
                    f"Reinitialized base vector DB with {chunk_count} chunks.", "info"
                )
            else:
                flash(
                    "No base documents remain. Base vector DB is now empty.", "warning"
                )
        except Exception as e:
            current_app.logger.error(f"Error reinitializing base vector DB: {str(e)}")
            flash(f"Error reinitializing base vector DB: {str(e)}", "warning")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting base document: {str(e)}")
        flash(f"Error deleting base document: {str(e)}", "danger")

    return redirect(url_for("admin.base_documents"))


def allowed_file(filename):
    """Check if a file type is allowed."""
    ALLOWED_EXTENSIONS = {"pdf"}
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@admin_bp.route("/subject/<int:subject_id>/chat")
@login_required(role="admin")
def subject_chat(subject_id):
    """Redirect to chat interface with subject preselected for easier access."""
    subject = Subject.query.get_or_404(subject_id)

    # Redirect to chat interface with subject_id parameter
    return redirect(url_for("chat.chat_interface", subject_id=subject_id))


@admin_bp.route("/quizzes")
@login_required(role="admin")
def quizzes():
    """Admin quiz management page."""
    # Get all quizzes with their associated subjects
    quizzes = Quiz.query.all()

    # Group quizzes by subject for better organization
    subjects = Subject.query.filter_by(is_active=True).all()
    quizzes_by_subject = {}

    for subject in subjects:
        subject_quizzes = [quiz for quiz in quizzes if quiz.subject_id == subject.id]
        if subject_quizzes:
            quizzes_by_subject[subject] = subject_quizzes

    return render_template(
        "admin/quizzes.html",
        quizzes=quizzes,
        quizzes_by_subject=quizzes_by_subject,
        subjects=subjects,
    )


@admin_bp.route("/quizzes/create", methods=["GET", "POST"])
@login_required(role="admin")
def create_quiz():
    """Create a new quiz."""
    form = QuizForm()

    # Populate the subject choices
    subjects = Subject.query.filter_by(is_active=True).order_by(Subject.name).all()
    form.subject_id.choices = [(s.id, f"{s.code} - {s.name}") for s in subjects]

    if form.validate_on_submit():
        try:
            # Debug info
            current_app.logger.info(f"Session data when creating quiz: {dict(session)}")
            current_app.logger.info(
                f"user_db_id in session: {session.get('user_db_id')}"
            )

            # Get user from database if user_db_id is missing
            created_by = session.get("user_db_id")
            if created_by is None:
                # Fallback: get user from firebase_uid
                user_id = session.get("user_id")  # This is firebase_uid
                if user_id:
                    user = User.query.filter_by(firebase_uid=user_id).first()
                    if user:
                        created_by = user.id
                        current_app.logger.info(
                            f"Recovered user_db_id: {created_by} from firebase_uid: {user_id}"
                        )
                        # Update the session to prevent future issues
                        session["user_db_id"] = user.id

            quiz = Quiz(
                title=form.title.data,
                description=form.description.data,
                subject_id=form.subject_id.data,
                created_by=created_by,
            )

            db.session.add(quiz)
            db.session.commit()

            flash(f"Quiz '{quiz.title}' created successfully!", "success")
            return redirect(url_for("admin.quiz_questions", quiz_id=quiz.id))

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error creating quiz: {str(e)}")
            flash(f"Error creating quiz: {str(e)}", "danger")

    return render_template("admin/create_quiz.html", form=form)


@admin_bp.route("/quizzes/<int:quiz_id>/edit", methods=["GET", "POST"])
@login_required(role="admin")
def edit_quiz(quiz_id):
    """Edit an existing quiz."""
    quiz = Quiz.query.get_or_404(quiz_id)
    form = QuizForm(obj=quiz)

    # Populate the subject choices
    subjects = Subject.query.filter_by(is_active=True).order_by(Subject.name).all()
    form.subject_id.choices = [(s.id, f"{s.code} - {s.name}") for s in subjects]

    if form.validate_on_submit():
        try:
            quiz.title = form.title.data
            quiz.description = form.description.data
            quiz.subject_id = form.subject_id.data

            db.session.commit()

            flash(f"Quiz '{quiz.title}' updated successfully!", "success")
            return redirect(url_for("admin.quizzes"))

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating quiz: {str(e)}")
            flash(f"Error updating quiz: {str(e)}", "danger")

    return render_template("admin/edit_quiz.html", form=form, quiz=quiz)


@admin_bp.route("/quizzes/<int:quiz_id>/delete", methods=["POST"])
@login_required(role="admin")
def delete_quiz(quiz_id):
    """Delete a quiz and all related records."""
    quiz = Quiz.query.get_or_404(quiz_id)
    quiz_title = quiz.title

    try:
        # First get all questions for the quiz
        questions = Question.query.filter_by(quiz_id=quiz_id).all()
        question_ids = [q.id for q in questions]
        
        # Delete all answers for those questions
        if question_ids:
            Answer.query.filter(Answer.question_id.in_(question_ids)).delete(synchronize_session=False)
        
        # Delete all questions for the quiz
        Question.query.filter_by(quiz_id=quiz_id).delete(synchronize_session=False)
        
        # Delete all quiz attempts
        attempts = QuizAttempt.query.filter_by(quiz_id=quiz_id).all()
        attempt_ids = [a.id for a in attempts]
        
        # Delete attempt answers
        if attempt_ids:
            AttemptAnswer.query.filter(AttemptAnswer.attempt_id.in_(attempt_ids)).delete(synchronize_session=False)
        
        # Delete attempts
        QuizAttempt.query.filter_by(quiz_id=quiz_id).delete(synchronize_session=False)
        
        # Finally delete the quiz
        db.session.delete(quiz)
        db.session.commit()

        # Log detailed deletion
        current_app.logger.info(
            f"Deleted quiz {quiz_id} ('{quiz_title}') with {len(question_ids)} questions and {len(attempt_ids)} attempts"
        )
        
        flash(f"Quiz '{quiz_title}' deleted successfully with all related data!", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting quiz: {str(e)}")
        flash(f"Error deleting quiz: {str(e)}", "danger")

    return redirect(url_for("admin.quizzes"))


@admin_bp.route("/quizzes/<int:quiz_id>/questions")
@login_required(role="admin")
def quiz_questions(quiz_id):
    """View and manage questions for a specific quiz."""
    quiz = Quiz.query.get_or_404(quiz_id)
    questions = (
        Question.query.filter_by(quiz_id=quiz_id)
        .order_by(Question.difficulty_level)
        .all()
    )

    # Get subject for context
    subject = Subject.query.get(quiz.subject_id)

    return render_template(
        "admin/quiz_questions.html", quiz=quiz, subject=subject, questions=questions
    )


@admin_bp.route("/quizzes/<int:quiz_id>/questions/create", methods=["GET", "POST"])
@login_required(role="admin")
def create_question(quiz_id):
    """Create a new question for a quiz."""
    quiz = Quiz.query.get_or_404(quiz_id)
    form = QuestionForm()

    if form.validate_on_submit():
        try:
            # Create the question
            question = Question(
                quiz_id=quiz_id,
                text=form.text.data,
                difficulty_level=form.difficulty_level.data,
            )

            db.session.add(question)
            db.session.flush()  # Get the question ID without committing

            # Check if answers were provided (handled by JavaScript in the form)
            answer_texts = request.form.getlist("answer_text[]")
            is_correct_list = request.form.getlist("is_correct[]")

            if len(answer_texts) < 2:
                flash("Please provide at least two answer options.", "warning")
                return render_template(
                    "admin/create_question.html", form=form, quiz=quiz
                )

            # Ensure at least one answer is marked as correct
            if "1" not in is_correct_list:
                flash("Please mark at least one answer as correct.", "warning")
                return render_template(
                    "admin/create_question.html", form=form, quiz=quiz
                )

            # Create the answers
            for i, text in enumerate(answer_texts):
                if text.strip():  # Only add non-empty answers
                    answer = Answer(
                        question_id=question.id,
                        text=text,
                        is_correct=(
                            is_correct_list[i] == "1"
                            if i < len(is_correct_list)
                            else False
                        ),
                    )
                    db.session.add(answer)

            db.session.commit()
            flash("Question added successfully!", "success")
            return redirect(url_for("admin.quiz_questions", quiz_id=quiz_id))

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error adding question: {str(e)}")
            flash(f"Error adding question: {str(e)}", "danger")

    return render_template("admin/create_question.html", form=form, quiz=quiz)


@admin_bp.route("/questions/<int:question_id>/edit", methods=["GET", "POST"])
@login_required(role="admin")
def edit_question(question_id):
    """Edit an existing question."""
    question = Question.query.get_or_404(question_id)
    quiz = Quiz.query.get_or_404(question.quiz_id)
    form = QuestionForm(obj=question)

    if form.validate_on_submit():
        try:
            question.text = form.text.data
            question.difficulty_level = form.difficulty_level.data

            # Handle answers update
            answer_ids = request.form.getlist("answer_id[]")
            answer_texts = request.form.getlist("answer_text[]")
            is_correct_list = request.form.getlist("is_correct[]")

            if len(answer_texts) < 2:
                flash("Please provide at least two answer options.", "warning")
                return render_template(
                    "admin/edit_question.html",
                    form=form,
                    question=question,
                    quiz=quiz,
                    answers=question.answers,
                )

            # Ensure at least one answer is marked as correct
            if "1" not in is_correct_list:
                flash("Please mark at least one answer as correct.", "warning")
                return render_template(
                    "admin/edit_question.html",
                    form=form,
                    question=question,
                    quiz=quiz,
                    answers=question.answers,
                )

            # Process existing answers and remove any not in the form
            existing_answer_ids = [str(a.id) for a in question.answers]
            for answer in question.answers:
                if str(answer.id) not in answer_ids:
                    db.session.delete(answer)

            # Update or create answers
            for i, text in enumerate(answer_texts):
                if text.strip():  # Only process non-empty answers
                    is_correct = (
                        is_correct_list[i] == "1" if i < len(is_correct_list) else False
                    )

                    if (
                        i < len(answer_ids) and answer_ids[i].isdigit()
                    ):  # Update existing
                        answer = Answer.query.get(int(answer_ids[i]))
                        if answer and answer.question_id == question.id:
                            answer.text = text
                            answer.is_correct = is_correct
                    else:  # Create new
                        answer = Answer(
                            question_id=question.id, text=text, is_correct=is_correct
                        )
                        db.session.add(answer)

            db.session.commit()
            flash("Question updated successfully!", "success")
            return redirect(url_for("admin.quiz_questions", quiz_id=question.quiz_id))

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating question: {str(e)}")
            flash(f"Error updating question: {str(e)}", "danger")

    return render_template(
        "admin/edit_question.html",
        form=form,
        question=question,
        quiz=quiz,
        answers=question.answers,
    )


@admin_bp.route("/questions/<int:question_id>/delete", methods=["POST"])
@login_required(role="admin")
def delete_question(question_id):
    """Delete a question."""
    question = Question.query.get_or_404(question_id)
    quiz_id = question.quiz_id

    try:
        db.session.delete(question)
        db.session.commit()
        return jsonify({"success": True, "message": "Question deleted successfully!"})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting question: {str(e)}")
        return jsonify({"success": False, "message": f"Error deleting question: {str(e)}"}), 500


@admin_bp.route("/quizzes/<int:quiz_id>/auto-generate", methods=["GET", "POST"])
@login_required(role="admin")
def auto_generate_questions(quiz_id):
    """Auto-generate quiz questions using RAG from subject knowledge documents."""
    quiz = Quiz.query.get_or_404(quiz_id)
    subject = Subject.query.get_or_404(quiz.subject_id)

    # Check if subject has documents with vector DB
    has_vector_db = _subject_has_vector_db(subject.id)

    # Ensure user_db_id is in session
    if session.get("user_db_id") is None:
        # Fallback: get user from firebase_uid
        user_id = session.get("user_id")  # This is firebase_uid
        if user_id:
            user = User.query.filter_by(firebase_uid=user_id).first()
            if user:
                session["user_db_id"] = user.id
                current_app.logger.info(
                    f"Set missing user_db_id: {user.id} in session for auto-generation"
                )

    if request.method == "POST":
        try:
            # Log session info for debugging
            current_app.logger.info(
                f"Session data for auto-generating questions: {dict(session)}"
            )

            # Get parameters from the form
            num_questions = int(request.form.get("num_questions", 5))
            difficulty_level = int(request.form.get("difficulty_level", 3))

            if not has_vector_db:
                flash(
                    "This subject doesn't have any knowledge documents with vector embeddings. Please upload documents first.",
                    "danger",
                )
                return redirect(url_for("admin.quiz_questions", quiz_id=quiz_id))

            # Generate questions
            questions_generated = generate_quiz_questions(
                subject_id=subject.id,
                quiz_id=quiz_id,
                num_questions=num_questions,
                difficulty_level=difficulty_level,
            )

            if questions_generated:
                flash(
                    f"Successfully generated {len(questions_generated)} questions.",
                    "success",
                )
            else:
                flash("Failed to generate questions. Please try again.", "danger")

            return redirect(url_for("admin.quiz_questions", quiz_id=quiz_id))

        except Exception as e:
            current_app.logger.error(f"Error generating questions: {str(e)}")
            flash(f"Error generating questions: {str(e)}", "danger")

    return render_template(
        "admin/auto_generate_questions.html",
        quiz=quiz,
        subject=subject,
        has_vector_db=has_vector_db,
    )


@admin_bp.route("/database/fix-schema", methods=["GET", "POST"])
@login_required(role="admin")
def fix_database_schema():
    """Admin route to manually fix database schema issues."""
    if request.method == "POST":
        try:
            connection = db.engine.raw_connection()
            cursor = connection.cursor()

            # Get a list of all tables
            cursor.execute("SHOW TABLES")
            tables = [table[0] for table in cursor.fetchall()]

            # Check knowledge_documents table
            if "knowledge_documents" in tables:
                # Check if subject_id column exists and is duplicate
                cursor.execute(
                    """
                    SELECT COUNT(*) 
                    FROM INFORMATION_SCHEMA.COLUMNS 
                    WHERE TABLE_NAME = 'knowledge_documents' 
                    AND COLUMN_NAME = 'subject_id'
                """
                )
                count = cursor.fetchone()[0]

                if count > 1:
                    # Fix duplicate subject_id column in knowledge_documents
                    current_app.logger.info(
                        "Fixing duplicate subject_id in knowledge_documents table"
                    )
                    try:
                        # First try to drop the column without foreign key
                        cursor.execute(
                            """
                            ALTER TABLE knowledge_documents 
                            DROP COLUMN subject_id;
                        """
                        )
                        connection.commit()
                        flash(
                            "Removed duplicate subject_id column from knowledge_documents table",
                            "success",
                        )
                    except Exception as e:
                        connection.rollback()
                        current_app.logger.error(f"Error dropping column: {str(e)}")

                        # Try to drop foreign key constraint first
                        try:
                            cursor.execute(
                                """
                                SELECT CONSTRAINT_NAME
                                FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
                                WHERE TABLE_NAME = 'knowledge_documents'
                                AND COLUMN_NAME = 'subject_id'
                                AND CONSTRAINT_NAME != 'PRIMARY'
                                AND REFERENCED_TABLE_NAME IS NOT NULL;
                            """
                            )
                            constraints = cursor.fetchall()

                            for constraint in constraints:
                                cursor.execute(
                                    f"""
                                    ALTER TABLE knowledge_documents
                                    DROP FOREIGN KEY {constraint[0]};
                                """
                                )

                            # Now try to drop the column again
                            cursor.execute(
                                """
                                ALTER TABLE knowledge_documents 
                                DROP COLUMN subject_id;
                            """
                            )
                            connection.commit()
                            flash(
                                "Removed duplicate subject_id column from knowledge_documents table",
                                "success",
                            )
                        except Exception as inner_e:
                            connection.rollback()
                            current_app.logger.error(
                                f"Error dropping constraint and column: {str(inner_e)}"
                            )
                            flash(
                                f"Could not fix subject_id column in knowledge_documents: {str(inner_e)}",
                                "danger",
                            )

            # Check quizzes table
            if "quizzes" in tables:
                # Check if subject_id foreign key exists
                cursor.execute(
                    """
                    SELECT COUNT(*) 
                    FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE 
                    WHERE TABLE_NAME = 'quizzes' 
                    AND COLUMN_NAME = 'subject_id'
                    AND REFERENCED_TABLE_NAME = 'subjects'
                """
                )
                has_fk = cursor.fetchone()[0] > 0

                if not has_fk:
                    try:
                        cursor.execute(
                            """
                            ALTER TABLE quizzes
                            ADD CONSTRAINT fk_quiz_subject 
                            FOREIGN KEY (subject_id) REFERENCES subjects(id);
                        """
                        )
                        connection.commit()
                        flash(
                            "Added foreign key constraint for subject_id in quizzes table",
                            "success",
                        )
                    except Exception as e:
                        connection.rollback()
                        current_app.logger.error(f"Error adding foreign key: {str(e)}")
                        flash(
                            f"Could not add foreign key to quizzes table: {str(e)}",
                            "danger",
                        )

            connection.close()
            flash("Database schema check completed", "info")

        except Exception as e:
            current_app.logger.error(f"Error fixing database schema: {str(e)}")
            flash(f"Error fixing database schema: {str(e)}", "danger")

    return render_template("admin/fix_schema.html")
