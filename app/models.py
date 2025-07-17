from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


# Student-Subject association table
class UserSubject(db.Model):
    """Association model for tracking student enrollment in subjects."""

    __tablename__ = "user_subjects"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    enrolled_date = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    user = db.relationship("User", back_populates="enrolled_subjects")
    subject = db.relationship("Subject", back_populates="enrolled_students")

    def __repr__(self):
        return f"<UserSubject: User {self.user_id}, Subject {self.subject_id}>"


class User(db.Model):
    """User model for student and admin profiles."""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    firebase_uid = db.Column(db.String(128), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=True)
    role = db.Column(db.String(20), default="student")  # 'student' or 'admin'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)

    # Relationships
    enrolled_subjects = db.relationship("UserSubject", back_populates="user", lazy=True)

    def __repr__(self):
        return f"<User {self.email}>"


class Subject(db.Model):
    """Model for academic subjects."""

    __tablename__ = "subjects"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(20), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    is_active = db.Column(db.Boolean, default=True)

    # Relationships
    creator = db.relationship("User", backref=db.backref("created_subjects", lazy=True))
    enrolled_students = db.relationship(
        "UserSubject", back_populates="subject", lazy=True
    )
    documents = db.relationship("KnowledgeDocument", backref="subject", lazy=True)

    def __repr__(self):
        return f"<Subject {self.code}: {self.name}>"


class KnowledgeDocument(db.Model):
    """Model for knowledge base documents."""

    __tablename__ = "knowledge_documents"

    id = db.Column(db.Integer, primary_key=True)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False, unique=True)
    file_path = db.Column(db.String(500), nullable=False)
    file_size = db.Column(db.Integer, nullable=False)  # Size in bytes
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    description = db.Column(db.Text, nullable=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=True)
    is_public = db.Column(
        db.Boolean, default=False
    )  # Marks document as university-wide

    # Relationship
    uploader = db.relationship(
        "User", backref=db.backref("uploaded_documents", lazy=True)
    )

    def __repr__(self):
        return f"<KnowledgeDocument {self.original_filename}>"


class Quiz(db.Model):
    """Model for quizzes."""

    __tablename__ = "quizzes"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)

    # Relationships
    creator = db.relationship("User", backref=db.backref("created_quizzes", lazy=True))
    subject = db.relationship("Subject", backref=db.backref("quizzes", lazy=True))
    questions = db.relationship(
        "Question", backref="quiz", lazy=True, cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Quiz {self.title}>"


class Question(db.Model):
    """Model for quiz questions."""

    __tablename__ = "questions"

    id = db.Column(db.Integer, primary_key=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey("quizzes.id"), nullable=False)
    text = db.Column(db.Text, nullable=False)
    difficulty_level = db.Column(db.Integer, default=1)  # 1-5 difficulty scale

    # Relationships
    answers = db.relationship(
        "Answer", backref="question", lazy=True, cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Question {self.id}: {self.text[:20]}...>"


class Answer(db.Model):
    """Model for question answers."""

    __tablename__ = "answers"

    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(db.Integer, db.ForeignKey("questions.id"), nullable=False)
    text = db.Column(db.Text, nullable=False)
    is_correct = db.Column(db.Boolean, default=False)

    def __repr__(self):
        return f"<Answer {self.id}: {self.text[:20]}...>"


class QuizAttempt(db.Model):
    """Model for tracking student quiz attempts."""

    __tablename__ = "quiz_attempts"

    id = db.Column(db.Integer, primary_key=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey("quizzes.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    start_time = db.Column(db.DateTime, default=datetime.utcnow)
    end_time = db.Column(db.DateTime, nullable=True)
    score = db.Column(db.Float, nullable=True)  # Percentage score

    # Relationships
    quiz = db.relationship("Quiz", backref=db.backref("attempts", lazy=True))
    user = db.relationship("User", backref=db.backref("quiz_attempts", lazy=True))
    answers = db.relationship(
        "AttemptAnswer", backref="attempt", lazy=True, cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<QuizAttempt {self.id}: User {self.user_id}, Quiz {self.quiz_id}>"


class AttemptAnswer(db.Model):
    """Model for storing student answers to questions in a quiz attempt."""

    __tablename__ = "attempt_answers"

    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(
        db.Integer, db.ForeignKey("quiz_attempts.id"), nullable=False
    )
    question_id = db.Column(db.Integer, db.ForeignKey("questions.id"), nullable=False)
    answer_id = db.Column(
        db.Integer, db.ForeignKey("answers.id"), nullable=True
    )  # Null if skipped
    is_correct = db.Column(db.Boolean, default=False)

    # Relationships
    question = db.relationship("Question")
    answer = db.relationship("Answer")

    def __repr__(self):
        return f"<AttemptAnswer {self.id}: Question {self.question_id}, Answer {self.answer_id}>"


class ChatConversation(db.Model):
    """Model for storing chat conversations."""

    __tablename__ = "chat_conversations"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=True)
    title = db.Column(
        db.String(200), nullable=True
    )  # Auto-generated from first message
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    user = db.relationship("User", backref=db.backref("conversations", lazy=True))
    subject = db.relationship("Subject", backref=db.backref("conversations", lazy=True))
    messages = db.relationship(
        "ChatMessage", backref="conversation", lazy=True, cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<ChatConversation {self.id}: User {self.user_id}, Subject {self.subject_id or 'None'}>"


class ChatMessage(db.Model):
    """Model for storing individual chat messages."""

    __tablename__ = "chat_messages"

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(
        db.Integer, db.ForeignKey("chat_conversations.id"), nullable=False
    )
    sender = db.Column(db.String(20), nullable=False)  # 'user' or 'ai'
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    # Store additional metadata
    context_used = db.Column(
        db.Text, nullable=True
    )  # Serialized context info used for response
    context_type = db.Column(
        db.String(20), nullable=True
    )  # 'base', 'subject', or 'student'

    def __repr__(self):
        return f"<ChatMessage {self.id}: {self.sender}, {self.timestamp}>"
