"""WebSocket workflow streaming and branch operations."""

import logging

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from lean_ai.db import (
    get_db,
    get_session,
    get_session_raw,
    log_commit,
    log_conversation_entry,
    update_session,
)
from lean_ai.routers.context_helpers import load_planning_context
from lean_ai.routers.dependencies import (
    expert_llm_client,
    llm_client,
    refiner,
    request_llm_client,
)
from lean_ai.tools.git_ops import (
    git_add_and_commit,
    git_checkout,
    git_create_branch,
    git_current_branch,
    git_current_sha,
    git_default_branch,
    git_delete_branch,
    git_is_repo,
    git_merge_branch,
    git_stash_pop,
    git_stash_push,
)
from lean_ai.workflow.pipeline import run_workflow
from lean_ai.workflow.ws_dispatcher import WorkflowCancelledError, WSMessageDispatcher

logger = logging.getLogger(__name__)

workflow_router = APIRouter()


@workflow_router.websocket("/sessions/{session_id}/stream")
async def session_stream(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for real-time workflow streaming.

    Client messages:
      - {"type": "user_message", "content": "...", "repo_root": "..."}
        Start the agentic workflow with a task.
      - {"type": "cancel"} — stop the running workflow
      - {"type": "approve_tool", ...} — approve a pending shell command
      - {"type": "ping"} — keepalive

    During workflow execution, additional ``user_message`` messages are
    treated as mid-workflow interrupts — the LLM reads them before its
    next turn.

    Server messages:
      - {"type": "stage_change", "stage": "..."}
      - {"type": "tool_progress", "tool": "...", "status": "...", ...}
      - {"type": "diff", "file": "...", "diff": "..."}
      - {"type": "test_result", ...}
      - {"type": "complete", "summary": "...", ...}
      - {"type": "cancelled"} — workflow was stopped by user
      - {"type": "error", "message": "...", "recoverable": bool}
      - {"type": "pong"}
    """
    await websocket.accept()
    logger.info("WebSocket connected for session %s", session_id)

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "user_message":
                content = data.get("content", "")
                repo_root = data.get("repo_root", "")
                attachments = data.get("attachments", [])

                if not repo_root:
                    await websocket.send_json({
                        "type": "error",
                        "message": "repo_root is required",
                        "recoverable": True,
                    })
                    continue

                try:
                    db = await get_db(repo_root)
                    try:
                        session = await get_session(db, session_id)
                        if session:
                            # --- Git branch setup ---
                            branch_name = ""
                            base_branch = ""
                            stashed = False
                            is_git = await git_is_repo(repo_root)

                            if is_git:
                                # Always branch from the default branch (master/main)
                                # so leftover lean-ai/* branches never contaminate new work
                                base_branch = await git_default_branch(repo_root)
                                branch_name = f"lean-ai/{session_id}"

                                # Remember original branch for recovery
                                orig_branch_result = await git_current_branch(repo_root)
                                original_branch = (
                                    orig_branch_result.output.strip()
                                    if orig_branch_result.success else ""
                                )

                                # Stash uncommitted changes before switching
                                stashed = await git_stash_push(repo_root)

                                # Switch to the default branch first, then create the work branch
                                await git_checkout(base_branch, repo_root)
                                create_result = await git_create_branch(branch_name, repo_root)
                                if create_result.success:
                                    await update_session(
                                        db, session_id,
                                        branch_name=branch_name,
                                        base_branch=base_branch,
                                        stashed=stashed,
                                    )
                                    await websocket.send_json({
                                        "type": "branch_created",
                                        "branch_name": branch_name,
                                        "base_branch": base_branch,
                                    })
                                else:
                                    logger.warning(
                                        "Failed to create branch %s: %s",
                                        branch_name, create_result.error,
                                    )
                                    branch_name = ""
                                    # Recover stashed changes
                                    if stashed:
                                        if original_branch:
                                            await git_checkout(
                                                original_branch, repo_root,
                                            )
                                        pop_result = await git_stash_pop(
                                            repo_root,
                                        )
                                        if pop_result.success:
                                            stashed = False
                                            logger.info(
                                                "Recovered stashed changes "
                                                "after branch failure",
                                            )
                                        else:
                                            logger.error(
                                                "Failed to recover stash "
                                                "after branch failure: %s "
                                                "— run 'git stash pop' "
                                                "manually",
                                                pop_result.error,
                                            )

                            # --- Load context and run workflow ---
                            context = load_planning_context(repo_root)

                            # --- Describe attached images via vision model ---
                            if attachments:
                                from lean_ai.llm.vision import (
                                    describe_images,
                                    format_image_descriptions,
                                    is_vision_available,
                                )

                                if is_vision_available():
                                    image_attachments = [
                                        {
                                            "data": a["data"],
                                            "filename": a.get("filename"),
                                        }
                                        for a in attachments
                                        if (a.get("mime_type") or "")
                                        .startswith("image/")
                                    ]
                                    if image_attachments:
                                        try:
                                            await websocket.send_json({
                                                "type": "stage_status",
                                                "stage": "VISION",
                                                "status": "running",
                                                "summary": (
                                                    f"Describing "
                                                    f"{len(image_attachments)} "
                                                    f"image(s)..."
                                                ),
                                            })
                                            results = await describe_images(
                                                image_attachments,
                                                prompt=content,
                                            )
                                            desc = format_image_descriptions(
                                                results,
                                            )
                                            if desc:
                                                content = (
                                                    f"{content}\n\n{desc}"
                                                )
                                                await websocket.send_json({
                                                    "type": "vision_description",
                                                    "descriptions": desc,
                                                })
                                            await websocket.send_json({
                                                "type": "stage_status",
                                                "stage": "VISION",
                                                "status": "done",
                                                "summary": (
                                                    f"Described "
                                                    f"{len(image_attachments)}"
                                                    f" image(s)"
                                                ),
                                            })
                                        except Exception as e:
                                            logger.warning(
                                                "Vision description failed "
                                                "(non-fatal): %s",
                                                e,
                                            )

                            # Conversation logger — writes chain-of-thought to DB
                            async def _log_conversation(
                                role: str,
                                log_content: str,
                                tool_name: str | None = None,
                                tool_args: str | None = None,
                                _db=db,
                            ) -> None:
                                try:
                                    await log_conversation_entry(
                                        _db, session_id, role, log_content,
                                        tool_name=tool_name, tool_args=tool_args,
                                    )
                                except Exception:
                                    logger.debug("Failed to log conversation entry", exc_info=True)

                            # Detect /fix or /request prefix → skip planning
                            mode = "plan"
                            task = content
                            if content.startswith("/fix "):
                                mode = "fix"
                                task = content[5:]  # strip "/fix " prefix
                            elif content.startswith("/request "):
                                mode = "request"
                                task = content[9:]  # strip "/request " prefix

                            # Refine task with local LLM before cloud execution
                            if refiner is not None and mode == "plan":
                                try:
                                    await websocket.send_json({
                                        "type": "refiner_status",
                                        "status": "running",
                                        "summary": "Refining task with local LLM...",
                                    })
                                    refiner_result = await refiner.refine_task(
                                        task=task,
                                        repo_root=repo_root,
                                        context=context,
                                    )
                                    if refiner_result.was_refined:
                                        task = refiner_result.refined
                                        await websocket.send_json({
                                            "type": "refiner_status",
                                            "status": "done",
                                            "summary": (
                                                f"Task refined "
                                                f"({refiner_result.duration_ms:.0f}ms)"
                                            ),
                                            "privacy_redactions": len(
                                                refiner_result.privacy_redactions
                                            ),
                                            "reference_injected": bool(
                                                refiner_result.reference_context
                                            ),
                                        })
                                    else:
                                        await websocket.send_json({
                                            "type": "refiner_status",
                                            "status": "skipped",
                                            "summary": "Task already well-structured",
                                        })
                                except Exception as e:
                                    logger.warning(
                                        "Refiner failed (non-fatal): %s", e,
                                    )
                                    await websocket.send_json({
                                        "type": "refiner_status",
                                        "status": "error",
                                        "summary": f"Refinement skipped: {e}",
                                    })

                            # Start the dispatcher so cancel / mid-workflow
                            # messages can be received during execution.
                            dispatcher = WSMessageDispatcher(websocket)
                            await dispatcher.start()
                            try:
                                commit_msg = await run_workflow(
                                    task=task,
                                    repo_root=repo_root,
                                    ws=websocket,
                                    llm_client=llm_client,
                                    context=context,
                                    branch_name=branch_name,
                                    base_branch=base_branch,
                                    conversation_logger=_log_conversation,
                                    mode=mode,
                                    session_id=session_id,
                                    refiner=refiner,
                                    expert_llm_client=expert_llm_client,
                                    request_llm_client=request_llm_client,
                                    dispatcher=dispatcher,
                                )
                            except WorkflowCancelledError:
                                logger.info(
                                    "Workflow cancelled by user for session %s",
                                    session_id,
                                )
                                try:
                                    await websocket.send_json({"type": "cancelled"})
                                except Exception:
                                    pass
                                await update_session(
                                    db, session_id, status="cancelled",
                                )
                                try:
                                    from lean_ai.workflow.hooks import (
                                        fire_workflow_event,
                                    )
                                    # Enrich the cancellation payload with a tail
                                    # snapshot from conversation_logs so training
                                    # consumers can reconstruct WHERE the user
                                    # gave up — the final N messages carry the
                                    # negative signal.
                                    tail_messages: list[dict] = []
                                    try:
                                        cursor = await db.execute(
                                            "SELECT role, content, tool_name, "
                                            "created_at FROM conversation_logs "
                                            "WHERE session_id = ? "
                                            "ORDER BY id DESC LIMIT 5",
                                            (session_id,),
                                        )
                                        rows = await cursor.fetchall()
                                        tail_messages = [
                                            {
                                                "role": r[0],
                                                "content": (r[1] or "")[:800],
                                                "tool_name": r[2],
                                                "created_at": r[3],
                                            }
                                            for r in reversed(rows)
                                        ]
                                    except Exception:
                                        logger.debug(
                                            "cancellation tail read failed",
                                            exc_info=True,
                                        )
                                    fire_workflow_event(
                                        repo_root=repo_root,
                                        session_id=session_id,
                                        event_type="cancellation",
                                        payload={
                                            "task": task,
                                            "mode": mode,
                                            "tail_messages": tail_messages,
                                        },
                                    )
                                except Exception:
                                    logger.debug(
                                        "cancellation capture failed (non-fatal)",
                                        exc_info=True,
                                    )
                                continue
                            finally:
                                await dispatcher.stop()

                            # --- Auto-commit agent changes ---
                            if branch_name:
                                commit_result = await git_add_and_commit(
                                    commit_msg, repo_root,
                                )
                                if commit_result.success:
                                    logger.info(
                                        "Auto-committed agent changes on %s",
                                        branch_name,
                                    )
                                    # Log the commit SHA for session correlation
                                    sha_result = await git_current_sha(repo_root)
                                    if sha_result.success:
                                        sha = sha_result.output.strip()
                                        await log_commit(
                                            db, session_id, sha, commit_msg,
                                        )

                            await update_session(db, session_id, status="completed")
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "message": f"Session {session_id} not found",
                                "recoverable": False,
                            })
                    finally:
                        await db.close()
                except WebSocketDisconnect:
                    raise
                except Exception as e:
                    logger.exception("Workflow error for session %s", session_id)
                    try:
                        await websocket.send_json({
                            "type": "error",
                            "message": str(e),
                            "recoverable": True,
                        })
                    except Exception:
                        pass

            elif msg_type == "resume":
                repo_root = data.get("repo_root", "")
                if not repo_root:
                    await websocket.send_json({
                        "type": "error",
                        "message": "repo_root is required",
                        "recoverable": True,
                    })
                    continue

                try:
                    db = await get_db(repo_root)
                    try:
                        session = await get_session_raw(db, session_id)
                        if not session:
                            await websocket.send_json({
                                "type": "error",
                                "message": f"Session {session_id} not found",
                                "recoverable": False,
                            })
                            continue

                        # Branch already checked out by POST /resume endpoint
                        branch_name = session.get("branch_name", "")

                        # Load project context
                        context = load_planning_context(repo_root)

                        # Conversation logger
                        async def _log_conversation_resume(
                            role: str,
                            log_content: str,
                            tool_name: str | None = None,
                            tool_args: str | None = None,
                            _db=db,
                        ) -> None:
                            try:
                                await log_conversation_entry(
                                    _db, session_id, role, log_content,
                                    tool_name=tool_name, tool_args=tool_args,
                                )
                            except Exception:
                                logger.debug("Failed to log conversation entry", exc_info=True)

                        # Build resume task from original task + journal + scratchpad
                        from lean_ai.tools.journal import read_journal
                        from lean_ai.tools.scratchpad import read_scratchpad
                        original_task = session.get("task", "")
                        pad_content = read_scratchpad(repo_root, session_id)
                        journal_content = read_journal(repo_root, session_id)

                        resume_parts = [f"ORIGINAL TASK:\n{original_task}"]
                        if journal_content:
                            resume_parts.append(
                                f"SESSION JOURNAL (permanent findings):\n{journal_content}"
                            )
                        if pad_content:
                            resume_parts.append(
                                f"SESSION SCRATCHPAD (resume from here):\n{pad_content}"
                            )
                        if journal_content or pad_content:
                            resume_parts.append(
                                "Continue where you left off. Do NOT redo completed work."
                            )
                            resume_task = "\n\n".join(resume_parts)
                        else:
                            resume_task = original_task

                        # Resume in fix mode (direct tool calling, no re-planning)
                        dispatcher = WSMessageDispatcher(websocket)
                        await dispatcher.start()
                        try:
                            commit_msg = await run_workflow(
                                task=resume_task,
                                repo_root=repo_root,
                                ws=websocket,
                                llm_client=llm_client,
                                context=context,
                                branch_name=branch_name,
                                conversation_logger=_log_conversation_resume,
                                mode="fix",
                                session_id=session_id,
                                expert_llm_client=expert_llm_client,
                                dispatcher=dispatcher,
                            )
                        except WorkflowCancelledError:
                            logger.info(
                                "Resume cancelled by user for session %s",
                                session_id,
                            )
                            try:
                                await websocket.send_json({"type": "cancelled"})
                            except Exception:
                                pass
                            await update_session(
                                db, session_id, status="cancelled",
                            )
                            continue
                        finally:
                            await dispatcher.stop()

                        # Auto-commit
                        if branch_name:
                            commit_result = await git_add_and_commit(
                                commit_msg, repo_root,
                            )
                            if commit_result.success:
                                sha_result = await git_current_sha(repo_root)
                                if sha_result.success:
                                    sha = sha_result.output.strip()
                                    await log_commit(
                                        db, session_id, sha, commit_msg,
                                    )

                        await update_session(db, session_id, status="completed")
                    finally:
                        await db.close()
                except WebSocketDisconnect:
                    raise
                except Exception as e:
                    logger.exception("Resume error for session %s", session_id)
                    try:
                        await websocket.send_json({
                            "type": "error",
                            "message": str(e),
                            "recoverable": True,
                        })
                    except Exception:
                        pass

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            # approve_tool / cancel handled by WSMessageDispatcher during workflow

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for session %s", session_id)
    except WorkflowCancelledError:
        logger.info("WebSocket workflow cancelled for session %s", session_id)
    except Exception:
        logger.exception("WebSocket error for session %s", session_id)


@workflow_router.post("/sessions/{session_id}/merge")
async def merge_session(session_id: str, repo_root: str):
    """Merge the agent's branch into the base branch and clean up."""
    db = await get_db(repo_root)
    try:
        session = await get_session_raw(db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        status = session.get("status")
        if status not in ("completed",):
            raise HTTPException(
                status_code=400,
                detail=f"Session in status '{status}' cannot be merged (must be 'completed')",
            )

        branch_name = session.get("branch_name")
        base_branch = session.get("base_branch")
        stashed = bool(session.get("stashed", 0))

        if not branch_name or not base_branch:
            raise HTTPException(status_code=400, detail="Session has no branch to merge")

        # Checkout base branch
        co_result = await git_checkout(base_branch, repo_root)
        if not co_result.success:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to checkout {base_branch}: {co_result.error}",
            )

        # Merge the agent branch
        merge_result = await git_merge_branch(branch_name, repo_root)
        if not merge_result.success:
            raise HTTPException(status_code=500, detail=f"Merge failed: {merge_result.error}")

        # Delete the branch
        await git_delete_branch(branch_name, repo_root)

        # Pop stash if we stashed before
        if stashed:
            await git_stash_pop(repo_root)

        # Get merge commit SHA
        sha_result = await git_current_sha(repo_root)
        merge_sha = sha_result.output.strip() if sha_result.success else ""

        # Log the merge commit for session correlation
        if merge_sha:
            await log_commit(db, session_id, merge_sha, f"merge: {branch_name}")

        # Clean up per-session scratchpad, journal, and observations
        from lean_ai.tools.journal import delete_journal
        from lean_ai.tools.observations import delete_observations
        from lean_ai.tools.scratchpad import delete_scratchpad
        delete_scratchpad(repo_root, session_id)
        delete_journal(repo_root, session_id)
        delete_observations(repo_root, session_id)

        await update_session(
            db, session_id, status="merged", merge_commit_sha=merge_sha,
        )

        return {
            "status": "merged",
            "merge_sha": merge_sha,
            "branch_deleted": True,
        }
    finally:
        await db.close()


@workflow_router.post("/sessions/{session_id}/abandon")
async def abandon_session(session_id: str, repo_root: str):
    """Abandon the agent's branch — checkout base and delete the branch."""
    db = await get_db(repo_root)
    try:
        session = await get_session_raw(db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        status = session.get("status")
        if status in ("merged", "abandoned"):
            raise HTTPException(
                status_code=400,
                detail=f"Session already closed (status: '{status}')",
            )

        branch_name = session.get("branch_name")
        base_branch = session.get("base_branch")
        stashed = bool(session.get("stashed", 0))

        if not branch_name or not base_branch:
            raise HTTPException(status_code=400, detail="Session has no branch to abandon")

        # Checkout base branch
        co_result = await git_checkout(base_branch, repo_root)
        if not co_result.success:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to checkout {base_branch}: {co_result.error}",
            )

        # Force-delete the unmerged branch
        await git_delete_branch(branch_name, repo_root, force=True)

        # Pop stash if we stashed before
        if stashed:
            await git_stash_pop(repo_root)

        # Clean up per-session scratchpad, journal, and observations
        from lean_ai.tools.journal import delete_journal
        from lean_ai.tools.observations import delete_observations
        from lean_ai.tools.scratchpad import delete_scratchpad
        delete_scratchpad(repo_root, session_id)
        delete_journal(repo_root, session_id)
        delete_observations(repo_root, session_id)

        await update_session(db, session_id, status="abandoned")

        return {"status": "abandoned", "branch_deleted": True}
    finally:
        await db.close()
