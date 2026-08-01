-- ANVESHA Knowledge Graph Schema
-- Neo4j Cypher statements for schema initialization

-- Uniqueness constraints
CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE;
CREATE CONSTRAINT document_id IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE;

-- Vector index for semantic search (768-dim embeddings from Gemini text-embedding-004)
CREATE VECTOR INDEX entity_embedding IF NOT EXISTS
FOR (e:Entity) ON (e.embedding)
OPTIONS {indexConfig: {
    `vector.dimensions`: 768,
    `vector.similarity_function`: 'cosine'
}};

-- Full-text index for lexical search
CREATE FULLTEXT INDEX entity_text IF NOT EXISTS
FOR (e:Entity) ON EACH [e.name, e.description, e.exact_span];

-- Entity types: Regulation, Requirement, Control, System, Asset, Evidence, Policy, Person, Incident
-- Relation types: REQUIRES, IMPLEMENTED_BY, EVIDENCED_BY, VIOLATES, SUPERSEDES, APPLIES_TO, RESPONSIBLE_FOR, MENTIONED_IN

-- Every node carries:
--   source_doc_id, source_location (page/timestamp/cell/bbox),
--   exact_span, valid_from, valid_to, extraction_confidence
