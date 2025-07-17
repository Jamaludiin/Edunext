import os
import time
from flask import (
    Blueprint,
    render_template,
    request,
    current_app,
    session,
    flash,
    redirect,
    url_for,
    jsonify,
    send_file,
    abort,
)
from werkzeug.utils import secure_filename
from app.utils.rag_chain import create_vector_db, get_answer_from_documents
from app.models import KnowledgeDocument, db
from app.utils.auth import login_required

main_bp = Blueprint("main", __name__)


def allowed_file(filename):
    """Check if the file extension is allowed (PDF only)."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() == "pdf"


@main_bp.route("/", methods=["GET"])
def index():
    """Landing page route."""
    # If user is already logged in, redirect to dashboard
    if "user_id" in session and session.get("authenticated"):
        # Use direct redirect without going through dashboard index route
        if session.get("role") == "admin":
            return redirect(url_for("admin.dashboard"))
        else:
            return redirect(url_for("dashboard.student_dashboard"))

    # Otherwise show landing page
    return render_template("index.html")


@main_bp.route("/debug/session", methods=["GET"])
def debug_session():
    """Debug route to check session state."""
    # Only enable in development mode
    if not current_app.debug:
        return jsonify({"error": "Debug routes only available in debug mode"}), 403

    return jsonify(
        {
            "vector_db_ready": session.get("vector_db_ready", False),
            "session_id": session.get("_id", None),
            "session_keys": list(session.keys()),
            "loaded_documents": session.get("loaded_documents", []),
            "document_chunk_count": session.get("document_chunk_count", 0),
        }
    )


@main_bp.route("/upload", methods=["POST"])
def upload_files():
    """Handle the document uploads."""
    if "files" not in request.files:
        flash("No file part", "danger")
        return redirect(request.url)

    files = request.files.getlist("files")

    if not files or files[0].filename == "":
        flash("No files selected", "danger")
        return redirect(url_for("main.index"))

    # Check total size of all files before processing
    max_content_length = current_app.config.get("MAX_CONTENT_LENGTH", 16 * 1024 * 1024)

    # Validate and save files
    saved_files = []
    document_names = []

    for file in files:
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file_path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
            file.save(file_path)

            # Check individual file size
            file_size = os.path.getsize(file_path)
            if file_size > max_content_length:
                os.remove(file_path)  # Remove the oversized file
                flash(f"File {filename} exceeds the maximum allowed size.", "danger")
                continue

            saved_files.append(file_path)
            document_names.append(
                {
                    "name": filename,
                    "size": f"{file_size / (1024 * 1024):.2f} MB",
                    "path": file_path,
                    "upload_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        else:
            flash(
                f"File {file.filename} is not allowed. Only PDF files are accepted.",
                "warning",
            )

    if not saved_files:
        flash("No valid files were uploaded", "danger")
        return redirect(url_for("main.index"))

    # Process the uploaded files and create vector database
    try:
        # Set session variable before calling create_vector_db to ensure it's set
        # even if there's an error during processing
        session["vector_db_ready"] = True
        session["loaded_documents"] = document_names

        # Create vector database and get chunk count
        chunk_count = create_vector_db(saved_files)
        session["document_chunk_count"] = chunk_count

        flash(
            f"Successfully processed {len(saved_files)} documents into {chunk_count} chunks",
            "success",
        )
        return redirect(url_for("main.index"))
    except Exception as e:
        # Don't reset vector_db_ready on error to prevent lost state
        flash(f"Error processing documents: {e}", "danger")
        return redirect(url_for("main.index"))


@main_bp.route("/ask", methods=["POST"])
def ask_question():
    """Process a question using the RAG chain."""
    if not session.get("vector_db_ready"):
        return jsonify({"error": "Please upload and process documents first"}), 400

    question = request.form.get("question", "")

    if not question:
        return jsonify({"error": "Question cannot be empty"}), 400

    try:
        start_time = time.process_time()
        answer, context = get_answer_from_documents(question)
        process_time = time.process_time() - start_time

        return jsonify(
            {
                "answer": answer,
                "context": context,
                "process_time": f"{process_time:.2f}",
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@main_bp.route("/document/<int:document_id>/download")
@login_required()
def download_document(document_id):
    """Download a document from the knowledge base."""
    # Get the document from the database
    document = KnowledgeDocument.query.get_or_404(document_id)

    # Check if the file exists
    if not os.path.isfile(document.file_path):
        current_app.logger.error(f"File not found: {document.file_path}")
        flash("The requested file could not be found on the server.", "danger")
        return redirect(url_for("main.index"))

    try:
        # Send the file to the client
        return send_file(
            document.file_path,
            as_attachment=True,
            download_name=document.original_filename,
            mimetype="application/pdf",
        )
    except Exception as e:
        current_app.logger.error(f"Error downloading file: {str(e)}")
        flash(f"Error downloading file: {str(e)}", "danger")
        return redirect(url_for("main.index"))


@main_bp.route("/clear", methods=["POST"])
def clear_session():
    """Clear the session and reset the application state."""
    if "vector_db_ready" in session:
        session.pop("vector_db_ready")
    if "loaded_documents" in session:
        session.pop("loaded_documents")
    if "document_chunk_count" in session:
        session.pop("document_chunk_count")

    flash("Application state has been reset. You can upload new documents.", "info")
    return redirect(url_for("main.index"))
