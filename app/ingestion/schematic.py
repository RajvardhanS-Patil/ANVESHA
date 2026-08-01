"""
ANVESHA Schematic Ingestion — Analyze diagrams/schematics via Gemini Vision.

Pipeline:
1. Accept image files (PNG, JPG, SVG, etc.)
2. Send to Gemini Flash Vision API for analysis
3. Extract components, relationships, and labels with bounding box provenance
"""

import logging
import time
from typing import Optional

from app.ingestion.base import (
    IngestedChunk,
    IngestionResult,
    ContentType,
    SourceLocation,
    SourceLocationType,
)
from app.providers.llm_router import get_llm_router

logger = logging.getLogger(__name__)

# Supported image formats
SUPPORTED_IMAGE_FORMATS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".tiff"}

# Vision analysis prompt
SCHEMATIC_ANALYSIS_PROMPT = """Analyze this technical diagram, schematic, or system architecture image.

Extract the following information in a structured format:

1. **Components/Entities**: List all systems, components, modules, services, databases, 
   or other identifiable entities in the diagram. For each, provide:
   - Name
   - Type (system, database, service, network, user, process, etc.)
   - Description (brief)

2. **Relationships/Connections**: List all connections, data flows, or relationships 
   between components. For each, provide:
   - Source component
   - Target component
   - Relationship type (connects_to, sends_data_to, depends_on, contains, etc.)
   - Label (if any arrow/line label is visible)

3. **Labels and Annotations**: Any text labels, notes, or annotations visible in the diagram.

4. **Overall Description**: A comprehensive text description of what this diagram represents.

Format your response as a detailed textual description that captures ALL information 
visible in the diagram, including spatial relationships and groupings."""


async def ingest_schematic(
    file_data: bytes,
    filename: str,
    doc_id: str,
    mime_type: str = "image/png",
) -> IngestionResult:
    """
    Ingest a schematic/diagram using Gemini Vision API.

    Args:
        file_data: Raw image bytes
        filename: Original filename
        doc_id: Unique document identifier
        mime_type: Image MIME type

    Returns:
        IngestionResult with analyzed schematic chunks
    """
    start_time = time.perf_counter()

    logger.info(f"Ingesting schematic: {filename} ({len(file_data)} bytes, {mime_type})")

    try:
        router = get_llm_router()

        # Analyze with Gemini Vision
        analysis = await router.vision(
            image_data=file_data,
            prompt=SCHEMATIC_ANALYSIS_PROMPT,
            mime_type=mime_type,
        )

        if not analysis or not analysis.strip():
            return IngestionResult(
                doc_id=doc_id,
                filename=filename,
                content_type="schematic",
                chunks=[],
                status="error",
                error="Vision API returned empty analysis",
                processing_time_seconds=time.perf_counter() - start_time,
            )

        # Create chunk with the full analysis
        chunk = IngestedChunk(
            source_doc_id=doc_id,
            source_filename=filename,
            content_type=ContentType.SCHEMATIC,
            raw_text=analysis,
            cleaned_text=analysis,
            source_location=SourceLocation(
                type=SourceLocationType.BBOX,
                bbox=(0, 0, 1, 1),  # Full image
            ),
            metadata={
                "mime_type": mime_type,
                "file_size_bytes": len(file_data),
                "analysis_method": "gemini_vision",
            },
        )

        # Try to extract structured entities from the analysis
        try:
            structured = await _extract_structured_from_analysis(router, analysis)
            if structured:
                chunk.vision_entities = structured.get("entities", [])
                chunk.vision_relationships = structured.get("relationships", [])
        except Exception as e:
            logger.warning(f"Structured extraction from vision analysis failed: {e}")

        elapsed = time.perf_counter() - start_time
        logger.info(f"Schematic ingestion complete: {filename} — {len(analysis)} chars in {elapsed:.2f}s")

        return IngestionResult(
            doc_id=doc_id,
            filename=filename,
            content_type="schematic",
            chunks=[chunk],
            total_chunks=1,
            status="success",
            processing_time_seconds=elapsed,
        )

    except Exception as e:
        logger.error(f"Schematic ingestion failed for {filename}: {e}")
        return IngestionResult(
            doc_id=doc_id,
            filename=filename,
            content_type="schematic",
            chunks=[],
            status="error",
            error=str(e),
            processing_time_seconds=time.perf_counter() - start_time,
        )


async def _extract_structured_from_analysis(router, analysis: str) -> Optional[dict]:
    """
    Extract structured entities and relationships from the vision analysis text.
    Uses LLM to parse the free-form analysis into structured JSON.
    """
    extraction_prompt = f"""Given the following analysis of a technical diagram, extract structured data.

Analysis:
{analysis}

Return a JSON object with:
{{
    "entities": [
        {{"name": "...", "type": "...", "description": "..."}}
    ],
    "relationships": [
        {{"source": "...", "target": "...", "type": "...", "label": "..."}}
    ]
}}

Extract ALL entities and relationships mentioned in the analysis."""

    from app.providers.llm_router import Provider
    result = await router.generate_json(
        prompt=extraction_prompt,
        system_prompt="You are a structured data extraction assistant. Return only valid JSON.",
        provider=Provider.GROQ,
        temperature=0.0,
    )
    return result if "error" not in result else None
