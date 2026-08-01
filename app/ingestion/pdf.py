"""
ANVESHA PDF Ingestion — Extract text from PDFs with page-level provenance.

Pipeline:
1. Try pdfplumber for text extraction (preserves layout)
2. Fallback to Tesseract OCR for scanned/image-based pages
3. Each page becomes an IngestedChunk with page number provenance
"""

import io
import logging
import time
from typing import Optional

import pdfplumber
from PIL import Image

from app.ingestion.base import (
    IngestedChunk,
    IngestionResult,
    ContentType,
    SourceLocation,
    SourceLocationType,
)

logger = logging.getLogger(__name__)

# Minimum text length to consider a page as having extractable text
MIN_TEXT_LENGTH = 50


async def ingest_pdf(
    file_data: bytes,
    filename: str,
    doc_id: str,
    use_ocr_fallback: bool = True,
) -> IngestionResult:
    """
    Ingest a PDF file and extract text with page-level provenance.

    Args:
        file_data: Raw PDF bytes
        filename: Original filename
        doc_id: Unique document identifier
        use_ocr_fallback: Whether to use Tesseract OCR for pages with little/no text

    Returns:
        IngestionResult with chunks per page
    """
    start_time = time.perf_counter()
    chunks: list[IngestedChunk] = []
    errors: list[str] = []

    logger.info(f"Ingesting PDF: {filename} ({len(file_data)} bytes)")

    try:
        with pdfplumber.open(io.BytesIO(file_data)) as pdf:
            total_pages = len(pdf.pages)
            logger.info(f"PDF has {total_pages} pages")

            for page_num, page in enumerate(pdf.pages, start=1):
                try:
                    chunk = _extract_page(
                        page, page_num, filename, doc_id, use_ocr_fallback, file_data
                    )
                    if chunk and chunk.raw_text.strip():
                        chunks.append(chunk)
                        logger.debug(
                            f"Page {page_num}: {len(chunk.raw_text)} chars "
                            f"({'OCR' if chunk.content_type == ContentType.PDF_OCR else 'text'})"
                        )
                    else:
                        logger.debug(f"Page {page_num}: no extractable text")
                except Exception as e:
                    error_msg = f"Page {page_num} failed: {e}"
                    logger.warning(error_msg)
                    errors.append(error_msg)

    except Exception as e:
        logger.error(f"PDF ingestion failed for {filename}: {e}")
        return IngestionResult(
            doc_id=doc_id,
            filename=filename,
            content_type="pdf",
            chunks=[],
            status="error",
            error=str(e),
            processing_time_seconds=time.perf_counter() - start_time,
        )

    elapsed = time.perf_counter() - start_time
    logger.info(
        f"PDF ingestion complete: {filename} — "
        f"{len(chunks)} chunks from {total_pages} pages in {elapsed:.2f}s"
    )

    return IngestionResult(
        doc_id=doc_id,
        filename=filename,
        content_type="pdf",
        chunks=chunks,
        total_chunks=len(chunks),
        status="success" if not errors else "partial",
        error="; ".join(errors) if errors else None,
        processing_time_seconds=elapsed,
    )


def _extract_page(
    page,
    page_num: int,
    filename: str,
    doc_id: str,
    use_ocr_fallback: bool,
    file_data: bytes,
) -> Optional[IngestedChunk]:
    """Extract text from a single PDF page."""
    # Try pdfplumber text extraction first
    text = page.extract_text() or ""
    content_type = ContentType.PDF_TEXT

    # If text is too short, try OCR fallback
    if len(text.strip()) < MIN_TEXT_LENGTH and use_ocr_fallback:
        ocr_text = _ocr_page(page)
        if ocr_text and len(ocr_text.strip()) > len(text.strip()):
            text = ocr_text
            content_type = ContentType.PDF_OCR

    if not text.strip():
        return None

    # Clean the text
    cleaned = _clean_text(text)

    return IngestedChunk(
        source_doc_id=doc_id,
        source_filename=filename,
        content_type=content_type,
        raw_text=text,
        cleaned_text=cleaned,
        source_location=SourceLocation(
            type=SourceLocationType.PAGE,
            page=page_num,
        ),
        metadata={
            "page_number": page_num,
            "page_width": float(page.width),
            "page_height": float(page.height),
            "extraction_method": content_type.value,
        },
    )


def _ocr_page(page) -> str:
    """OCR a PDF page using Tesseract."""
    try:
        import pytesseract

        # Convert page to image
        img = page.to_image(resolution=300)
        pil_image = img.original

        # Run Tesseract OCR
        text = pytesseract.image_to_string(pil_image, lang="eng")
        return text
    except ImportError:
        logger.warning("pytesseract not available — skipping OCR")
        return ""
    except Exception as e:
        logger.warning(f"OCR failed: {e}")
        return ""


def _clean_text(text: str) -> str:
    """Clean extracted text — normalize whitespace, remove artifacts."""
    import re

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text)
    # Remove common PDF artifacts
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # Normalize quotes
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    # Strip
    text = text.strip()
    return text
