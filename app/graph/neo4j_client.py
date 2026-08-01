"""
ANVESHA Neo4j Client — Graph database connection with retry-with-backoff.

Handles connection lifecycle, schema initialization, and graceful reconnection.
A mid-demo connection drop reconnects gracefully instead of throwing a raw exception.
"""

import logging
import asyncio
from typing import Optional, Any

from neo4j import AsyncGraphDatabase, AsyncDriver, AsyncSession
from neo4j.exceptions import ServiceUnavailable, SessionExpired, AuthError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from app.config import get_settings

logger = logging.getLogger(__name__)

# Schema Cypher statements for initialization
SCHEMA_STATEMENTS = [
    # Uniqueness constraints
    "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE",
    "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE",

    # Vector index for entity embeddings
    """CREATE VECTOR INDEX entity_embedding IF NOT EXISTS
    FOR (e:Entity) ON (e.embedding)
    OPTIONS {indexConfig: {
        `vector.dimensions`: 768,
        `vector.similarity_function`: 'cosine'
    }}""",

    # Full-text index for text search
    """CREATE FULLTEXT INDEX entity_text IF NOT EXISTS
    FOR (e:Entity) ON EACH [e.name, e.description, e.exact_span]""",
]


class Neo4jClient:
    """
    Async Neo4j client with retry-with-backoff.

    Features:
    - Automatic reconnection on connection drops
    - Schema initialization on first connect
    - Connection pooling via the driver
    - Graceful shutdown
    """

    def __init__(self):
        self._settings = get_settings()
        self._driver: Optional[AsyncDriver] = None
        self._connected: bool = False

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=1, max=30),
        retry=retry_if_exception_type((ServiceUnavailable, OSError, ConnectionError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def connect(self) -> None:
        """
        Connect to Neo4j with retry-with-backoff.

        Retries up to 5 times with exponential backoff (1s, 2s, 4s, 8s, 16s).
        """
        settings = self._settings
        if not settings.neo4j_uri or not settings.neo4j_password:
            logger.warning("⚠ Neo4j credentials not set — running in offline mode")
            self._connected = False
            return

        logger.info(f"Connecting to Neo4j: {settings.neo4j_uri}")
        try:
            self._driver = AsyncGraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
                max_connection_pool_size=5,
                connection_acquisition_timeout=30,
            )
            # Verify connectivity
            await self._driver.verify_connectivity()
            self._connected = True
            logger.info("✓ Neo4j connected successfully")

            # Initialize schema
            await self._init_schema()

        except AuthError as e:
            logger.error(f"Neo4j authentication failed: {e}")
            self._connected = False
            raise
        except Exception as e:
            logger.error(f"Neo4j connection failed: {e}")
            self._connected = False
            raise

    async def _init_schema(self) -> None:
        """Initialize graph schema (constraints + indexes)."""
        if not self._driver:
            return

        logger.info("Initializing Neo4j schema...")
        async with self._driver.session(database=self._settings.neo4j_database) as session:
            for stmt in SCHEMA_STATEMENTS:
                try:
                    await session.run(stmt)
                    logger.debug(f"Schema: {stmt[:60]}... OK")
                except Exception as e:
                    # Some statements may fail if already exists — that's fine
                    logger.debug(f"Schema statement skipped (may already exist): {e}")

        logger.info("✓ Neo4j schema initialized")

    async def get_session(self) -> AsyncSession:
        """
        Get a Neo4j session, reconnecting if needed.

        Returns:
            AsyncSession for executing queries
        """
        if not self._connected or not self._driver:
            await self.connect()

        if not self._driver:
            raise RuntimeError("Neo4j is not available — check credentials")

        return self._driver.session(database=self._settings.neo4j_database)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((ServiceUnavailable, SessionExpired)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def execute_query(
        self,
        query: str,
        parameters: Optional[dict] = None,
        write: bool = False,
    ) -> list[dict]:
        """
        Execute a Cypher query with automatic retry on connection issues.

        Args:
            query: Cypher query string
            parameters: Query parameters
            write: Whether this is a write transaction

        Returns:
            List of result records as dicts
        """
        if not self._connected:
            logger.warning("Neo4j not connected — attempting reconnection")
            await self.connect()

        async def _tx_work(tx):
            result = await tx.run(query, parameters or {})
            records = [record.data() async for record in result]
            return records

        async with await self.get_session() as session:
            try:
                if write:
                    records = await session.execute_write(_tx_work)
                else:
                    records = await session.execute_read(_tx_work)
                return records
            except (ServiceUnavailable, SessionExpired) as e:
                logger.warning(f"Neo4j session error, will retry: {e}")
                self._connected = False
                raise

    async def execute_write(self, query: str, parameters: Optional[dict] = None) -> list[dict]:
        """Convenience wrapper for write queries."""
        return await self.execute_query(query, parameters, write=True)

    async def execute_read(self, query: str, parameters: Optional[dict] = None) -> list[dict]:
        """Convenience wrapper for read queries."""
        return await self.execute_query(query, parameters, write=False)

    async def vector_search(
        self,
        query_embedding: list[float],
        top_k: int = 10,
        min_score: float = 0.5,
    ) -> list[dict]:
        """
        Vector similarity search using Neo4j's native vector index.

        Args:
            query_embedding: Query vector
            top_k: Number of results
            min_score: Minimum similarity score

        Returns:
            List of matched entities with scores
        """
        query = """
        CALL db.index.vector.queryNodes('entity_embedding', $top_k, $embedding)
        YIELD node, score
        WHERE score >= $min_score
        RETURN node {
            .id, .name, .entity_type, .description, .exact_span,
            .source_doc_id, .source_location, .extraction_confidence
        } AS entity, score
        ORDER BY score DESC
        """
        return await self.execute_read(query, {
            "embedding": query_embedding,
            "top_k": top_k,
            "min_score": min_score,
        })

    async def k_hop_traversal(
        self,
        entity_ids: list[str],
        k_hops: int = 2,
        max_nodes: int = 50,
    ) -> dict:
        """
        k-hop graph traversal from seed entities.

        Returns:
            Dict with 'nodes' and 'edges' for the evidence subgraph
        """
        query = """
        UNWIND $entity_ids AS eid
        MATCH (seed:Entity {id: eid})
        CALL apoc.path.subgraphAll(seed, {
            maxLevel: $k_hops,
            limit: $max_nodes
        })
        YIELD nodes, relationships
        UNWIND nodes AS n
        WITH COLLECT(DISTINCT n) AS allNodes, relationships
        UNWIND relationships AS r
        WITH allNodes, COLLECT(DISTINCT r) AS allRels
        RETURN
            [n IN allNodes | n {.id, .name, .entity_type, .description, .exact_span,
                .source_doc_id, .source_location}] AS nodes,
            [r IN allRels | {
                source: startNode(r).id,
                target: endNode(r).id,
                type: type(r),
                properties: properties(r)
            }] AS edges
        """
        # Try APOC-based traversal first, fall back to manual BFS if APOC isn't available
        try:
            results = await self.execute_read(query, {
                "entity_ids": entity_ids,
                "k_hops": k_hops,
                "max_nodes": max_nodes,
            })
            if results:
                return results[0]
        except Exception as e:
            logger.warning(f"APOC traversal failed (may not be installed), using manual BFS: {e}")

        # Manual BFS fallback (works without APOC)
        return await self._manual_k_hop(entity_ids, k_hops, max_nodes)

    async def _manual_k_hop(
        self,
        entity_ids: list[str],
        k_hops: int,
        max_nodes: int,
    ) -> dict:
        """Manual k-hop BFS traversal without APOC."""
        # Build query with literal hop count (Cypher doesn't support parameterized path length)
        query = f"""
        UNWIND $entity_ids AS eid
        MATCH (seed:Entity {{id: eid}})
        OPTIONAL MATCH path = (seed)-[*1..{k_hops}]-(neighbor:Entity)
        WHERE neighbor IS NOT NULL
        WITH seed, neighbor, relationships(path) AS rels
        LIMIT $max_nodes
        WITH collect(DISTINCT seed {{.id, .name, .entity_type, .description,
             .exact_span, .source_doc_id, .source_location}}) +
             collect(DISTINCT neighbor {{.id, .name, .entity_type, .description,
             .exact_span, .source_doc_id, .source_location}}) AS allNodes,
             collect(rels) AS allRelPaths
        UNWIND allRelPaths AS relPath
        UNWIND relPath AS r
        WITH allNodes, collect(DISTINCT {{
            source: startNode(r).id,
            target: endNode(r).id,
            type: type(r)
        }}) AS edges
        RETURN allNodes AS nodes, edges
        """

        try:
            results = await self.execute_read(query, {
                "entity_ids": entity_ids,
                "max_nodes": max_nodes,
            })
            if results:
                return results[0]
        except Exception as e:
            logger.error(f"Manual k-hop traversal failed: {e}")

        return {"nodes": [], "edges": []}

    async def get_graph_stats(self) -> dict:
        """Get basic graph statistics."""
        if not self._connected:
            return {"status": "disconnected", "nodes": 0, "relationships": 0}

        try:
            node_count = await self.execute_read("MATCH (n) RETURN count(n) AS count")
            rel_count = await self.execute_read("MATCH ()-[r]->() RETURN count(r) AS count")
            entity_types = await self.execute_read(
                "MATCH (e:Entity) RETURN DISTINCT e.entity_type AS type, count(*) AS count"
            )
            return {
                "status": "connected",
                "nodes": node_count[0]["count"] if node_count else 0,
                "relationships": rel_count[0]["count"] if rel_count else 0,
                "entity_types": {r["type"]: r["count"] for r in entity_types} if entity_types else {},
            }
        except Exception as e:
            logger.error(f"Failed to get graph stats: {e}")
            return {"status": "error", "error": str(e)}

    async def close(self) -> None:
        """Close the Neo4j driver."""
        if self._driver:
            await self._driver.close()
            self._connected = False
            logger.info("Neo4j connection closed")

    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._connected


# Singleton
_client: Optional[Neo4jClient] = None


def get_neo4j_client() -> Neo4jClient:
    """Get or create the global Neo4j client singleton."""
    global _client
    if _client is None:
        _client = Neo4jClient()
    return _client


async def shutdown_neo4j_client():
    """Shutdown the global Neo4j client."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None
