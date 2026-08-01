"""
ANVESHA LLM Router — Provider abstraction layer.

All LLM/Vision/ASR calls go through this single interface.
Swapping a provider means editing one file, not hunting through the codebase.

Providers:
  - Groq: Primary LLM (Llama 3.3 70B) + Whisper ASR
  - Gemini: Verification LLM + Vision (schematics)
  - OpenRouter: Documented fallback if primary providers fail
"""

import json
import time
import logging
import asyncio
from typing import Optional, Any
from enum import Enum

import httpx
from groq import Groq, AsyncGroq
import google.generativeai as genai

from app.config import get_settings

logger = logging.getLogger(__name__)


class Provider(str, Enum):
    """Supported LLM providers."""
    GROQ = "groq"
    GEMINI = "gemini"
    OPENROUTER = "openrouter"


class RateLimiter:
    """Simple token-bucket rate limiter for API calls."""

    def __init__(self, max_rpm: int):
        self.max_rpm = max_rpm
        self.interval = 60.0 / max_rpm
        self._last_call: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self):
        """Wait until we can make the next API call."""
        async with self._lock:
            now = time.monotonic()
            wait_time = self._last_call + self.interval - now
            if wait_time > 0:
                logger.debug(f"Rate limiter: waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
            self._last_call = time.monotonic()


class LLMRouter:
    """
    Unified provider abstraction for all AI service calls.

    Usage:
        router = LLMRouter()
        response = await router.generate("What is GDPR?", provider=Provider.GROQ)
        transcript = await router.transcribe(audio_bytes, provider=Provider.GROQ)
        description = await router.vision(image_bytes, "Describe this schematic", provider=Provider.GEMINI)
    """

    def __init__(self):
        settings = get_settings()
        self._settings = settings
        self._groq_client: Optional[AsyncGroq] = None
        self._groq_sync_client: Optional[Groq] = None
        self._gemini_configured: bool = False
        self._openrouter_client: Optional[httpx.AsyncClient] = None

        # Rate limiters
        self._groq_limiter = RateLimiter(settings.groq_max_rpm)
        self._gemini_limiter = RateLimiter(settings.gemini_max_rpm)

        # Initialize available providers
        self._init_providers()

    def _init_providers(self):
        """Initialize API clients for configured providers."""
        settings = self._settings

        # Groq
        if settings.groq_api_key:
            self._groq_client = AsyncGroq(api_key=settings.groq_api_key)
            self._groq_sync_client = Groq(api_key=settings.groq_api_key)
            logger.info("✓ Groq provider initialized")
        else:
            logger.warning("⚠ Groq API key not set — primary LLM/ASR unavailable")

        # Gemini
        if settings.gemini_api_key:
            genai.configure(api_key=settings.gemini_api_key)
            self._gemini_configured = True
            logger.info("✓ Gemini provider initialized")
        else:
            logger.warning("⚠ Gemini API key not set — verification/vision unavailable")

        # OpenRouter (lazy — only when needed)
        if settings.openrouter_api_key:
            logger.info("✓ OpenRouter fallback available")

    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        provider: Provider = Provider.GROQ,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        response_format: Optional[dict] = None,
    ) -> str:
        """
        Generate text from an LLM.

        Args:
            prompt: User prompt
            system_prompt: System instruction
            provider: Which provider to use
            temperature: Sampling temperature
            max_tokens: Max tokens in response
            response_format: JSON schema for structured output (Groq only)

        Returns:
            Generated text string
        """
        try:
            if provider == Provider.GROQ:
                return await self._groq_generate(
                    prompt, system_prompt, temperature, max_tokens, response_format
                )
            elif provider == Provider.GEMINI:
                return await self._gemini_generate(
                    prompt, system_prompt, temperature, max_tokens
                )
            elif provider == Provider.OPENROUTER:
                return await self._openrouter_generate(
                    prompt, system_prompt, temperature, max_tokens
                )
        except Exception as e:
            logger.error(f"Provider {provider} failed: {e}")
            # Attempt fallback
            return await self._fallback_generate(
                prompt, system_prompt, temperature, max_tokens, failed_provider=provider
            )

    async def generate_json(
        self,
        prompt: str,
        system_prompt: str = "",
        provider: Provider = Provider.GROQ,
        temperature: float = 0.0,
    ) -> dict:
        """Generate structured JSON output from an LLM."""
        try:
            if provider == Provider.GROQ:
                response = await self._groq_generate(
                    prompt, system_prompt, temperature, 4096,
                    response_format={"type": "json_object"}
                )
            elif provider == Provider.GEMINI:
                json_prompt = f"{prompt}\n\nRespond ONLY with valid JSON, no markdown or other text."
                response = await self._gemini_generate(
                    json_prompt, system_prompt, temperature, 4096
                )
            elif provider == Provider.OPENROUTER:
                json_prompt = f"{prompt}\n\nRespond ONLY with valid JSON, no markdown or other text."
                response = await self._openrouter_generate(
                    json_prompt, system_prompt, temperature, 4096
                )
            else:
                response = await self.generate(
                    f"{prompt}\n\nRespond ONLY with valid JSON.", system_prompt, provider, temperature
                )
        except Exception as e:
            logger.error(f"generate_json failed with {provider}: {e}")
            return {"error": str(e)}

        # Parse JSON from response
        try:
            # Strip markdown code fences if present
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:].strip()
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from LLM response: {e}\nResponse: {response[:500]}")
            return {"error": "Failed to parse JSON", "raw_response": response[:500]}

    async def transcribe(
        self,
        audio_data: bytes,
        filename: str = "audio.wav",
        language: str = "en",
    ) -> dict:
        """
        Transcribe audio using Groq Whisper API.

        Returns:
            Dict with 'text' and 'segments' (timestamped chunks)
        """
        if not self._groq_sync_client:
            raise RuntimeError("Groq not configured — cannot transcribe audio")

        await self._groq_limiter.acquire()
        logger.info(f"Transcribing audio: {filename} ({len(audio_data)} bytes)")

        try:
            # Groq Whisper API requires sync client
            transcription = self._groq_sync_client.audio.transcriptions.create(
                file=(filename, audio_data),
                model=self._settings.groq_whisper_model,
                language=language,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
            result = {
                "text": transcription.text,
                "segments": [],
                "language": language,
            }
            if hasattr(transcription, "segments") and transcription.segments:
                for seg in transcription.segments:
                    result["segments"].append({
                        "start": seg.get("start", seg.start) if hasattr(seg, "start") else seg.get("start", 0),
                        "end": seg.get("end", seg.end) if hasattr(seg, "end") else seg.get("end", 0),
                        "text": seg.get("text", seg.text) if hasattr(seg, "text") else seg.get("text", ""),
                    })
            logger.info(f"Transcription complete: {len(result['text'])} chars, {len(result['segments'])} segments")
            return result
        except Exception as e:
            logger.error(f"Whisper transcription failed: {e}")
            raise

    async def vision(
        self,
        image_data: bytes,
        prompt: str,
        mime_type: str = "image/png",
    ) -> str:
        """
        Analyze an image using Gemini Vision.

        Args:
            image_data: Raw image bytes
            prompt: What to analyze/extract
            mime_type: Image MIME type

        Returns:
            Text description/analysis
        """
        if not self._gemini_configured:
            raise RuntimeError("Gemini not configured — cannot analyze images")

        await self._gemini_limiter.acquire()
        logger.info(f"Vision analysis: {len(image_data)} bytes, mime={mime_type}")

        try:
            model = genai.GenerativeModel(self._settings.gemini_model)
            response = model.generate_content(
                [
                    prompt,
                    {"mime_type": mime_type, "data": image_data},
                ],
                generation_config=genai.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=4096,
                ),
            )
            return response.text
        except Exception as e:
            logger.error(f"Gemini vision failed: {e}")
            raise

    async def embed(self, text: str) -> list[float]:
        """
        Generate text embedding using Gemini embedding model.

        Returns:
            List of floats (embedding vector)
        """
        if not self._gemini_configured:
            # Fallback: simple TF-IDF style hash embedding
            return self._fallback_embed(text)

        await self._gemini_limiter.acquire()
        try:
            result = genai.embed_content(
                model="models/text-embedding-004",
                content=text,
                task_type="retrieval_document",
            )
            return result["embedding"]
        except Exception as e:
            logger.warning(f"Gemini embedding failed, using fallback: {e}")
            return self._fallback_embed(text)

    def _fallback_embed(self, text: str) -> list[float]:
        """
        Deterministic fallback embedding using character-level hashing.
        Not semantically meaningful but consistent — used only when APIs are down.
        """
        import hashlib
        import numpy as np

        dims = self._settings.embedding_dimensions
        h = hashlib.sha512(text.encode()).digest()
        seed = int.from_bytes(h[:4], "big")
        rng = np.random.RandomState(seed)
        vec = rng.randn(dims).astype(np.float32)
        # Normalize
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.tolist()

    # --- Private provider implementations ---

    async def _groq_generate(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
        response_format: Optional[dict] = None,
    ) -> str:
        if not self._groq_client:
            raise RuntimeError("Groq not configured")

        await self._groq_limiter.acquire()

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        kwargs = {
            "model": self._settings.groq_llm_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            kwargs["response_format"] = response_format

        response = await self._groq_client.chat.completions.create(**kwargs)
        return response.choices[0].message.content

    async def _gemini_generate(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        if not self._gemini_configured:
            raise RuntimeError("Gemini not configured")

        await self._gemini_limiter.acquire()

        model = genai.GenerativeModel(
            self._settings.gemini_model,
            system_instruction=system_prompt if system_prompt else None,
        )
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        )
        return response.text

    async def _openrouter_generate(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        if not self._settings.openrouter_api_key:
            raise RuntimeError("OpenRouter not configured")

        if not self._openrouter_client:
            self._openrouter_client = httpx.AsyncClient(timeout=60.0)

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = await self._openrouter_client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self._settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._settings.openrouter_model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    async def _fallback_generate(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
        failed_provider: Provider,
    ) -> str:
        """Try alternate providers when primary fails."""
        fallback_order = [Provider.GROQ, Provider.GEMINI, Provider.OPENROUTER]
        fallback_order = [p for p in fallback_order if p != failed_provider]

        for provider in fallback_order:
            try:
                logger.info(f"Attempting fallback to {provider}")
                if provider == Provider.GROQ:
                    return await self._groq_generate(
                        prompt, system_prompt, temperature, max_tokens
                    )
                elif provider == Provider.GEMINI:
                    return await self._gemini_generate(
                        prompt, system_prompt, temperature, max_tokens
                    )
                elif provider == Provider.OPENROUTER:
                    return await self._openrouter_generate(
                        prompt, system_prompt, temperature, max_tokens
                    )
            except Exception as e:
                logger.warning(f"Fallback {provider} also failed: {e}")
                continue

        raise RuntimeError("All LLM providers failed — no generation possible")

    async def close(self):
        """Cleanup resources."""
        if self._groq_client:
            await self._groq_client.close()
        if self._openrouter_client:
            await self._openrouter_client.aclose()
        logger.info("LLM Router closed")

    def health_check(self) -> dict:
        """Return provider availability status."""
        return {
            "groq": self._groq_client is not None,
            "gemini": self._gemini_configured,
            "openrouter": bool(self._settings.openrouter_api_key),
        }


# Singleton instance
_router: Optional[LLMRouter] = None


def get_llm_router() -> LLMRouter:
    """Get or create the global LLM router singleton."""
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router


async def shutdown_llm_router():
    """Shutdown the global LLM router."""
    global _router
    if _router is not None:
        await _router.close()
        _router = None
