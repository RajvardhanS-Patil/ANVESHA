"""
ANVESHA Table Ingestion — Extract tables from PDFs with cell-level provenance.

Pipeline:
1. Use pdfplumber to detect and extract tables from PDFs
2. Fall back to camelot for complex table structures
3. Each table becomes a chunk with row/column/cell provenance
"""

import io
import logging
import time
from typing import Optional

import pdfplumber

from app.ingestion.base import (
    IngestedChunk,
    IngestionResult,
    ContentType,
    SourceLocation,
    SourceLocationType,
)

logger = logging.getLogger(__name__)


async def ingest_tables(
    file_data: bytes,
    filename: str,
    doc_id: str,
) -> IngestionResult:
    """
    Extract tables from a PDF file.

    Args:
        file_data: Raw PDF bytes
        filename: Original filename
        doc_id: Unique document identifier

    Returns:
        IngestionResult with table chunks (one per table found)
    """
    start_time = time.perf_counter()
    chunks: list[IngestedChunk] = []
    errors: list[str] = []

    logger.info(f"Extracting tables from: {filename} ({len(file_data)} bytes)")

    try:
        # Primary: pdfplumber table extraction
        pdfplumber_chunks = _extract_tables_pdfplumber(file_data, filename, doc_id)
        chunks.extend(pdfplumber_chunks)

        # If pdfplumber found nothing, try camelot as fallback
        if not chunks:
            camelot_chunks = _extract_tables_camelot(file_data, filename, doc_id)
            chunks.extend(camelot_chunks)

    except Exception as e:
        error_msg = f"Table extraction failed: {e}"
        logger.error(error_msg)
        errors.append(error_msg)

    elapsed = time.perf_counter() - start_time
    logger.info(
        f"Table extraction complete: {filename} — "
        f"{len(chunks)} tables in {elapsed:.2f}s"
    )

    return IngestionResult(
        doc_id=doc_id,
        filename=filename,
        content_type="table",
        chunks=chunks,
        total_chunks=len(chunks),
        status="success" if chunks else ("error" if errors else "no_tables_found"),
        error="; ".join(errors) if errors else None,
        processing_time_seconds=elapsed,
    )


def _extract_tables_pdfplumber(
    file_data: bytes,
    filename: str,
    doc_id: str,
) -> list[IngestedChunk]:
    """Extract tables using pdfplumber."""
    chunks = []
    table_index = 0

    try:
        with pdfplumber.open(io.BytesIO(file_data)) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                tables = page.extract_tables()
                if not tables:
                    continue

                for table in tables:
                    if not table or len(table) < 2:  # Need at least header + 1 row
                        continue

                    table_index += 1

                    # Extract headers (first row)
                    headers = [str(cell).strip() if cell else "" for cell in table[0]]

                    # Extract data rows
                    data_rows = []
                    for row in table[1:]:
                        data_rows.append(
                            [str(cell).strip() if cell else "" for cell in row]
                        )

                    # Convert table to text representation
                    text = _table_to_text(headers, data_rows, table_index, page_num)

                    chunk = IngestedChunk(
                        source_doc_id=doc_id,
                        source_filename=filename,
                        content_type=ContentType.TABLE,
                        raw_text=text,
                        cleaned_text=text,
                        source_location=SourceLocation(
                            type=SourceLocationType.PAGE,
                            page=page_num,
                        ),
                        table_data=data_rows,
                        table_headers=headers,
                        metadata={
                            "table_index": table_index,
                            "page_number": page_num,
                            "num_rows": len(data_rows),
                            "num_cols": len(headers),
                            "extraction_method": "pdfplumber",
                        },
                    )
                    chunks.append(chunk)
                    logger.debug(
                        f"Table {table_index} on page {page_num}: "
                        f"{len(data_rows)} rows × {len(headers)} cols"
                    )

    except Exception as e:
        logger.warning(f"pdfplumber table extraction failed: {e}")

    return chunks


def _extract_tables_camelot(
    file_data: bytes,
    filename: str,
    doc_id: str,
) -> list[IngestedChunk]:
    """Extract tables using camelot as fallback."""
    chunks = []

    try:
        import camelot
        import tempfile
        import os

        # Camelot requires a file path, not bytes
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_data)
            tmp_path = tmp.name

        try:
            # Try lattice mode first (for bordered tables)
            tables = camelot.read_pdf(tmp_path, flavor="lattice", pages="all")

            if not tables or len(tables) == 0:
                # Fall back to stream mode (for borderless tables)
                tables = camelot.read_pdf(tmp_path, flavor="stream", pages="all")

            for i, table in enumerate(tables):
                df = table.df
                if df.empty or len(df) < 2:
                    continue

                headers = [str(v) for v in df.iloc[0].values]
                data_rows = [[str(v) for v in row] for row in df.iloc[1:].values]

                text = _table_to_text(headers, data_rows, i + 1, table.page)

                chunk = IngestedChunk(
                    source_doc_id=doc_id,
                    source_filename=filename,
                    content_type=ContentType.TABLE,
                    raw_text=text,
                    cleaned_text=text,
                    source_location=SourceLocation(
                        type=SourceLocationType.PAGE,
                        page=table.page,
                    ),
                    table_data=data_rows,
                    table_headers=headers,
                    metadata={
                        "table_index": i + 1,
                        "page_number": table.page,
                        "num_rows": len(data_rows),
                        "num_cols": len(headers),
                        "extraction_method": "camelot",
                        "accuracy": table.accuracy,
                    },
                )
                chunks.append(chunk)

        finally:
            os.unlink(tmp_path)

    except ImportError:
        logger.warning("camelot not available — skipping fallback table extraction")
    except Exception as e:
        logger.warning(f"Camelot table extraction failed: {e}")

    return chunks


def _table_to_text(
    headers: list[str],
    data_rows: list[list[str]],
    table_index: int,
    page_num: int,
) -> str:
    """Convert a table to a readable text representation."""
    lines = [f"Table {table_index} (Page {page_num}):"]
    lines.append("Headers: " + " | ".join(headers))
    lines.append("-" * 40)

    for row_idx, row in enumerate(data_rows):
        # Create key-value pairs for each cell
        pairs = []
        for col_idx, cell in enumerate(row):
            if col_idx < len(headers) and headers[col_idx]:
                pairs.append(f"{headers[col_idx]}: {cell}")
            else:
                pairs.append(cell)
        lines.append(f"Row {row_idx + 1}: " + " | ".join(pairs))

    return "\n".join(lines)
