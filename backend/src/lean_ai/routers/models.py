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


class GenerateProjectContextRequest(BaseModel):
    repo_root: str
    skip_if_exists: bool = True


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


class IndexKnowledgeRequest(BaseModel):
    repo_root: str
    force_reindex: bool = False


class IndexKnowledgeResponse(BaseModel):
    status: str
    doc_count: int = 0
    chunk_count: int = 0


class WorkspaceContext(BaseModel):
    workspace_name: str | None = None
    workspace_root: str | None = None
    active_file: str | None = None
    active_language: str | None = None
    active_selection: str | None = None


class ChatRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = []
    workspace: WorkspaceContext | None = None


class ChatResponse(BaseModel):
    reply: str
    tokens_per_second: float | None = None
    eval_count: int | None = None


class InlinePredictRequest(BaseModel):
    file_path: str
    language: str
    prefix: str
    suffix: str
    cursor_line: int
    cursor_character: int


class ResumeSessionRequest(BaseModel):
    repo_root: str


class GenerateFrameworkGuideRequest(BaseModel):
    repo_root: str
    skip_if_exists: bool = False


class GenerateFrameworkGuideResponse(BaseModel):
    path: str
    chars: int
    skipped: bool = False


class GenerateStyleGuideRequest(BaseModel):
    repo_root: str
    skip_if_exists: bool = False


class GenerateStyleGuideResponse(BaseModel):
    path: str
    chars: int
    skipped: bool = False
