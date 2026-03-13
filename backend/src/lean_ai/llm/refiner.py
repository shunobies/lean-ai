"""Local LLM prompt refiner for cloud-bound requests.

Uses a local Ollama instance to:
1. Enrich prompts with knowledge base context (RAG) without leaking raw content
2. Strip/generalize sensitive data before cloud transmission
3. Restructure vague requests into well-formed prompts

The refiner is a transparent preprocessing layer — all failures are non-fatal
and fall back to passing the original text through unchanged.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lean_ai.llm.prompts import (
    PRIVACY_STRIP_PROMPT,
    REFINER_CHAT_PROMPT,
    REFINER_TASK_PROMPT,
)

if TYPE_CHECKING:
    from lean_ai.llm.client import OllamaProvider

logger = logging.getLogger(__name__)


@dataclass
class RefinerResult:
    """Output from a refinement operation."""

    original: str
    refined: str
    knowledge_context: str = ""
    privacy_redactions: list[str] = field(default_factory=list)
    was_refined: bool = False
    duration_ms: float = 0.0
    error: str | None = None


class PromptRefiner:
    """Local LLM pre-processor for cloud-bound prompts.

    Uses a local Ollama instance to enrich prompts with knowledge base
    context and strip sensitive data before cloud transmission.  This class
    is a no-op when ``ollama_provider`` is None (e.g. Ollama unreachable).
    """

    def __init__(
        self,
        ollama_provider: "OllamaProvider | None",
        *,
        enable_knowledge: bool = True,
        enable_privacy: bool = True,
        knowledge_chunks: int = 5,
        timeout: float = 30.0,
    ) -> None:
        self._ollama = ollama_provider
        self._enable_knowledge = enable_knowledge
        self._enable_privacy = enable_privacy
        self._knowledge_chunks = knowledge_chunks
        self._timeout = timeout

    @property
    def is_active(self) -> bool:
        """True when the refiner has a working Ollama provider."""
        return self._ollama is not None

    # ── Public API ──────────────────────────────────────────────

    async def refine_chat_message(
        self,
        user_message: str,
        repo_root: str | None = None,
        history: list[dict] | None = None,
    ) -> RefinerResult:
        """Refine a user message for the chat endpoint."""
        if not self.is_active:
            return RefinerResult(original=user_message, refined=user_message)

        start = time.monotonic()
        try:
            return await asyncio.wait_for(
                self._do_refine(
                    text=user_message,
                    repo_root=repo_root,
                    prompt_template=REFINER_CHAT_PROMPT,
                    text_placeholder="user_message",
                ),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Refiner timed out after %.1fs", self._timeout)
            return RefinerResult(
                original=user_message,
                refined=user_message,
                duration_ms=(time.monotonic() - start) * 1000,
                error="timeout",
            )
        except Exception as e:
            logger.warning("Refiner failed (non-fatal): %s", e)
            return RefinerResult(
                original=user_message,
                refined=user_message,
                duration_ms=(time.monotonic() - start) * 1000,
                error=str(e),
            )

    async def refine_task(
        self,
        task: str,
        repo_root: str,
        context: str = "",
    ) -> RefinerResult:
        """Refine a workflow task before planning."""
        if not self.is_active:
            return RefinerResult(original=task, refined=task)

        start = time.monotonic()
        try:
            return await asyncio.wait_for(
                self._do_refine(
                    text=task,
                    repo_root=repo_root,
                    prompt_template=REFINER_TASK_PROMPT,
                    text_placeholder="task",
                ),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Refiner timed out after %.1fs", self._timeout)
            return RefinerResult(
                original=task,
                refined=task,
                duration_ms=(time.monotonic() - start) * 1000,
                error="timeout",
            )
        except Exception as e:
            logger.warning("Refiner failed (non-fatal): %s", e)
            return RefinerResult(
                original=task,
                refined=task,
                duration_ms=(time.monotonic() - start) * 1000,
                error=str(e),
            )

    async def strip_privacy(self, text: str) -> tuple[str, list[str]]:
        """Strip sensitive data from text using the local LLM.

        Returns (sanitized_text, list_of_redaction_descriptions).
        """
        if not self._enable_privacy or self._ollama is None:
            return text, []

        prompt = PRIVACY_STRIP_PROMPT.format(text=text)
        try:
            response, _ = await asyncio.wait_for(
                self._ollama.chat_raw(
                    [{"role": "user", "content": prompt}],
                    temperature=0.1,
                ),
                timeout=self._timeout,
            )
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning("Privacy stripping failed: %s", e)
            return text, []

        sanitized, redactions = self._parse_privacy_response(response)

        # Over-stripping guard: if sanitized text is less than 60% of
        # original length, the LLM likely mangled it — keep original
        if len(sanitized) < len(text) * 0.6:
            logger.warning(
                "Privacy strip removed >40%% of text (%d -> %d chars), "
                "keeping original",
                len(text),
                len(sanitized),
            )
            return text, []

        return sanitized, redactions

    # ── Internal ────────────────────────────────────────────────

    async def _do_refine(
        self,
        text: str,
        repo_root: str | None,
        prompt_template: str,
        text_placeholder: str,
    ) -> RefinerResult:
        """Core refinement pipeline: knowledge query -> refine -> privacy strip."""
        assert self._ollama is not None
        start = time.monotonic()

        # Step 1: Query knowledge base for context
        knowledge_context = ""
        if self._enable_knowledge and repo_root:
            knowledge_context = await self._query_knowledge(
                query=text,
                repo_root=repo_root,
                limit=self._knowledge_chunks,
            ) or ""

        # Step 2: Build and execute refinement prompt
        knowledge_section = ""
        if knowledge_context:
            knowledge_section = (
                "DOMAIN KNOWLEDGE (extract insights, do NOT copy verbatim):\n"
                f"{knowledge_context}\n\n"
            )

        prompt = prompt_template.format(
            knowledge_section=knowledge_section,
            **{text_placeholder: text},
        )

        response, _ = await self._ollama.chat_raw(
            [{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        refined = response.strip()

        # Length guard: if refined text is less than 50% of original,
        # the LLM likely mangled it — keep original
        if len(refined) < len(text) * 0.5:
            logger.warning(
                "Refined text too short (%d -> %d chars), keeping original",
                len(text),
                len(refined),
            )
            return RefinerResult(
                original=text,
                refined=text,
                knowledge_context=knowledge_context,
                duration_ms=(time.monotonic() - start) * 1000,
            )

        # Step 3: Privacy strip the refined text
        privacy_redactions: list[str] = []
        if self._enable_privacy:
            refined, privacy_redactions = await self.strip_privacy(refined)

        duration = (time.monotonic() - start) * 1000
        logger.info(
            "Refined prompt: %d -> %d chars, knowledge=%s, redactions=%d (%.0fms)",
            len(text),
            len(refined),
            bool(knowledge_context),
            len(privacy_redactions),
            duration,
        )

        return RefinerResult(
            original=text,
            refined=refined,
            knowledge_context=knowledge_context,
            privacy_redactions=privacy_redactions,
            was_refined=refined != text,
            duration_ms=duration,
        )

    async def _query_knowledge(
        self,
        query: str,
        repo_root: str,
        limit: int = 5,
    ) -> str | None:
        """Query the knowledge base and format results for injection."""
        try:
            from lean_ai.knowledge.indexer import (
                is_knowledge_available,
                search_knowledge,
            )
        except ImportError:
            return None

        if not is_knowledge_available(repo_root):
            return None

        try:
            chunks = await asyncio.to_thread(
                search_knowledge, repo_root, query, limit,
            )
        except Exception as e:
            logger.debug("Knowledge query failed: %s", e)
            return None

        if not chunks:
            return None

        parts = []
        for chunk in chunks:
            title = chunk.get("doc_title", "Unknown")
            section = chunk.get("section", "")
            content = chunk.get("content", "")[:500]
            header = f"[{title} > {section}]" if section else f"[{title}]"
            parts.append(f"{header}\n{content}")

        return "\n\n".join(parts)

    @staticmethod
    def _parse_privacy_response(response: str) -> tuple[str, list[str]]:
        """Parse the LLM privacy strip response into sanitized text + redactions."""
        if "---REDACTIONS---" not in response:
            # No separator — return original response as-is
            return response.strip(), []

        sanitized, redactions_block = response.split("---REDACTIONS---", 1)
        redactions = [
            line.strip().lstrip("- ")
            for line in redactions_block.strip().splitlines()
            if line.strip() and line.strip() not in ("- None", "None")
        ]
        return sanitized.strip(), redactions
