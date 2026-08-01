# ANVESHA — Multi-Modal Knowledge Graph Enterprise Compliance Intelligence

> **GraphRAG-powered compliance Q&A with zero hallucinations.**

ANVESHA synthesizes fragmented compliance data from PDFs, audio recordings, structured tables, and system schematics into a unified knowledge graph, then answers compliance questions with traceable citations and cross-provider verification.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Frontend (HTML/CSS/JS) — Premium Dark Dashboard             │
│  ┌─────────┐  ┌──────────────┐  ┌─────────────────────────┐ │
│  │Doc Panel│  │Chat + Graph  │  │Details + Citations      │ │
│  │Upload   │  │vis-network.js│  │Confidence + Evidence    │ │
│  └────┬────┘  └──────┬───────┘  └─────────────────────────┘ │
├───────┼──────────────┼───────────────────────────────────────┤
│       │    FastAPI    │   Backend                             │
│  ┌────▼────┐  ┌──────▼───────┐  ┌────────────────┐          │
│  │Ingest   │  │GraphRAG      │  │Verification    │          │
│  │PDF/Audio│  │Query Engine  │  │Entailment Gate │          │
│  │Table    │  │Vector + k-hop│  │Cross-Provider  │          │
│  │Schematic│  └──────┬───────┘  │Groq → Gemini   │          │
│  └────┬────┘         │          └────────────────┘          │
│       │         ┌────▼─────┐                                 │
│  ┌────▼────┐    │Neo4j     │    ┌────────────────┐          │
│  │Extractor│    │AuraDB    │    │Evidence Bundle │          │
│  │Ontology │───►│Knowledge │    │SHA-256 Signed  │          │
│  │LLM (Groq)│   │Graph     │    └────────────────┘          │
│  └─────────┘    └──────────┘                                 │
└──────────────────────────────────────────────────────────────┘
```

## Key Features

### 1. Multi-Modal Ingestion
- **PDF**: pdfplumber + Tesseract OCR fallback, page-level provenance
- **Audio**: Groq Whisper API, timestamped segment-level chunks
- **Tables**: pdfplumber + camelot, cell-level provenance
- **Schematics**: Gemini Vision analysis, structured entity extraction

### 2. Knowledge Graph Construction
- **Ontology**: 9 core entity types (Regulation, Requirement, Control, System, Asset, Evidence, Policy, Person, Incident)
- **Extraction**: LLM-based with span validation and confidence scoring
- **Normalization**: Automatic type normalization (80+ variant → core mapping)
- **Deduplication**: Cross-chunk entity and relationship deduplication

### 3. GraphRAG Retrieval
- **Vector Search**: Query embedding → Neo4j vector index → seed entities
- **k-hop Traversal**: Cypher-based graph traversal for evidence subgraph
- **Evidence Serialization**: Structured context with source provenance
- **Fallback**: In-memory keyword search when Neo4j is offline

### 4. Verification Gate (Zero Hallucinations)
- **Claim Decomposition**: Answers split into atomic verifiable claims
- **Lexical Pre-filter**: Cheap word/entity overlap check (no API call)
- **Cross-Provider Entailment**: Groq generates → Gemini verifies (uncorrelated errors)
- **Abstention**: System refuses to answer when evidence is insufficient
- **Evidence Bundles**: SHA-256 signed JSON for tamper detection

### 5. Evaluation Harness
- 15 gold standard questions (basic, multi-hop, analytical, multi-modal, trap)
- 5 metrics: abstention accuracy, keyword recall, citation presence, confidence calibration, entity coverage
- Entity extraction F1 (precision, recall)
- Per-difficulty breakdown

## Quick Start

### Prerequisites
- Python 3.11+
- Neo4j AuraDB account (free tier)
- Groq API key
- Gemini API key

### Setup

```bash
# Clone
git clone https://github.com/RajvardhanS-Patil/ANVESHA.git
cd ANVESHA

# Create virtual environment
python -m venv .venv
.venv/Scripts/activate  # Windows
# source .venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Run
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open [http://localhost:8000](http://localhost:8000) to access the dashboard.

### Docker

```bash
docker build -t anvesha .
docker run -p 8000:8000 --env-file .env anvesha
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | System health + provider status |
| `POST` | `/api/ingest` | Upload and ingest a file |
| `GET` | `/api/documents` | List ingested documents |
| `POST` | `/api/query` | Ask a compliance question (GraphRAG) |
| `POST` | `/api/query/verified` | Ask with full verification pipeline |
| `GET` | `/api/evidence/{id}` | Download signed evidence bundle |
| `GET` | `/api/graph` | Get full knowledge graph |
| `POST` | `/api/eval/run` | Run evaluation harness |
| `GET` | `/api/eval/gold-set` | View gold standard questions |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | Yes | Primary LLM (Llama 3.3 70B) + Whisper ASR |
| `GEMINI_API_KEY` | Yes | Verification LLM + Vision + Embeddings |
| `NEO4J_URI` | Yes | Neo4j AuraDB connection URI |
| `NEO4J_USER` | Yes | Neo4j username |
| `NEO4J_PASSWORD` | Yes | Neo4j password |
| `OPENROUTER_API_KEY` | No | Fallback LLM provider |
| `APP_ENV` | No | `development` or `production` |

## Deployment (Render Free Tier)

1. Push to GitHub
2. Connect repository on [Render](https://render.com)
3. Set environment variables in Render dashboard
4. Deploy — `render.yaml` handles the rest

**Constraints**: 512MB RAM, 1 worker, auto-sleep after 15 min inactivity.

## Tech Stack

- **Backend**: FastAPI, Python 3.11
- **Graph DB**: Neo4j AuraDB (managed)
- **LLMs**: Groq (Llama 3.3 70B), Google Gemini 2.0 Flash
- **ASR**: Groq Whisper (large-v3-turbo)
- **PDF**: pdfplumber, pytesseract
- **Audio**: Groq Whisper API
- **Tables**: pdfplumber, camelot-py
- **Vision**: Gemini Vision API
- **Frontend**: Vanilla HTML/CSS/JS, vis-network.js
- **Deployment**: Docker, Render

## License

MIT
