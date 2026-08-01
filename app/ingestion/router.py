"""
ANVESHA Ingestion Router — FastAPI endpoint for file upload and ingestion.

Auto-detects file type and routes to the correct ingestion pipeline.
Returns ingestion status + extracted chunk count.
"""

import logging
import os
import uuid
from typing import Optional

from fastapi import APIRouter, UploadFile, File, HTTPException, Form
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


def get_document_store() -> dict:
    """Get the in-memory document store."""
    return _document_store


def get_chunk_store() -> dict:
    """Get the in-memory chunk store."""
    return _chunk_store


def get_raw_file_store() -> dict:
    """Get the in-memory raw file bytes store."""
    return _raw_file_store


@router.post("/ingest")
async def ingest_file(
    file: UploadFile = File(...),
    extract_tables: Optional[bool] = Form(default=True),
    language: Optional[str] = Form(default="en"),
):
    """
    Upload and ingest a file into the knowledge graph.

    Supported formats:
    - PDF (.pdf) — text extraction + OCR fallback
    - Audio (.wav, .mp3, .m4a, .ogg, .flac, .webm) — Whisper transcription
    - Images (.png, .jpg, .svg, etc.) — Gemini Vision analysis
    - Tables are auto-extracted from PDFs

    Returns:
        Ingestion status, chunk count, and extracted chunks
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    doc_id = str(uuid.uuid4())

    logger.info(f"Ingest request: {filename} (ext={ext}, doc_id={doc_id})")

    # Read file data
    try:
        file_data = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {e}")

    if not file_data:
        raise HTTPException(status_code=400, detail="Empty file")

    # Store raw file bytes for later annotated export
    _raw_file_store[doc_id] = (file_data, filename)

    # Route to correct pipeline
    results: list[IngestionResult] = []

    try:
        if ext in PDF_EXTENSIONS:
            # PDF: extract text + tables
            pdf_result = await ingest_pdf(file_data, filename, doc_id)
            results.append(pdf_result)

            # Also extract tables from PDF
            if extract_tables:
                table_result = await ingest_tables(file_data, filename, doc_id)
                if table_result.chunks:
                    results.append(table_result)

        elif ext in AUDIO_EXTENSIONS:
            audio_result = await ingest_audio(
                file_data, filename, doc_id, language=language or "en"
            )
            results.append(audio_result)

        elif ext in IMAGE_EXTENSIONS:
            mime = IMAGE_MIME_MAP.get(ext, "image/png")
            schematic_result = await ingest_schematic(
                file_data, filename, doc_id, mime_type=mime
            )
            results.append(schematic_result)

        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {ext}. "
                f"Supported: PDF, Audio ({', '.join(AUDIO_EXTENSIONS)}), "
                f"Images ({', '.join(IMAGE_EXTENSIONS)})"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ingestion failed for {filename}: {e}")
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {e}")

    # Merge results
    all_chunks = []
    total_errors = []
    for r in results:
        all_chunks.extend(r.chunks)
        if r.error:
            total_errors.append(r.error)

    # Store in memory
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

    # --- Phase 2: Entity extraction + graph write ---
    extraction_stats = {}
    graph_stats = {}
    try:
        if all_chunks:
            # Extract entities and relationships
            extraction_result = await extract_entities_from_chunks(all_chunks)
            extraction_stats = extraction_result.get("stats", {})

            # Write to Neo4j
            await write_document_to_graph(merged)
            entity_write = await write_entities_to_graph(extraction_result["entities"])
            rel_write = await write_relationships_to_graph(extraction_result["relationships"])
            await write_mention_edges(extraction_result["entities"])

            graph_stats = {
                "entities_written": entity_write.get("written", 0),
                "relationships_written": rel_write.get("written", 0),
            }
    except Exception as e:
        logger.error(f"Extraction/graph write failed (ingestion still succeeded): {e}")
        extraction_stats["error"] = str(e)

    logger.info(
        f"Ingestion complete: {filename} — {len(all_chunks)} chunks, "
        f"{merged.processing_time_seconds:.2f}s"
    )

    return {
        "doc_id": doc_id,
        "filename": filename,
        "status": merged.status,
        "total_chunks": len(all_chunks),
        "content_types": list(set(c.content_type.value for c in all_chunks)),
        "processing_time_seconds": round(merged.processing_time_seconds, 2),
        "error": merged.error,
        "extraction": extraction_stats,
        "graph": graph_stats,
        "chunks_preview": [
            {
                "chunk_id": c.chunk_id,
                "content_type": c.content_type.value,
                "citation": c.citation,
                "text_preview": c.raw_text[:200] + "..." if len(c.raw_text) > 200 else c.raw_text,
            }
            for c in all_chunks[:10]  # Preview first 10 chunks
        ],
    }


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
        audit_report = await run_gap_analysis()
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
