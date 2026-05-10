6257198 Non-mutating tools allowed in all implementation steps.
c51af13 Added implicit tool non mutating list to all implementation steps.
8907540 Added a heartbeat to the system to prevent hung LLMs where they think and dont respond.clear
3d549eb Fixed Failure states from fail hard to fail safe also discovered a bug that could cause double context refresh causing a failure state.
d2c6db2 Fixed planning feedback
e14ba2b Fixed llm inheritance settings bug. Fixed tool call failed bug that halted process. Fixed init bug that wouldn't refresh project_context.md
30d96c3 Fixed error in tool calling on failed tool call as well as project context not refreshing logic bug.
8cdc634 No longer inherit from primary model settings and allow for defaults from ollama by leaving settings blank.
960a174 Fixed alot of small edge case issues as well as broke down files into smaller chunks.
0d6e4c0 Fixed file sizes and fixed a few edge cases found for hardening application working towards version 1.0
64591e1 Updated how planning works in phase 4. Trying to allow a little more flexibility for the implementation model(Primary)
d62ca79 Changed planning steps.
73b8b8e Adjusted how project_context.md is build to reduce time it takes to build or rebuild on larger projects by adding a hashtable that determines if a file needs to be reviewed for project context update.
1f7ffa9 lean-ai: Implement Content Hashing in the project context generation pipeline to
f5f0f28 lean-ai: Update the default GitHub co-author name from "LeanAI" to "LeanAI-bot" a
7367889 Fixed Jetbrains build of application workflow
47198bb fix jetbrains
9e9cd36 Fixed feedback loop so when you need to steer your model back to working properly it will respond quicker was to much of dealy between user input and LLM acknolegement.
581f7a6 Fixed interview-prep and mock-interview to pause between questions properly and ask them in order.
55517cf Fixed pillow dependencie include to resolve flagged CVE
348493b lean-ai(fix): We have 6 code vulnerabilites related to versions of dependencies need t
655ecf8 Fixed Context Calculation in UI and some miscalculations and context clearing that should have been implemented earlier in the build.
bc84755 Hardening Implementation phase.
8ed92f9 Improved Phase 2 process.
022b7c2 Harden Phase 2 planning handoff
82ba4d5 Github Lean-AI Co-author added.
858f228 Added slash command improve-codebase and fixed grill-me prompt to enhance llm and your understanding of changes.
4e111d5 add a slash improve-codebase-architecture command and fixed grill me prompt for inital chat. The goal it to give the local LLM as much relavent data as possible in the most consumable easy way.
010e637 Fixed nudging
9d7673d Fixed chat_with_tools prompt
34884d0 Merge pull request #27 from shunobies/codex/update-readme-for-/skills-command
d689c7f docs: document /skill command and add skills guide
7ee9615 Added grillme protocol to primary chat.
65a4c39 Merge pull request #26 from shunobies/codex/evaluate-ui-tools-for-web-crawling
fe0166d Harden UI verification web capture browser fallback
99c838d Merge pull request #25 from shunobies/codex/fix-github-action-build-for-jetbrains-plugin
ade3f5f Fix IntelliJ Platform dependency declaration for plugin build
9bbc4b6 Fixed Jetbrains extension build in github actions
361031c Merge pull request #24 from shunobies/codex/add-explicit-plugin-verification-configuration
65acd29 Configure IntelliJ plugin verification settings
a5f44ba Merge pull request #23 from shunobies/codex/add-plugin-verifier-dependency-in-build.gradle.kts
dd4f7e3 jetbrains-plugin: add IntelliJ plugin verifier dependency
eb5039c Added ledger for better process flow and context tracking.
3086b44 Merge pull request #22 from shunobies/codex/add-resume-logic-and-recovery-rule
b714e97 Add Phase 1 resume and discrepancy recovery logic
e5d8d66 Merge pull request #21 from shunobies/codex/revise-terminology-in-documentation-files
e1b403b Update Find_Car ledger terminology and clarify dual-ledger roles
7b055f1 Merge pull request #20 from shunobies/codex/add-required-event-emissions-matrix
41764a1 docs(find-car): add per-phase state event emission matrices
52f7889 Merge pull request #19 from shunobies/codex/update-find_car-documentation-for-state-model
7f1de1a Clarify Find_Car two-layer state and event logging workflow
5dd716e Merge pull request #18 from shunobies/codex/review-lean-ai-process-against-repository
4deefff Inject state ledger summaries into phase-2 refresh
7c08f4e Trying to fix issues with /skill command
032b098 Merge pull request #17 from shunobies/codex/fix-find_car-skill-for-reliable-llm-operation
7c78d63 Add unique ledger anchors for safe targeted edits
fbaef9c Merge pull request #16 from shunobies/codex/fix-interaction-flow-for-clarifying-questions
3271a72 Fix request/interview clarification pause and resume flow
1fde7bf Added a /skills command layout
998b5e8 Merge pull request #15 from shunobies/codex/update-find_car-requirements-and-workflow
fb8db64 Align Find_Car skill with instructions.md entrypoint
6f05aa5 Merge pull request #14 from shunobies/codex/align-/skill-command-with-/request-workflow
1fd2af0 Route /skill through request workflow in extension and backend
9b38b36 Merge pull request #13 from shunobies/codex/break-down-template-for-find_car-task
5b95c57 Add documented curl fallback workflow for Find_Car skill
78d34da Merge pull request #12 from shunobies/codex/add-slash-skill-support
38acd0e Add /skill slash command for local skill instructions
5c80d8a Merge pull request #11 from shunobies/codex/implement-context-refresh-with-journaling
19dbbe5 Tie pre-refresh nudge to configured refresh threshold
a712033 Merge pull request #10 from shunobies/codex/investigate-search_url-tool-results-reliability
8eb74e9 Fix Google search parser URL extraction
ebed7d1 lean-ai: ## Goal  Fix the Phase 2 exploration loop where the LLM re-explores the
9f5d152 lean-ai(request): Can you fix the following: Investigation Summary: Why Phase 2 isn't foll
e9d9b1c Added a /memories command
126991c lean-ai(request): Can you review where the /help command list is stored and ensure that /m
ca1a8c5 lean-ai(request): It doesn't seem like even after approving or rejecting a workflow that t
d29024c lean-ai(fix): What I found    The LLM receives only the human‑friendly description tha
1983791 lean-ai(request): Create a new documentation file at docs/job-assistant.md that covers the
82d8904 lean-ai(fix): ## Task: Update all documentation to match the actual registered slash c
fc5772a Bug fix for command /interview-prep hyphen broke the shcema
4cd600b lean-ai: /^(\/\w+)(?:\s+(.*))?$/s ```  The `\w+` quantifier matches only word cha
a44d2c3 Added Job Search assistant
0d3d56e Phase 4: /mock-interview — adaptive Q&A with 5-dim rubric scoring
f79c962 Phase 3: /log-applied — tracker row + git commit
632eeb6 Phase 2: /ats-check and /batch-prep
b1925e8 Phase 1: /thank-you, /recruiter-reply, /negotiate, /analyse-rejection, /help
16acd20 /interview-prep: per-application folders, 20+ questions, tracker row
c289899 Add job-search scaffold for high-volume application workflows
fb08221 Extension: /interview-prep slash command for resume tailoring
e6cdb49 Backend: .docx support in read_file + deterministic convert-docx endpoint
534b4d4 Phase 2: hard-gate task_complete on recorded observations
19ea559 Training archive Tier S+A + TDD Phase C dispute policy
ec622be TDD: wire request_test_change into Phase C + add Test Modification Policy
c50c4c6 Training archive Tier S + A: per-turn capture, 5 new tables, ingestion guide
180f17f v0.16.0 — Per-role Reasoning Effort (Ollama interrupt + cloud native params)
f4fd430 Tests + docs for reasoning_effort feature
65d62d3 Extension + WebSocket: reasoning_effort dropdown + budget-interrupt indicator
3447cdd Backend: reasoning_effort per-role + max_thinking_tokens safety rail
a92602e v0.15.2 — preserve_thinking via client-side fold on Ollama
6483ce8 Fold thinking into content on Ollama (renderer-agnostic preserve_thinking)
23d7185 v0.15.1 — Per-role min_p / presence_penalty + preserve_thinking
f7a8776 Tests + docs for per-role min_p/presence_penalty + preserve_thinking
ca4998a Extension: wire min_p/presence_penalty/preserve_thinking across settings UI
5d2b305 Thread preserve_thinking + min_p/presence_penalty through Serve + role factory
80a2cd7 Add per-role min_p/presence_penalty + preserve_thinking (config + Ollama + facade)
7e366e7 v0.15.0 — Per-LLM image/audio capability flags (active-role routing)
2ae8c52 Add tests + docs for per-model capability flags
49af865 Wire STT LLM handoff + extension capability UI
5a73bf2 Wire chat image routing + resolve_image/audio_handler helpers
2fa55b9 Add capability flags + media_messages module (per-LLM image/audio scaffolding)
d7f346c v0.14.3 — Fall back to system Chromium when Playwright lacks OS support
b5109c9 Fall back to system Chromium/Chrome when Playwright can't install on this OS
9abd94f Bump @vscode/vsce and pin uuid ≥14.0.0 to close Dependabot alert
d5be7c2 v0.14.2 — Inline JSON schema in Ollama structured output calls
d2a04ce Inline JSON schema in Ollama structured calls so the LLM sees the shape
c6d469e v0.14.1 — Surface UI verification controls in the custom Settings panel
ca0ac74 Surface UI verification controls in the custom Settings panel
531fd05 v0.14.0 — UI verification tools (verify_web_ui + verify_desktop_ui)
da87721 Add docs/ui-verification.md setup guide + README/configuration links
62b85ba Gate pygetwindow install on Windows only — unusable on Linux
1c355a6 Add CLAUDE.md documentation and unit tests for UI verification
67d21d9 Add extension commands + config schema for UI verification
1b315d2 Add /api/ui-verification REST endpoints and surface in /health
f9d9c52 Wire verify_web_ui and verify_desktop_ui through all tool surfaces
0b33e68 Add desktop capture adapters for Windows, macOS, Linux X11, Linux Wayland
73ca727 Add headless Chromium web capture for verify_web_ui
baa4939 Add multi-pass UI analysis pipeline (ui_analysis.py + prompt registry entries)
3865d6e Add ui-verification extras group, config, and structured vision helper
bca894b Harden TDD prompts for TDD-naive and MoE models
ff90cfe Teach TDD from first principles in Phase A + B prompts
83e06d2 Route planner phases 1-2 to primary model
2dfde42 Document worker tool-output compression as deferred
7424a9c Route planner phases 1-2 through primary model, not request
9b047c1 Bump lxml 6.0.3 -> 6.1.0 (CVE-2026-41066 XXE fix)
e09fc33 Bump lxml 6.0.3 → 6.1.0 (fixes CVE-2026-41066 XXE)
c815980 Docs: move exampleFlow.md into docs/ and cross-link it
3b2b340 Phase 5 testing strengthening: strict contract, regression protection, core-functionality tagging
afbfa95 Docs: add exampleFlow.md — end-to-end walkthrough of the planning harness
2694e11 Add testing-environment awareness + invert post-command priority
ae3864f Docs: Phase 5 Strengthening — env vars, conventions, regression protection
ff6b9a6 Phase 5 Strengthening PR 5-7: fix-bias + Phase 4 testability + always-run
18ca8de Phase 5 Strengthening PR 4: core-functionality detection + regression mandate
7b7d410 Phase 5 Strengthening PR 3: regression guard + fix-loop banner + training column
8cf976a Phase 5 Strengthening PR 2: testing inventory + coverage validator
17d043d Phase 5 Strengthening PR 1: strict-test-contract prompts + feature flag
3ea23a5 Extension: clear Execution Progress card on workflow terminal events
45e3c4b Docs: bring all references up to date with v0.12.0
d51c284 Add self-improvement data pipeline (curated memory + training archive + export API)
8ba6ceb Add self-improvement Phase B–E: training archive + export API
83b470c Add curated-memory Phase A: self-improvement data pipeline (Layer 2)
8a7dd6b Split Phase 1 into clarification (1) + scope synthesis (1a); add opt-in request_clarification tool
7f6f40c Split Phase 1 into clarification (1) + scope synthesis (1a)
820620b Guarantee Phase 1 always emits an 8-section scope, never raw prose
a52e733 Remove planner's pre-Phase-1 clarify step — chat handles it upstream
ee69075 Reframe Phase 1 prompts as pure task rewrite — no question priming
1ac4eab Reframe Phase 1 prompts as pure task rewrite — no question priming
14b30f3 Harden chat Round 1/2 exclusion + enforce Phase 1 scope via ScopeDocument schema
0b66c46 Structured-enforce Phase 1 scope output via ScopeDocument schema
73f71ba Enforce Round 1 vs Round 2 exclusion in chat two-round protocol
0a28cfc Rename knowledge base to reference library (breaking)
6828cc9 Rename knowledge base → reference library across the stack
f4aac5e Append KB doc listing to unfiltered search_knowledge responses
9787878 Skip EmbeddingStore.compact() in /init — rewrite was wedging on slow disks
7ea9e0d Instrument orphan cleanup + compact() to isolate remaining /init hang
3790065 Delete the show() preload — /init now relies on the real embed() call
28e3991 Surface /init embedding breakdown so idle runs aren't mistaken for hangs
5188710 Fix /init hang on Ollama show() during embedding batch sizing
7974d21 Release 0.10.3: fix /init loading embedding model when there's nothing to embed + documentation alignment with v0.10.x planner
6f37a09 Fix /init wasting embedding-model load on already-indexed workspaces
789c57e docs: align CLAUDE.md, SPECIFICATION.md, and /docs with v0.10.x planner
b048b97 Release 0.10.2: health monitor never auto-restarts on timeouts (only on ECONNREFUSED) + ollama.warmup busy tag for /api/health visibility during cold model loads
024cfc4 Health monitor: never auto-restart on timeouts, only on ECONNREFUSED
15bcb3a Release 0.10.1: fix /init backend restart loop during embedding generation (health probe timeout + consecutive-failure threshold + ECONNREFUSED fast-path + busy signal + batch-size cap)
303f34e Fix backend restart loop during /init embedding generation
591d8fb Release 0.10.0: structured schemas + validators across planning Phases 1-5 + always-explore chat flow with two-round agent prompts
48a473c Planner Phase 5: registry-backed prompts + structured targets + path check
83898c2 Planner Phase 4: structured naming/name_registry + plan validation
2f6c2d5 Planner Phase 3: structured DesignAndRisks + drop scratchpad/journal bridge
0363975 Tests: stop pytest hanging at process exit
1f75473 Planner Phase 2: deterministic observation capture + structured synthesis
f251fe2 Add incomplete.md with Phase 2 parallel-exploration deferred note
f29887c Planner Phase 1: tool-enabled scope analysis with 8-section output
97db888 Chat: always-explore default + strict two-round agent-prompt protocol
df9018f Release 0.9.13: list_knowledge_documents tool + scoped search_knowledge via new document filter
b750d26 Add list_knowledge_documents tool + document filter on search_knowledge
aaff0ed Release 0.9.12: TDD prompt overhaul (Phase 5 design-from-plan, Phase A gets impl plan, Phase B/C tool fixes) + execution-progress card accent
9f12dbc TDD prompt fixes + accent on execution-progress card
b5e8dc0 Release 0.9.11: pinned execution progress card above chat
555d4ed Add pinned Execution Progress card above chat
3d1b909 Release 0.9.10: manual embedding-model context window override
bc25f17 Expose embedding context window in extension Advanced settings
57caf69 Reflow embedding_context_window comment to satisfy ruff E501
d2c5a1a Document LEAN_AI_EMBEDDING_CONTEXT_WINDOW in CLAUDE.md
ff4ed0b Add LEAN_AI_EMBEDDING_CONTEXT_WINDOW manual-override setting
dbf9d49 Bump pypdf to >=6.10.2 to patch 4 moderate Dependabot advisories (FlateDecode RAM exhaustion and xref long-runtime CVEs).
20f25d3 Bump pypdf to >=6.10.2 to patch 4 moderate RAM-exhaustion CVEs
20bb750 Enlarge knowledge-base chunks and add small-to-big neighbor expansion for better long-form prose Q&A.
80d7ea0 Enlarge KB chunks and add small-to-big retrieval for prose Q&A
d8d4b6c Add search_knowledge tool, fix KB chunk truncation, expose refiner chunks setting
d0df154 Add search_knowledge tool and fix knowledge base retrieval
2245d84 updated claude instructions
ea9d4c1 Scale condensation target with actual context window
d21f3ef Scale condensation target with actual context window
86d1ff6 Use structured JSON for per-file context extraction
0e0f3b9 Fix /init crash and deduplicate context MD writes
7b41441 Revert to request model for context extraction
6b79ff2 Write project_context.md once per generation run
b55afe7 Always use primary model for context extraction
fb6cc7e Fix extraction model resolution and add comprehensive context tests
f3c6dfb SQLite-backed file-by-file context generation with query tool
fa17cb7 Replace batch context generation with SQLite-backed file-by-file pipeline
8157e0a Replace file-by-file context generation with 3-step pipeline
e4442d7 Replace file-by-file context generation with 3-step pipeline
c28aa33 Strengthen ruff lint rules and Phase 5 verification prompts
429c2dc Strengthen ruff lint rules and Phase 5 verification prompts
