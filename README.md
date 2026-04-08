# CareerPilot AI

CareerPilot AI is an AI-powered resume and job match platform for students, new graduates, and early-career professionals. The system allows a user to upload a resume and one or more target job descriptions, then analyzes job-fit relevance, highlights skill gaps with evidence, and generates tailored application materials such as resume improvement suggestions, cover letter drafts, and interview preparation questions. The platform is designed as a full-stack application with authentication, persistent storage, semantic retrieval, structured AI outputs, and cross-session user memory.

## Team Members

- [Min-Chi Hong] - GitHub: [mhong26]
- [Shih-I Tsai] - GitHub: [stsai124]
- [Amie Nguyen] - GitHub: [honganhnguyen-lab]
- [Hung-Ju Lin] - GitHub: [NSYSUHermit]

## Development Environment Setup

### 1. Clone the repository

```bash
git clone https://github.com/your-org/careerpilot-ai.git
cd careerpilot-ai
```

### 2. Create environment files

Copy the sample environment file and fill in your own values:

```bash
cp .env.example .env
```

### 3. Start the database services

The project uses PostgreSQL and pgvector through Docker Compose.

```bash
docker compose up -d db
```

### 4. Backend setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### 5. Frontend setup

Open a second terminal:

```bash
cd frontend
npm install
npm run dev
```

### 6. Access the app

- Frontend: `http://localhost:3000`
- Backend API: `http://localhost:8000`
- API docs: `http://localhost:8000/docs`

## Required Tools and Dependencies

### Core tools
- Python 3.11+
- Node.js 20+
- npm 10+
- Docker and Docker Compose
- Git

### Backend dependencies
- FastAPI
- Uvicorn
- SQLAlchemy
- Alembic
- psycopg / asyncpg
- pgvector
- Pydantic
- python-multipart
- pytest

### Frontend dependencies
- Next.js
- React
- TypeScript
- Tailwind CSS
- React Query
- Zod

### AI / data dependencies
- OpenAI or Anthropic SDK (via provider wrapper)
- tiktoken or equivalent tokenizer utility
- PDF / DOCX text extraction library
- sentence-transformers or provider embedding SDK (if applicable)

## `.env.example`

This repository includes a sample `.env.example` file. At minimum, it should define values similar to the following:

```env
APP_ENV=development
FRONTEND_URL=http://localhost:3000
BACKEND_URL=http://localhost:8000

DATABASE_URL=postgresql://postgres:postgres@localhost:5432/careerpilot
VECTOR_DB_ENABLED=true

JWT_SECRET=replace_me
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60

LLM_PROVIDER=openai
OPENAI_API_KEY=replace_me
OPENAI_MODEL=gpt-4.1-mini
EMBEDDING_MODEL=text-embedding-3-large

MAX_UPLOAD_SIZE_MB=10
LOG_LEVEL=INFO
```