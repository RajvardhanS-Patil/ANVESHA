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


def get_document_store() -> dict:
    """Get the in-memory document store."""
    return _document_store


def get_chunk_store() -> dict:
    """Get the in-memory chunk store."""
    return _chunk_store


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
