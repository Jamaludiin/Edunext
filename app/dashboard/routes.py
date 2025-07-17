import os
import uuid
from datetime import datetime
from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    current_app,
    flash,
    jsonify,
    session,
)
from werkzeug.utils import secure_filename
from app.models import (
    db,
    User,
    KnowledgeDocument,
    Subject,
    UserSubject,
    Quiz,
    Question,
    Answer,
    QuizAttempt,
    AttemptAnswer,
    ChatConversation,
)
from app.utils.auth import login_required
from app.utils.rag_chain import create_vector_db, get_vector_db_status

dashboard_bp = Blueprint("dashboard", __name__, template_folder="templates")

@dashboard_bp.route("/profile")
@login_required()
def profile():
    """Display the profile page for any logged-in user."""
    user_id = session.get("user_id")
    user = User.query.filter_by(firebase_uid=user_id).first()
    if not user:
        flash("User profile not found. Please log out and log in again.", "danger")
        return redirect(url_for("auth.logout"))
    return render_template("dashboard/profile.html", user=user)

@dashboard_bp.route("/profile/change-password", methods=["POST"])
@login_required()
def change_password():
    """Allow non-Google users to change their password."""
    user_id = session.get("user_id")
    user = User.query.filter_by(firebase_uid=user_id).first()
    if not user or user.auth_provider == "google":
        flash("Password change is not allowed for this account.", "danger")
        return redirect(url_for("dashboard.profile"))

    current_password = request.form.get("current_password")
    new_password = request.form.get("new_password")
    confirm_password = request.form.get("confirm_password")

    # Validate current password
    if not user.check_password(current_password):
        flash("Current password is incorrect.", "danger")
        return redirect(url_for("dashboard.profile"))

    # Validate new password length
    if not new_password or len(new_password) < 8:
        flash("New password must be at least 8 characters.", "danger")
        return redirect(url_for("dashboard.profile"))

    # Validate new password match
    if new_password != confirm_password:
        flash("New password and confirmation do not match.", "danger")
        return redirect(url_for("dashboard.profile"))

    # Change password
    user.set_password(new_password)
    from app.models import db
    db.session.commit()
    flash("Password changed successfully!", "success")
    return redirect(url_for("dashboard.profile"))


@dashboard_bp.route("/")
@login_required()
def index():
    """Redirect to the appropriate dashboard based on user role."""
    user_id = session.get("user_id")
    user = User.query.filter_by(firebase_uid=user_id).first()

    if not user:
        flash("User profile not found. Please log out and log in again.", "danger")
        return redirect(url_for("auth.logout"))

    if user.role == "admin":
        return redirect(url_for("admin.dashboard"))
    else:
        return redirect(url_for("dashboard.student_dashboard"))


@dashboard_bp.route("/student")
@login_required(role="student")
def student_dashboard():
    """Student dashboard displaying personal learning info."""
    user_id = session.get("user_id")
    user = User.query.filter_by(firebase_uid=user_id).first()

    if not user:
        flash("User profile not found. Please log out and log in again.", "danger")
        return redirect(url_for("auth.logout"))

    # Get user's knowledge documents (if any)
    documents = KnowledgeDocument.query.filter_by(uploaded_by=user.id).all()

    # Check for available vector DBs to set the session variable
    vector_status = get_vector_db_status()

    # If there are any available vector databases, mark as ready
    has_base_db = vector_status.get("base", {}).get("status") == "Ready"
    has_subject_dbs = any(
        s.get("status") == "Ready" for s in vector_status.get("subjects", {}).values()
    )

    # Check if student has personal documents
    has_student_db = (
        vector_status.get("students", {}).get(user.id, {}).get("status") == "Ready"
    )

    # Set session variable if any vector DB is available
    if has_base_db or has_subject_dbs or has_student_db:
        session["vector_db_ready"] = True
    else:
        session["vector_db_ready"] = False

    # Get chat history counts
    history_subjects = ChatConversation.query.filter_by(
        user_id=user.id
    ).filter(ChatConversation.subject_id.isnot(None)).all()
    
    history_general = ChatConversation.query.filter_by(
        user_id=user.id, subject_id=None
    ).all()

    return render_template(
        "dashboard/student.html",
        user=user,
        documents=documents,
        has_vector_db=has_base_db or has_subject_dbs or has_student_db,
        history_subjects=history_subjects,
        history_general=history_general,
    )


@dashboard_bp.route("/upload", methods=["GET", "POST"])
@login_required()
def upload_documents():
    """Handle document uploads for the knowledge base."""
    user_id = session.get("user_id")
    user = User.query.filter_by(firebase_uid=user_id).first()

    if not user:
        flash("User profile not found. Please log out and log in again.", "danger")
        return redirect(url_for("auth.logout"))

    if request.method == "POST":
        # Check if files were uploaded
        if "documents" not in request.files:
            flash("No files were selected for upload.", "warning")
            return redirect(request.url)

        files = request.files.getlist("documents")
        subject_id = request.form.get("subject_id", None)

        # Convert empty string to None
        if subject_id == "":
            subject_id = None

        # List to track uploaded files for vector DB creation
        uploaded_paths = []

        for file in files:
            # If user doesn't select a file, browser may
            # submit an empty file without a filename
            if file.filename == "":
                continue

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
                    description=request.form.get("description", ""),
                    subject_id=subject_id,
                )
                db.session.add(document)
            else:
                flash(
                    f"File {file.filename} is not allowed. Only PDF files are supported.",
                    "warning",
                )

        # If we have files to process, create the vector database
        if uploaded_paths:
            try:
                # Mark session as processing (in case of long-running operations)
                session["vector_db_processing"] = True

                # Save changes to the database
                db.session.commit()

                # Determine document type and create appropriate vector DBs
                user_firebase_uid = session.get("user_id")
                user = User.query.filter_by(firebase_uid=user_firebase_uid).first()

                # Create appropriate vector databases based on document type
                if subject_id:
                    # Subject-specific document
                    subject_chunk_count = create_vector_db(
                        uploaded_paths, subject_id=subject_id
                    )
                    flash(
                        f"Added {subject_chunk_count} chunks to subject-specific knowledge base.",
                        "success",
                    )
                elif user.role == "student":
                    # Student document (personal)
                    student_chunk_count = create_vector_db(
                        uploaded_paths, user_id=user.id
                    )
                    flash(
                        f"Added {student_chunk_count} chunks to your personal knowledge base.",
                        "success",
                    )
                else:
                    # Base/general document (admin uploads without subject)
                    is_base = user.role == "admin" and not subject_id
                    base_chunk_count = create_vector_db(uploaded_paths, is_base=is_base)
                    flash(
                        f"Added {base_chunk_count} chunks to base knowledge base.",
                        "success",
                    )

                # Mark vector database as ready in session
                session["vector_db_ready"] = True
                session["vector_db_processing"] = False
                session["vector_db_document_count"] = len(uploaded_paths)
                session["vector_db_chunk_count"] = (
                    subject_chunk_count if subject_id else base_chunk_count
                )

                # Redirect based on user role
                if user.role == "admin":
                    return redirect(url_for("admin.documents"))
                else:
                    return redirect(url_for("chat.chat_interface"))

            except Exception as e:
                current_app.logger.error(f"Error processing documents: {str(e)}")
                db.session.rollback()
                flash(f"Error processing documents: {str(e)}", "danger")
                session["vector_db_processing"] = False
        else:
            flash("No valid files were uploaded.", "warning")

    # Get existing documents for this user
    documents = KnowledgeDocument.query.filter_by(uploaded_by=user.id).all()

    # Get active subjects for selection
    subjects = Subject.query.filter_by(is_active=True).order_by(Subject.code).all()

    return render_template(
        "dashboard/upload.html", documents=documents, subjects=subjects
    )


@dashboard_bp.route("/documents/<int:document_id>/delete", methods=["POST"])
@login_required()
def delete_document(document_id):
    """Delete a document from the knowledge base."""
    document = KnowledgeDocument.query.get_or_404(document_id)
    user_id = session.get("user_id")
    user = User.query.filter_by(firebase_uid=user_id).first()

    if not user:
        flash("User profile not found. Please log out and log in again.", "danger")
        return redirect(url_for("auth.logout"))

    # Verify ownership
    if document.uploaded_by != user.id and user.role != "admin":
        flash("You do not have permission to delete this document.", "danger")
        return redirect(url_for("dashboard.upload_documents"))

    try:
        # Delete the file from disk
        if os.path.exists(document.file_path):
            os.remove(document.file_path)

        # Delete from database
        db.session.delete(document)
        db.session.commit()

        # Reset vector database session variables
        # This forces reprocessing of documents
        session["vector_db_ready"] = False
        session["vector_db_processing"] = False

        flash("Document deleted successfully.", "success")
    except Exception as e:
        current_app.logger.error(f"Error deleting document: {str(e)}")
        db.session.rollback()
        flash(f"Error deleting document: {str(e)}", "danger")

    return redirect(url_for("dashboard.upload_documents"))


def allowed_file(filename):
    """Check if a file type is allowed."""
    ALLOWED_EXTENSIONS = {"pdf"}
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@dashboard_bp.route("/initialize-vector-db")
@login_required(role="admin")
def initialize_vector_db():
    """Initialize hierarchical vector databases from existing documents."""
    from app.utils.rag_chain import initialize_from_existing_documents

    try:
        # Get all documents
        documents = KnowledgeDocument.query.all()

        if not documents:
            flash("No documents found to initialize vector database.", "warning")
            return redirect(url_for("admin.documents"))

        # 1. Initialize base vector DB (university-wide documents)
        base_docs = [
            doc for doc in documents if doc.subject_id is None and doc.is_public == True
        ]
        if base_docs:
            base_chunk_count = initialize_from_existing_documents(
                base_docs, is_base=True
            )
            flash(
                f"Initialized base vector DB with {base_chunk_count} chunks.", "success"
            )
        else:
            flash("No base documents found for university-wide knowledge.", "info")

        # 2. Initialize subject-specific vector DBs
        subjects = Subject.query.filter_by(is_active=True).all()
        for subject in subjects:
            subject_docs = [doc for doc in documents if doc.subject_id == subject.id]
            if subject_docs:
                subject_chunk_count = initialize_from_existing_documents(
                    subject_docs, subject_id=subject.id
                )
                if subject_chunk_count > 0:
                    flash(
                        f"Initialized vector DB for subject '{subject.code}' with {subject_chunk_count} chunks.",
                        "success",
                    )
            else:
                flash(f"No documents found for subject '{subject.code}'.", "info")

        # 3. Initialize student-specific vector DBs
        students = User.query.filter_by(role="student").all()
        for student in students:
            student_docs = [doc for doc in documents if doc.uploaded_by == student.id]
            if student_docs:
                student_chunk_count = initialize_from_existing_documents(
                    student_docs, user_id=student.id
                )
                if student_chunk_count > 0:
                    flash(
                        f"Initialized vector DB for student '{student.name}' with {student_chunk_count} chunks.",
                        "success",
                    )

        # Set session variables to indicate vector DBs are ready
        session["vector_db_ready"] = True
        session["vector_db_processing"] = False

        return redirect(url_for("admin.documents"))
    except Exception as e:
        current_app.logger.error(f"Error initializing vector DB: {str(e)}")
        flash(f"Error initializing vector database: {str(e)}", "danger")
        return redirect(url_for("admin.documents"))


@dashboard_bp.route("/quizzes")
@login_required(role="student")
def student_quizzes():
    """View available quizzes for the student."""
    user_id = session.get("user_id")
    user = User.query.filter_by(firebase_uid=user_id).first()

    if not user:
        flash("User profile not found. Please log out and log in again.", "danger")
        return redirect(url_for("auth.logout"))

    # Get subjects the student is enrolled in
    enrolled_subject_ids = [
        enrollment.subject_id for enrollment in user.enrolled_subjects
    ]

    # Get quizzes from those subjects
    quizzes = Quiz.query.filter(Quiz.subject_id.in_(enrolled_subject_ids)).all()

    # Get quiz attempt history for each quiz
    attempts = {}
    for quiz in quizzes:
        quiz_attempts = (
            QuizAttempt.query.filter_by(quiz_id=quiz.id, user_id=user.id)
            .order_by(QuizAttempt.start_time.desc())
            .all()
        )

        if quiz_attempts:
            attempts[quiz.id] = {
                "count": len(quiz_attempts),
                "best_score": max(
                    [
                        attempt.score
                        for attempt in quiz_attempts
                        if attempt.score is not None
                    ]
                    or [0]
                ),
                "last_attempt": quiz_attempts[0],
            }
        else:
            attempts[quiz.id] = {"count": 0, "best_score": 0, "last_attempt": None}

    return render_template(
        "dashboard/student_quizzes.html", user=user, quizzes=quizzes, attempts=attempts
    )


@dashboard_bp.route("/quizzes/<int:quiz_id>/start", methods=["GET", "POST"])
@login_required(role="student")
def start_quiz(quiz_id):
    """Start a new quiz attempt."""
    user_id = session.get("user_id")
    user = User.query.filter_by(firebase_uid=user_id).first()

    if not user:
        flash("User profile not found. Please log out and log in again.", "danger")
        return redirect(url_for("auth.logout"))

    # Get the quiz
    quiz = Quiz.query.get_or_404(quiz_id)

    # Check if student is enrolled in the subject
    is_enrolled = UserSubject.query.filter_by(
        user_id=user.id, subject_id=quiz.subject_id
    ).first()

    if not is_enrolled:
        flash("You are not enrolled in this subject.", "danger")
        return redirect(url_for("dashboard.student_quizzes"))

    # Create a new quiz attempt
    attempt = QuizAttempt(
        quiz_id=quiz.id,
        user_id=user.id,
        start_time=datetime.utcnow(),
    )

    db.session.add(attempt)
    db.session.commit()

    return redirect(url_for("dashboard.take_quiz", attempt_id=attempt.id))


@dashboard_bp.route("/quiz/attempt/<int:attempt_id>", methods=["GET", "POST"])
@login_required(role="student")
def take_quiz(attempt_id):
    """Take a quiz."""
    user_id = session.get("user_id")
    user = User.query.filter_by(firebase_uid=user_id).first()

    # Get the quiz attempt
    attempt = QuizAttempt.query.get_or_404(attempt_id)

    # Security check - ensure the attempt belongs to this user
    if attempt.user_id != user.id:
        flash("You do not have permission to access this quiz attempt.", "danger")
        return redirect(url_for("dashboard.student_quizzes"))

    # Check if the attempt is already completed
    if attempt.end_time is not None:
        return redirect(url_for("dashboard.quiz_results", attempt_id=attempt.id))

    # Get the quiz and questions
    quiz = attempt.quiz
    questions = Question.query.filter_by(quiz_id=quiz.id).all()

    if request.method == "POST":
        # Process quiz submission
        correct_count = 0
        total_questions = len(questions)

        # Record answers
        for question in questions:
            selected_answer_id = request.form.get(f"question_{question.id}")

            if selected_answer_id:
                selected_answer = Answer.query.get(selected_answer_id)
                is_correct = selected_answer.is_correct

                if is_correct:
                    correct_count += 1

                # Save the student's answer
                attempt_answer = AttemptAnswer(
                    attempt_id=attempt.id,
                    question_id=question.id,
                    answer_id=selected_answer.id,
                    is_correct=is_correct,
                )
                db.session.add(attempt_answer)
            else:
                # Record skipped question
                attempt_answer = AttemptAnswer(
                    attempt_id=attempt.id, question_id=question.id, is_correct=False
                )
                db.session.add(attempt_answer)

        # Calculate score (percentage)
        if total_questions > 0:
            score = (correct_count / total_questions) * 100
        else:
            score = 0

        # Update the attempt
        attempt.end_time = datetime.utcnow()
        attempt.score = score

        db.session.commit()

        return redirect(url_for("dashboard.quiz_results", attempt_id=attempt.id))

    return render_template(
        "dashboard/take_quiz.html",
        user=user,
        quiz=quiz,
        questions=questions,
        attempt=attempt,
    )


@dashboard_bp.route("/quiz/results/<int:attempt_id>")
@login_required(role="student")
def quiz_results(attempt_id):
    """View results of a completed quiz attempt."""
    user_id = session.get("user_id")
    user = User.query.filter_by(firebase_uid=user_id).first()

    # Get the quiz attempt
    attempt = QuizAttempt.query.get_or_404(attempt_id)

    # Security check - ensure the attempt belongs to this user
    if attempt.user_id != user.id:
        flash("You do not have permission to view these quiz results.", "danger")
        return redirect(url_for("dashboard.student_quizzes"))

    # Check if the attempt is completed
    if attempt.end_time is None:
        flash("This quiz is not yet completed.", "warning")
        return redirect(url_for("dashboard.take_quiz", attempt_id=attempt.id))

    # Get the quiz and answers
    quiz = attempt.quiz

    # Get all questions and the student's answers
    questions = Question.query.filter_by(quiz_id=quiz.id).all()

    # Create a dictionary to store question data with answers
    question_data = {}
    for question in questions:
        # Get the student's answer for this question
        student_answer = AttemptAnswer.query.filter_by(
            attempt_id=attempt.id, question_id=question.id
        ).first()

        # Get all possible answers
        all_answers = Answer.query.filter_by(question_id=question.id).all()

        question_data[question.id] = {
            "question": question,
            "student_answer": student_answer,
            "all_answers": all_answers,
        }

    return render_template(
        "dashboard/quiz_results.html",
        user=user,
        quiz=quiz,
        attempt=attempt,
        question_data=question_data,
    )


@dashboard_bp.route("/quiz/history")
@login_required(role="student")
def quiz_history():
    """View history of all quiz attempts."""
    user_id = session.get("user_id")
    user = User.query.filter_by(firebase_uid=user_id).first()

    # Get all quiz attempts for this user
    attempts = (
        QuizAttempt.query.filter_by(user_id=user.id)
        .order_by(QuizAttempt.start_time.desc())
        .all()
    )

    return render_template("dashboard/quiz_history.html", user=user, attempts=attempts)

@dashboard_bp.route("/analytics")
@login_required(role="student")
def analytics():
    """View analytics for subjects and quizzes."""
    user_id = session.get("user_id")
    user = User.query.filter_by(firebase_uid=user_id).first()

    # Get all quiz attempts for this user
    attempts = (
        QuizAttempt.query.filter_by(user_id=user.id)
        .order_by(QuizAttempt.start_time.desc())
        .all()
    )

    # Get all subjects the user is enrolled in
    subjects = [enrollment.subject for enrollment in user.enrolled_subjects]

    return render_template(
        "dashboard/analytics.html",
        user=user,
        attempts=attempts,
        subjects=subjects
    )
