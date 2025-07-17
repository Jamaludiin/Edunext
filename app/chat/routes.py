from flask import (
    Blueprint,
    render_template,
    request,
    jsonify,
    session,
    current_app,
    redirect,
    url_for,
)
from app.utils.auth import login_required
from app.models import (
    db,
    User,
    Subject,
    KnowledgeDocument,
    ChatConversation,
    ChatMessage,
)
from app.utils.rag_chain import (
    get_answer_from_documents,
    get_vector_db_status,
    _base_vector_db,
)
from werkzeug.utils import secure_filename
import os
import uuid
import json
from datetime import datetime

chat_bp = Blueprint("chat", __name__, template_folder="templates")


@chat_bp.route("/")
@login_required()
def chat_interface():
    """Render the chat interface."""
    # Check for available vector DBs to set the session variable
    vector_status = get_vector_db_status()

    # If there are any available vector databases, mark as ready
    has_base_db = vector_status.get("base", {}).get("status") == "Ready"
    has_subject_dbs = any(
        s.get("status") == "Ready" for s in vector_status.get("subjects", {}).values()
    )

    # Get current user's database ID
    user_id = session.get("user_id")
    user = User.query.filter_by(firebase_uid=user_id).first()
    user_db_id = user.id if user else None

    # Check if student has personal documents
    has_student_db = False
    if user_db_id and user.role == "student":
        has_student_db = (
            vector_status.get("students", {}).get(user_db_id, {}).get("status")
            == "Ready"
        )

    # Set session variable if any vector DB is available
    if has_base_db or has_subject_dbs or has_student_db:
        session["vector_db_ready"] = True

    # Get subjects based on user role
    subjects = []
    if user and user.role == "student":
        # For students, only show registered subjects
        subjects = [
            enrollment.subject
            for enrollment in user.enrolled_subjects
            if enrollment.subject.is_active
        ]

        # If student has no registered subjects, redirect to general chat
        if not subjects:
            return redirect(url_for("chat.general_chat_interface"))
    else:
        # For admin, show all subjects
        subjects = Subject.query.filter_by(is_active=True).order_by(Subject.code).all()

    # Get subject_id from query parameter if present
    initial_subject_id = request.args.get("subject_id")
    if initial_subject_id:
        try:
            initial_subject_id = int(initial_subject_id)

            # For students, verify the selected subject is one they're registered for
            if user and user.role == "student":
                subject_ids = [s.id for s in subjects]
                if initial_subject_id not in subject_ids:
                    initial_subject_id = None
        except ValueError:
            initial_subject_id = None

    # Get conversation_id from query parameter if present
    requested_conversation_id = request.args.get("conversation_id")
    if requested_conversation_id:
        try:
            requested_conversation_id = int(requested_conversation_id)
        except ValueError:
            requested_conversation_id = None

    # Find the most recent conversation for this user and subject (if specified)
    latest_conversation = None
    conversation_messages = []
    current_conversation_id = None

    if user_db_id:
        # If a specific conversation was requested, try to load it
        if requested_conversation_id:
            latest_conversation = ChatConversation.query.filter_by(
                id=requested_conversation_id, user_id=user_db_id
            ).first()
        else:
            # Find the most recent conversation based on subject selection
            query = ChatConversation.query.filter_by(user_id=user_db_id)

            # If a specific subject is selected, filter by that subject
            if initial_subject_id:
                query = query.filter_by(subject_id=initial_subject_id)
            else:
                # If no specific subject, still only show subject-specific conversations (not general ones)
                query = query.filter(ChatConversation.subject_id != None)

            # Get the most recent conversation
            latest_conversation = query.order_by(
                ChatConversation.updated_at.desc()
            ).first()

        # If we found a conversation, get its messages and ID
        if latest_conversation:
            current_conversation_id = latest_conversation.id
            conversation_messages = (
                ChatMessage.query.filter_by(conversation_id=latest_conversation.id)
                .order_by(ChatMessage.timestamp.asc())
                .all()
            )

    # Format messages for the template/JSON embedding
    formatted_messages = []
    if conversation_messages:
        for msg in conversation_messages:
            message_data = {
                "id": msg.id,
                "sender": msg.sender,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat(),
            }

            # Include context for AI messages
            if msg.sender == "ai" and msg.context_used:
                try:
                    # Attempt to parse context, provide empty list on failure
                    parsed_context = json.loads(msg.context_used)
                    message_data["context"] = (
                        parsed_context if isinstance(parsed_context, list) else []
                    )
                    message_data["context_type"] = msg.context_type
                except json.JSONDecodeError:
                    current_app.logger.warning(
                        f"Could not parse context for message {msg.id}"
                    )
                    message_data["context"] = []
                    message_data["context_type"] = (
                        msg.context_type
                    )  # Still pass type even if context fails

            formatted_messages.append(message_data)

    # Pass data needed by JavaScript to the template
    return render_template(
        "chat/index.html",
        subjects=subjects,
        initial_data={
            "initial_subject_id": initial_subject_id,
            "conversation_id": current_conversation_id,
            "messages": formatted_messages,
        },  # Pass all initial state in one dict
    )


@chat_bp.route("/upload-pdf", methods=["POST"])
@login_required(role="student")
def upload_pdf():
    """Upload a PDF directly from chat interface for student RAG"""
    from app.utils.rag_chain import create_vector_db

    user_id = session.get("user_id")
    user = User.query.filter_by(firebase_uid=user_id).first()

    if not user or user.role != "student":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    # Check if files were uploaded
    if "pdf_file" not in request.files:
        return jsonify({"success": False, "error": "No file was uploaded"}), 400

    file = request.files["pdf_file"]

    # Check if file is valid
    if file.filename == "":
        return jsonify({"success": False, "error": "No file selected"}), 400

    # Check allowed file types
    if not file or not allowed_file(file.filename):
        return jsonify({"success": False, "error": "Only PDF files are allowed"}), 400

    try:
        # Generate a unique filename
        filename = secure_filename(file.filename)
        unique_filename = f"{uuid.uuid4().hex}_{filename}"

        # Save file
        knowledge_base_path = os.path.join(
            current_app.config["UPLOAD_FOLDER"], "knowledge_base"
        )
        file_path = os.path.join(knowledge_base_path, unique_filename)

        # Create directory if needed
        os.makedirs(knowledge_base_path, exist_ok=True)

        # Save the file
        file.save(file_path)

        # Create record in database
        document = KnowledgeDocument(
            original_filename=filename,
            stored_filename=unique_filename,
            file_path=file_path,
            file_size=os.path.getsize(file_path),
            uploaded_by=user.id,
            description="Uploaded from chat interface",
            subject_id=None,
        )
        db.session.add(document)

        # Create vector DB for this file
        chunk_count = create_vector_db([file_path], user_id=user.id)

        # Save changes
        db.session.commit()

        # Mark vector DB as ready
        session["vector_db_ready"] = True

        return jsonify(
            {
                "success": True,
                "message": f"File uploaded and processed with {chunk_count} chunks",
                "filename": filename,
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error uploading file: {str(e)}")
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


def allowed_file(filename):
    """Check if file type is allowed"""
    ALLOWED_EXTENSIONS = {"pdf"}
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@chat_bp.route("/ask", methods=["POST"])
@login_required()
def ask_question():
    """Process a question from the user and return AI response."""
    # Get the user's question and subject from the request
    data = request.get_json()

    if not data or not data.get("question"):
        return jsonify({"success": False, "error": "No question provided"}), 400

    question = data.get("question")
    subject_id = data.get("subject_id")
    conversation_id = data.get("conversation_id")  # May be None for new conversations

    # Get the current user for personalized context
    user_id = session.get("user_id")
    user = User.query.filter_by(firebase_uid=user_id).first()

    if not user:
        return jsonify({"success": False, "error": "User not found"}), 401

    # Convert subject_id to integer if provided
    if subject_id:
        try:
            subject_id = int(subject_id)
        except ValueError:
            subject_id = None

    # Check vector DB availability
    vector_status = get_vector_db_status()
    has_base_db = vector_status.get("base", {}).get("status") == "Ready"
    has_subject_db = False

    if subject_id:
        has_subject_db = (
            vector_status.get("subjects", {}).get(subject_id, {}).get("status")
            == "Ready"
        )

    # Check if student has personal documents
    has_student_db = False
    if user.role == "student":
        has_student_db = (
            vector_status.get("students", {}).get(user.id, {}).get("status") == "Ready"
        )

    # If no vector DBs available, return error
    if not has_base_db and not has_subject_db and not has_student_db:
        session["vector_db_ready"] = False
        return (
            jsonify(
                {
                    "success": False,
                    "error": "No documents have been uploaded for the AI to use. Please upload documents first.",
                    "redirect_url": "/dashboard/upload",
                }
            ),
            400,
        )
    else:
        # Ensure session variable is set if we have databases
        session["vector_db_ready"] = True

    # Determine which context to use
    context_type = "base"  # Default
    user_db_id = None

    # Priority 1: If student has uploaded documents, use them
    if user.role == "student" and has_student_db:
        user_db_id = user.id
        context_type = "student"

    # Priority 2: If a subject is selected and has documents, use them
    if subject_id and has_subject_db:
        subject_id = subject_id
        context_type = "subject"
        user_db_id = None  # Override student context if subject is selected

    # Priority 3: Fall back to base documents
    # (This happens automatically if neither of the above conditions are met)

    try:
        # Get the answer from the RAG chain with hierarchical context
        answer, context_list = get_answer_from_documents(
            question, subject_id=subject_id, user_id=user_db_id
        )

        # Get subject name if subject_id is provided
        subject_name = None
        if subject_id:
            subject = Subject.query.get(subject_id)
            if subject:
                subject_name = f"{subject.code}: {subject.name}"

        # Store conversation in the database
        # Find or create conversation
        if conversation_id:
            conversation = ChatConversation.query.get(conversation_id)
            if not conversation or conversation.user_id != user.id:
                # Create new conversation if ID invalid or belongs to another user
                conversation = None

        if not conversation_id or not conversation:
            # Create a new conversation
            conversation = ChatConversation(
                user_id=user.id,
                subject_id=subject_id,
                title=(
                    question[:100] if len(question) <= 100 else f"{question[:97]}..."
                ),  # Create title from first question
            )
            db.session.add(conversation)
            db.session.flush()  # Get ID without committing

        # Store the user's question
        user_message = ChatMessage(
            conversation_id=conversation.id,
            sender="user",
            content=question,
            timestamp=datetime.utcnow(),
        )
        db.session.add(user_message)

        # Store the AI's answer
        ai_message = ChatMessage(
            conversation_id=conversation.id,
            sender="ai",
            content=answer,
            timestamp=datetime.utcnow(),
            context_used=json.dumps(context_list) if context_list else None,
            context_type=context_type,
        )
        db.session.add(ai_message)

        # Update conversation last_updated
        conversation.updated_at = datetime.utcnow()

        # Commit changes
        db.session.commit()

        # Return the answer and context
        return jsonify(
            {
                "success": True,
                "answer": answer,
                "context": context_list,
                "question": question,
                "subject": subject_name,
                "context_type": context_type,
                "conversation_id": conversation.id,  # Return conversation ID for future messages
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error processing question: {str(e)}")
        db.session.rollback()  # Rollback in case of error
        return (
            jsonify(
                {"success": False, "error": f"Error processing your question: {str(e)}"}
            ),
            500,
        )


@chat_bp.route("/history")
@login_required()
def chat_history():
    """Get the user's SUBJECT-SPECIFIC chat history."""
    history = []  # Initialize history here

    # Only handle API requests for now
    if (
        request.headers.get("Content-Type") == "application/json"
        or request.headers.get("Accept") == "application/json"
    ):
        # API request - return JSON data
        # Get the current user
        user_id = session.get("user_id")
        user = User.query.filter_by(firebase_uid=user_id).first()

        if not user:
            return jsonify({"success": False, "error": "User not found"}), 401

        # For students, only show subject-specific conversations for subjects they're enrolled in
        if user.role == "student":
            enrolled_subject_ids = [
                enrollment.subject_id for enrollment in user.enrolled_subjects
            ]

            # If student has no enrolled subjects, return empty history
            if not enrolled_subject_ids:
                return jsonify({"success": True, "history": []})

            # Query for conversations with subject_id in enrolled subjects
            conversations = (
                ChatConversation.query.filter(
                    ChatConversation.user_id == user.id,
                    ChatConversation.subject_id.in_(enrolled_subject_ids),
                )
                .order_by(ChatConversation.updated_at.desc())
                .limit(50)
                .all()
            )
        else:
            # For admin, show all subject-specific conversations
            conversations = (
                ChatConversation.query.filter(
                    ChatConversation.user_id == user.id,
                    ChatConversation.subject_id
                    != None,  # Filter for subject conversations
                )
                .order_by(ChatConversation.updated_at.desc())
                .limit(50)  # Increased limit slightly
                .all()
            )

        # Format the conversations for the response
        for conv in conversations:
            # Get first user question and AI response
            first_question = (
                ChatMessage.query.filter_by(conversation_id=conv.id, sender="user")
                .order_by(ChatMessage.timestamp.asc())
                .first()
            )

            first_answer = (
                ChatMessage.query.filter_by(conversation_id=conv.id, sender="ai")
                .order_by(ChatMessage.timestamp.asc())
                .first()
            )

            if first_question and first_answer:
                # Get subject name if available
                subject_name = None
                if conv.subject_id:
                    subject = Subject.query.get(conv.subject_id)
                    if subject:
                        subject_name = f"{subject.code}: {subject.name}"

                history.append(
                    {
                        "conversation_id": conv.id,
                        "title": conv.title,
                        "timestamp": conv.created_at.isoformat(),
                        "updated_at": conv.updated_at.isoformat(),
                        "subject": subject_name,  # Will always have a value here
                        "subject_id": conv.subject_id,  # Add the subject ID for linking back
                        "snippet": {
                            "question": (
                                first_question.content[:100] + "..."
                                if len(first_question.content) > 100
                                else first_question.content
                            ),
                            "answer": (
                                first_answer.content[:100] + "..."
                                if len(first_answer.content) > 100
                                else first_answer.content
                            ),
                        },
                    }
                )
    # Always return JSON for this endpoint
    return jsonify({"success": True, "history": history})


# New endpoint for General History JSON data
@chat_bp.route("/history/general")
@login_required()
def general_chat_history():
    """Get the user's GENERAL chat history (no subject)."""
    history = []
    user_id = session.get("user_id")
    user = User.query.filter_by(firebase_uid=user_id).first()

    if not user:
        return jsonify({"success": False, "error": "User not found"}), 401

    # Query for conversations WHERE SUBJECT_ID IS NULL
    conversations = (
        ChatConversation.query.filter(
            ChatConversation.user_id == user.id,
            ChatConversation.subject_id == None,  # Filter for general conversations
        )
        .order_by(ChatConversation.updated_at.desc())
        .limit(50)
        .all()
    )

    # Format the conversations
    for conv in conversations:
        first_question = (
            ChatMessage.query.filter_by(conversation_id=conv.id, sender="user")
            .order_by(ChatMessage.timestamp.asc())
            .first()
        )
        first_answer = (
            ChatMessage.query.filter_by(conversation_id=conv.id, sender="ai")
            .order_by(ChatMessage.timestamp.asc())
            .first()
        )

        if first_question and first_answer:
            history.append(
                {
                    "conversation_id": conv.id,
                    "title": conv.title,
                    "timestamp": conv.created_at.isoformat(),
                    "updated_at": conv.updated_at.isoformat(),
                    "subject": None,  # Explicitly None for general
                    "snippet": {
                        "question": (
                            first_question.content[:100] + "..."
                            if len(first_question.content) > 100
                            else first_question.content
                        ),
                        "answer": (
                            first_answer.content[:100] + "..."
                            if len(first_answer.content) > 100
                            else first_answer.content
                        ),
                    },
                }
            )

    return jsonify({"success": True, "history": history})


# --- New (Optional) View Routes for History Pages ---


@chat_bp.route("/history/subjects-view")
@login_required()
def subject_chat_history_view():
    """Render a page to display subject-specific chat history."""
    # For students, check if they have registered subjects
    user_id = session.get("user_id")
    user = User.query.filter_by(firebase_uid=user_id).first()

    if user and user.role == "student":
        if not user.enrolled_subjects:
            # If student has no registered subjects, redirect to general chat history
            return redirect(url_for("chat.general_chat_history_view"))

    # This template would likely use JS to fetch from /chat/history
    return render_template("chat/history_subjects.html")


@chat_bp.route("/history/general-view")
@login_required()
def general_chat_history_view():
    """Render a page to display general chat history."""
    # This template would likely use JS to fetch from /chat/history/general
    return render_template("chat/history_general.html")


@chat_bp.route("/conversation/<int:conversation_id>")
@login_required()
def get_conversation(conversation_id):
    """Get a specific conversation by ID."""
    # Check if this is an API request or page view
    if (
        request.headers.get("Content-Type") == "application/json"
        or request.headers.get("Accept") == "application/json"
    ):
        # API request - return JSON data
        # Get the current user
        user_id = session.get("user_id")
        user = User.query.filter_by(firebase_uid=user_id).first()

        if not user:
            return jsonify({"success": False, "error": "User not found"}), 401

        # Get the conversation, ensuring it belongs to this user
        conversation = ChatConversation.query.filter_by(
            id=conversation_id, user_id=user.id
        ).first()

        if not conversation:
            return jsonify({"success": False, "error": "Conversation not found"}), 404

        # Get all messages in this conversation
        messages = (
            ChatMessage.query.filter_by(conversation_id=conversation_id)
            .order_by(ChatMessage.timestamp.asc())
            .all()
        )

        # Format the response
        messages_list = []
        for msg in messages:
            message_data = {
                "id": msg.id,
                "sender": msg.sender,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat(),
            }

            # Add context if this is an AI message and has context
            if msg.sender == "ai" and msg.context_used:
                try:
                    message_data["context"] = json.loads(msg.context_used)
                    message_data["context_type"] = msg.context_type
                except:
                    # Handle JSON parsing errors gracefully
                    message_data["context"] = []

            messages_list.append(message_data)

        # Get subject info if available
        subject_info = None
        if conversation.subject_id:
            subject = Subject.query.get(conversation.subject_id)
            if subject:
                subject_info = {
                    "id": subject.id,
                    "name": subject.name,
                    "code": subject.code,
                }

        return jsonify(
            {
                "success": True,
                "conversation": {
                    "id": conversation.id,
                    "title": conversation.title,
                    "created_at": conversation.created_at.isoformat(),
                    "updated_at": conversation.updated_at.isoformat(),
                    "subject": subject_info,
                    "messages": messages_list,
                },
            }
        )
    else:
        # Page view - render template
        return render_template("chat/conversation.html")


# --- New Routes for General Chat ---


@chat_bp.route("/general")
@login_required()
def general_chat_interface():
    """Render the general chat interface (using only base knowledge)."""
    # Find the most recent *general* conversation for this user
    user_id = session.get("user_id")
    user = User.query.filter_by(firebase_uid=user_id).first()
    latest_conversation = None
    conversation_messages = []
    current_conversation_id = None

    # Get vector DB status
    vector_status = get_vector_db_status()

    # Check if base vector DB is ready
    has_base_db = vector_status.get("base", {}).get("status") == "Ready"
    base_chunk_count = vector_status.get("base", {}).get("chunk_count", 0)

    # Check if student has personal documents (for students only)
    has_student_db = False
    student_chunk_count = 0
    student_docs_count = 0

    if user and user.role == "student":
        student_status = vector_status.get("students", {}).get(user.id, {})
        has_student_db = student_status.get("status") == "Ready"
        student_chunk_count = student_status.get("chunk_count", 0)
        student_docs_count = student_status.get("document_count", 0)

    # Generate system message based on DB status
    system_message = "Hello! I'm the general AI Assistant. "

    if has_base_db and has_student_db:
        system_message += "I can answer questions based on both your personal documents and the university's shared knowledge."
    elif has_base_db and not has_student_db:
        system_message += (
            "I can answer questions based on the university's shared knowledge base."
        )
        if user and user.role == "student":
            system_message += " You haven't uploaded any personal documents yet. To get personalized answers, click the upload button below."
    elif has_student_db and not has_base_db:
        system_message += "I can answer questions based only on your personal documents. The university's shared knowledge base is not available."
    else:
        system_message = "I'm the general AI Assistant, but no knowledge base is currently available. Please upload documents or contact an administrator."

    # Update vector DB readiness in session
    session["vector_db_ready"] = has_base_db or has_student_db

    # Get conversation_id from query parameter if present
    requested_conversation_id = request.args.get("conversation_id")
    if requested_conversation_id:
        try:
            requested_conversation_id = int(requested_conversation_id)
        except ValueError:
            requested_conversation_id = None

    if user:
        # If a specific conversation was requested, try to load it
        if requested_conversation_id:
            latest_conversation = ChatConversation.query.filter_by(
                id=requested_conversation_id, user_id=user.id, subject_id=None
            ).first()
        else:
            # Otherwise find the most recent general conversation
            query = ChatConversation.query.filter_by(user_id=user.id, subject_id=None)
            latest_conversation = query.order_by(
                ChatConversation.updated_at.desc()
            ).first()

        if latest_conversation:
            current_conversation_id = latest_conversation.id
            messages = (
                ChatMessage.query.filter_by(conversation_id=latest_conversation.id)
                .order_by(ChatMessage.timestamp.asc())
                .all()
            )
            # Format messages
            for msg in messages:
                message_data = {
                    "id": msg.id,
                    "sender": msg.sender,
                    "content": msg.content,
                    "timestamp": msg.timestamp.isoformat(),
                }
                if msg.sender == "ai" and msg.context_used:
                    try:
                        parsed_context = json.loads(msg.context_used)
                        message_data["context"] = (
                            parsed_context if isinstance(parsed_context, list) else []
                        )
                        message_data["context_type"] = msg.context_type
                    except json.JSONDecodeError:
                        message_data["context"] = []
                        message_data["context_type"] = msg.context_type
                conversation_messages.append(message_data)

    # Generate warning about empty DBs if applicable
    warning_message = None
    if (has_base_db and base_chunk_count == 0) or (
        has_student_db and student_chunk_count == 0
    ):
        warning_message = "Warning: The knowledge base contains very little or no content. Responses may be limited."

    return render_template(
        "chat/general.html",
        initial_data={
            "conversation_id": current_conversation_id,
            "messages": conversation_messages,
            "system_message": system_message,
            "warning_message": warning_message,
            "vector_db_ready": session.get("vector_db_ready", False),
        },
    )


@chat_bp.route("/ask-general", methods=["POST"])
@login_required()
def ask_general_question():
    """Process a question using only the base knowledge."""
    data = request.get_json()
    if not data or not data.get("question"):
        return jsonify({"success": False, "error": "No question provided"}), 400

    question = data.get("question")
    conversation_id = data.get("conversation_id")

    user_id = session.get("user_id")
    user = User.query.filter_by(firebase_uid=user_id).first()

    if not user:
        return jsonify({"success": False, "error": "User not found"}), 401

    # Get comprehensive vector DB status
    vector_status = get_vector_db_status()

    # Check if base vector DB is ready
    has_base_db = vector_status.get("base", {}).get("status") == "Ready"
    base_chunk_count = vector_status.get("base", {}).get("chunk_count", 0)

    # Check if student has personal documents
    has_student_db = False
    student_chunk_count = 0
    user_db_id = None

    if user.role == "student":
        student_status = vector_status.get("students", {}).get(user.id, {})
        has_student_db = student_status.get("status") == "Ready"
        student_chunk_count = student_status.get("chunk_count", 0)
        if has_student_db:
            user_db_id = user.id

    # Update session flag for vector DB readiness
    session["vector_db_ready"] = has_base_db or has_student_db

    # Ensure at least one vector DB with content is available
    if not session["vector_db_ready"]:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "No knowledge base is available. Please upload documents or contact an administrator.",
                }
            ),
            400,
        )

    # Warn if vector DBs exist but are empty
    if (has_base_db and base_chunk_count == 0) or (
        has_student_db and student_chunk_count == 0
    ):
        warning_message = "Warning: The knowledge base contains very little or no content. Responses may be limited."
    else:
        warning_message = None

    try:
        # Determine which context to use
        context_type = "base"  # Default

        # If student has uploaded documents, use them
        if user.role == "student" and has_student_db:
            user_db_id = user.id
            context_type = "student"

        # Get the answer from the RAG chain
        answer, context_list = get_answer_from_documents(
            question, subject_id=None, user_id=user_db_id
        )

        # Store conversation (ensure it's marked as general, i.e., subject_id=None)
        conversation = None
        if conversation_id:
            conversation = ChatConversation.query.get(conversation_id)
            # Validate it belongs to user and is general
            if (
                not conversation
                or conversation.user_id != user.id
                or conversation.subject_id is not None
            ):
                conversation = None  # Treat as new if invalid

        if not conversation:
            conversation = ChatConversation(
                user_id=user.id,
                subject_id=None,  # Explicitly None for general chat
                title=(
                    f"General: {question[:90]}"
                    if len(question) <= 90
                    else f"General: {question[:87]}..."
                ),
            )
            db.session.add(conversation)
            db.session.flush()  # Get ID

        # Store messages
        user_message = ChatMessage(
            conversation_id=conversation.id,
            sender="user",
            content=question,
            timestamp=datetime.utcnow(),
        )
        db.session.add(user_message)

        ai_message = ChatMessage(
            conversation_id=conversation.id,
            sender="ai",
            content=answer,
            timestamp=datetime.utcnow(),
            context_used=json.dumps(context_list) if context_list else None,
            context_type=context_type,  # Use the determined context type
        )
        db.session.add(ai_message)

        conversation.updated_at = datetime.utcnow()
        db.session.commit()

        response_data = {
            "success": True,
            "answer": answer,
            "context": context_list,
            "question": question,
            "context_type": context_type,
            "conversation_id": conversation.id,
        }

        # Add warning to response if present
        if warning_message:
            response_data["warning"] = warning_message

        return jsonify(response_data)

    except Exception as e:
        current_app.logger.error(f"Error processing general question: {str(e)}")
        db.session.rollback()
        return (
            jsonify(
                {"success": False, "error": f"Error processing question: {str(e)}"}
            ),
            500,
        )
