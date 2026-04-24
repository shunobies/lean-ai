"""Pydantic request/response models for all API endpoints."""

from pydantic import BaseModel


class CreateSessionRequest(BaseModel):
    repo_root: str
    task: str = ""


class CreateSessionResponse(BaseModel):
    session_id: str
    status: str


class InitWorkspaceRequest(BaseModel):
    repo_root: str
    force_reindex: bool = False


class InitWorkspaceResponse(BaseModel):
    index_status: str
    index_file_count: int | None = None
    index_chunk_count: int | None = None
    commands_detected: dict[str, str] | None = None
    num_parallel: int = 1
    reference_status: str | None = None
    reference_doc_count: int | None = None
    reference_chunk_count: int | None = None
    reference_skipped_extensions: list[str] | None = None
    # embedding_status now includes ``up_to_date`` and ``partial`` in
    # addition to ``skipped`` / ``success`` / ``failed`` so the extension
    # can tell the difference between "nothing to do" and "silently broken".
    embedding_status: str = "skipped"
    embedding_code_count: int = 0
    embedding_reference_count: int = 0
    embedding_code_unchanged: int = 0
    embedding_reference_unchanged: int = 0
    embedding_failed_batches: int = 0
    embedding_total_batches: int = 0
    embedding_message: str = ""


class GenerateProjectContextRequest(BaseModel):
    repo_root: str
    skip_if_exists: bool = True
    stream: bool = False


class GenerateProjectContextResponse(BaseModel):
    path: str
    chars: int
    skipped: bool = False


class ScaffoldRequest(BaseModel):
    scaffold_name: str
    project_name: str
    parent_dir: str


class ScaffoldResponse(BaseModel):
    scaffold_name: str
    project_dir: str
    files_created: list[str]
    command_output: str
    message: str


class ScaffoldInfo(BaseModel):
    name: str
    display_name: str
    description: str
    language: str
    framework: str | None
    aliases: list[str]
    setup_type: str


class ScaffoldListResponse(BaseModel):
    scaffolds: list[ScaffoldInfo]


class IndexReferenceRequest(BaseModel):
    repo_root: str
    force_reindex: bool = False


class IndexReferenceResponse(BaseModel):
    status: str
    doc_count: int = 0
    chunk_count: int = 0
    embedding_count: int = 0


class WorkspaceContext(BaseModel):
    workspace_name: str | None = None
    workspace_root: str | None = None
    active_file: str | None = None
    active_language: str | None = None
    active_selection: str | None = None


class Attachment(BaseModel):
    """A file attachment (typically an image) sent with a message."""

    data: str  # Base64-encoded content
    filename: str | None = None
    mime_type: str | None = None  # e.g. "image/png", "image/jpeg"


class ChatRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = []
    workspace: WorkspaceContext | None = None
    attachments: list[Attachment] = []
    user_name: str | None = None
    skip_web_search: bool = False
    # Optional per-request override of the tool-turn budget. Used by the
    # ``/mock-interview`` extension command where 10 scored rounds need
    # more than the default ``_CHAT_MAX_TURNS``. Backend caps at
    # ``_CHAT_EXTENDED_TURNS_MAX`` so a caller can't request arbitrary
    # compute.
    extended_turns: int | None = None


class ChatResponse(BaseModel):
    reply: str
    tokens_per_second: float | None = None
    eval_count: int | None = None
    refined: bool = False
    privacy_redactions: int = 0
    image_descriptions: str | None = None


class InlinePredictRequest(BaseModel):
    file_path: str
    language: str
    prefix: str
    suffix: str
    cursor_line: int
    cursor_character: int


class ResumeSessionRequest(BaseModel):
    repo_root: str


class GenerateStyleGuideRequest(BaseModel):
    repo_root: str
    skip_if_exists: bool = False
    stream: bool = False


class GenerateStyleGuideResponse(BaseModel):
    path: str
    chars: int
    skipped: bool = False


class ModelInfo(BaseModel):
    provider: str       # "ollama", "openai", "anthropic"
    model: str          # e.g. "qwen3-coder:30b", "gpt-4o"
    display_name: str   # Human-readable: "Ollama: qwen3-coder:30b"
    is_default: bool    # True for the server's primary configured model


class ModelsResponse(BaseModel):
    models: list[ModelInfo]
    default_provider: str
    default_model: str


# ── Voice models ──


class STTStartRequest(BaseModel):
    auto_stop: bool = False


class STTStopResponse(BaseModel):
    text: str
    language: str | None = None
    duration_seconds: float = 0.0


class TTSRequest(BaseModel):
    text: str
    voice: str = ""
    speed: float = 0.0


class TTSResponse(BaseModel):
    audio_base64: str
    duration_seconds: float = 0.0


class VoiceConfigRequest(BaseModel):
    voice: str = ""
    speed: float = 0.0


class VoiceInfo(BaseModel):
    id: str
    name: str
    language: str
    gender: str | None = None


class VoiceListResponse(BaseModel):
    voices: list[VoiceInfo]


class EnsureModelsResponse(BaseModel):
    downloaded: bool = False
    already_cached: bool = False
    size_mb: float = 0.0


class VoiceStatusResponse(BaseModel):
    stt_available: bool = False
    tts_available: bool = False
    wake_word_available: bool = False
    setup: dict = {}
