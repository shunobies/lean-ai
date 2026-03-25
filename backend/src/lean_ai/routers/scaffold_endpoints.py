"""Scaffold project bootstrapping endpoints."""

from fastapi import APIRouter, HTTPException

from lean_ai.routers.models import (
    ScaffoldInfo,
    ScaffoldListResponse,
    ScaffoldRequest,
    ScaffoldResponse,
)

scaffold_router = APIRouter()


@scaffold_router.get("/scaffold/list", response_model=ScaffoldListResponse)
async def list_scaffolds():
    """List all available scaffold templates."""
    from lean_ai.tools.scaffold import get_scaffold_registry

    registry = get_scaffold_registry()
    return ScaffoldListResponse(
        scaffolds=[
            ScaffoldInfo(
                name=t.name,
                display_name=t.display_name,
                description=t.description,
                language=t.language,
                framework=t.framework,
                aliases=t.aliases,
                setup_type=t.setup_type,
            )
            for t in registry.list_all()
        ]
    )


@scaffold_router.post("/scaffold", response_model=ScaffoldResponse)
async def scaffold_project(request: ScaffoldRequest):
    """Set up a new project from a scaffold recipe."""
    from lean_ai.tools.scaffold import get_scaffold_registry, get_scaffold_runner

    registry = get_scaffold_registry()
    template = registry.get(request.scaffold_name)
    if template is None:
        available = [t.name for t in registry.list_all()]
        raise HTTPException(
            status_code=404,
            detail=f"Unknown scaffold '{request.scaffold_name}'. Available: {available}",
        )

    runner = get_scaffold_runner()
    result = await runner.run(template, request.project_name, request.parent_dir)

    if not result.success:
        error_msg = result.error or "Scaffold failed"
        if result.command_output:
            error_msg = f"{error_msg}\n\nCommand output:\n{result.command_output.strip()}"
        raise HTTPException(status_code=500, detail=error_msg)

    return ScaffoldResponse(
        scaffold_name=result.scaffold_name,
        project_dir=result.project_dir,
        files_created=result.files_created,
        command_output=result.command_output,
        message=(
            f"Created {template.display_name} project '{request.project_name}' "
            f"at {result.project_dir}"
        ),
    )
