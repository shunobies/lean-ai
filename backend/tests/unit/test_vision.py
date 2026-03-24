"""Tests for the vision service module."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from lean_ai.llm.vision import (
    VisionResult,
    describe_image,
    describe_images,
    format_image_descriptions,
    is_vision_available,
)


class TestIsVisionAvailable:
    def test_available_when_model_set(self):
        with patch("lean_ai.llm.vision.settings") as mock_settings:
            mock_settings.vision_model = "qwen3-vl:8b"
            assert is_vision_available() is True

    def test_unavailable_when_model_empty(self):
        with patch("lean_ai.llm.vision.settings") as mock_settings:
            mock_settings.vision_model = ""
            assert is_vision_available() is False


class TestDescribeImage:
    @pytest.mark.asyncio
    async def test_returns_error_when_no_model(self):
        with patch("lean_ai.llm.vision.settings") as mock_settings:
            mock_settings.vision_model = ""
            result = await describe_image("base64data")
            assert result.success is False
            assert "No vision model" in result.error

    @pytest.mark.asyncio
    async def test_successful_description(self):
        mock_response = {"message": {"content": "A screenshot of a React app."}}

        with patch("lean_ai.llm.vision.settings") as mock_settings, \
             patch("lean_ai.llm.vision.ollama_lib") as mock_ollama:
            mock_settings.vision_model = "qwen3-vl:8b"
            mock_settings.effective_vision_url = "http://localhost:11434"
            mock_settings.vision_max_tokens = 1024
            mock_settings.vision_timeout = 60.0

            mock_client = AsyncMock()
            mock_client.chat = AsyncMock(return_value=mock_response)
            mock_ollama.AsyncClient.return_value = mock_client

            result = await describe_image("base64data", prompt="What is this?")

            assert result.success is True
            assert result.description == "A screenshot of a React app."
            mock_client.chat.assert_called_once()
            call_kwargs = mock_client.chat.call_args
            assert call_kwargs.kwargs["model"] == "qwen3-vl:8b"

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self):
        with patch("lean_ai.llm.vision.settings") as mock_settings, \
             patch("lean_ai.llm.vision.ollama_lib") as mock_ollama:
            mock_settings.vision_model = "qwen3-vl:8b"
            mock_settings.effective_vision_url = "http://localhost:11434"
            mock_settings.vision_max_tokens = 1024
            mock_settings.vision_timeout = 0.001  # Very short timeout

            mock_client = AsyncMock()
            mock_client.chat = AsyncMock(
                side_effect=asyncio.TimeoutError(),
            )
            mock_ollama.AsyncClient.return_value = mock_client

            # Use wait_for patch to simulate timeout
            with patch("lean_ai.llm.vision.asyncio.wait_for", side_effect=asyncio.TimeoutError()):
                result = await describe_image("base64data")

            assert result.success is False
            assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_connection_error_returns_error(self):
        with patch("lean_ai.llm.vision.settings") as mock_settings, \
             patch("lean_ai.llm.vision.ollama_lib") as mock_ollama:
            mock_settings.vision_model = "qwen3-vl:8b"
            mock_settings.effective_vision_url = "http://localhost:11434"
            mock_settings.vision_max_tokens = 1024
            mock_settings.vision_timeout = 60.0

            mock_client = AsyncMock()
            mock_client.chat = AsyncMock(side_effect=ConnectionError("refused"))
            mock_ollama.AsyncClient.return_value = mock_client

            wait_for_patch = patch(
                "lean_ai.llm.vision.asyncio.wait_for",
                side_effect=ConnectionError("refused"),
            )
            with wait_for_patch:
                result = await describe_image("base64data")

            assert result.success is False
            assert "Cannot reach Ollama" in result.error

    @pytest.mark.asyncio
    async def test_includes_filename_in_prompt(self):
        mock_response = {"message": {"content": "An error dialog."}}

        with patch("lean_ai.llm.vision.settings") as mock_settings, \
             patch("lean_ai.llm.vision.ollama_lib") as mock_ollama:
            mock_settings.vision_model = "qwen3-vl:8b"
            mock_settings.effective_vision_url = "http://localhost:11434"
            mock_settings.vision_max_tokens = 1024
            mock_settings.vision_timeout = 60.0

            mock_client = AsyncMock()
            mock_client.chat = AsyncMock(return_value=mock_response)
            mock_ollama.AsyncClient.return_value = mock_client

            await describe_image(
                "base64data",
                filename="error_screenshot.png",
            )

            messages = mock_client.chat.call_args.kwargs["messages"]
            assert "error_screenshot.png" in messages[1]["content"]


class TestDescribeImages:
    @pytest.mark.asyncio
    async def test_empty_list_returns_empty(self):
        with patch("lean_ai.llm.vision.settings") as mock_settings:
            mock_settings.vision_model = "qwen3-vl:8b"
            result = await describe_images([])
            assert result == []

    @pytest.mark.asyncio
    async def test_disabled_returns_empty(self):
        with patch("lean_ai.llm.vision.settings") as mock_settings:
            mock_settings.vision_model = ""
            result = await describe_images([{"data": "abc"}])
            assert result == []

    @pytest.mark.asyncio
    async def test_multiple_images_described_in_parallel(self):
        mock_response = {"message": {"content": "Description."}}

        with patch("lean_ai.llm.vision.settings") as mock_settings, \
             patch("lean_ai.llm.vision.ollama_lib") as mock_ollama:
            mock_settings.vision_model = "qwen3-vl:8b"
            mock_settings.effective_vision_url = "http://localhost:11434"
            mock_settings.vision_max_tokens = 1024
            mock_settings.vision_timeout = 60.0

            mock_client = AsyncMock()
            mock_client.chat = AsyncMock(return_value=mock_response)
            mock_ollama.AsyncClient.return_value = mock_client

            images = [
                {"data": "img1", "filename": "a.png"},
                {"data": "img2", "filename": "b.png"},
            ]
            results = await describe_images(images, prompt="context")

            assert len(results) == 2
            assert all(r.success for r in results)


class TestFormatImageDescriptions:
    def test_single_success(self):
        results = [VisionResult(success=True, description="A button with text 'Submit'.")]
        text = format_image_descriptions(results)
        assert "[Attached Image Description]" in text
        assert "A button with text 'Submit'." in text
        assert "[End Attached Image Description]" in text

    def test_multiple_success(self):
        results = [
            VisionResult(success=True, description="First image."),
            VisionResult(success=True, description="Second image."),
        ]
        text = format_image_descriptions(results)
        assert "[Image 1 Description]" in text
        assert "[Image 2 Description]" in text
        assert "First image." in text
        assert "Second image." in text

    def test_single_failure(self):
        results = [VisionResult(success=False, error="Vision model timed out")]
        text = format_image_descriptions(results)
        assert "could not be described" in text
        assert "timed out" in text

    def test_mixed_results(self):
        results = [
            VisionResult(success=True, description="An error dialog."),
            VisionResult(success=False, error="Ollama unreachable"),
        ]
        text = format_image_descriptions(results)
        assert "[Image 1 Description]" in text
        assert "An error dialog." in text
        assert "[Image 2: could not be described" in text

    def test_empty_list(self):
        text = format_image_descriptions([])
        assert text == ""
