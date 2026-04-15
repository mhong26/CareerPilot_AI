# Software Requirements Specification (SRS)  
## Project Title
**CareerPilot AI: AI-Powered Resume and Job Match Platform**


## Prepared For
SER 594: AI for Software Engineers Course Project

---

# 1. Introduction

## 1.1 Purpose
This document defines the software requirements for **CareerPilot AI**. This system is intended for job seekers and provides features including resume upload, job posting import, job matching, skill gap analysis, customized resume suggestions, cover letter draft generation, interview preparation question generation, as well as memory for preferences and application history. This document also serves as the formal basis for team development, testing, acceptance, and course deliverables. The design objective of this system is also explicitly aligned with the course document’s requirement for a production-grade AI-powered system, rather than a simple chat interface.

## 1.2 Scope
CareerPilot AI is a full-stack AI job search assistance platform. Users can create accounts, log in, upload resumes, and input target job posting content. The system will structurally parse resumes and job postings, create vector indexes for job postings, perform job match ranking, use RAG to identify skill gaps, then use an agent workflow to generate resume revision suggestions, cover letter drafts, and interview preparation questions, while also remembering users’ job search preferences and application history across sessions. This design directly responds to the content of the 9th suggested project topic in the course document.

## 1.3 Definitions, Acronyms, and Abbreviations
- **SRS**: Software Requirements Specification  
- **RAG**: Retrieval-Augmented Generation  
- **LLM**: Large Language Model  
- **Embedding**: A method for converting text into vector representations  
- **Vector Store**: A database that stores vectors and supports similarity retrieval  
- **Structured Output**: JSON or typed objects output by an LLM according to a schema  
- **Agent Workflow**: An AI orchestration process that can make multi-step decisions, choose tools, and execute tasks  
- **Memory**: A mechanism for preserving user preferences, history, and summary information across sessions  
- **Match Score**: A quantitative score of how well a resume fits a job posting  

## 1.4 References
This SRS is written based on the course project specification document and is particularly aligned with the following requirements:  
- full-stack application  
- authentication mandatory  
- persistent data layer  
- AI integration such as RAG / embeddings / agent / structured outputs / memory  
- 15+ tests  
- CI  
- deployment  
- quantitative AI evaluation and system evaluation。

---

# 2. Overall Description

## 2.1 Product Perspective
CareerPilot AI is an independent web-based full-stack system composed of a frontend, backend, primary database, vector database, file processing module, AI orchestration layer, and evaluation module. The system is not merely a chat tool, but a complete job search workflow platform that meets the course requirements for multi-step workflows, persistent state, and domain-specific logic.

## 2.2 Product Functions
The system mainly provides the following high-level functions:

1. User registration, login, and logout  
2. Resume upload, text extraction, and structured parsing  
3. Job posting content import, structured parsing, and vector indexing  
4. Job match ranking and score explanation  
5. Skill gap analysis with RAG and source attribution  
6. Tailored resume suggestions  
7. Cover letter draft generation  
8. Interview preparation question generation  
9. Memory for user job search preferences and application history  
10. Application tracking  
11. AI result storage, version control, and feedback collection  
12. Support for evaluation, testing, deployment, and monitoring。

## 2.3 User Classes and Characteristics
### 2.3.1 End User (Job Seeker)
- Primary user
- No technical background required
- Wants to quickly analyze the degree of fit between themselves and job postings
- Wants actionable and specific resume and interview suggestions

### 2.3.2 Admin / Developer
- Maintains the system, reviews logs, performs evaluations, and fixes workflow errors
- Manages deployment, CI, and environment configuration

## 2.4 Operating Environment
- Frontend: modern browsers (Chrome, Edge, Firefox, Safari)
- Backend: Dockerized Linux environment
- Primary database: PostgreSQL
- Vector store: pgvector
- Backend language: Python
- Backend framework: FastAPI
- Frontend framework: React / Next.js
- AI provider: one of OpenAI / Anthropic / Gemini, abstracted through a replaceable wrapper  
All build / local deployment dependencies must be freely obtainable and reproducible.

## 2.5 Design and Implementation Constraints
1. The system must not be merely a chatbot wrapper.  
2. The system must not be just a single API call.  
3. Authentication is required.  
4. Persistent storage is required.  
5. If RAG/semantic search is used, a vector store is required.  
6. The system must be publicly deployable or reproducible locally with Docker.  
7. There must be at least 15 unit/integration tests.  
8. Quantitative AI evaluation and baseline comparison must be provided.  
9. CI is required.  
10. Secrets must not be committed to the repository.

## 2.6 Assumptions and Dependencies
1. Users can provide parseable resume files (PDF / DOCX / text).  
2. Users can paste or upload job descriptions.  
3. The LLM and embedding provider are accessible when the system runs.  
4. Docker, the database, and API keys will be correctly configured by the team.  
5. The evaluation dataset will be created by the team with at least 25 curated profiles / scenarios to support the final evaluation. This is consistent with the evaluation description for a resume/job match platform in the example topic.

---

# 3. System Features and Functional Requirements

The following requirements use **FR-x** numbering and are expressed using **shall**.

## 3.1 User Authentication and Account Management

### FR-1 User Registration
The system shall allow new users to complete registration with email and password.

### FR-2 User Login
The system shall allow registered users to log in and establish a protected session.

### FR-3 User Logout
The system shall allow users to log out and invalidate the current session.

### FR-4 Session Isolation
The system shall ensure that different users’ resumes, job postings, AI artifacts, preferences, and application records are isolated from one another.

### FR-5 Protected Routes
Unauthenticated users shall not access protected functions, including resume management, job analysis, and AI-generated results.

### FR-6 Persistent User Identity
The system shall still recognize the same user across sessions and load their historical data.  
The above directly aligns with the course document’s requirements for authentication and at least two distinct user sessions.

---

## 3.2 Resume Ingestion and Parsing

### FR-7 Resume Upload
The system shall allow users to upload resume files, supporting at least PDF and DOCX, and may optionally support direct text paste.

### FR-8 Resume Text Extraction
The system shall convert uploaded resumes into plain text and preserve both the original content and the extracted result.

### FR-9 Structured Resume Parsing
The system shall use an LLM and a structured output schema to parse resumes into structured data, including but not limited to:
- Basic information
- Summary
- Skills
- Work Experience
- Projects
- Education
- Certifications
- Skill evidence

### FR-10 Resume Validation
The system shall perform schema validation on parsing results. If the output format is incorrect, it shall execute retry or fallback parsing instead of failing directly.

### FR-11 Resume Editing
The system shall allow users to inspect and manually correct parsed resume results in the UI.

### FR-12 Resume Versioning
The system shall preserve resume versions to support comparison and rollback between the original version and AI-suggested versions.

---

## 3.3 Job Description Ingestion and Indexing

### FR-13 Job Input
The system shall allow users to add job descriptions, supporting at least text paste and file upload.

### FR-14 Job Parsing
The system shall parse job descriptions into structured fields, including but not limited to:
- Company
- Job title
- Responsibilities
- Required skills
- Preferred skills
- Qualifications
- Experience requirements
- Location / work mode

### FR-15 Job Chunking
The system shall split job descriptions into semantically meaningful chunks and preserve section metadata.

### FR-16 Job Embedding Generation
The system shall generate embeddings for each job chunk.

### FR-17 Vector Indexing
The system shall write job embeddings into the vector store and support subsequent similarity search.

### FR-18 Persistent Job Storage
The system shall persistently store the original job text, parsed results, chunks, and indexing metadata.  
These requirements align with the document’s requirements for the ingestion pipeline, chunking, and indexing for embeddings and RAG.

---

## 3.4 Job Matching and Ranking

### FR-19 Match Execution
The system shall allow users to select one resume and one or more job postings to perform match analysis.

### FR-20 Match Score
The system shall calculate a match score for each resume-job pair, and the score shall reflect at least:
- embedding similarity
- required skill coverage
- preferred skill coverage
- experience alignment
- optional rerank score

### FR-21 Ranked Results
The system shall provide a job list sorted by match score.

### FR-22 Match Explanation
The system shall provide a readable explanation for each match result, including:
- why it matches
- major skill overlap
- missing key skills
- risks or weaknesses

### FR-23 Match Persistence
The system shall preserve match results so users can review and compare them later.

---

## 3.5 Skill Gap Analysis with RAG

### FR-24 Skill Gap Workflow
The system shall be able to perform skill gap analysis for a specific job posting.

### FR-25 Retrieval
The system shall retrieve job chunks related to skill gaps from the vector store.

### FR-26 Re-ranking
The system shall support re-ranking of retrieval results to improve evidence relevance.

### FR-27 RAG Generation
The system shall use retrieved results and structured resume data to generate skill gap analysis.

### FR-28 Source Attribution
The system shall provide source attribution for each skill gap, indicating at least the corresponding job evidence.

### FR-29 Gap Severity
The system shall classify skill gaps into different severity levels, such as high / medium / low.

### FR-30 Improvement Suggestions
The system shall provide improvement suggestions for skill gaps.  
These requirements directly satisfy the document’s minimum expectations for RAG: chunking, retrieval, re-ranking, and generation with source attribution.

---

## 3.6 AI-Generated Resume Suggestions

### FR-31 Tailored Suggestions
The system shall generate customized resume suggestions for a specific job posting rather than general resume advice.

### FR-32 Section-Level Suggestions
The system shall allocate suggestions to specific sections, such as Summary, Experience, Projects, and Skills.

### FR-33 Bullet Rewrite Suggestions
The system shall provide bullet rewrite suggestions that can be directly adopted.

### FR-34 Keyword Suggestions
The system shall identify keywords and skill expressions that are recommended to be added or strengthened.

### FR-35 Reasoning Evidence
Each suggestion shall include reasons whenever possible, and shall align with job requirements or skill gap analysis.

### FR-36 Save Suggestions
The system shall preserve generated resume suggestions and their corresponding job context.

---

## 3.7 Cover Letter Generation

### FR-37 Cover Letter Draft
The system shall be able to generate a cover letter draft for a specific job.

### FR-38 Personalization
The cover letter shall be customized based on the following information:
- resume content
- target job requirements
- user preferences
- match / skill gap analysis results

### FR-39 Structured Cover Letter Output
The system shall manage the cover letter in at least structured sections such as intro, body, and closing, to facilitate editing and testing.

### FR-40 Editable Draft
Users shall be able to edit the cover letter in the UI and save the final version.

---

## 3.8 Interview Preparation Generation

### FR-41 Interview Questions
The system shall be able to generate interview preparation questions based on the job posting and the resume.

### FR-42 Question Categories
The system shall support at least the following types:
- technical
- behavioral
- project-based
- skill-gap-focused

### FR-43 Structured Output for Questions
Each question shall include at least:
- question
- category
- why it matters
- related resume area
- suggested answer outline

### FR-44 Persist Interview Prep
The system shall preserve generated interview preparation artifacts.

---

## 3.9 User Preferences and Memory

### FR-45 Preference Storage
The system shall preserve user preferences, including but not limited to:
- target roles
- preferred locations
- remote/hybrid/onsite preference
- industries
- preferred tone/style
- skills to strengthen

### FR-46 Preference Reuse
The system shall use preserved preferences in subsequent job ranking, cover letter generation, and interview prep generation.

### FR-47 Application History Memory
The system shall preserve the user’s application history and status.

### FR-48 Cross-Session Memory
The system shall preserve preferences and application history across login sessions.

### FR-49 Feedback Memory
The system shall be able to record user feedback on AI suggestions for subsequent optimization.  
These requirements align with the document’s requirements for persistent memory / user preference learning / application history tracking.

---

## 3.10 Application Tracking

### FR-50 Status Tracking
The system shall allow users to mark application status for job postings, such as:
- saved
- ready to apply
- applied
- interview
- rejected
- offer

### FR-51 Notes
The system shall allow users to save private notes.

### FR-52 Timeline View
The system shall be able to display the user’s job application history.

---

## 3.11 Feedback and Result Management

### FR-53 AI Feedback
The system shall allow users to provide ratings and text feedback on match results, resume suggestions, cover letters, or interview prep.

### FR-54 Artifact History
The system shall preserve the historical records of all generated artifacts.

### FR-55 Export Capability
The system shall support at least copying or exporting generated content in a readable format.

---

# 4. External Interface Requirements

## 4.1 User Interface Requirements
The system frontend shall provide the following pages or equivalent interfaces:

1. Login / Registration Page  
2. Dashboard  
3. Resume Management Page  
4. Job Management Page  
5. Job Detail / Match Analysis Page  
6. Application Kit Page  
7. Preferences Page  
8. Application Tracker Page  

The UI shall provide a clear operational flow and avoid degrading the primary use case into simple text chat.

## 4.2 API Interface Requirements
The frontend and backend shall communicate through well-defined APIs, preferably REST. Core endpoints include:

- `/auth/register`
- `/auth/login`
- `/auth/me`
- `/resumes/upload`
- `/resumes/current`
- `/jobs`
- `/matches/run`
- `/jobs/{id}/skill-gap`
- `/jobs/{id}/generate-application-kit`
- `/preferences`
- `/applications`
- `/feedback`

## 4.3 Hardware Interfaces
No special hardware requirements. A general personal computer and cloud deployment environment are sufficient.

## 4.4 Software Interfaces
- PostgreSQL
- pgvector
- LLM provider API
- Embedding API
- Docker / docker-compose
- CI system (GitHub Actions)  
The course document explicitly requires that tests be executable with a single command and that CI run on every push.

---

# 5. Data Requirements

## 5.1 Primary Entities
The system shall include at least the following main data entities:
- User
- Resume
- ResumeVersion
- Job
- JobChunk
- JobEmbedding
- MatchResult
- SkillGapReport
- GeneratedArtifact
- UserPreference
- ApplicationHistory
- AIFeedback

## 5.2 Persistence Requirements
All data must be persistently stored and must not exist only in memory. This requirement includes:
- user accounts
- original resume text and parsed results
- original job text and parsed results
- vector indexes
- match results
- AI-generated content
- preferences and history.  
This directly aligns with the course requirement for data persistence.

## 5.3 Data Integrity Requirements
1. All data shall include user ownership.  
2. Each artifact shall be associated with its source resume, job, and user.  
3. Structured outputs shall have schema validation.  
4. Deleting or replacing versions shall avoid damaging historical records.  

---

# 6. AI and Workflow Requirements

## 6.1 Required AI Techniques
This system will implement and declare the following AI techniques:

1. **Vector Search / Embeddings**  
2. **RAG**  
3. **AI Agents / Multi-Step Workflows**  
4. **Prompt Engineering with Structured Outputs**  
5. **Memory / Conversation Management**  
6. **LLM API Integration**  


## 6.2 Agent Requirements
### FR-56 Multi-Step Agent
The system shall implement at least one agent workflow responsible for determining and executing multi-step tasks from job and resume data.

### FR-57 Tool Selection
The agent shall be able to use at least 3 different tools / action types, such as:
- obtaining resume data
- retrieving job evidence
- calculating match scores
- generating structured content
- preserving artifacts

### FR-58 Conditional Logic
The agent shall be able to determine subsequent flow based on match score or skill gaps, such as performing gap analysis before resume tailoring.  
This satisfies the document’s minimum expectations for AI agents.

## 6.3 Structured Output Requirements
### FR-59 Structured Parsing
Resume parsing, job parsing, skill gap reports, interview prep, and cover letters shall all use structured outputs with verifiable schemas.

### FR-60 Malformed Output Handling
If the LLM returns a nonconforming format, the system shall automatically retry or fall back.

### FR-61 Auditability
The system shall preserve parsing failure or schema validation failure logs for debugging and evaluation.

## 6.4 LLM API Wrapper Requirements
### FR-62 Provider Abstraction
The system shall use a provider-agnostic wrapper to encapsulate LLM calls so that models can be replaced.

### FR-63 API Key Security
API keys shall not be written into the code repository and shall use environment variables.

### FR-64 Retry Logic
The system shall execute retry on transient API failures.

### FR-65 Cost and Token Tracking
The system shall record token usage, latency, and estimated cost.  

---

# 7. Non-Functional Requirements

## 7.1 Performance
### NFR-1 Latency
The system shall measure at least the p50 and p95 latency of the following operations:
- resume parsing
- job indexing
- match generation
- application kit generation  
The course document requires reporting latency for key user actions in the final deliverable.

### NFR-2 Responsiveness
General UI operations shall respond within a reasonable time, and long tasks shall display loading/progress status.

## 7.2 Reliability
### NFR-3 Error Handling
The system shall provide understandable error messages when failures occur in the LLM, embedding service, file processing, or database.

### NFR-4 Graceful Degradation
Failure of a single AI module shall not cause the entire system to crash.

### NFR-5 Error Rate Tracking
The system shall be able to track the error rate under normal usage conditions. This is one of the system evaluation metrics required by the document.

## 7.3 Security
### NFR-6 Credential Security
API keys, passwords, or credentials shall not be committed to the repository.

### NFR-7 Access Control
All user data shall be protected by authentication and authorization.

### NFR-8 Data Isolation
Data between different users shall not be visible to one another.

## 7.4 Maintainability
### NFR-9 Modular Architecture
The system shall adopt a modular design that separates frontend, backend, eval, tests, and docs.

### NFR-10 Repository Structure
The repository shall follow the course recommendation of a monorepo or at most two repos, and the structure shall be clear.

### NFR-11 Code Quality
The system shall use a formatter, explicit dependency management, and consistent naming conventions.

## 7.5 Testability
### NFR-12 Automated Tests
The system shall include at least 15 unit/integration tests covering APIs, the data layer, the AI pipeline, and authentication.

### NFR-13 CI
The system shall automatically run tests through GitHub Actions on every push.  

## 7.6 Deployability
### NFR-14 Public or Docker Deployment
The system shall provide a public URL, or provide a fully reproducible Docker deployment.

### NFR-15 Setup Reproducibility
The README and `.env.example` shall be sufficient for the teaching assistant to build and run the system in a clean environment.

---

# 8. Testing and Evaluation Requirements

## 8.1 Test Suite Requirements
### TR-1 Minimum Test Count
The system shall provide at least 15 unit/integration tests.

### TR-2 Coverage Areas
Tests shall cover at least:
- authentication
- API endpoints
- data layer operations
- AI pipeline components

### TR-3 Single Command Execution
All tests shall be executable with a single command.

### TR-4 CI Execution
All tests shall be automatically executed in GitHub Actions.  

## 8.2 AI Evaluation Requirements
### ER-1 Quantitative Metrics
The system shall define and calculate at least two quantitative AI metrics.

### ER-2 Baseline Comparison
All major AI metrics shall be compared against a baseline.

### ER-3 Suggested Metrics for This Project
This project shall implement at least the following two:
1. **Job matching relevance metric**  
   For example, Precision@K, MRR, or a custom relevance score  
2. **Resume suggestion quality metric**  
   For example, a rubric-based average score  

### ER-4 Evaluation Dataset
At least 25 curated profile / job scenario pairs shall be established for evaluation.

## 8.3 System Evaluation Requirements
### ER-5 System Metrics
The following shall be reported:
- p50 latency
- p95 latency
- error rate
- test coverage percentage  
These are the system-level quality metrics required by the document.

---

# 9. Architecture Requirements

## 9.1 High-Level Architecture
The system shall include the following core layers:

1. **Frontend Layer**  
   - React
   - dashboard, forms, results, editing interface  

2. **Backend API Layer**  
   - authentication  
   - business logic  
   - orchestration  
   - persistence  

3. **AI Services Layer**  
   - resume parser  
   - job parser  
   - embedding/indexing  
   - retrieval/rerank  
   - agent workflow  
   - content generation  
   - memory service  

4. **Data Layer**  
   - PostgreSQL  
   - pgvector  
   - file storage  

5. **Evaluation Layer**  
   - metrics scripts  
   - baseline comparison  
   - reporting outputs  



## 9.2 Recommended Repository Structure
```text
project-root/
├── frontend/
├── backend/
├── eval/
├── tests/
├── docs/
├── .github/workflows/
├── docker-compose.yml
├── .env.example
└── README.md
```


---

# 10. Acceptance Criteria

This system shall be considered complete and ready for submission only if it satisfies at least the following acceptance criteria:

1. Users can complete registration and login, and data is isolated across different accounts.  
2. Users can upload resumes and obtain editable structured parsing results.  
3. Users can add job descriptions, and the system can build indexes and preserve them.  
4. The system can generate job match rankings and match explanations.  
5. The system can perform skill gap analysis with source attribution.  
6. The system can generate resume suggestions, cover letters, and interview prep for a specific job.  
7. The system can preserve user preferences and application history across sessions.  
8. The system implements at least 3 in-depth AI techniques.  
9. The system includes at least 15 automated tests and CI.  
10. The system has a deployment result or a Docker reproduction method.  
11. The system provides at least two quantitative AI metrics and baseline comparison.  
12. The system is not merely a chatbot wrapper, nor a single API call system.  

---

# 11. Out of Scope

The following are not included in the scope of v1.0 for now:
- Automatically scraping all job postings from external job boards
- Real-time official LinkedIn / Indeed integration
- Multilingual resume optimization
- Real-time voice interview simulation
- Multi-tenant enterprise admin backend

---

# 12. Risks and Mitigation

## Risk 1: Unstable LLM Output
**Mitigation:** structured outputs, schema validation, retry, fallback parser

## Risk 2: Insufficient Job Matching Quality
**Mitigation:** hybrid ranking strategy, reranking, manually labeled datasets, and baseline evaluation

## Risk 3: Excessive Cost or Unstable APIs
**Mitigation:** provider wrapper, token tracking, caching, limiting generation length
