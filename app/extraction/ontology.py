"""
ANVESHA Ontology — Entity and relationship type definitions.

Fixed, small schema for accurate extraction and meaningful traversal.
Every extraction must conform to this ontology or be normalized to it.
"""

import json
import logging
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# === Core Entity Types ===
ENTITY_TYPES = [
    "Regulation",    # Laws, standards, frameworks (GDPR, ISO 27001, NIST CSF)
    "Requirement",   # Specific mandates within regulations
    "Control",       # Security/compliance controls that implement requirements
    "System",        # IT systems, applications, platforms
    "Asset",         # Data assets, infrastructure, resources
    "Evidence",      # Audit evidence, logs, certifications
    "Policy",        # Internal policies and procedures
    "Person",        # Roles, individuals (DPO, CISO, auditor)
    "Incident",      # Security incidents, breaches, violations
]

# === Extended Types (auto-normalized to core or allowed if justified) ===
EXTENDED_TYPES_ALLOWLIST = [
    "Vendor",        # Third-party vendors/suppliers
    "Risk",          # Identified risks
    "AuditFinding",  # Audit findings and observations
    "Process",       # Business processes
    "Standard",      # Technical standards (subtype of Regulation)
    "Framework",     # Compliance frameworks (subtype of Regulation)
]

ALL_ENTITY_TYPES = ENTITY_TYPES + EXTENDED_TYPES_ALLOWLIST

# === Normalization Map (extended → core) ===
TYPE_NORMALIZATION = {
    "law": "Regulation",
    "legislation": "Regulation",
    "act": "Regulation",
    "directive": "Regulation",
    "standard": "Regulation",
    "framework": "Regulation",
    "guideline": "Regulation",
    "mandate": "Requirement",
    "obligation": "Requirement",
    "clause": "Requirement",
    "article": "Requirement",
    "safeguard": "Control",
    "measure": "Control",
    "mechanism": "Control",
    "countermeasure": "Control",
    "application": "System",
    "platform": "System",
    "software": "System",
    "service": "System",
    "infrastructure": "Asset",
    "resource": "Asset",
    "data": "Asset",
    "database": "Asset",
    "proof": "Evidence",
    "certificate": "Evidence",
    "log": "Evidence",
    "record": "Evidence",
    "report": "Evidence",
    "procedure": "Policy",
    "rule": "Policy",
    "protocol": "Policy",
    "role": "Person",
    "officer": "Person",
    "manager": "Person",
    "team": "Person",
    "department": "Person",
    "breach": "Incident",
    "violation": "Incident",
    "event": "Incident",
    "alert": "Incident",
    "vulnerability": "Risk",
    "threat": "Risk",
    "supplier": "Vendor",
    "provider": "Vendor",
    "partner": "Vendor",
    "finding": "AuditFinding",
    "observation": "AuditFinding",
    "nonconformity": "AuditFinding",
    "workflow": "Process",
    "operation": "Process",
    "activity": "Process",
}

# === Core Relationship Types ===
RELATION_TYPES = [
    "REQUIRES",          # Regulation → Requirement (regulation mandates a requirement)
    "IMPLEMENTED_BY",    # Requirement → Control (requirement implemented by a control)
    "EVIDENCED_BY",      # Control → Evidence (control proven by evidence)
    "VIOLATES",          # Incident → Regulation/Requirement (incident violates)
    "SUPERSEDES",        # Regulation → Regulation (newer replaces older)
    "APPLIES_TO",        # Regulation/Control → System/Asset
    "RESPONSIBLE_FOR",   # Person → Control/System/Policy
    "MENTIONED_IN",      # Entity → Document (entity appears in document)
    "RELATED_TO",        # Generic relationship fallback
    "CONTAINS",          # System → Asset, Framework → Requirement
    "MITIGATES",         # Control → Risk
    "IDENTIFIED_IN",     # Risk/Incident → Evidence/AuditFinding
]


def normalize_entity_type(proposed_type: str) -> str:
    """
    Normalize a proposed entity type to the ontology.

    1. Check if it's already a valid core/extended type
    2. Try normalization map
    3. Default to closest match or 'Asset' as catch-all
    """
    if not proposed_type:
        return "Asset"

    # Direct match (case-insensitive)
    for t in ALL_ENTITY_TYPES:
        if proposed_type.lower() == t.lower():
            return t

    # Normalization map
    normalized = TYPE_NORMALIZATION.get(proposed_type.lower())
    if normalized:
        return normalized

    # Partial match
    proposed_lower = proposed_type.lower()
    for key, value in TYPE_NORMALIZATION.items():
        if key in proposed_lower or proposed_lower in key:
            return value

    logger.debug(f"Unknown entity type '{proposed_type}' — defaulting to 'Asset'")
    return "Asset"


def normalize_relation_type(proposed_type: str) -> str:
    """Normalize a proposed relation type."""
    if not proposed_type:
        return "RELATED_TO"

    proposed_upper = proposed_type.upper().replace(" ", "_").replace("-", "_")

    # Direct match
    if proposed_upper in RELATION_TYPES:
        return proposed_upper

    # Common alternatives
    relation_normalization = {
        "IMPLEMENTS": "IMPLEMENTED_BY",
        "PROVES": "EVIDENCED_BY",
        "EVIDENCE_FOR": "EVIDENCED_BY",
        "DEMONSTRATES": "EVIDENCED_BY",
        "BREAKS": "VIOLATES",
        "BREACHES": "VIOLATES",
        "REPLACES": "SUPERSEDES",
        "UPDATES": "SUPERSEDES",
        "APPLIES": "APPLIES_TO",
        "COVERS": "APPLIES_TO",
        "MANAGES": "RESPONSIBLE_FOR",
        "OWNS": "RESPONSIBLE_FOR",
        "OVERSEES": "RESPONSIBLE_FOR",
        "REFERENCES": "MENTIONED_IN",
        "DESCRIBES": "MENTIONED_IN",
        "INCLUDES": "CONTAINS",
        "HAS": "CONTAINS",
        "PART_OF": "CONTAINS",
        "ADDRESSES": "MITIGATES",
        "REDUCES": "MITIGATES",
        "PREVENTS": "MITIGATES",
        "FOUND_IN": "IDENTIFIED_IN",
        "DETECTED_IN": "IDENTIFIED_IN",
        "CONNECTS_TO": "RELATED_TO",
        "DEPENDS_ON": "RELATED_TO",
        "INTERACTS_WITH": "RELATED_TO",
    }

    normalized = relation_normalization.get(proposed_upper)
    if normalized:
        return normalized

    return "RELATED_TO"


# === Extraction Prompt Templates ===

ENTITY_EXTRACTION_SYSTEM_PROMPT = """You are a compliance knowledge graph entity and relationship extractor.
Your task is to extract entities and relationships from compliance-related text.

ENTITY TYPES (use ONLY these):
{entity_types}

RELATIONSHIP TYPES (use ONLY these):
{relation_types}

RULES:
1. Extract ALL entities mentioned in the text
2. For each entity, provide: name, type, description, and the EXACT text span where it appears
3. Extract ALL relationships between entities
4. The exact_span MUST be a substring that actually appears in the source text
5. Be precise — do not infer entities or relationships not supported by the text
6. If unsure about a type, use the closest match from the allowed types"""

ENTITY_EXTRACTION_USER_PROMPT = """Extract entities and relationships from the following text.

SOURCE: {source_filename}, {source_location}

TEXT:
{text}

Return a JSON object with this exact structure:
{{
    "entities": [
        {{
            "name": "entity name",
            "entity_type": "one of the allowed entity types",
            "description": "brief description",
            "exact_span": "exact text from source where this entity is mentioned"
        }}
    ],
    "relationships": [
        {{
            "source_entity": "source entity name",
            "target_entity": "target entity name",
            "relation_type": "one of the allowed relation types",
            "description": "brief description of the relationship",
            "exact_span": "exact text supporting this relationship"
        }}
    ]
}}"""


def get_extraction_system_prompt() -> str:
    """Get the system prompt for entity extraction."""
    return ENTITY_EXTRACTION_SYSTEM_PROMPT.format(
        entity_types=", ".join(ENTITY_TYPES),
        relation_types=", ".join(RELATION_TYPES),
    )


def get_extraction_user_prompt(text: str, source_filename: str, source_location: str) -> str:
    """Get the user prompt for entity extraction."""
    return ENTITY_EXTRACTION_USER_PROMPT.format(
        text=text[:4000],  # Truncate to fit context window
        source_filename=source_filename,
        source_location=source_location,
    )
