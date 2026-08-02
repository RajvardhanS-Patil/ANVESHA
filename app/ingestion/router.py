"""
ANVESHA Ingestion Router — FastAPI endpoint for file upload and ingestion.

Auto-detects file type and routes to the correct ingestion pipeline.
Returns ingestion status + extracted chunk count.
"""

import logging
import os
import uuid
from typing import Optional

from fastapi import APIRouter, UploadFile, File, HTTPException, Form, BackgroundTasks
from fastapi.responses import JSONResponse

from app.ingestion.base import IngestionResult
from app.ingestion.pdf import ingest_pdf
from app.ingestion.audio import ingest_audio, SUPPORTED_AUDIO_FORMATS
from app.ingestion.table import ingest_tables
from app.ingestion.schematic import ingest_schematic, SUPPORTED_IMAGE_FORMATS
from app.extraction.extractor import extract_entities_from_chunks
from app.graph.writer import (
    write_document_to_graph,
    write_entities_to_graph,
    write_relationships_to_graph,
    write_mention_edges,
    get_full_graph,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# File type detection
PDF_EXTENSIONS = {".pdf"}
AUDIO_EXTENSIONS = SUPPORTED_AUDIO_FORMATS
IMAGE_EXTENSIONS = SUPPORTED_IMAGE_FORMATS

# MIME type mapping for images
IMAGE_MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".tiff": "image/tiff",
}

# In-memory document store (for now — will be replaced by Neo4j in Phase 2)
_document_store: dict[str, IngestionResult] = {}
_chunk_store: dict[str, dict] = {}
_raw_file_store: dict[str, tuple[bytes, str]] = {}  # doc_id -> (raw_bytes, filename)

# Processing status tracker — allows the frontend to poll for progress
_processing_status: dict[str, dict] = {}


def get_document_store() -> dict:
    """Get the in-memory document store."""
    return _document_store


def get_chunk_store() -> dict:
    """Get the in-memory chunk store."""
    return _chunk_store


def get_raw_file_store() -> dict:
    """Get the in-memory raw file bytes store."""
    return _raw_file_store


async def _process_full_ingestion(
    doc_id: str,
    file_data: bytes,
    filename: str,
    ext: str,
    extract_tables_flag: bool,
    language: str,
):
    """
    Background task: runs the FULL ingestion pipeline (file parsing + entity
    extraction + graph writes). Updates _processing_status at each phase so
    the frontend can poll for progress.
    """
    import time

    start_time = time.perf_counter()

    try:
        # ── Phase 1: File-type routing → parse chunks ──────────────────
        _processing_status[doc_id]["phase"] = "ingesting"
        _processing_status[doc_id]["detail"] = "Parsing document..."
        logger.info(f"[BG] Phase 1 — ingesting {filename}")

        results: list[IngestionResult] = []

        if ext in PDF_EXTENSIONS:
            pdf_result = await ingest_pdf(file_data, filename, doc_id)
            results.append(pdf_result)

            if extract_tables_flag:
                _processing_status[doc_id]["detail"] = "Extracting tables..."
                table_result = await ingest_tables(file_data, filename, doc_id)
                if table_result.chunks:
                    results.append(table_result)

        elif ext in AUDIO_EXTENSIONS:
            audio_result = await ingest_audio(
                file_data, filename, doc_id, language=language
            )
            results.append(audio_result)

        elif ext in IMAGE_EXTENSIONS:
            mime = IMAGE_MIME_MAP.get(ext, "image/png")
            schematic_result = await ingest_schematic(
                file_data, filename, doc_id, mime_type=mime
            )
            results.append(schematic_result)

        # Merge results
        all_chunks = []
        total_errors = []
        for r in results:
            all_chunks.extend(r.chunks)
            if r.error:
                total_errors.append(r.error)

        merged = IngestionResult(
            doc_id=doc_id,
            filename=filename,
            content_type=ext.lstrip("."),
            chunks=all_chunks,
            total_chunks=len(all_chunks),
            status="success" if all_chunks else "error",
            error="; ".join(total_errors) if total_errors else None,
            processing_time_seconds=sum(r.processing_time_seconds for r in results),
        )
        _document_store[doc_id] = merged

        # Store individual chunks for retrieval
        for chunk in all_chunks:
            _chunk_store[chunk.chunk_id] = chunk.to_dict()

        _processing_status[doc_id]["total_chunks"] = len(all_chunks)

        if not all_chunks:
            _processing_status[doc_id]["phase"] = "error"
            _processing_status[doc_id]["error"] = merged.error or "No content extracted"
            return

        # ── Phase 2: Entity extraction + graph write ───────────────────
        _processing_status[doc_id]["phase"] = "extracting"
        _processing_status[doc_id]["detail"] = "Extracting entities & building graph..."
        logger.info(f"[BG] Phase 2 — extracting entities for {filename}")

        try:
            extraction_result = await extract_entities_from_chunks(all_chunks)
            await write_document_to_graph(merged)
            await write_entities_to_graph(extraction_result["entities"])
            await write_relationships_to_graph(extraction_result["relationships"])
            await write_mention_edges(extraction_result["entities"])
            _processing_status[doc_id]["unique_entities"] = len(extraction_result.get("entities", []))
        except Exception as e:
            logger.error(f"[BG] Extraction/graph write failed for {filename}: {e}")
            # Non-fatal: ingestion succeeded, extraction failed
            _processing_status[doc_id]["extraction_error"] = str(e)

        # ── Done ───────────────────────────────────────────────────────
        elapsed = time.perf_counter() - start_time
        _processing_status[doc_id]["phase"] = "complete"
        _processing_status[doc_id]["detail"] = "Ingestion complete"
        _processing_status[doc_id]["processing_time_seconds"] = round(elapsed, 2)
        logger.info(
            f"[BG] Ingestion complete: {filename} — {len(all_chunks)} chunks in {elapsed:.2f}s"
        )

    except Exception as e:
        logger.error(f"[BG] Full ingestion failed for {filename}: {e}")
        _processing_status[doc_id]["phase"] = "error"
        _processing_status[doc_id]["error"] = str(e)


@router.post("/ingest")
async def ingest_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    extract_tables: Optional[bool] = Form(default=True),
    language: Optional[str] = Form(default="en"),
):
    """
    Upload and ingest a file into the knowledge graph.

    Returns immediately with a doc_id. All heavy processing (parsing, entity
    extraction, graph writes) runs in a background task to avoid Render's
    30-second proxy timeout. Poll GET /ingest/status/{doc_id} for progress.

    Supported formats:
    - PDF (.pdf) — text extraction + OCR fallback
    - Audio (.wav, .mp3, .m4a, .ogg, .flac, .webm) — Whisper transcription
    - Images (.png, .jpg, .svg, etc.) — Gemini Vision analysis
    - Tables are auto-extracted from PDFs
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    doc_id = str(uuid.uuid4())

    # Validate file type before accepting
    all_supported = PDF_EXTENSIONS | AUDIO_EXTENSIONS | IMAGE_EXTENSIONS
    if ext not in all_supported:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext}. "
            f"Supported: PDF, Audio ({', '.join(AUDIO_EXTENSIONS)}), "
            f"Images ({', '.join(IMAGE_EXTENSIONS)})"
        )

    logger.info(f"Ingest request: {filename} (ext={ext}, doc_id={doc_id})")

    # Read file data (fast — just buffering bytes)
    try:
        file_data = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {e}")

    if not file_data:
        raise HTTPException(status_code=400, detail="Empty file")

    # Store raw file bytes for later annotated export
    _raw_file_store[doc_id] = (file_data, filename)

    # Initialize processing status
    _processing_status[doc_id] = {
        "doc_id": doc_id,
        "filename": filename,
        "phase": "accepted",
        "detail": "File accepted, queued for processing...",
        "total_chunks": 0,
        "unique_entities": 0,
        "error": None,
        "extraction_error": None,
        "processing_time_seconds": 0,
    }

    # Launch ALL processing in background — response returns instantly
    background_tasks.add_task(
        _process_full_ingestion,
        doc_id, file_data, filename, ext,
        extract_tables or True, language or "en",
    )

    logger.info(f"File accepted, background ingestion launched: {filename} (doc_id={doc_id})")

    return {
        "doc_id": doc_id,
        "filename": filename,
        "status": "processing",
    }


@router.get("/ingest/status/{doc_id}")
async def get_ingestion_status(doc_id: str):
    """
    Poll for the processing status of an ingestion job.

    Phases: accepted → ingesting → extracting → complete (or error)
    """
    status = _processing_status.get(doc_id)
    if not status:
        # Check if it's already in the document store (from a previous run)
        if doc_id in _document_store:
            doc = _document_store[doc_id]
            return {
                "doc_id": doc_id,
                "filename": doc.filename,
                "phase": "complete",
                "detail": "Ingestion complete",
                "total_chunks": doc.total_chunks,
                "unique_entities": 0,
                "error": doc.error,
                "processing_time_seconds": round(doc.processing_time_seconds, 2),
            }
        raise HTTPException(status_code=404, detail=f"No ingestion job found for doc_id: {doc_id}")
    return status


@router.get("/documents")
async def list_documents():
    """List all ingested documents."""
    return {
        "total_documents": len(_document_store),
        "documents": [
            {
                "doc_id": doc_id,
                "filename": result.filename,
                "content_type": result.content_type,
                "total_chunks": result.total_chunks,
                "status": result.status,
            }
            for doc_id, result in _document_store.items()
        ],
    }


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str):
    """Get details of a specific ingested document."""
    if doc_id not in _document_store:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")

    result = _document_store[doc_id]
    return result.to_dict()


@router.get("/chunks/{chunk_id}")
async def get_chunk(chunk_id: str):
    """Get a specific ingested chunk."""
    if chunk_id not in _chunk_store:
        raise HTTPException(status_code=404, detail=f"Chunk {chunk_id} not found")

    return _chunk_store[chunk_id]


@router.get("/graph")
async def get_graph(as_of: Optional[str] = None):
    """Get the full knowledge graph for visualization (supports time-travel filtering)."""
    return await get_full_graph(as_of=as_of)


from pydantic import BaseModel, Field

class CustomEntityRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Entity name")
    entity_type: str = Field(..., description="Ontology entity type")
    description: Optional[str] = Field(default="")

class CustomRelationshipRequest(BaseModel):
    source_entity: str = Field(..., description="Source entity name")
    target_entity: str = Field(..., description="Target entity name")
    relation_type: str = Field(..., description="Ontology relationship type")
    description: Optional[str] = Field(default="")


@router.post("/graph/entity")
async def add_custom_entity(request: CustomEntityRequest):
    """Add a custom entity to the knowledge graph."""
    from app.graph.writer import write_entities_to_graph
    from app.extraction.ontology import normalize_entity_type
    
    normalized_type = normalize_entity_type(request.entity_type)
    
    entity = {
        "name": request.name.strip(),
        "entity_type": normalized_type,
        "description": request.description.strip(),
        "source_filename": "Manual Curation",
        "extraction_confidence": 1.0
    }
    
    result = await write_entities_to_graph([entity], generate_embeddings=True)
    return result


@router.post("/graph/relationship")
async def add_custom_relationship(request: CustomRelationshipRequest):
    """Add a custom relationship to the knowledge graph."""
    from app.graph.writer import write_relationships_to_graph
    from app.extraction.ontology import normalize_relation_type
    import uuid
    
    normalized_rel = normalize_relation_type(request.relation_type)
    
    relationship = {
        "id": str(uuid.uuid4()),
        "source_entity": request.source_entity.strip(),
        "target_entity": request.target_entity.strip(),
        "relation_type": normalized_rel,
        "description": request.description.strip(),
        "source_filename": "Manual Curation"
    }
    
    result = await write_relationships_to_graph([relationship])
    return result


@router.post("/ingest/analyze/{doc_id}")
async def analyze_document(doc_id: str):
    """
    Run the full ANVESHA pipeline for a specific uploaded document:
    1. Retrieve the document's chunks from the store
    2. Build a compliance context from chunk text
    3. Run the Multi-Agent Debate (Advocate vs Skeptic vs Judge)
    4. Run the Compliance Gap Analysis
    5. Return combined result with compliance score and debate transcript
    """
    if doc_id not in _document_store:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found. Upload it first.")

    doc = _document_store[doc_id]
    filename = doc.filename

    # Build context text from the document's chunks
    doc_chunks = [c for cid, c in _chunk_store.items() if c.get("doc_id") == doc_id]
    if not doc_chunks:
        # Also try searching by filename match in chunk store
        doc_chunks = [c for cid, c in _chunk_store.items() if c.get("source_filename") == filename]

    if not doc_chunks:
        # Fall back: use all chunks in store (we have the doc but chunks may use different key)
        doc_chunks = list(_chunk_store.values())[:20]  # first 20 chunks

    # Build a condensed text context (first 4000 chars from doc)
    context_parts = []
    for chunk in doc_chunks[:15]:
        text = chunk.get("raw_text", "")
        if text:
            context_parts.append(f"[{chunk.get('citation', filename)}]\n{text[:500]}")

    doc_context = "\n\n---\n\n".join(context_parts) if context_parts else f"Document: {filename}"

    # Build the debate question for this specific document
    debate_question = (
        f"Perform a comprehensive compliance analysis of the uploaded document '{filename}'. "
        f"Identify all compliance gaps, regulatory risks, and control deficiencies present in this document. "
        f"Assess against GDPR, ISO 27001, SOC2, and other applicable frameworks. "
        f"Document Context:\n\n{doc_context[:3000]}"
    )

    logger.info(f"Starting compliance debate for doc {doc_id} ({filename})")

    try:
        from app.retrieval.debate import run_compliance_debate
        debate_result = await run_compliance_debate(
            question=debate_question,
            k_hops=2,
            top_k_seeds=8,
            doc_id=doc_id,
        )
    except Exception as e:
        logger.error(f"Debate failed for {doc_id}: {e}")
        debate_result = {
            "verdict": "PARTIAL",
            "confidence": 40,
            "answer": f"Debate engine encountered an error: {e}. Please retry.",
            "advocate_argument": "Analysis in progress...",
            "skeptic_argument": "Analysis in progress...",
            "citations": [],
            "debate_mode": True,
        }

    # Run the gap analysis to get a structured compliance report
    try:
        from app.audit.gap_analysis import run_gap_analysis, get_audit_reports
        audit_report = await run_gap_analysis(doc_id=doc_id)
        report_id = audit_report["report_id"]
    except Exception as e:
        logger.error(f"Gap analysis failed for {doc_id}: {e}")
        audit_report = None
        report_id = None

    return {
        "doc_id": doc_id,
        "filename": filename,
        "debate": {
            "verdict": debate_result.get("verdict", "PARTIAL"),
            "confidence": debate_result.get("confidence", 0),
            "advocate_argument": debate_result.get("advocate_argument", ""),
            "skeptic_argument": debate_result.get("skeptic_argument", ""),
            "judge_ruling": debate_result.get("answer", ""),
            "citations": debate_result.get("citations", []),
        },
        "compliance_report": {
            "report_id": report_id,
            "compliance_score": audit_report.get("compliance_score", 0) if audit_report else 0,
            "summary": audit_report.get("summary", {}) if audit_report else {},
        },
        "status": "complete"
    }
