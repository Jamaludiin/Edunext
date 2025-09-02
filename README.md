# AI-Based Personalized Learning Assistance for Students

An intelligent learning system designed to provide personalized study support through AI, incorporating real-time AI chatbot assistance, adaptive assessments, progress tracking, and tailored study recommendations.

## 🚀 Key Features

- **AI Chatbot**: Real-time assistance powered by Retrieval-Augmented Generation (RAG) to answer academic queries
- **Knowledge Base Management**: Upload and manage study materials (PDFs) for reference by the AI
- **Multi-Role System**: Separate interfaces for students and administrators
- **Firebase Authentication**: Secure email/password and social login options
- **Adaptive Quizzes**: Adjusts difficulty based on student performance
- **Progress Tracking**: Visual dashboards to track learning progress

## 🛠️ Technologies Used

- **Backend**: Flask (Python)
- **Frontend**: HTML, CSS, JavaScript with Bootstrap 5
- **Database**: MySQL with SQLAlchemy ORM
- **AI/ML**: 
  - Groq API for LLM inference
  - Google Generative AI Embeddings
  - LangChain for RAG implementation
  - FAISS for vector search
- **Authentication**: Firebase Authentication
- **Vector Database**: FAISS (Facebook AI Similarity Search)

## 📦 Installation

### 1. Clone the Repository
```bash
git clone https://github.com/yourusername/ai-learning-assistance.git
cd ai-learning-assistance
```

### 2. Create and Activate Virtual Environment
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Environment Configuration
Create a `.env` file with the following variables:
```env
SECRET_KEY=your_secret_key
DATABASE_URI=mysql://root:1234@localhost:3306/learning_assistance
GROQ_API_KEY=your_groq_api_key
GOOGLE_API_KEY=your_google_api_key
FIREBASE_ADMIN_SDK_PATH=path/to/firebase-adminsdk.json
```

### 5. Database Setup
```bash
mysql -u root -p
CREATE DATABASE learning_assistance;
exit
```

### 6. Initialize Database
```bash
flask db init
flask db migrate -m "Initial migration"
flask db upgrade
```

## 🚀 Running the Application

### 1. Start the Flask Application
```bash
python app.py
```

### 2. Access the Application
Open a web browser and navigate to `http://localhost:5000`

## 📁 Application Structure

```
app/
├── __init__.py              # Application factory and configuration
├── routes.py                # Main routes
├── models.py                # Database models
├── auth/                    # Authentication routes and templates
├── admin/                   # Admin routes and templates
├── chat/                    # Chat routes and templates
├── dashboard/               # Dashboard routes and templates
├── utils/                   # Utility functions
│   ├── auth.py             # Authentication utilities
│   └── rag_chain.py        # RAG implementation
├── templates/               # HTML templates
└── static/                  # Static assets (CSS, JS, images)
```

## 📚 Usage

### Student Flow
1. **Register/Login** using email or social login
2. **Upload Study Materials** (PDFs) from the dashboard
3. **Chat with AI** about the uploaded materials
4. **Take Adaptive Quizzes** tailored to your performance
5. **Track Progress** through visual dashboards

### Admin Flow
1. **Access Admin Dashboard**
2. **Manage Users** (view, toggle roles)
3. **Manage Documents** and study materials
4. **Create and Manage Quizzes** for students

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature-name`
3. Commit your changes: `git commit -m 'Add some feature'`
4. Push to the branch: `git push origin feature-name`
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgements

- **Michigan State University** for academic support
- **Groq API** for LLM access
- **Google Generative AI** for embeddings
- **LangChain** for RAG frameworks
- **Firebase** for authentication services

---

## 🔗 Quick Links

- [Live Demo](#)
- [Documentation](#)
- [API Reference](#)
- [Support](#)

---

**Built with ❤️ for Students**

