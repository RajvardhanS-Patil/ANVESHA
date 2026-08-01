"""
ANVESHA Audio Ingestion — Transcribe audio via Groq Whisper API.

Pipeline:
1. Accept audio files (wav, mp3, m4a, ogg, flac, webm)
2. Send to Groq Whisper API for timestamped transcription
3. Split transcript into segment-level chunks with timestamp provenance
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

# Supported audio formats
SUPPORTED_AUDIO_FORMATS = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm", ".mp4"}

# Maximum audio file size (25MB — Groq limit)
MAX_AUDIO_SIZE = 25 * 1024 * 1024


async def ingest_audio(
    file_data: bytes,
    filename: str,
    doc_id: str,
    language: str = "en",
    chunk_by_segments: bool = True,
) -> IngestionResult:
    """
    Ingest an audio file using Groq Whisper API.

    Args:
        file_data: Raw audio bytes
        filename: Original filename
        doc_id: Unique document identifier
        language: Audio language code
        chunk_by_segments: Whether to split into segment-level chunks

    Returns:
        IngestionResult with timestamped chunks
    """
    start_time = time.perf_counter()

    logger.info(f"Ingesting audio: {filename} ({len(file_data)} bytes)")

    # Validate file size
    if len(file_data) > MAX_AUDIO_SIZE:
        return IngestionResult(
            doc_id=doc_id,
            filename=filename,
            content_type="audio",
            chunks=[],
            status="error",
            error=f"Audio file too large ({len(file_data)} bytes). Max: {MAX_AUDIO_SIZE} bytes",
            processing_time_seconds=time.perf_counter() - start_time,
        )

    try:
        # Get LLM router for Whisper API
        router = get_llm_router()

        # Transcribe via Groq Whisper
        result = await router.transcribe(
            audio_data=file_data,
            filename=filename,
            language=language,
        )

        chunks: list[IngestedChunk] = []

        if chunk_by_segments and result.get("segments"):
            # Create a chunk per segment
            for i, segment in enumerate(result["segments"]):
                segment_text = segment.get("text", "").strip()
                if not segment_text:
                    continue

                chunk = IngestedChunk(
                    source_doc_id=doc_id,
                    source_filename=filename,
                    content_type=ContentType.AUDIO_TRANSCRIPT,
                    raw_text=segment_text,
                    cleaned_text=segment_text,
                    source_location=SourceLocation(
                        type=SourceLocationType.TIMESTAMP,
                        start_time=segment.get("start", 0),
                        end_time=segment.get("end", 0),
                    ),
                    metadata={
                        "segment_index": i,
                        "language": language,
                        "total_segments": len(result["segments"]),
                    },
                )
                chunks.append(chunk)
        else:
            # Single chunk for the entire transcript
            full_text = result.get("text", "").strip()
            if full_text:
                chunk = IngestedChunk(
                    source_doc_id=doc_id,
                    source_filename=filename,
                    content_type=ContentType.AUDIO_TRANSCRIPT,
                    raw_text=full_text,
                    cleaned_text=full_text,
                    source_location=SourceLocation(
                        type=SourceLocationType.TIMESTAMP,
                        start_time=0,
                        end_time=0,
                    ),
                    metadata={
                        "language": language,
                        "transcription_method": "groq_whisper",
                    },
                )
                chunks.append(chunk)

        elapsed = time.perf_counter() - start_time
        logger.info(
            f"Audio ingestion complete: {filename} — "
            f"{len(chunks)} segments in {elapsed:.2f}s"
        )

        return IngestionResult(
            doc_id=doc_id,
            filename=filename,
            content_type="audio",
            chunks=chunks,
            total_chunks=len(chunks),
            status="success",
            processing_time_seconds=elapsed,
        )

    except Exception as e:
        logger.error(f"Audio ingestion failed for {filename}: {e}")
        return IngestionResult(
            doc_id=doc_id,
            filename=filename,
            content_type="audio",
            chunks=[],
            status="error",
            error=str(e),
            processing_time_seconds=time.perf_counter() - start_time,
        )
