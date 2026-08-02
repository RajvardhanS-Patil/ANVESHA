"""
ANVESHA Configuration — All settings via environment variables.

No hardcoded paths, no local disk persistence assumptions.
Every config value is overridable via env vars for Render deployment.
"""

import os
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # --- App ---
    app_name: str = "ANVESHA"
    app_version: str = "1.0.0"
    app_env: str = Field(default="development", description="development | staging | production")
    log_level: str = Field(default="INFO", description="DEBUG | INFO | WARNING | ERROR")
    max_upload_size_mb: int = Field(default=50, description="Max file upload size in MB")

    # --- Neo4j AuraDB ---
    neo4j_uri: str = Field(default="", description="Neo4j Bolt URI (neo4j+s://...)")
    neo4j_user: str = Field(default="neo4j", description="Neo4j username")
    neo4j_password: str = Field(default="", description="Neo4j password")
    neo4j_database: str = Field(default="neo4j", description="Neo4j database name")
    neo4j_max_retries: int = Field(default=5, description="Max connection retry attempts")
    neo4j_retry_delay: float = Field(default=2.0, description="Base delay between retries (seconds)")

    # --- Groq API (Primary LLM + Whisper ASR) ---
    groq_api_key: str = Field(default="", description="Groq API key")
    groq_llm_model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Groq LLM model for generation"
    )
    groq_whisper_model: str = Field(
        default="whisper-large-v3-turbo",
        description="Groq Whisper model for ASR"
    )
    groq_max_rpm: int = Field(default=30, description="Groq rate limit (requests per minute)")

    # --- Google Gemini API (Verification + Vision) ---
    gemini_api_key: str = Field(default="", description="Google Gemini API key")
    gemini_model: str = Field(
        default="gemini-2.0-flash",
        description="Gemini model for verification and vision"
    )
    gemini_max_rpm: int = Field(default=15, description="Gemini rate limit (requests per minute)")

    # --- OpenRouter (Fallback Provider) ---
    openrouter_api_key: str = Field(default="", description="OpenRouter API key (fallback)")
    openrouter_model: str = Field(
        default="openrouter/free",
        description="OpenRouter fallback model"
    )

    # --- Embedding ---
    embedding_dimensions: int = Field(default=768, description="Vector embedding dimensions")

    # --- GraphRAG ---
    graphrag_k_hops: int = Field(default=2, description="Number of hops for graph traversal")
    graphrag_top_k_seeds: int = Field(default=10, description="Top-k vector search seeds")
    graphrag_max_context_nodes: int = Field(default=50, description="Max nodes in evidence subgraph")

    # --- Verification ---
    verification_confidence_threshold: float = Field(
        default=0.6, description="Min confidence to include a claim"
    )

    # --- Lyzr AI ---
    lyzr_api_key: Optional[str] = Field(default=None, description="Lyzr API Key")
    lyzr_agent_id: Optional[str] = Field(default=None, description="Lyzr Agent ID")

    # --- Vapi (AI Voice Calling) ---
    vapi_api_key: Optional[str] = Field(default=None, description="Vapi API Key")
    vapi_assistant_id: Optional[str] = Field(default=None, description="Vapi Assistant ID")
    vapi_phone_number_id: Optional[str] = Field(default=None, description="Vapi Phone Number ID")

    # --- Server ---
    host: str = Field(default="0.0.0.0", description="Server host")
    port: int = Field(default=8000, description="Server port")

    model_config = {
        "env_file": os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }


@lru_cache()
def get_settings() -> Settings:
    """Cached settings singleton — load once, reuse everywhere."""
    return Settings()
