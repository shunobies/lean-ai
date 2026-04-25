"""Google Gemini LLM provider — wraps the google-genai SDK."""

import logging
from collections.abc import AsyncIterator

from pydantic import BaseModel, ValidationError

from lean_ai.config import settings
from lean_ai.llm.base import LLMMetrics, LLMProvider, ToolCallInfo, retry_with_backoff

logger = logging.getLogger(__name__)


def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """Extract system prompt from messages and return (system_str, non_system_messages).

    Gemini uses system_instruction as a separate config parameter.
    """
    system_parts: list[str] = []
    filtered: list[dict] = []
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content") or ""
            if content:
                system_parts.append(content)
        else:
            filtered.append(msg)
    return "\n\n".join(system_parts), filtered


def _convert_tools(tools: list[dict], types_mod) -> list:
    """Convert OpenAI-format tool definitions to Gemini FunctionDeclaration format.

    OpenAI: ``{"type": "function", "function": {"name": ..., ...}}``
    Gemini: ``types.Tool(function_declarations=[{"name": ..., "parameters": ...}])``
    """
    declarations = []
    for tool in tools:
        fn = tool.get("function", tool)
        declarations.append(
            {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
            }
        )
    return [types_mod.Tool(function_declarations=declarations)]


class GeminiProvider(LLMProvider):
    """LLM provider backed by Google's Gemini API via the google-genai SDK."""

    _THINKING_BUDGET_PERCENT = 0.8

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        max_tokens: int = 262144,
        context_window: int = 1048576,
        temperature: float = 0.7,
        retry_max: int | None = None,
        retry_base_delay: float | None = None,
        enable_thinking: bool = False,
        reasoning_effort: str = "",
    ):
        from google import genai
        from google.genai import types

        self._genai = genai
        self._types = types
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._max_tokens_val = max_tokens
        self._context_window_val = context_window
        self._temperature = temperature
        self._enable_thinking = enable_thinking
        # Reasoning effort → ThinkingConfig.thinking_budget override.  When
        # unset ("" / "max") we use Gemini's dynamic budget (-1) so the
        # model decides; low / medium / high cap explicitly.
        self._reasoning_effort = reasoning_effort or ""
        self._retry_max = retry_max if retry_max is not None else settings.llm_retry_max
        self._retry_base_delay = (
            retry_base_delay if retry_base_delay is not None else settings.llm_retry_base_delay
        )

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def context_window(self) -> int:
        return self._context_window_val

    @property
    def max_tokens(self) -> int:
        return self._max_tokens_val

    async def _retry_with_backoff(self, coro_factory, label: str = "Gemini call"):
        """Retry with exponential backoff for transient errors."""

        def _is_retryable(exc: Exception) -> bool:
            exc_name = type(exc).__name__
            return any(
                kw in exc_name.lower()
                for kw in ("unavailable", "resourceexhausted", "deadline", "internal")
            )

        return await retry_with_backoff(
            coro_factory,
            retryable_exceptions=(ConnectionError, TimeoutError, OSError),
            is_retryable=_is_retryable,
            max_retries=self._retry_max,
            base_delay=self._retry_base_delay,
            label=label,
        )

    def _build_contents(self, messages: list[dict]) -> list:
        """Convert standard message dicts to Gemini Content format.

        Handles regular text messages and tool call/result messages tagged
        by format_assistant_tool_message / format_tool_result_messages.
        """
        types = self._types
        contents = []

        for msg in messages:
            role = msg.get("role", "user")
            gemini_role = "model" if role == "assistant" else "user"

            # Assistant message with function calls
            gemini_fcs = msg.get("_gemini_function_calls")
            if gemini_fcs:
                parts = []
                text = msg.get("content")
                if text:
                    parts.append(types.Part(text=text))
                for fc in gemini_fcs:
                    parts.append(
                        types.Part(
                            function_call=types.FunctionCall(
                                name=fc["name"],
                                args=fc["args"],
                                id=fc.get("id"),
                            )
                        )
                    )
                contents.append(types.Content(role="model", parts=parts))
                continue

            # User message with function response
            gemini_fr = msg.get("_gemini_function_response")
            if gemini_fr:
                part = types.Part.from_function_response(
                    name=gemini_fr["name"],
                    response=gemini_fr["response"],
                    id=gemini_fr.get("id"),
                )
                contents.append(types.Content(role="user", parts=[part]))
                continue

            # Regular text message
            content = msg.get("content") or ""
            # Handle content-block messages (list of dicts).  Image and
            # audio blocks produced by media_messages.attach_* are emitted
            # as ``Part.from_bytes`` so the model sees the media natively
            # instead of a text placeholder.
            if isinstance(content, list):
                parts_built: list = []
                text_buf: list[str] = []

                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        text_buf.append(block.get("text", ""))
                    elif btype in ("tool_result", "tool_use"):
                        text_buf.append(str(block.get("content", "")))
                    elif btype in ("image", "audio"):
                        # Flush any pending text before the media part.
                        if text_buf:
                            parts_built.append(types.Part(text="\n".join(text_buf)))
                            text_buf = []
                        raw_b64 = block.get("data") or ""
                        mime = block.get("mime_type") or (
                            "image/png" if btype == "image" else "audio/wav"
                        )
                        if raw_b64:
                            import base64

                            try:
                                raw_bytes = base64.b64decode(raw_b64)
                            except Exception:
                                logger.warning(
                                    "Gemini: failed to decode base64 %s block; skipping",
                                    btype,
                                )
                                continue
                            parts_built.append(
                                types.Part.from_bytes(
                                    data=raw_bytes,
                                    mime_type=mime,
                                )
                            )

                if text_buf:
                    parts_built.append(types.Part(text="\n".join(text_buf)))

                if parts_built:
                    contents.append(
                        types.Content(
                            role=gemini_role,
                            parts=parts_built,
                        )
                    )
                continue

            if content:
                contents.append(
                    types.Content(
                        role=gemini_role,
                        parts=[types.Part(text=content)],
                    )
                )

        return contents

    def _extract_metrics(
        self,
        response,
        *,
        stop_reason: str | None = None,
    ) -> LLMMetrics:
        """Extract metrics from a Gemini response."""
        usage = getattr(response, "usage_metadata", None)
        if usage:
            return LLMMetrics.from_usage(
                getattr(usage, "prompt_token_count", 0),
                getattr(usage, "candidates_token_count", 0),
                stop_reason=stop_reason,
            )
        return LLMMetrics(stop_reason=stop_reason)

    def _get_finish_reason(self, response) -> str | None:
        """Extract finish reason from Gemini response candidates."""
        candidates = getattr(response, "candidates", None)
        if candidates and len(candidates) > 0:
            reason = getattr(candidates[0], "finish_reason", None)
            if reason is not None:
                return str(reason)
        return None

    def _apply_thinking_config(self, config, tokens: int) -> None:
        """Add thinking config when enabled.

        Budget resolution:
        1. ``reasoning_effort`` in (``low``, ``medium``, ``high``) maps to
           1024 / 4096 / 16384 respectively.
        2. ``reasoning_effort`` in (``""``, ``"max"``) → ``-1`` (dynamic —
           Gemini decides).  Matches today's behaviour by default.

        Users who rely on the old ``_THINKING_BUDGET_PERCENT * tokens``
        heuristic can pick the effort level that matches (medium=4096 is
        close to 80% of the typical 5-10k thinking windows).
        """
        if not self._enable_thinking:
            return
        try:
            from lean_ai.config import reasoning_effort_to_gemini_budget

            budget = reasoning_effort_to_gemini_budget(self._reasoning_effort)
            config.thinking_config = self._types.ThinkingConfig(
                thinking_budget=budget,
            )
        except Exception:
            logger.warning(
                "ThinkingConfig not supported for %s, skipping",
                self._model,
            )

    @staticmethod
    def _iter_chunk_parts(chunk):
        """Yield (thought, text) tuples from a streaming chunk's parts."""
        candidates = getattr(chunk, "candidates", None)
        if not candidates:
            return
        content = getattr(candidates[0], "content", None)
        if not content:
            return
        for part in getattr(content, "parts", []) or []:
            is_thought = getattr(part, "thought", False)
            text = getattr(part, "text", None) or ""
            if text:
                yield is_thought, text

    async def chat_raw(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        *,
        stream_callback=None,
        thinking_callback=None,
    ) -> tuple[str, LLMMetrics]:
        temp = temperature if temperature is not None else self._temperature
        tokens = max_tokens if max_tokens is not None else self._max_tokens_val
        system_prompt, filtered_messages = _split_system(messages)
        contents = self._build_contents(filtered_messages)

        logger.info(
            "Gemini chat_raw: model=%s messages=%d temp=%.1f max_tokens=%d "
            "streaming=%s thinking=%s",
            self._model,
            len(filtered_messages),
            temp,
            tokens,
            bool(stream_callback or thinking_callback),
            self._enable_thinking,
        )

        types = self._types
        config = types.GenerateContentConfig(
            temperature=temp,
            max_output_tokens=tokens,
        )
        if system_prompt:
            config.system_instruction = system_prompt

        if self._enable_thinking and (stream_callback or thinking_callback):
            self._apply_thinking_config(config, tokens)
            return await self._chat_raw_streaming(
                contents,
                config,
                stream_callback,
                thinking_callback,
            )

        if stream_callback:
            # Streaming path (content only)
            async def _chat():
                chunks: list[str] = []
                final_response = None
                async for chunk in self._client.aio.models.generate_content_stream(
                    model=self._model,
                    contents=contents,
                    config=config,
                ):
                    final_response = chunk
                    text = getattr(chunk, "text", None) or ""
                    if text:
                        chunks.append(text)
                        await stream_callback(text)
                return "".join(chunks), final_response

            text, final_response = await self._retry_with_backoff(_chat, label="chat_raw(stream)")
        else:

            async def _chat():
                return await self._client.aio.models.generate_content(
                    model=self._model,
                    contents=contents,
                    config=config,
                )

            response = await self._retry_with_backoff(_chat, label="chat_raw")
            text = getattr(response, "text", None) or ""
            final_response = response

        metrics = self._extract_metrics(
            final_response,
            stop_reason=self._get_finish_reason(final_response),
        )
        logger.info("Gemini chat_raw response (%d chars): %s", len(text), text[:200])
        return text, metrics

    async def _chat_raw_streaming(
        self,
        contents,
        config,
        stream_callback,
        thinking_callback,
    ) -> tuple[str, LLMMetrics]:
        """Stream chat_raw with thinking support via part inspection."""
        content_parts: list[str] = []
        thinking_parts: list[str] = []

        async def _chat():
            final_response = None
            async for chunk in self._client.aio.models.generate_content_stream(
                model=self._model,
                contents=contents,
                config=config,
            ):
                final_response = chunk
                for is_thought, text in self._iter_chunk_parts(chunk):
                    if is_thought:
                        thinking_parts.append(text)
                        if thinking_callback:
                            await thinking_callback(text)
                    else:
                        content_parts.append(text)
                        if stream_callback:
                            await stream_callback(text)
            return final_response

        final_response = await self._retry_with_backoff(
            _chat,
            label="chat_raw(thinking)",
        )

        text = "".join(content_parts)
        thinking = "\n".join(thinking_parts) or None
        metrics = self._extract_metrics(
            final_response,
            stop_reason=self._get_finish_reason(final_response),
        )
        metrics.thinking = thinking

        logger.info(
            "Gemini chat_raw response (%d chars, thinking=%d chars): %s",
            len(text),
            len(thinking or ""),
            text[:200],
        )
        return text, metrics

    async def chat_structured(
        self,
        messages: list[dict],
        schema: type[BaseModel],
        temperature: float | None = None,
        max_tokens: int | None = None,
        *,
        thinking_callback=None,
    ) -> tuple[BaseModel, LLMMetrics]:
        temp = temperature if temperature is not None else self._temperature
        tokens = max_tokens if max_tokens is not None else self._max_tokens_val
        system_prompt, filtered_messages = _split_system(messages)
        contents = self._build_contents(filtered_messages)

        logger.info(
            "Gemini chat_structured: schema=%s model=%s thinking=%s",
            schema.__name__,
            self._model,
            self._enable_thinking,
        )

        types = self._types
        config = types.GenerateContentConfig(
            temperature=temp,
            max_output_tokens=tokens,
            response_mime_type="application/json",
            response_json_schema=schema,
        )
        if system_prompt:
            config.system_instruction = system_prompt

        use_thinking = self._enable_thinking and thinking_callback
        if use_thinking:
            self._apply_thinking_config(config, tokens)

        last_error = None
        for attempt in range(2):
            if use_thinking:
                raw, response = await self._chat_structured_streaming(
                    contents,
                    config,
                    thinking_callback,
                    label=f"structured({schema.__name__})",
                )
            else:

                async def _chat():
                    return await self._client.aio.models.generate_content(
                        model=self._model,
                        contents=contents,
                        config=config,
                    )

                response = await self._retry_with_backoff(
                    _chat,
                    label=f"structured({schema.__name__})",
                )
                raw = getattr(response, "text", None) or ""

            metrics = self._extract_metrics(
                response,
                stop_reason=self._get_finish_reason(response),
            )

            # Strip markdown code fences if present
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                if lines[-1].strip() == "```":
                    lines = lines[1:-1]
                else:
                    lines = lines[1:]
                cleaned = "\n".join(lines)

            try:
                return schema.model_validate_json(cleaned), metrics
            except ValidationError as exc:
                last_error = exc
                if attempt == 0:
                    logger.warning(
                        "Schema validation failed for %s, retrying: %s",
                        schema.__name__,
                        exc.errors(),
                    )
                    continue
                logger.error(
                    "Schema validation failed after retry for %s. Raw: %s",
                    schema.__name__,
                    raw[:1000],
                )
                raise
        raise last_error  # type: ignore[misc]

    async def _chat_structured_streaming(
        self,
        contents,
        config,
        thinking_callback,
        label: str = "structured(stream)",
    ) -> tuple[str, object]:
        """Stream structured output, forwarding thinking tokens via callback."""
        content_parts: list[str] = []

        async def _chat():
            final_response = None
            async for chunk in self._client.aio.models.generate_content_stream(
                model=self._model,
                contents=contents,
                config=config,
            ):
                final_response = chunk
                for is_thought, text in self._iter_chunk_parts(chunk):
                    if is_thought:
                        await thinking_callback(text)
                    else:
                        content_parts.append(text)
            return final_response

        response = await self._retry_with_backoff(_chat, label=label)
        return "".join(content_parts), response

    async def chat_with_tools_single(
        self,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int | None = None,
        *,
        stream_callback=None,
        thinking_callback=None,
    ) -> tuple[str, list[ToolCallInfo], LLMMetrics]:
        tokens = max_tokens or self._max_tokens_val
        system_prompt, filtered_messages = _split_system(messages)
        contents = self._build_contents(filtered_messages)
        types = self._types

        gemini_tools = _convert_tools(tools, types)

        config = types.GenerateContentConfig(
            temperature=self._temperature,
            max_output_tokens=tokens,
            tools=gemini_tools,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        if system_prompt:
            config.system_instruction = system_prompt

        if stream_callback:

            async def _chat():
                chunks_text: list[str] = []
                final_response = None
                async for chunk in self._client.aio.models.generate_content_stream(
                    model=self._model,
                    contents=contents,
                    config=config,
                ):
                    final_response = chunk
                    text = getattr(chunk, "text", None) or ""
                    if text:
                        chunks_text.append(text)
                        await stream_callback(text)
                return "".join(chunks_text), final_response

            text, response = await self._retry_with_backoff(
                _chat,
                label="chat_with_tools_single(stream)",
            )
        else:

            async def _chat():
                return await self._client.aio.models.generate_content(
                    model=self._model,
                    contents=contents,
                    config=config,
                )

            response = await self._retry_with_backoff(
                _chat,
                label="chat_with_tools_single",
            )
            text = ""

        metrics = self._extract_metrics(
            response,
            stop_reason=self._get_finish_reason(response),
        )

        # Parse response content parts
        content_parts: list[str] = []
        tool_calls: list[ToolCallInfo] = []

        candidates = getattr(response, "candidates", None)
        if candidates and len(candidates) > 0:
            parts = getattr(candidates[0].content, "parts", None) or []
            for part in parts:
                if getattr(part, "text", None):
                    content_parts.append(part.text)
                fc = getattr(part, "function_call", None)
                if fc:
                    tool_calls.append(
                        ToolCallInfo(
                            name=fc.name,
                            arguments=dict(fc.args) if fc.args else {},
                            id=getattr(fc, "id", None),
                        )
                    )

        content = "\n".join(content_parts) if content_parts else text
        return content, tool_calls, metrics

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        *,
        thinking_callback=None,
    ) -> AsyncIterator[str]:
        temp = temperature if temperature is not None else self._temperature
        tokens = max_tokens if max_tokens is not None else self._max_tokens_val
        system_prompt, filtered_messages = _split_system(messages)
        contents = self._build_contents(filtered_messages)

        types = self._types
        config = types.GenerateContentConfig(
            temperature=temp,
            max_output_tokens=tokens,
        )
        if system_prompt:
            config.system_instruction = system_prompt

        async def _stream():
            return self._client.aio.models.generate_content_stream(
                model=self._model,
                contents=contents,
                config=config,
            )

        stream = await self._retry_with_backoff(_stream, label="chat_stream")

        async for chunk in stream:
            text = getattr(chunk, "text", None) or ""
            if text:
                yield text

    async def check_health(self) -> bool:
        try:
            types = self._types
            await self._client.aio.models.generate_content(
                model=self._model,
                contents="ping",
                config=types.GenerateContentConfig(max_output_tokens=1),
            )
            return True
        except Exception:
            logger.exception("Gemini health check failed")
            return False

    # ── Message formatting (Gemini-specific) ──

    def format_tool_result_messages(
        self,
        tool_call: ToolCallInfo,
        content: str,
    ) -> list[dict]:
        """Gemini uses FunctionResponse parts in user messages."""
        return [
            {
                "role": "user",
                "content": content,
                "_gemini_function_response": {
                    "name": tool_call.name,
                    "response": {"result": content},
                    "id": tool_call.id,
                },
            }
        ]

    def format_assistant_tool_message(
        self,
        content: str,
        tool_calls: list[ToolCallInfo],
    ) -> dict:
        """Gemini assistant messages contain FunctionCall parts."""
        return {
            "role": "assistant",
            "content": content,
            "_gemini_function_calls": [
                {"name": tc.name, "args": tc.arguments, "id": tc.id} for tc in tool_calls
            ],
        }
