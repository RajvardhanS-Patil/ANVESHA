"""
ANVESHA Annotated Document Export — Highlight compliance findings on source PDFs.

Takes a completed audit report and overlays colored highlight annotations
directly on the original uploaded PDF pages:
  - RED    = GAP (non-compliant / missing controls)
  - YELLOW = PARTIAL (partially addressed)
  - GREEN  = MET (fully compliant)

For non-PDF sources (audio transcripts, schematics), generates an annotated
HTML report with inline color-coded markers.
"""

import io
import logging
import os
import zipfile
from datetime import datetime, timezone
from typing import Optional

import fitz  # PyMuPDF

from app.audit.gap_analysis import get_audit_reports
from app.ingestion.router import get_raw_file_store, get_document_store, get_chunk_store

logger = logging.getLogger(__name__)

# --- Color mapping for highlight annotations (RGB 0-1 range for PyMuPDF) ---
STATUS_COLORS = {
    "GAP":     (1.0, 0.3, 0.3),     # Red
    "PARTIAL": (1.0, 0.85, 0.2),    # Yellow / Orange
    "MET":     (0.2, 0.8, 0.4),     # Green
}

STATUS_LABELS = {
    "GAP":     "❌ GAP — Non-Compliant",
    "PARTIAL": "⚠️ PARTIAL — Partially Addressed",
    "MET":     "✅ MET — Fully Compliant",
}

# HTML colors for non-PDF annotated reports
STATUS_HTML_COLORS = {
    "GAP":     "#ff4d4d",
    "PARTIAL": "#ffbf00",
    "MET":     "#33cc66",
}


def _collect_findings_by_source(report: dict) -> dict:
    """
    Group audit findings by the source document they reference.

    Returns:
        Dict mapping source_filename -> list of finding dicts with
        {text_snippets: [...], status: str, control_name: str, reasoning: str}
    """
    findings_by_source: dict[str, list[dict]] = {}

    for control in report.get("controls", []):
        status = control.get("status", "GAP")
        control_name = control.get("name", "Unknown Control")
        reasoning = control.get("reasoning", "")
        evidence_list = control.get("evidence_found", [])

        # Each evidence snippet is a text string that may contain source references
        for evidence_text in evidence_list:
            if not evidence_text or not evidence_text.strip():
                continue

            # Try to infer the source doc from the evidence text
            # Evidence strings may contain "Source: filename" or "Document: filename"
            source_key = _extract_source_from_evidence(evidence_text)

            if source_key not in findings_by_source:
                findings_by_source[source_key] = []

            findings_by_source[source_key].append({
                "text_snippet": evidence_text.strip(),
                "status": status,
                "control_name": control_name,
                "reasoning": reasoning,
            })

        # Also search for the control description itself in documents
        description = control.get("description", "")
        if description and len(description) > 20:
            source_key = "__all__"
            if source_key not in findings_by_source:
                findings_by_source[source_key] = []
            findings_by_source[source_key].append({
                "text_snippet": description.strip(),
                "status": status,
                "control_name": control_name,
                "reasoning": reasoning,
            })

    return findings_by_source


def _extract_source_from_evidence(evidence_text: str) -> str:
    """Try to extract a source filename from an evidence text string."""
    text_lower = evidence_text.lower()

    # Look for common patterns like "Source: filename.pdf" or "Document xyz"
    for prefix in ["source:", "document:", "file:", "from "]:
        idx = text_lower.find(prefix)
        if idx != -1:
            after = evidence_text[idx + len(prefix):].strip()
            # Take until next whitespace or end
            parts = after.split()
            if parts:
                candidate = parts[0].strip("()[]{}\"',.")
                if candidate:
                    return candidate

    return "__all__"


def _build_search_terms(finding: dict) -> list[str]:
    """
    Extract meaningful search terms from a finding to locate in the PDF.

    Returns short phrases (3-8 words) that are likely to appear verbatim
    in the source document.
    """
    terms = []
    snippet = finding.get("text_snippet", "")

    # Clean the snippet — remove source attribution prefixes
    for prefix in ["Source:", "Document:", "Semantic Match", "Connected "]:
        if snippet.startswith(prefix):
            # Take content after the prefix marker
            colon_idx = snippet.find(":")
            if colon_idx != -1 and colon_idx < 40:
                snippet = snippet[colon_idx + 1:].strip()

    # Extract quoted text if present (these are exact spans)
    import re
    quoted = re.findall(r'"([^"]{10,})"', snippet)
    terms.extend(quoted)

    # Break the snippet into sentence fragments for searching
    sentences = re.split(r'[.;!\n]', snippet)
    for sent in sentences:
        sent = sent.strip()
        if len(sent) > 15 and len(sent) < 200:
            terms.append(sent)
        elif len(sent) >= 200:
            # Take first 100 chars as a search term
            terms.append(sent[:100])

    # Also try the control name
    control_name = finding.get("control_name", "")
    if control_name and len(control_name) > 5:
        terms.append(control_name)

    return terms


def annotate_pdf(pdf_bytes: bytes, findings: list[dict]) -> bytes:
    """
    Annotate a PDF with colored highlights on text matching audit findings.

    Args:
        pdf_bytes: Raw PDF file bytes
        findings: List of finding dicts with text_snippet, status, control_name, reasoning

    Returns:
        Annotated PDF bytes with highlight annotations
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    annotations_added = 0

    for finding in findings:
        status = finding.get("status", "GAP")
        color = STATUS_COLORS.get(status, STATUS_COLORS["GAP"])
        control_name = finding.get("control_name", "")
        reasoning = finding.get("reasoning", "")

        search_terms = _build_search_terms(finding)

        for term in search_terms:
            if len(term) < 8:
                continue

            for page in doc:
                # Search for the text on this page
                text_instances = page.search_for(term, quads=True)

                if not text_instances:
                    # Try a shorter substring (first 50 chars)
                    if len(term) > 50:
                        text_instances = page.search_for(term[:50], quads=True)

                for inst in text_instances:
                    try:
                        annot = page.add_highlight_annot(inst)
                        annot.set_colors(stroke=color)
                        # Add popup note with audit details
                        popup_text = (
                            f"[{STATUS_LABELS.get(status, status)}]\n"
                            f"Control: {control_name}\n"
                            f"Reasoning: {reasoning[:200]}"
                        )
                        annot.set_info(
                            title=f"ANVESHA Audit — {status}",
                            content=popup_text,
                        )
                        annot.update()
                        annotations_added += 1
                    except Exception as e:
                        logger.debug(f"Failed to annotate instance on page {page.number}: {e}")

    # --- Append a legend page at the end ---
    _append_legend_page(doc, annotations_added)

    # Save to bytes
    output = io.BytesIO()
    doc.save(output)
    doc.close()
    output.seek(0)

    logger.info(f"PDF annotation complete: {annotations_added} highlights added")
    return output.read()


def _append_legend_page(doc: fitz.Document, total_annotations: int):
    """Append a legend/summary page at the end of the annotated PDF."""
    # Add a new page (A4 size)
    page = doc.new_page(width=595, height=842)

    # Title
    title_rect = fitz.Rect(50, 40, 545, 80)
    page.insert_textbox(
        title_rect,
        "ANVESHA — Compliance Audit Annotation Legend",
        fontsize=16,
        fontname="helv",
        color=(0.3, 0.2, 0.5),
        align=fitz.TEXT_ALIGN_CENTER,
    )

    # Subtitle
    sub_rect = fitz.Rect(50, 85, 545, 110)
    page.insert_textbox(
        sub_rect,
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} | Total Annotations: {total_annotations}",
        fontsize=9,
        fontname="helv",
        color=(0.4, 0.4, 0.4),
        align=fitz.TEXT_ALIGN_CENTER,
    )

    # Divider line
    page.draw_line(fitz.Point(50, 120), fitz.Point(545, 120), color=(0.7, 0.7, 0.7), width=0.5)

    # Legend entries
    y_offset = 150
    legend_items = [
        ("GAP — Non-Compliant", STATUS_COLORS["GAP"],
         "Red highlights indicate text passages related to compliance requirements "
         "where NO evidence or controls were found. These represent critical gaps "
         "requiring immediate remediation."),
        ("PARTIAL — Partially Addressed", STATUS_COLORS["PARTIAL"],
         "Yellow/orange highlights indicate passages where some controls or evidence "
         "exist but are incomplete. Implementation details may be missing, or sub-controls "
         "are not fully covered."),
        ("MET — Fully Compliant", STATUS_COLORS["MET"],
         "Green highlights indicate passages where full compliance evidence was found. "
         "Controls are properly implemented and documented."),
    ]

    for label, color, description in legend_items:
        # Color swatch
        swatch_rect = fitz.Rect(50, y_offset, 80, y_offset + 20)
        page.draw_rect(swatch_rect, color=color, fill=color)

        # Label
        label_rect = fitz.Rect(90, y_offset, 545, y_offset + 20)
        page.insert_textbox(
            label_rect,
            label,
            fontsize=12,
            fontname="helv",
            color=(0.1, 0.1, 0.1),
        )

        # Description
        desc_rect = fitz.Rect(90, y_offset + 22, 545, y_offset + 60)
        page.insert_textbox(
            desc_rect,
            description,
            fontsize=8,
            fontname="helv",
            color=(0.35, 0.35, 0.35),
        )

        y_offset += 75

    # Footer
    footer_rect = fitz.Rect(50, 780, 545, 810)
    page.insert_textbox(
        footer_rect,
        "Powered by ANVESHA — Multi-Modal Knowledge Graph Compliance Intelligence",
        fontsize=8,
        fontname="helv",
        color=(0.5, 0.4, 0.6),
        align=fitz.TEXT_ALIGN_CENTER,
    )


def generate_annotated_html(report: dict) -> str:
    """
    Generate an annotated HTML report for non-PDF sources (audio transcripts, schematics).

    Produces inline color-coded <mark> tags around relevant text segments.
    """
    chunk_store = get_chunk_store()
    controls = report.get("controls", [])

    html_parts = [
        "<!DOCTYPE html>",
        "<html lang='en'><head>",
        "<meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>",
        "<title>ANVESHA Annotated Compliance Report</title>",
        "<style>",
        "  body { font-family: 'Segoe UI', system-ui, sans-serif; background: #1a1a2e; color: #e0e0e0; padding: 2rem; max-width: 900px; margin: 0 auto; }",
        "  h1 { color: #a078ff; border-bottom: 2px solid #a078ff33; padding-bottom: 0.5rem; }",
        "  h2 { color: #cebdff; margin-top: 2rem; }",
        "  .legend { display: flex; gap: 1.5rem; margin: 1rem 0 2rem; padding: 1rem; background: #ffffff08; border-radius: 8px; border: 1px solid #ffffff10; }",
        "  .legend-item { display: flex; align-items: center; gap: 0.5rem; font-size: 0.85rem; }",
        "  .legend-swatch { width: 20px; height: 14px; border-radius: 3px; display: inline-block; }",
        f"  .gap {{ background: {STATUS_HTML_COLORS['GAP']}33; border-left: 3px solid {STATUS_HTML_COLORS['GAP']}; padding: 2px 6px; border-radius: 0 4px 4px 0; }}",
        f"  .partial {{ background: {STATUS_HTML_COLORS['PARTIAL']}33; border-left: 3px solid {STATUS_HTML_COLORS['PARTIAL']}; padding: 2px 6px; border-radius: 0 4px 4px 0; }}",
        f"  .met {{ background: {STATUS_HTML_COLORS['MET']}33; border-left: 3px solid {STATUS_HTML_COLORS['MET']}; padding: 2px 6px; border-radius: 0 4px 4px 0; }}",
        "  .source-block { background: #ffffff05; border: 1px solid #ffffff10; border-radius: 8px; padding: 1rem 1.5rem; margin: 1rem 0; }",
        "  .source-title { font-size: 0.8rem; color: #a078ff; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 0.5rem; }",
        "  .source-text { white-space: pre-wrap; font-size: 0.9rem; line-height: 1.8; }",
        "  .control-note { font-size: 0.75rem; color: #888; margin-top: 2px; font-style: italic; }",
        "  .footer { margin-top: 3rem; text-align: center; color: #666; font-size: 0.75rem; border-top: 1px solid #ffffff10; padding-top: 1rem; }",
        "</style>",
        "</head><body>",
        "<h1>ANVESHA — Annotated Compliance Document</h1>",
        f"<p style='color:#888;font-size:0.85rem'>Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</p>",
        "<div class='legend'>",
        f"  <div class='legend-item'><span class='legend-swatch' style='background:{STATUS_HTML_COLORS['GAP']}'></span> GAP (Non-Compliant)</div>",
        f"  <div class='legend-item'><span class='legend-swatch' style='background:{STATUS_HTML_COLORS['PARTIAL']}'></span> PARTIAL</div>",
        f"  <div class='legend-item'><span class='legend-swatch' style='background:{STATUS_HTML_COLORS['MET']}'></span> MET (Compliant)</div>",
        "</div>",
    ]

    # Build a mapping of text -> (status, control_name) for highlighting
    highlight_map: list[tuple[str, str, str]] = []
    for control in controls:
        status = control.get("status", "GAP")
        control_name = control.get("name", "")
        for ev in control.get("evidence_found", []):
            if ev and len(ev.strip()) > 10:
                highlight_map.append((ev.strip(), status, control_name))

    # Render each chunk from the chunk store with highlights
    chunks_rendered = 0
    for chunk_id, chunk in chunk_store.items():
        raw_text = chunk.get("raw_text", "")
        source_filename = chunk.get("source_filename", "Unknown")
        content_type = chunk.get("content_type", "")
        location = chunk.get("source_location", {})

        if not raw_text.strip():
            continue

        # Build location string
        loc_str = ""
        if location:
            loc_type = location.get("type", "")
            if loc_type == "page":
                loc_str = f" — Page {location.get('page', '?')}"
            elif loc_type == "timestamp":
                start = location.get("start_time", 0)
                end = location.get("end_time", 0)
                loc_str = f" — {int(start // 60):02d}:{int(start % 60):02d} – {int(end // 60):02d}:{int(end % 60):02d}"
            elif loc_type == "cell":
                loc_str = f" — Row {location.get('row', '?')}, Col {location.get('col', '?')}"

        # Apply highlights to the text
        annotated_text = raw_text
        for evidence_text, status, ctrl_name in highlight_map:
            # Find the evidence snippet within this chunk's text
            import re
            # Try exact substring match
            search_text = evidence_text[:80]  # Use first 80 chars for matching
            idx = annotated_text.lower().find(search_text.lower())
            if idx != -1:
                matched = annotated_text[idx:idx + len(search_text)]
                css_class = status.lower()
                replacement = (
                    f"<mark class='{css_class}' title='{ctrl_name}'>{matched}</mark>"
                    f"<div class='control-note'>↑ {ctrl_name} [{status}]</div>"
                )
                annotated_text = annotated_text[:idx] + replacement + annotated_text[idx + len(search_text):]

        html_parts.append(f"<div class='source-block'>")
        html_parts.append(f"  <div class='source-title'>{source_filename}{loc_str} [{content_type}]</div>")
        html_parts.append(f"  <div class='source-text'>{annotated_text}</div>")
        html_parts.append(f"</div>")
        chunks_rendered += 1

    if chunks_rendered == 0:
        html_parts.append("<p style='color:#888;text-align:center;padding:2rem;'>No source documents available for annotation.</p>")

    html_parts.append("<div class='footer'>Powered by ANVESHA — Multi-Modal Knowledge Graph Compliance Intelligence</div>")
    html_parts.append("</body></html>")

    return "\n".join(html_parts)


async def generate_annotated_export(report_id: str) -> tuple[bytes, str]:
    """
    Generate annotated source documents for a given audit report.

    Returns:
        Tuple of (zip_bytes, filename) containing all annotated documents.
    """
    reports = get_audit_reports()
    if report_id not in reports:
        raise ValueError(f"Audit report {report_id} not found")

    report = reports[report_id]
    raw_file_store = get_raw_file_store()
    document_store = get_document_store()

    findings_by_source = _collect_findings_by_source(report)

    # Collect all findings into a flat list for global search
    all_findings = []
    for source_findings in findings_by_source.values():
        all_findings.extend(source_findings)

    # Create a ZIP archive with annotated documents
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        annotated_count = 0

        # Process each stored raw file
        for doc_id, (raw_bytes, filename) in raw_file_store.items():
            ext = os.path.splitext(filename)[1].lower()

            if ext == ".pdf":
                try:
                    # Annotate the PDF with all findings
                    annotated_bytes = annotate_pdf(raw_bytes, all_findings)
                    output_name = f"annotated_{filename}"
                    zf.writestr(output_name, annotated_bytes)
                    annotated_count += 1
                    logger.info(f"Annotated PDF: {filename} -> {output_name}")
                except Exception as e:
                    logger.error(f"Failed to annotate PDF {filename}: {e}")
                    # Include the original file as fallback
                    zf.writestr(f"original_{filename}", raw_bytes)
            else:
                # For non-PDF files, include the original + note
                zf.writestr(f"original_{filename}", raw_bytes)
                annotated_count += 1

        # Always include the annotated HTML report (covers audio, schematics, tables)
        try:
            html_report = generate_annotated_html(report)
            zf.writestr("annotated_report.html", html_report.encode("utf-8"))
        except Exception as e:
            logger.error(f"Failed to generate annotated HTML: {e}")

        # Include a README
        readme = (
            f"ANVESHA Annotated Compliance Export\n"
            f"===================================\n\n"
            f"Report ID: {report_id}\n"
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Compliance Score: {report.get('compliance_score', 0)}%\n\n"
            f"Files in this archive:\n"
            f"- annotated_*.pdf — Original PDFs with colored highlights\n"
            f"- annotated_report.html — Full annotated report (open in browser)\n"
            f"- original_* — Original non-PDF source files\n\n"
            f"Highlight Legend:\n"
            f"  RED    = GAP (non-compliant, missing controls)\n"
            f"  YELLOW = PARTIAL (partially addressed)\n"
            f"  GREEN  = MET (fully compliant)\n\n"
            f"Click on any highlight in the PDF to see the audit reasoning.\n"
        )
        zf.writestr("README.txt", readme)

    zip_buffer.seek(0)
    zip_filename = f"anvesha_annotated_{report_id[:8]}.zip"

    logger.info(f"Annotated export complete: {annotated_count} files in {zip_filename}")
    return zip_buffer.read(), zip_filename
