"""
ANVESHA Ingestion Base — Common data structures for all ingestion pipelines.

Every ingested chunk carries full provenance metadata so that any claim
in a final answer can be traced back to the exact source location.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import uuid


class ContentType(str, Enum):
    """Type of source content."""
    PDF_TEXT = "pdf_text"
    PDF_OCR = "pdf_ocr"
    AUDIO_TRANSCRIPT = "audio_transcript"
    TABLE = "table"
    SCHEMATIC = "schematic"
    IMAGE = "image"


class SourceLocationType(str, Enum):
    """Type of source location reference."""
    PAGE = "page"               # PDF page number
    TIMESTAMP = "timestamp"     # Audio timestamp (seconds)
    CELL = "cell"               # Table cell reference (row, col)
    BBOX = "bbox"               # Bounding box (x0, y0, x1, y1)
    LINE = "line"               # Line number


@dataclass
class SourceLocation:
    """Precise location within a source document."""
    type: SourceLocationType
    page: Optional[int] = None
    start_time: Optional[float] = None  # seconds
    end_time: Optional[float] = None    # seconds
    row: Optional[int] = None
    col: Optional[int] = None
    bbox: Optional[tuple] = None        # (x0, y0, x1, y1)
    line_start: Optional[int] = None
    line_end: Optional[int] = None

    def to_dict(self) -> dict:
        """Serialize to dict, excluding None values."""
        result = {"type": self.type.value}
        for k, v in self.__dict__.items():
            if v is not None and k != "type":
                result[k] = v
        return result

    def to_citation_string(self) -> str:
        """Human-readable citation string."""
        if self.type == SourceLocationType.PAGE:
            return f"Page {self.page}"
        elif self.type == SourceLocationType.TIMESTAMP:
            start = self._format_time(self.start_time or 0)
            end = self._format_time(self.end_time or 0)
            return f"Timestamp {start}–{end}"
        elif self.type == SourceLocationType.CELL:
            return f"Row {self.row}, Col {self.col}"
        elif self.type == SourceLocationType.BBOX:
            return f"Region {self.bbox}"
        elif self.type == SourceLocationType.LINE:
            return f"Lines {self.line_start}–{self.line_end}"
        return "Unknown location"

    @staticmethod
    def _format_time(seconds: float) -> str:
        """Format seconds to MM:SS."""
        m, s = divmod(int(seconds), 60)
        return f"{m:02d}:{s:02d}"


@dataclass
class IngestedChunk:
    """
    A single chunk of ingested content with full provenance.

    This is the universal output format for all ingestion pipelines.
    Every chunk must have enough metadata to trace back to the exact
    source location for citation purposes.
    """
    # Unique ID for this chunk
    chunk_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Source document identification
    source_doc_id: str = ""           # Unique document identifier
    source_filename: str = ""         # Original filename
    content_type: ContentType = ContentType.PDF_TEXT

    # Content
    raw_text: str = ""                # The extracted text
    cleaned_text: str = ""            # Cleaned/normalized text for processing

    # Provenance — exact source location
    source_location: Optional[SourceLocation] = None

    # Metadata
    metadata: dict = field(default_factory=dict)
    ingested_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # For tables: structured data
    table_data: Optional[list] = None  # List of rows (list of cells)
    table_headers: Optional[list] = None

    # For schematics: component/relationship data from vision
    vision_entities: Optional[list] = None
    vision_relationships: Optional[list] = None

    def to_dict(self) -> dict:
        """Serialize to dict for storage/API responses."""
        result = {
            "chunk_id": self.chunk_id,
            "source_doc_id": self.source_doc_id,
            "source_filename": self.source_filename,
            "content_type": self.content_type.value,
            "raw_text": self.raw_text,
            "cleaned_text": self.cleaned_text,
            "source_location": self.source_location.to_dict() if self.source_location else None,
            "metadata": self.metadata,
            "ingested_at": self.ingested_at,
        }
        if self.table_data:
            result["table_data"] = self.table_data
            result["table_headers"] = self.table_headers
        if self.vision_entities:
            result["vision_entities"] = self.vision_entities
        if self.vision_relationships:
            result["vision_relationships"] = self.vision_relationships
        return result

    @property
    def citation(self) -> str:
        """Generate a human-readable citation string."""
        loc = self.source_location.to_citation_string() if self.source_location else "Unknown"
        return f"[{self.source_filename} — {loc}]"

    def __repr__(self) -> str:
        text_preview = self.raw_text[:80] + "..." if len(self.raw_text) > 80 else self.raw_text
        return (
            f"IngestedChunk(id={self.chunk_id[:8]}, "
            f"type={self.content_type.value}, "
            f"source={self.source_filename}, "
            f"text='{text_preview}')"
        )


@dataclass
class IngestionResult:
    """Result of ingesting a document."""
    doc_id: str
    filename: str
    content_type: str
    chunks: list[IngestedChunk]
    total_chunks: int = 0
    status: str = "success"
    error: Optional[str] = None
    processing_time_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "filename": self.filename,
            "content_type": self.content_type,
            "total_chunks": self.total_chunks or len(self.chunks),
            "status": self.status,
            "error": self.error,
            "processing_time_seconds": round(self.processing_time_seconds, 2),
            "chunks": [c.to_dict() for c in self.chunks],
        }
