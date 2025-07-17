import os
import time
import google.generativeai as genai
from flask import current_app, session
from langchain_groq import ChatGroq
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import ChatPromptTemplate
from langchain.chains import create_retrieval_chain
from langchain_community.vectorstores import FAISS
from langchain_community.document_loaders import PyPDFLoader
from app.models import Subject, User, Quiz, Question, Answer, db
import re

# Global variables to store the vector databases
_base_vector_db = None  # General knowledge base (university-wide docs)
_subject_dbs = {}  # Subject-specific vector DBs
_student_dbs = {}  # Student-specific vector DBs
_merged_dbs = {}  # Cache for merged DBs (subject+base, student+base)
_llm = None

# Directory for storing FAISS indices
VECTOR_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'vector_store')
os.makedirs(VECTOR_DIR, exist_ok=True)

# Subdirectories for different types of indices
BASE_VECTOR_DIR = os.path.join(VECTOR_DIR, 'base')
SUBJECT_VECTOR_DIR = os.path.join(VECTOR_DIR, 'subjects')
STUDENT_VECTOR_DIR = os.path.join(VECTOR_DIR, 'students')

# Create subdirectories
os.makedirs(BASE_VECTOR_DIR, exist_ok=True)
os.makedirs(SUBJECT_VECTOR_DIR, exist_ok=True)
os.makedirs(STUDENT_VECTOR_DIR, exist_ok=True)


def _initialize_llm():
    """Initialize the LLM with GROQ API."""
    global _llm

    if _llm is None:
        groq_api_key = os.getenv("GROQ_API_KEY")
        if not groq_api_key:
            raise ValueError("GROQ_API_KEY not found in environment variables")

        _llm = ChatGroq(groq_api_key=groq_api_key, model_name="Llama3-8b-8192")

    return _llm


def _initialize_embeddings():
    """Initialize Google Generative AI Embeddings."""
    google_api_key = os.getenv("GOOGLE_API_KEY")
    if not google_api_key:
        raise ValueError("GOOGLE_API_KEY not found in environment variables")

    # Configure the Google Generative AI library
    genai.configure(api_key=google_api_key)

    return GoogleGenerativeAIEmbeddings(
        model="models/text-embedding-004", google_api_key=google_api_key
    )


def create_vector_db(file_paths, subject_id=None, user_id=None, is_base=False):
    """
    Create a vector database from uploaded PDF files and persist to disk.

    Args:
        file_paths: List of paths to the uploaded PDF files
        subject_id: Optional subject ID to associate with these documents
        user_id: Optional user ID to associate with these documents
        is_base: Whether this is the base vector DB (university-wide docs)

    Returns:
        int: Number of document chunks created
    """
    global _base_vector_db, _subject_dbs, _student_dbs, _merged_dbs

    # Determine the save path based on the type
    if is_base:
        save_path = BASE_VECTOR_DIR
    elif subject_id is not None:
        save_path = os.path.join(SUBJECT_VECTOR_DIR, str(subject_id))
        os.makedirs(save_path, exist_ok=True)
    elif user_id is not None:
        save_path = os.path.join(STUDENT_VECTOR_DIR, str(user_id))
        os.makedirs(save_path, exist_ok=True)
    else:
        raise ValueError("Must specify either is_base=True, subject_id, or user_id")

    # Check if vector DB already exists on disk
    if os.path.exists(save_path) and os.path.isfile(os.path.join(save_path, 'index.faiss')):
        # Load existing vector DB
        embeddings = _initialize_embeddings()
        vector_db = FAISS.load_local(save_path, embeddings)

        # Update global variables
        if is_base:
            _base_vector_db = vector_db
        elif subject_id is not None:
            _subject_dbs[subject_id] = vector_db
        else:  # user_id is not None
            _student_dbs[user_id] = vector_db

        try:
            return len(vector_db.index_to_docstore_id)
        except:
            return 0

    # Initialize embeddings
    embeddings = _initialize_embeddings()

    # Load documents
    docs = []
    for file_path in file_paths:
        loader = PyPDFLoader(file_path)
        docs.extend(loader.load())

    # Split documents
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    split_docs = text_splitter.split_documents(docs)

    if not split_docs:
        raise ValueError("No document content found after splitting")

    # Create the appropriate vector store based on parameters
    if is_base:
        _base_vector_db = FAISS.from_documents(split_docs, embeddings)
        print(f"Base vector DB created with {len(split_docs)} document chunks")
        # When base DB changes, invalidate all merged DBs
        _merged_dbs = {}
    elif subject_id is not None:
        _subject_dbs[subject_id] = FAISS.from_documents(split_docs, embeddings)
        print(f"Subject vector DB created with {len(split_docs)} document chunks")
        # When a subject DB changes, invalidate any merged DBs involving it
        if f"subject_{subject_id}" in _merged_dbs:
            del _merged_dbs[f"subject_{subject_id}"]
    elif user_id is not None:
        _student_dbs[user_id] = FAISS.from_documents(split_docs, embeddings)
        print(f"Student vector DB created with {len(split_docs)} document chunks")
        # When a student DB changes, invalidate any merged DBs involving it
        if f"student_{user_id}" in _merged_dbs:
            del _merged_dbs[f"student_{user_id}"]

    # Get chunk count
    chunk_count = len(split_docs)
    return chunk_count


def merge_vector_dbs(db1, db2, embeddings=None):
    """
    Merge two FAISS vector stores.

    Args:
        db1: First FAISS vector store
        db2: Second FAISS vector store
        embeddings: Optional embeddings model (will be initialized if None)

    Returns:
        FAISS: Merged vector store
    """
    if embeddings is None:
        embeddings = _initialize_embeddings()

    # Get documents from both DBs
    docs1 = [db1.docstore.search(idx) for idx in db1.index_to_docstore_id.values()]
    docs2 = [db2.docstore.search(idx) for idx in db2.index_to_docstore_id.values()]

    # Combine all documents
    all_docs = docs1 + docs2

    # Create a new vector store with all documents
    return FAISS.from_documents(all_docs, embeddings)


def get_hierarchical_db(subject_id=None, user_id=None):
    """
    Get the appropriate vector DB based on hierarchical context.

    Args:
        subject_id: Optional subject ID
        user_id: Optional user ID

    Returns:
        FAISS: Appropriate vector store for the context
    """
    global _base_vector_db, _subject_dbs, _student_dbs, _merged_dbs

    # Initialize embeddings
    embeddings = _initialize_embeddings()

    # Load base DB if not loaded
    if _base_vector_db is None and os.path.isfile(os.path.join(BASE_VECTOR_DIR, 'index.faiss')):
        _base_vector_db = FAISS.load_local(BASE_VECTOR_DIR, embeddings)

    # If no context provided, use base DB
    if subject_id is None and user_id is None:
        return _base_vector_db
        
    # Case 1: Student-specific query
    if user_id is not None and user_id in _student_dbs:
        # Check if we have a merged DB already
        merged_key = f"student_{user_id}"
        if merged_key not in _merged_dbs and _base_vector_db is not None:
            # Merge student DB with base DB
            _merged_dbs[merged_key] = merge_vector_dbs(
                _student_dbs[user_id], _base_vector_db, embeddings
            )
            print(f"Created merged DB for student {user_id}")
            
        # Return merged DB if available, otherwise just student DB
        if merged_key in _merged_dbs:
            return _merged_dbs[merged_key]
        return _student_dbs[user_id]

    # Case 2: Subject-specific query
    if subject_id is not None and subject_id in _subject_dbs:
        # Check if we have a merged DB already
        merged_key = f"subject_{subject_id}"
        if merged_key not in _merged_dbs and _base_vector_db is not None:
            # Merge subject DB with base DB
            _merged_dbs[merged_key] = merge_vector_dbs(
                _subject_dbs[subject_id], _base_vector_db, embeddings
            )
            print(f"Created merged DB for subject {subject_id}")

        # Return merged DB if available, otherwise just subject DB
        if merged_key in _merged_dbs:
            return _merged_dbs[merged_key]
        return _subject_dbs[subject_id]

    # Case 3: General query - use base DB
    if _base_vector_db is not None:
        return _base_vector_db

    # No appropriate DB found
    raise ValueError("No vector database available for this context")


def get_answer_from_documents(question, subject_id=None, user_id=None):
    """
    Process a question using the RAG chain and return the answer.

    Args:
        question: User's question string
        subject_id: Optional subject ID to use for filtering documents
        user_id: Optional user ID for personalized answers

    Returns:
        tuple: (answer, context_list)
    """
    try:
        # Get the appropriate vector DB based on hierarchical context
        vector_db = get_hierarchical_db(subject_id, user_id)

        # Initialize LLM
        llm = _initialize_llm()

        # Create prompt template
        prompt = ChatPromptTemplate.from_template(
            """
            Answer the questions based on the provided context only.
            Please provide the most accurate response based on the question
            <context>
            {context}
            </context>
            Questions:{input}
            """
        )

        # Create document chain
        document_chain = create_stuff_documents_chain(llm, prompt)

        # Create retriever
        retriever = vector_db.as_retriever()

        # Create retrieval chain
        retrieval_chain = create_retrieval_chain(retriever, document_chain)

        # Execute chain
        response = retrieval_chain.invoke({"input": question})

        # Extract answer and context
        answer = response.get("answer", "No answer found")

        # Extract context documents for display
        context_list = []
        if "context" in response and response["context"]:
            for i, doc in enumerate(response["context"]):
                context_list.append({"index": i + 1, "content": doc.page_content})

        return answer, context_list
    except ValueError as e:
        # Handle missing vector DB
        return str(e), []
    except Exception as e:
        # Handle other errors
        return f"Error processing question: {str(e)}", []


def initialize_from_existing_documents(
    documents, subject_id=None, user_id=None, is_base=False
):
    """
    Initialize vector databases from existing documents in the database.

    Args:
        documents: List of KnowledgeDocument objects
        subject_id: Optional subject ID for subject-specific DB
        user_id: Optional user ID for user-specific DB
        is_base: Whether to create the base vector DB

    Returns:
        int: Number of document chunks created
    """
    # Filter documents appropriately
    if is_base:
        # For base DB, use documents marked as university-wide (no subject and is_public=True)
        filtered_documents = [
            doc for doc in documents if doc.subject_id is None and doc.is_public == True
        ]
    elif subject_id is not None:
        # For subject DB, use subject-specific documents
        filtered_documents = [doc for doc in documents if doc.subject_id == subject_id]
    elif user_id is not None:
        # For student DB, use user-specific documents
        filtered_documents = [doc for doc in documents if doc.uploaded_by == user_id]
    else:
        filtered_documents = documents

    # Extract file paths
    file_paths = [doc.file_path for doc in filtered_documents]

    if not file_paths:
        print(
            f"No documents found for initialization with params: subject_id={subject_id}, user_id={user_id}, is_base={is_base}"
        )
        return 0

    # Create vector DB
    return create_vector_db(file_paths, subject_id, user_id, is_base)


def get_vector_db_status():
    """
    Get the status of all vector databases.

    Returns:
        dict: Status information for base, subject, and student DBs
    """
    global _base_vector_db, _subject_dbs, _student_dbs, _merged_dbs

    # Initialize result dictionary
    status = {
        "base": {"status": "Not initialized", "document_count": 0, "chunk_count": 0},
        "subjects": {},
        "students": {},
        "merged": len(_merged_dbs),
    }

    # Check base vector DB
    if _base_vector_db is not None:
        try:
            chunk_count = len(_base_vector_db.index_to_docstore_id)
            status["base"] = {
                "status": "Ready",
                "document_count": -1,  # We don't track this separately
                "chunk_count": chunk_count,
            }
        except Exception as e:
            status["base"] = {
                "status": f"Error: {str(e)}",
                "document_count": 0,
                "chunk_count": 0,
            }

    # Get all subjects from database
    subjects = Subject.query.all()
    for subject in subjects:
        # Default status for subject
        subject_status = {
            "id": subject.id,
            "name": subject.name,
            "code": subject.code,
            "status": "Not initialized",
            "document_count": 0,
            "chunk_count": 0,
            "merged_with_base": f"subject_{subject.id}" in _merged_dbs,
        }

        # Check if subject has a vector DB
        if subject.id in _subject_dbs:
            try:
                chunk_count = len(_subject_dbs[subject.id].index_to_docstore_id)
                subject_status.update(
                    {
                        "status": "Ready",
                        "document_count": len(subject.documents),
                        "chunk_count": chunk_count,
                    }
                )
            except Exception as e:
                subject_status.update(
                    {
                        "status": f"Error: {str(e)}",
                        "document_count": len(subject.documents),
                        "chunk_count": 0,
                    }
                )
        else:
            # No vector DB but might have documents
            subject_status.update({"document_count": len(subject.documents)})

        status["subjects"][subject.id] = subject_status

    # Get all students from database
    students = User.query.filter_by(role="student").all()
    for student in students:
        # Default status for student
        student_status = {
            "id": student.id,
            "name": student.name,
            "email": student.email,
            "status": "Not initialized",
            "document_count": 0,
            "chunk_count": 0,
            "merged_with_base": f"student_{student.id}" in _merged_dbs,
        }

        # Check if student has a vector DB
        if student.id in _student_dbs:
            try:
                chunk_count = len(_student_dbs[student.id].index_to_docstore_id)
                document_count = len([d for d in student.uploaded_documents])
                student_status.update(
                    {
                        "status": "Ready",
                        "document_count": document_count,
                        "chunk_count": chunk_count,
                    }
                )
            except Exception as e:
                student_status.update(
                    {
                        "status": f"Error: {str(e)}",
                        "document_count": 0,
                        "chunk_count": 0,
                    }
                )

        status["students"][student.id] = student_status

    return status


def generate_quiz_questions(subject_id, quiz_id, num_questions=5, difficulty_level=3):
    """
    Generate quiz questions for a specific subject using RAG.

    Args:
        subject_id: ID of the subject
        quiz_id: ID of the quiz
        num_questions: Number of questions to generate (default: 5)
        difficulty_level: Difficulty level from 1-5 (default: 3)

    Returns:
        list: List of generated Question objects
    """
    # Validate parameters
    if num_questions <= 0 or num_questions > 20:
        raise ValueError("Number of questions must be between 1 and 20")
    if difficulty_level < 1 or difficulty_level > 5:
        raise ValueError("Difficulty level must be between 1 and 5")

    # Check if subject exists and has a vector DB
    if subject_id not in _subject_dbs:
        raise ValueError(f"No vector database found for subject ID: {subject_id}")

    # Get the subject details
    subject = Subject.query.get(subject_id)
    if not subject:
        raise ValueError(f"Subject with ID {subject_id} not found")

    # Get the quiz details
    quiz = Quiz.query.get(quiz_id)
    if not quiz:
        raise ValueError(f"Quiz with ID {quiz_id} not found")

    # Initialize LLM for generation
    llm = _initialize_llm()

    # Get vector DB for the subject
    vector_db = get_hierarchical_db(subject_id=subject_id)
    retriever = vector_db.as_retriever(search_kwargs={"k": 5})

    # Create a prompt for generating quiz questions
    difficulty_descriptions = {
        1: "very easy (basic recall of facts)",
        2: "easy (simple understanding of concepts)",
        3: "medium (application of concepts)",
        4: "hard (analysis and evaluation)",
        5: "very hard (synthesis and deep understanding)",
    }

    # Create the prompt template for generating questions
    prompt_template = """
    You are a quiz question generator for a {subject_name} course.
    
    Based on the following context information from course materials, generate {num_questions} multiple-choice questions at a {difficulty_level} difficulty level.
    
    For each question:
    1. Create a clear and well-formulated question based on the content.
    2. Generate exactly 4 answer options (labeled A, B, C, D).
    3. Exactly one answer should be correct.
    4. The other answers should be plausible but incorrect.
    5. Mark the correct answer using the exact format shown below.
    
    IMPORTANT: FORMAT YOUR RESPONSE EXACTLY LIKE THIS WITH NO MARKDOWN OR ADDITIONAL FORMATTING:
    
    QUESTION: [Question text]
    A: [Option text]
    B: [Option text]
    C: [Option text]
    D: [Option text]
    CORRECT: [Letter of correct answer - just A, B, C, or D]
    
    DO NOT use markdown formatting like **bold** or other styling. Do not use alternative formats.
    DO NOT include any additional text, explanations, or commentary between questions.
    
    CONTEXT:
    {context}
    """

    prompt = ChatPromptTemplate.from_template(prompt_template)

    # Create the chain for document processing
    document_chain = create_stuff_documents_chain(
        llm, prompt, document_variable_name="context"
    )

    # Create the retrieval chain
    retrieval_chain = create_retrieval_chain(retriever, document_chain)

    # Generate the questions - we use the subject description as the initial prompt
    result = retrieval_chain.invoke(
        {
            "input": f"Generate quiz questions about {subject.name}: {subject.description}",
            "subject_name": subject.name,
            "num_questions": num_questions,
            "difficulty_level": difficulty_descriptions[difficulty_level],
        }
    )

    # Parse the response into Question and Answer objects
    generated_questions = []
    raw_output = result["answer"]

    # Log the raw output for debugging
    current_app.logger.info(f"Raw LLM output for question generation:\n{raw_output}")

    # First, strip any introductory text before the first QUESTION:
    if "QUESTION:" in raw_output:
        # Extract everything from the first QUESTION: onwards
        raw_output = "QUESTION:" + raw_output.split("QUESTION:", 1)[1]

    # Split the text into individual questions - try multiple approaches
    # First try the standard format with QUESTION: prefix
    question_blocks = raw_output.split("QUESTION:")

    # If no QUESTION: markers found, try splitting by numbered questions
    if len(question_blocks) <= 1:
        # Try to split by "Question 1", "Question 2", etc.
        question_blocks = re.split(r"(?:\*\*)?Question \d+(?:\*\*)?", raw_output)

    # Remove any empty entries
    question_blocks = [block.strip() for block in question_blocks if block.strip()]

    # Process each question block
    for block in question_blocks:
        try:
            # Extract the question text - everything until the first option
            option_start = block.find("\nA:")
            if option_start == -1:
                # Try alternative format (markdown with **Question N**)
                question_parts = block.split("\n")
                question_text = None
                options = {}
                correct_answer = None

                # If the first line doesn't contain option markers, use it as the question text
                if question_parts and not any(
                    marker in question_parts[0]
                    for marker in ["A:", "B:", "C:", "D:", "CORRECT:"]
                ):
                    question_text = question_parts[0].strip()

                for i, line in enumerate(question_parts):
                    # Extract options A through D
                    if line.startswith("A:") or line.strip().startswith("A:"):
                        options["A"] = line.split(":", 1)[1].strip()
                    elif line.startswith("B:") or line.strip().startswith("B:"):
                        options["B"] = line.split(":", 1)[1].strip()
                    elif line.startswith("C:") or line.strip().startswith("C:"):
                        options["C"] = line.split(":", 1)[1].strip()
                    elif line.startswith("D:") or line.strip().startswith("D:"):
                        options["D"] = line.split(":", 1)[1].strip()
                    # Handle "A. " or "A) " format
                    elif re.match(r"^A[.)]", line.strip()):
                        options["A"] = re.split(r"^A[.)]", line.strip(), 1)[1].strip()
                    elif re.match(r"^B[.)]", line.strip()):
                        options["B"] = re.split(r"^B[.)]", line.strip(), 1)[1].strip()
                    elif re.match(r"^C[.)]", line.strip()):
                        options["C"] = re.split(r"^C[.)]", line.strip(), 1)[1].strip()
                    elif re.match(r"^D[.)]", line.strip()):
                        options["D"] = re.split(r"^D[.)]", line.strip(), 1)[1].strip()
                    # Handle just "A" at the beginning of a line (matching AI format)
                    elif re.match(r"^A\s", line.strip()):
                        options["A"] = re.split(r"^A\s", line.strip(), 1)[1].strip()
                    elif re.match(r"^B\s", line.strip()):
                        options["B"] = re.split(r"^B\s", line.strip(), 1)[1].strip()
                    elif re.match(r"^C\s", line.strip()):
                        options["C"] = re.split(r"^C\s", line.strip(), 1)[1].strip()
                    elif re.match(r"^D\s", line.strip()):
                        options["D"] = re.split(r"^D\s", line.strip(), 1)[1].strip()

                    # Look for correct answer format in different variations
                    if "**CORRECT:" in line:
                        correct_part = line.split("**CORRECT:", 1)[1].strip()
                        if correct_part:
                            correct_answer = correct_part.strip("* ")
                    elif "CORRECT:" in line:
                        correct_part = line.split("CORRECT:", 1)[1].strip()
                        if correct_part:
                            correct_answer = correct_part.strip("* ")
                    # Look for format like "**CORRECT: B**"
                    elif re.search(r"\*\*CORRECT:\s*([A-D])\*\*", line):
                        match = re.search(r"\*\*CORRECT:\s*([A-D])\*\*", line)
                        if match:
                            correct_answer = match.group(1)
                    # Look for format like "CORRECT ANSWER: B"
                    elif "CORRECT ANSWER:" in line:
                        correct_part = line.split("CORRECT ANSWER:", 1)[1].strip()
                        if correct_part:
                            # Extract just the letter
                            match = re.search(r"([A-D])", correct_part)
                            if match:
                                correct_answer = match.group(1)

                # If we couldn't parse this format either, skip it
                if not question_text or len(options) != 4 or not correct_answer:
                    current_app.logger.warning(
                        f"Invalid question format (alternative): {block}\nFound question: {question_text}\nOptions count: {len(options)}\nCorrect answer: {correct_answer}"
                    )
                    continue

                # Extra validation - make sure the correct answer is one of the options
                if correct_answer not in options:
                    current_app.logger.warning(
                        f"Correct answer '{correct_answer}' not in options: {list(options.keys())}"
                    )
                    continue
            else:
                # Original format parsing
                question_text = block[:option_start].strip()

                # Extract options and correct answer
                options_text = block[option_start:].strip()
                options_parts = options_text.split("\n")

                options = {}
                correct_answer = None

                for part in options_parts:
                    if part.startswith("A:"):
                        options["A"] = part[2:].strip()
                    elif part.startswith("B:"):
                        options["B"] = part[2:].strip()
                    elif part.startswith("C:"):
                        options["C"] = part[2:].strip()
                    elif part.startswith("D:"):
                        options["D"] = part[2:].strip()
                    elif part.startswith("CORRECT:"):
                        correct_answer = part[8:].strip()

                # Validate we have all options and a correct answer
                if (
                    len(options) != 4
                    or not correct_answer
                    or correct_answer not in options
                ):
                    current_app.logger.warning(f"Invalid question format: {block}")
                    continue

            # Create the Question in the database
            db_question = Question(
                quiz_id=quiz_id, text=question_text, difficulty_level=difficulty_level
            )
            db.session.add(db_question)
            db.session.flush()  # Get the question ID

            # Create the Answer objects
            for option_key, option_text in options.items():
                answer = Answer(
                    question_id=db_question.id,
                    text=option_text,
                    is_correct=(option_key == correct_answer),
                )
                db.session.add(answer)

            generated_questions.append(db_question)

            # If we've reached the requested number of questions, stop
            if len(generated_questions) >= num_questions:
                break

        except Exception as e:
            current_app.logger.error(f"Error processing question: {str(e)}")
            # Continue with next question

    # Commit the changes to the database
    if generated_questions:
        db.session.commit()

    return generated_questions
