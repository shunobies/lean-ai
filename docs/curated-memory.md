# Curated Memory

Lean AI remembers things it learns while working on your project, so it
can make better plans next time. This page explains what gets saved,
where it lives, who can see it, and how you stay in control.

> **TL;DR** — Every time Lean AI finishes a task, it writes a few short
> "memories" (like sticky notes) about what it learned. You decide
> which ones it keeps. Confirmed memories are read back into future
> planning so the tool stops repeating the same mistakes.

## What is a memory?

A memory is a single sentence or two that captures something useful
for the future. Some examples:

- *"When `pytest` fails with `ModuleNotFoundError`, check that
  `PYTHONPATH` includes the `src/` folder."*
- *"Plans that skip writing unit tests first usually get rejected in
  this project."*
- *"The `User` model uses `email` as the unique key, not `username`."*

Each memory has:

| Field | Meaning |
|---|---|
| **content** | The short lesson (1–3 sentences). |
| **category** | What kind of lesson it is — see [Categories](#categories). |
| **tags** | Search keywords, like `pytest`, `auth`, `refactor`. |
| **source phase** | Where the memory came from — see [How memories are created](#how-memories-are-created). |
| **curation status** | Whether you've approved it — see [Curation status](#curation-status). |
| **confidence** | A number between 0 and 1 showing how sure we are. |
| **seen count** | How many times Lean AI has extracted the same lesson. |

Memories are stored in your project's `.lean_ai/lean_ai.db` SQLite file
(a single local file — nothing leaves your computer unless you choose
to export it).

## Categories

A memory is always tagged with exactly one category so retrieval can
ask for "only show me the gotchas" or "only show me fix patterns."

| Category | When to use it |
|---|---|
| `architecture` | Big-picture structural truths (e.g. "this service uses event sourcing"). |
| `build` | Build system quirks (e.g. "the Docker build needs `--platform=linux/amd64`"). |
| `testing` | Test framework setup, fixtures, or test patterns in use. |
| `pattern` | A code pattern or design pattern the project prefers. |
| `gotcha` | Something that failed unexpectedly and should be avoided. |
| `convention` | A naming, formatting, or import-style rule the project follows. |
| `discovery` | A fact you looked up and verified (e.g. "library X version 2.1 breaks on Python 3.13"). |
| `rejection` | A lesson learned from a plan you rejected. |
| `fix_pattern` | A `(failing command, root cause, fix)` triple that worked. |
| `success_pattern` | A workflow or approach that was accepted without revision. |

> **Tip** — You don't pick the category yourself. The LLM that extracts
> the memory picks one. You can re-categorize any memory later in the
> Memories panel.

## Curation status

Every memory has a status that controls whether the planner is allowed
to read it. You (or the auto-promotion rules) change the status over
time.

| Status | Meaning | Visible to planner? |
|---|---|---|
| `auto` | Lean AI just created this memory. Waiting for you to confirm. | ❌ No |
| `user_confirmed` | You clicked "Save" or the equivalent in the Memories panel. | ✅ Yes |
| `high_confidence_auto` | Lean AI has seen this same lesson 3+ times across sessions, so it auto-promoted. | ✅ Yes |
| `user_rejected` | You clicked "Dismiss." Never injected again. | ❌ No |
| `superseded` | Either explicitly archived, or invalidated when you bulk-deprecated a model's output. | ❌ No |

> **Why the default excludes `auto`?** Raw extractions can be noisy or
> wrong. Filtering them out until a human (or enough repetition) signs
> off keeps bad lessons from poisoning future plans. You can change
> this with [`LEAN_AI_MEMORY_RETRIEVAL_STATUSES`](configuration.md#curated-memory).

## How memories are created

Lean AI watches for three moments that teach it something:

### 1. A plan you rejected and then approved

When you type feedback into the chat asking for a plan revision, then
approve the revised plan, Lean AI knows the *first* plan was wrong in
a specific way. It extracts a `rejection` memory so the next time a
similar task comes up, the planner sees: *"Last time you asked for
something like this, I proposed X and you rejected it because Y. Try
Z instead."*

### 2. A validation failure that got fixed

When tests or lint fail and the fix loop makes them pass, Lean AI
extracts a `fix_pattern` memory. This includes the error signature,
the diagnosis, and the kind of fix that worked.

### 3. A completed session (general lessons)

After every successful session, Lean AI reads the whole session and
extracts up to 5 general memories of any category.

> **Note** — If you cancel a session, no memories are extracted from
> it. The archive still records that a cancellation happened, but no
> lesson is saved.

## How memories are used

Three planning phases read memories. Each one has its own small
context budget (2% of the model's context window by default).

| Phase | What it reads | What it's looking for |
|---|---|---|
| **Phase 1 — Scope** | All categories | General context on past sessions. |
| **Phase 3 — Design** | `gotcha`, `convention`, `rejection` | Design mistakes to avoid. |
| **Fix loop** | `fix_pattern`, `gotcha` | Past fixes for similar failing commands. |

Phase 3 and fix-loop injection are both on by default. Turn them off
with `LEAN_AI_ENABLE_PHASE3_MEMORY=false` or
`LEAN_AI_ENABLE_FIX_LOOP_MEMORY=false`.

## The Memories panel

The easiest way to review memories is the Memories panel in the
VSCode extension.

**How to open it:**

1. Click the Lean AI icon in the sidebar.
2. In the "Sessions" view, click the lightbulb icon in the title bar.

The panel has three tabs:

- **Pending Review** — new memories with status `auto`. Click
  **Confirm** to promote, **Reject** to archive, or **Delete** to
  remove completely.
- **Confirmed** — memories the planner currently uses.
- **Archive** — rejected or superseded memories (kept for history).

You can also add a memory yourself. Click **+ Add a memory manually**,
pick a category, write the content, and hit **Save memory**. Manual
memories go straight to `user_confirmed`.

## Inline suggestions during workflow

After a plan rejection or a fix-loop success, Lean AI surfaces the
newly-extracted memory as an inline chip in the chat stream:

```
💡 Remember: "When pytest fails with ModuleNotFoundError, check
   that src/ is on PYTHONPATH."
   [Save]  [Dismiss]
```

Click **Save** and the memory becomes `user_confirmed` without you
having to open the Memories panel. Click **Dismiss** and it's marked
`user_rejected`.

> **Tip** — You can close the chat panel without clicking; the memory
> stays in "Pending Review" until you handle it.

## Auto-promotion (when the system confirms for you)

If Lean AI keeps extracting the exact same lesson across multiple
sessions, that's a strong signal. Once the same memory is seen 3
times (configurable via `LEAN_AI_MEMORY_AUTOPROMOTE_THRESHOLD`), its
status is automatically promoted from `auto` to
`high_confidence_auto` — making it visible to the planner without
explicit user action.

**Rules of the road:**

- Promotion only applies to `auto` memories. Already-confirmed or
  rejected memories are never auto-changed.
- The "sameness" check is forgiving: different capitalization, small
  punctuation changes, and minor wording differences still count as
  the same lesson.
- Content you previously rejected will **not** be re-introduced, even
  if the LLM extracts it again.

## Memory lifespan

By default, memories never expire. You can set a time-to-live per
memory by calling `set_expiry_from_ttl`:

```python
from lean_ai.memory.db import set_expiry_from_ttl, get_db

db = await get_db(repo_root)
await set_expiry_from_ttl(db, memory_id="abc123", ttl_days=90)
```

Expired memories are hidden from retrieval but kept in the database
so you can undo the expiry later.

> **When should memories expire?** If a memory describes something
> that changes over time — a library version, a deprecated command, a
> project that's being rewritten — give it a TTL. Evergreen lessons
> (naming conventions, test patterns) should stay permanent.

## Bulk actions

Sometimes a single model goes bad (for example, you upgrade to a new
version and its extractions get worse). Instead of rejecting each
memory one by one, you can invalidate all memories from that model in
one call:

```python
from lean_ai.training.maintenance import bulk_invalidate_by_model, get_db

db = await get_db(repo_root)
count = await bulk_invalidate_by_model(db, model_name="bad-model-v1")
print(f"Invalidated {count} memories")
```

All affected memories are moved to `superseded` status.

## Privacy and what leaves your computer

**Nothing leaves your computer automatically.** Memories live in a
local SQLite file (`.lean_ai/lean_ai.db`) alongside your project.

If you explicitly opt in to cross-workspace sharing by setting
`LEAN_AI_EXPORT_API_KEY`, a coordinator (like `lean-ai-serve`) can
pull your memories through the [`/api/export/memories`](api-reference.md#export-memories)
endpoint. Before they leave your machine, they go through a second
anonymization pass that:

1. Replaces absolute paths with `<WORKSPACE_PATH>`.
2. Replaces project-specific file names with `<WORKSPACE_FILE>`.
3. Replaces project-specific class/function names flanked by code
   framing (backticks, "the X class") with `<WORKSPACE_SYMBOL>`.
4. **Drops** any memory where more than 40% of its content had to be
   redacted — those are too project-specific to be useful elsewhere.

See [Training Pipeline — Anonymization of exported data](training.md#anonymization-of-exported-data)
for the full story.

## Where everything is stored

| Thing | Location |
|---|---|
| Memory rows (SQLite) | `.lean_ai/lean_ai.db` (table: `session_memories`) |
| Full-text search index (Whoosh) | `.lean_ai/memory_index/` |
| Observations from Phase 2 planning | `.lean_ai/observations/{session_id}.json` (cleaned up at session close) |
| Scratchpad (within-session volatile) | `.lean_ai/scratchpad/{session_id}.md` (cleaned up at session close) |
| Session journal (within-session append-only) | `.lean_ai/journals/{session_id}.md` (cleaned up at session close) |

You can delete `.lean_ai/lean_ai.db` at any time — Lean AI will
recreate it on the next run, empty.

## Troubleshooting

**Q: I confirmed a memory but the planner isn't using it.**

The planner only reads memories that match what you're asking for.
Retrieval is keyword-based (Whoosh BM25F). If your new task wording
is different from the task that produced the memory, you might not
see it injected. Try adding related tags or rephrasing.

**Q: A wrong memory keeps showing up.**

Open the Memories panel and click **Reject** on it. After that, even
if the LLM tries to extract the same lesson again, it will be skipped
(`supersede_user_rejected`).

**Q: The Memories panel is empty but I've run many sessions.**

The panel defaults to the "Pending Review" tab. Check the "Confirmed"
and "Archive" tabs. If all three are empty, cross-session memory may
be disabled (`LEAN_AI_ENABLE_SESSION_MEMORY=false`).

**Q: How do I opt out of memory extraction entirely?**

Set `LEAN_AI_ENABLE_SESSION_MEMORY=false` in `backend/.env` or
`config.yaml`. Existing memories stay in the database but no new ones
are created and retrieval is disabled.

## Related pages

- [Training Pipeline](training.md) — the separate, permanent archive
  used for future fine-tuning.
- [Configuration Reference — Curated Memory](configuration.md#curated-memory)
  — all the environment variables.
- [API Reference — Memories](api-reference.md#memories) — the REST
  endpoints the extension uses.
- [Workflow Flow](workflow-flow.md#memory-lifecycle) — a diagram of
  how a memory moves from extraction to retrieval.
