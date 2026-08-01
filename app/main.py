"""
ANVESHA — Multi-Modal Knowledge Graph Enterprise Compliance Intelligence System.

FastAPI entrypoint. Serves the API + static frontend from a single service.
"""

import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from app.config import get_settings
from app.providers.llm_router import get_llm_router, shutdown_llm_router
from app.graph.neo4j_client import get_neo4j_client, shutdown_neo4j_client

# Configure logging
settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s | %(name)-30s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown events."""
    # --- Startup ---
    logger.info("=" * 60)
    logger.info(f"  ANVESHA v{settings.app_version} starting up...")
    logger.info(f"  Environment: {settings.app_env}")
    logger.info("=" * 60)

    # Initialize LLM Router
    llm_router = get_llm_router()
    provider_status = llm_router.health_check()
    logger.info(f"  LLM Providers: {provider_status}")

    # Connect to Neo4j (with retry)
    neo4j_client = get_neo4j_client()
    try:
        await neo4j_client.connect()
    except Exception as e:
        logger.warning(f"  Neo4j connection failed (will retry on first query): {e}")

    logger.info("  ✓ ANVESHA ready")
    logger.info("=" * 60)

    yield

    # --- Shutdown ---
    logger.info("ANVESHA shutting down...")
    await shutdown_llm_router()
    await shutdown_neo4j_client()
    logger.info("ANVESHA shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="ANVESHA",
    description=(
        "Multi-Modal Knowledge Graph Enterprise Compliance Intelligence System. "
        "Ingests heterogeneous compliance data, builds a dynamic knowledge graph, "
        "and answers questions with GraphRAG — zero hallucinations, full citation traceability."
    ),
    version=settings.app_version,
    lifespan=lifespan,
)

# CORS (allow all for development, restrict in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request timing middleware ---
@app.middleware("http")
async def add_timing_header(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start
    response.headers["X-Response-Time"] = f"{elapsed:.3f}s"
    return response


# --- Core endpoints ---

@app.get("/health", tags=["System"])
async def health_check():
    """
    Liveness check — used by Render health checks and the keep-warm pinger.
    Returns provider status and graph connectivity.
    """
    llm_router = get_llm_router()
    neo4j_client = get_neo4j_client()

    graph_stats = await neo4j_client.get_graph_stats()

    return {
        "status": "healthy",
        "version": settings.app_version,
        "environment": settings.app_env,
        "providers": llm_router.health_check(),
        "graph": graph_stats,
    }


@app.get("/api/stats", tags=["System"])
async def get_system_stats():
    """Get comprehensive system statistics."""
    neo4j_client = get_neo4j_client()
    llm_router = get_llm_router()

    graph_stats = await neo4j_client.get_graph_stats()

    return {
        "graph": graph_stats,
        "providers": llm_router.health_check(),
        "config": {
            "graphrag_k_hops": settings.graphrag_k_hops,
            "graphrag_top_k_seeds": settings.graphrag_top_k_seeds,
            "embedding_dimensions": settings.embedding_dimensions,
        },
    }


# --- Static files (frontend) ---
# Serve static files from /static directory
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", tags=["Frontend"])
async def serve_frontend():
    """Serve the main frontend page."""
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse(
        content={
            "message": "ANVESHA API is running. Frontend not yet deployed.",
            "docs": "/docs",
            "health": "/health",
        }
    )


# --- Include API routers ---
from app.ingestion.router import router as ingestion_router
from app.retrieval.router import router as retrieval_router
from app.verification.router import router as verification_router
from app.audit.router import router as audit_router
from eval.router import router as eval_router
app.include_router(ingestion_router, prefix="/api", tags=["Ingestion"])
app.include_router(retrieval_router, prefix="/api", tags=["Retrieval"])
app.include_router(verification_router, prefix="/api", tags=["Verification"])
app.include_router(audit_router, prefix="/api", tags=["Audit"])
app.include_router(eval_router, prefix="/api", tags=["Evaluation"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.app_env == "development",
        log_level=settings.log_level.lower(),
    )
