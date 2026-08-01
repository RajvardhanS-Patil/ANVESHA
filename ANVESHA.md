# Multi-Modal Compliance Knowledge Graph — Build Brief

**Hand this file to Antigravity as project context (e.g. save as `ARCHITECTURE.md` in the repo root) before starting Phase 0.** Each phase below is scoped as one agent task with an explicit definition of done, so it can be run as a discrete Antigravity task rather than one giant prompt.

---

## 1. Mission

Ingest heterogeneous compliance data (PDFs, audio logs, tables, schematics), extract entities and relationships into a versioned, provenance-locked Neo4j knowledge graph, and answer complex compliance questions using GraphRAG — where every sentence in the answer is either backed by a traceable citation or the system explicitly abstains. No answer may reach the user without a resolvable path back to raw source text.

## 2. Non-negotiable constraints

- **$0 cost**, no credit card requirement anywhere in the stack.
- **Deployable to Render's free tier** as the final step — the architecture must run inside 512MB RAM / 0.1 CPU with no local disk persistence and tolerate the service sleeping after 15 minutes of inactivity.
- **No mocked or fabricated demo content.** Ingest real public compliance frameworks (ISO/IEC 27001 Annex A, NIST CSF, GDPR articles, SOC 2 Trust Criteria — all public PDFs). Synthesized audio is acceptable only because real audit recordings aren't public; the *information* in it must still be realistic, not fabricated to game the demo.
- Every generated claim must be traceable to an exact page, timestamp, or cell reference, or the system must decline to answer.
- All LLM/vision/ASR calls go through a single provider-abstraction interface, never called directly from business logic — free-tier terms change without warning (Gemini's did, in December 2025), and a provider swap should mean editing one file, not hunting through the codebase.

## 3. Confirmed free-tier stack (verified against current vendor docs, Aug 2026)

| Layer | Service | Free-tier ceiling that matters | Why this one |
|---|---|---|---|
| Graph + vector store | Neo4j AuraDB Free | 1 instance, 200k nodes / 400k relationships, **native vector index included** since the Jan 2026 Aura release | Eliminates a separate vector DB entirely — one less service to deploy or keep in sync |
| LLM generation + Whisper ASR | Groq API (free) | 30 req/min per model, Llama 3.3 70B / Llama 4 Scout, ~2,000 audio transcriptions/day on Whisper Large v3 | Fastest inference available free; hosted Whisper means no local ASR model to load into 512MB RAM |
| Vision (schematics) + 2nd verifier | Google AI Studio / Gemini API (free) | Gemini 2.5/3 Flash: ~1,500 requests/day, 15 RPM, multimodal | Native image understanding, and a *different provider* from Groq — needed for the cross-provider verification gate (§7) |
| OCR | Tesseract (local, in-process) | N/A — CPU-only, no GPU needed | Light enough to run inside the Render web service itself |
| Table parsing | pdfplumber / camelot-py | N/A | Preserves cell coordinates for citation |
| Backend | FastAPI + Uvicorn | N/A | One deployable service, one URL |
| Frontend | Single static HTML/JS page served by the same FastAPI app, `vis-network.js` via CDN for the graph view | N/A | Avoids a second Render service to keep warm and in sync |
| Hosting | Render free Web Service (Docker) | 750 free instance-hours/month, spins down after 15 min idle, ~30–60s cold start on wake | Confirmed current limits — plan around them, don't ignore them |
| Fallback provider | OpenRouter free-tier models | N/A — documented backup, not called unless the primary providers fail | Insurance against a Groq/Gemini free-tier change breaking the demo the morning of judging |

**What was deliberately left out and why:** local Ollama, local Whisper, local NLI/entailment models. All three are individually larger than the 512MB/0.1 CPU envelope Render's free tier gives you. Keep Ollama for local dev/prototyping if you want, but the **deployed** path must go through the hosted APIs above.

## 4. Architecture

```
[PDF / audio / table / schematic]
        |
        v
[FastAPI ingestion endpoint]
  - Tesseract OCR (PDFs, scanned pages, schematic labels)
  - Groq Whisper API (audio -> timestamped transcript)
  - pdfplumber/camelot (tables -> cell-referenced text)
  - Gemini Flash vision (schematics -> component relationships)
        |
        v
[Ontology-constrained extraction] --(Groq LLM, structured JSON output)-->
  entities + relations, each span-validated against source text
        |
        v
[Neo4j AuraDB]  <- versioned nodes/edges (valid_from/valid_to)
                <- provenance fields: source_doc_id, page/timestamp/cell, exact span
                <- native vector index on entity embeddings
        |
        v
[GraphRAG query] -- vector seed (Neo4j vector index) --> candidate entities
                  -- k-hop Cypher traversal --> evidence subgraph
        |
        v
[Draft answer] --(Groq LLM)--> decomposed into atomic claims
        |
        v
[Lexical/embedding overlap pre-filter]
  - Reject claims with near-zero word or embedding overlap against the evidence subgraph immediately, no API call spent
        |
        v
[Cross-provider verification gate]
  - Surviving claims checked against evidence subgraph by Gemini (different provider than the generator)
  - Supported -> kept, with citation
  - Unsupported -> stripped; if nothing survives, abstain outright
        |
        v
[Answer + citations + confidence score + signed evidence bundle] -> user
```

**Deployment topology:** one Render Web Service (Docker) hosts FastAPI + the static frontend. Neo4j AuraDB and the Groq/Gemini APIs are external managed services reached over HTTPS/Bolt — nothing else to deploy.

## 5. Data model / ontology

Keep this fixed and small — it's what makes extraction accurate and traversal meaningful.

**Entity types:** `Regulation`, `Requirement`, `Control`, `System`, `Asset`, `Evidence`, `Policy`, `Person`, `Incident`

**Relation types:** `REQUIRES`, `IMPLEMENTED_BY`, `EVIDENCED_BY`, `VIOLATES`, `SUPERSEDES`, `APPLIES_TO`, `RESPONSIBLE_FOR`, `MENTIONED_IN`

Every node and edge carries: `source_doc_id`, `source_location` (page / timestamp / cell ref / bbox), `exact_span`, `valid_from`, `valid_to`, `extraction_confidence`.

```cypher
CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE;
CREATE VECTOR INDEX entity_embedding IF NOT EXISTS
FOR (e:Entity) ON (e.embedding)
OPTIONS {indexConfig: {`vector.dimensions`: 768, `vector.similarity_function`: 'cosine'}};
```

**Extensibility rule:** extraction may propose a candidate type outside this list (e.g. `Vendor`, `Risk`, `AuditFinding`) when a source clearly calls for one. Before it's written to the graph, normalize it against the core 8 — map it to the nearest existing type, or add it to a small reviewed `extended_types` allow-list. This keeps the schema queryable and the F1 score meaningful while still covering frameworks the fixed 8 didn't anticipate.

## 6. Suggested repo structure

```
/app
  main.py                # FastAPI entrypoint
  /providers
    llm_router.py          # provider abstraction: swap Groq/Gemini/OpenRouter behind one interface
  /ingestion
    pdf.py  audio.py  table.py  schematic.py
  /extraction
    ontology.py          # entity/relation schema + prompts + candidate-type normalization
    extractor.py         # LLM call + span validation
  /graph
    neo4j_client.py       # includes retry-with-backoff on connect
    schema.cypher
  /retrieval
    graphrag.py           # vector seed + k-hop traversal
  /verification
    lexical_prefilter.py  # cheap overlap check before spending an API call
    entailment_gate.py    # cross-provider claim check + abstention
    evidence_bundle.py    # hash + export signed audit artifact
  /static
    index.html             # chat UI + vis-network graph panel
/eval
  qa_gold_set.json
  run_metrics.py           # computes the 4 evaluation metrics
Dockerfile
render.yaml
requirements.txt
```

## 7. Build phases (each = one Antigravity task)

**Phase 0 — Deployability-first scaffolding**
Set up FastAPI app, Dockerfile (installs `tesseract-ocr` via apt), `requirements.txt`, config entirely via environment variables (no hardcoded local paths, no assumption of persistent disk). Also scaffold `providers/llm_router.py` with Groq and Gemini as the two backends and OpenRouter as a documented third, and `graph/neo4j_client.py` with retry-with-backoff on connect. *Done when:* `docker build` + `docker run` serves a `/health` endpoint locally, and swapping the router's default provider requires editing one line.

**Phase 1 — Ingestion**
Implement the four ingestion paths (PDF/OCR, Whisper via Groq, table parsing, Gemini vision for schematics), each returning normalized chunks with full provenance metadata attached. *Done when:* four real public compliance documents ingest successfully and produce chunks with correct source pointers.

**Phase 2 — Extraction + graph write**
Ontology-constrained extraction via Groq structured JSON output, span-validated against source text, written to Neo4j with versioning fields. *Done when:* the ingested docs produce a non-trivial graph (50+ nodes) visible in Neo4j Browser, a manual spot-check of 10 relations confirms they're actually in the source, and a forced connection drop reconnects gracefully instead of throwing a raw exception at the user.

**Phase 3 — GraphRAG retrieval**
Vector-seed candidate entities via the Neo4j vector index, expand via k-hop Cypher traversal, serialize the evidence subgraph for the LLM. *Done when:* a multi-hop question spanning at least two source types returns a coherent draft answer with the correct subgraph attached.

**Phase 4 — Verification gate (non-negotiable, build this even if time is short)**
Claim decomposition, a lexical/embedding overlap pre-filter that rejects obviously ungrounded claims before spending an API call, then a cross-provider entailment check on the survivors (generate with Groq, verify with Gemini), strip unsupported claims, abstain if nothing survives, attach a 0–100 grounding confidence score. *Done when:* a deliberately unanswerable question (no grounding in the corpus) triggers explicit abstention, and a well-grounded question returns citations that all resolve correctly.

**Phase 5 — Differentiator features (stretch, in priority order)**
Note for the pitch: the "dynamic knowledge graph" core requirement is already satisfied by Phase 1–2 — the graph grows and updates as documents are ingested. What follows is the bonus layer on top, not a substitute for it. Say this explicitly when presenting, so judges don't read the core requirement as unmet just because the advanced version is labeled stretch.
1. Cross-modal contradiction detection between sources.
2. Temporal queries ("was Control-114 compliant on date X") — the advanced expression of "dynamic," built on the versioning fields already written in Phase 2.
3. Signed evidence-bundle export (SHA-256 hash of the subgraph + sources behind an answer, downloadable as JSON).

**Phase 6 — Render deployment (last step)**
Push Docker image, connect Render Web Service to the repo, set environment variables (below), confirm `/health` responds after a cold start, set up a free external pinger (cron-job.org or a GitHub Actions scheduled workflow hitting `/health` every 10 min) to keep the service warm during the judging window.

## 8. API contract

| Endpoint | Method | Purpose |
|---|---|---|
| `/ingest` | POST | Upload a file, returns ingestion status + extracted entity count |
| `/query` | POST | Ask a compliance question, returns answer + citations + confidence + subgraph |
| `/graph/subgraph` | GET | Fetch the evidence subgraph for a given answer, for the frontend graph view |
| `/evidence/{answer_id}` | GET | Download the signed evidence bundle |
| `/health` | GET | Liveness check, also used by the keep-warm pinger |

## 9. Environment variables

```
NEO4J_URI=neo4j+s://<your-instance>.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=<from Aura console>
GROQ_API_KEY=<from console.groq.com>
GEMINI_API_KEY=<from aistudio.google.com>
OPENROUTER_API_KEY=<from openrouter.ai, documented fallback if Groq/Gemini free-tier terms change>
```

## 10. `render.yaml` sketch

```yaml
services:
  - type: web
    name: compliance-kg
    env: docker
    plan: free
    healthCheckPath: /health
    envVars:
      - key: NEO4J_URI
        sync: false
      - key: NEO4J_USER
        sync: false
      - key: NEO4J_PASSWORD
        sync: false
      - key: GROQ_API_KEY
        sync: false
      - key: GEMINI_API_KEY
        sync: false
      - key: OPENROUTER_API_KEY
        sync: false
```

## 11. Evaluation harness (`/eval/run_metrics.py`)

Build a small script that:
- Loads `qa_gold_set.json` (10–15 hand-written questions — enough for an honest precision/F1 report without burning hours on manual labeling — with gold relevant nodes and expected abstentions) → computes **retrieval precision@k**.
- Compares extracted entities/relations on a document subset against a hand-annotated gold set → **entity extraction F1**.
- Runs all gold questions through the pipeline, checks what fraction of atomic claims pass the entailment gate, and confirms trap questions correctly abstain → **hallucination containment rate**.
- For every citation in every answer, resolves it against the raw source and checks for an exact match → **citation traceability**.

## 12. Honest tradeoffs to state up front (don't get caught flat-footed on these)

- Schematic understanding via a vision LLM is the weakest link — curate clean, clearly-labeled diagrams for the demo rather than dense engineering schematics.
- Render's free tier cold start (~30–60s) will show on the first request after idle — warm it a few minutes before presenting, and mention the keep-alive pinger if asked.
- LLM-as-judge verification is good but not as rigorous as a fine-tuned NLI classifier — say so if a judge asks, and note it as a documented upgrade path (self-hosted NLI model) once compute isn't free-tier-constrained.
- Cross-provider verification reduces correlated hallucination risk but doesn't eliminate it — both models can still independently agree on the same plausible-but-wrong claim. The lexical/embedding pre-filter catches the most blatant cases deterministically; the rest is a documented residual risk, not a solved one.
- Neo4j AuraDB Free has no backups and can hit unscheduled maintenance. The retry-with-backoff on the client handles a mid-demo reconnect, but export a manual backup snapshot the night before presenting, just in case.
