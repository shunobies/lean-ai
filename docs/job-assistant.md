# Job Assistant

The job assistant is a family of slash commands that automate the repetitive parts of a technical job search — tailoring resumes, generating cover letters, running ATS keyword gap reports, drafting thank-you notes, researching compensation, and practicing interviews. Every command operates on per-application folders under `applications/{slug}/` so you keep one clean workspace for your entire hunt.

> **TL;DR** — The job assistant turns a single `.docx` resume into a tailored, role-specific application folder with a markdown resume, cover letter, company research, and interview prep. A companion suite of commands then handles ATS gap reports, thank-you notes, recruiter replies, compensation research, rejection post-mortems, and mock interviews — all tied to the same folder so nothing gets lost.

## Overview

Applying for jobs is a numbers game, but tailoring each application by hand is exhausting. The job assistant commands let you batch-process dozens of roles in one sitting, then use the remaining commands to manage the follow-up — thank-you notes, recruiter outreach, negotiation prep, and interview practice — without switching contexts.

Every command shares the same folder convention: `applications/{company}_{role}/`. The slug is derived from the company name and job title (lowercased, hyphenated). If you pass a slug explicitly, the command validates it against the filesystem and falls back to a QuickPick dialog if the slug doesn't match any existing folder.

> **Tip** — Run `/scaffold jobs <name>` first to bootstrap a job-search workspace with a tracker (`applications.md`), STAR story bank (`star_stories.md`), and templates. The job assistant commands use these files when they're present.

## Commands at a Glance

| Command | What it does | Output location |
|---|---|---|
| `/interview-prep` | Convert a `.docx` resume and tailor it for a specific role | `applications/{slug}/resume.md`, `cover_letter.md`, `research.md`, `interview_questions.md` |
| `/batch-prep` | Tailor resumes + cover letters for many roles in one run | One `applications/{slug}/` folder per job (see below) |
| `/ats-check [slug]` | Keyword gap report comparing resume to the job description | `applications/{slug}/ats_report.md` |
| `/thank-you [slug]` | Draft a post-interview thank-you note | `applications/{slug}/thank_you_sent.md` |
| `/recruiter-reply` | Draft a reply to a recruiter's cold outreach | `recruiter_replies/{date}_{slug}.md` |
| `/negotiate [slug]` | Research market comp and build a negotiation brief | `applications/{slug}/negotiation.md` |
| `/analyse-rejection [slug]` | Post-mortem a rejection with concrete takeaways | `applications/{slug}/post_mortem.md` |
| `/log-applied [slug]` | Append a tracker row and commit the application folder to git | `applications.md` (tracker), git commit |
| `/mock-interview [slug]` | Interactive Q&A practice with rubric scoring | In-chat (no files written) |

## Detailed Command Reference

### /interview-prep

Convert a `.docx` resume into a tailored application folder for a single role.

**Prompts for:** your `.docx` resume file, company name, job title, and an optional job posting URL.

**Outputs:**
| File | Purpose |
|---|---|
| `applications/{slug}/resume.md` | Markdown copy of your resume, tailored for this role |
| `applications/{slug}/cover_letter.md` | Cover letter tied to the research and your clarifications |
| `applications/{slug}/research.md` | Company overview, talking points, red flags |
| `applications/{slug}/interview_questions.md` | 20+ questions across role-specific, behavioural, technical, company-specific, and "questions to ask them" sections |

**Special behaviors:**
- If `star_stories.md` exists at the workspace root, it is read before writing the cover letter or interview questions.
- If `applications.md` exists at the workspace root, a tracker row is appended automatically.
- If the resume file already exists, you are asked whether to overwrite.
- The command asks clarifying questions in chat before writing anything to disk — it will wait for your reply.

**Example:**

```
/interview-prep
→ Select your resume (.docx)
→ Company: Acme Corp
→ Job title: Senior Software Engineer
→ Job posting URL: https://acme.example/careers/123
```

### /batch-prep

Tailor resumes, cover letters, research, and interview prep for multiple roles in one run.

**Prompts for:** your master `.docx` resume, then opens a `.job_queue.md` file for you to edit.

**Outputs:** One `applications/{slug}/` folder per job (slug format: `{company_snake}_{role_snake}`), each containing `resume.md`, `cover_letter.md`, `research.md`, and `interview_questions.md`. Tracker rows are appended to `applications.md` when present.

**Special behaviors:**
- Jobs are processed **sequentially** — one completes fully before the next begins.
- Queue format: `company | role | url` (one per line). Edit `.job_queue.md` in the editor, save, then click "Process queue."
- If a job posting URL returns an error or anti-bot wall, that job is skipped with a WARNING note in its `research.md`.
- Maximum 10 jobs per run (a warning dialog appears for larger batches).
- If the JD URL is unreachable, the agent does **not** fabricate job description content.

**Example:**

```
/batch-prep
→ Select master resume (.docx)
→ Edit .job_queue.md:
    Acme Corp | Senior Software Engineer | https://acme.example/careers/123
    BigCo | Backend Engineer | https://bigco.example/jobs/456
→ Click "Process queue"
```

### /ats-check [slug]

Produce an ATS keyword gap report comparing your tailored resume against the job description.

**Prompts for:** an application slug (or QuickPick if omitted).

**Outputs:** `applications/{slug}/ats_report.md` containing:
- A summary paragraph of the biggest ATS risks
- A keyword table (`Keyword | Status | Evidence`) with 15–25 terms classified as verbatim, synonym, or missing
- Top 5 prioritized rewrite suggestions with before/after examples

**Constraints:**
- Requires `applications/{slug}/resume.md` and `applications/{slug}/research.md` to exist. If they are missing, the command tells you to run `/interview-prep` first.
- Does **not** auto-apply rewrites to `resume.md` — it only suggests changes you can review.
- Honest about gaps: if a keyword is missing because you genuinely lack the experience, the report says so rather than suggesting fabrication.

**Example:**

```
/ats-check acme-corp-senior-software-engineer
```

### /thank-you [slug]

Draft a post-interview thank-you note for a specific application.

**Prompts for:** an application slug, interviewer name(s), and discussion notes.

**Outputs:** `applications/{slug}/thank_you_sent.md`

**Special behaviors:**
- Personalises the note by referencing something specific from the interview — not a generic "I enjoyed our chat."
- Ties one sentence back to relevant experience from your resume.
- Keeps the note under 150 words.
- If multiple interviewers are listed, produces one variant per person separated by a `---` divider in the same file.
- Draft only — you review and send it yourself.

**Example:**

```
/thank-you acme-corp-senior-software-engineer
→ Interviewer(s): Jane Smith (Hiring Manager), Raj Patel (Engineering Lead)
→ What stood out: discussed migration to Django, they asked about Kafka throughput, I promised to share a blog link
```

### /recruiter-reply

Draft a reply to a recruiter's cold outreach message.

**Prompts for:** the recruiter's message text and your intent.

**Outputs:** `recruiter_replies/{date}_{slug}.md` (creates the directory if it needs it).

**Intent options:**

| Intent | What it does |
|---|---|
| Interested — ask for more details | Asks about comp, tech stack, team size, remote/hybrid policy |
| Interested — propose times | Offers 2–3 concrete time slots to talk |
| Not interested — polite decline | Short courteous no without detailed reasons |
| Not interested — stay in touch | Declines but leaves the door open for future roles |

**Special behaviors:**
- Grounds the reply in your real background by reading the most recently modified `applications/*/resume.md` if any exist.
- Stays under 120 words.
- Draft only — you review and send it yourself.

**Example:**

```
/recruiter-reply
→ Paste the recruiter's message
→ Intent: Interested — ask for more details
```

### /negotiate [slug]

Research market compensation and build a negotiation brief for a specific application.

**Prompts for:** an application slug, your work location, current/last total compensation (optional), and their offer so far (optional).

**Outputs:** `applications/{slug}/negotiation.md` containing:
- Market range (low, target, stretch) for base + equity + bonus
- Anchor number with a one-sentence justification
- Walk-away number
- Five negotiating phrases tied to specific strengths from your resume
- Three non-salary asks (signing bonus, equity refresh, PTO, remote flexibility, etc.)
- Sources with URLs and publication dates

**Special behaviors:**
- Researches using `search_internet` and `fetch_url` — prefers Levels.fyi, Glassdoor, BuiltIn, and recent tech-comp news.
- If data is thin or contradictory, the brief says so rather than fabricating confidence.
- Pre-offer prep is supported: leave "their offer so far" blank to get range-based guidance.

**Example:**

```
/negotiate acme-corp-senior-software-engineer
→ Location: Portland, OR
→ Current comp: $165k base + $30k bonus + $80k/yr equity
→ Their offer so far: $180k base, $25k signing, 0.1% equity 4yr vest
```

### /analyse-rejection [slug]

Post-mortem a rejection with concrete, actionable takeaways for your next application.

**Prompts for:** an application slug and the rejection signal (paste the rejection email text or type "ghosted").

**Outputs:** `applications/{slug}/post_mortem.md` containing:
- Ranked hypotheses for what went wrong (ATS mismatch, over/under-qualification, missing quantified results, cover letter issues, internal fill, etc.)
- Specific evidence quoted from your application artefacts for each hypothesis
- Three concrete changes to try on the next similar application

**Special behaviors:**
- Reads every file in the application folder that exists: `resume.md`, `research.md`, `cover_letter.md`, `ats_report.md`, `interview_questions.md`.
- Candid — the analysis is designed to help you iterate, not to flatter.

**Example:**

```
/analyse-rejection acme-corp-senior-software-engineer
→ Outcome signal: "We've decided to move forward with other candidates..."
```

### /log-applied [slug]

Append a row to the application tracker (`applications.md`) and commit the application folder to git.

**Prompts for:** an application slug, company name, job title, and source (LinkedIn, Company website, Referral, Recruiter, Batch, or Other).

**Outputs:**
- A new row in `applications.md` (if it exists) with date, company, role, source, status "applied", last contact "—", next action "Follow up in 7 days"
- A git commit of the application folder

**Special behaviors:**
- Tracker update and git commit are independent — one may succeed while the other is skipped (e.g., no `applications.md` found, or no git repo initialised).
- The commit message includes the company and role.

**Example:**

```
/log-applied acme-corp-senior-software-engineer
→ Company: Acme Corp
→ Role: Senior Software Engineer
→ Source: LinkedIn
```

### /mock-interview [slug]

Run an interactive mock interview with rubric-based scoring.

**Prompts for:** an application slug, difficulty level, and question count.

**Outputs:** In-chat only — no files written.

**Difficulty options:**

| Difficulty | Focus |
|---|---|
| Recruiter screening round | Broad fit-and-motivation questions |
| Hiring manager | Role fit, past projects, collaboration |
| Technical deep-dive | Implementation, trade-offs, architecture |
| Executive / final round | Strategy, leadership, vision |

**Question counts:** 3, 5, 7, or 10.

**Scoring rubric (1–10 each, composite out of 10):**
| Dimension | What it measures |
|---|---|
| Structure | STAR or coherent flow with clear beginning, middle, end |
| Specificity | Concrete names, numbers, dates, technologies |
| Relevance | Addresses the question and ties to a real JD requirement |
| Ownership | "I" language, clear about personal contribution |
| Impact | Quantified outcomes or genuine self-awareness for failure questions |

**Special behaviors:**
- Uses the chat endpoint (not `/request`) with `extended_turns=40` so all rounds complete.
- Reads `star_stories.md` at the workspace root if present for reusable evidence.
- Offers exactly **one** improvement suggestion per answer — the highest-leverage change.
- Calibrated to be strict: short non-answers score 1–2, platitudes cap at 4, flattery is suppressed.

**Example:**

```
/mock-interview acme-corp-senior-software-engineer
→ Difficulty: Technical deep-dive
→ Question count: 7
```

## Common Patterns

### Slug resolution

All commands that accept a `[slug]` argument follow the same resolution logic:

1. If you provide a slug, it is validated against the `applications/` directory. If it matches, that folder is used.
2. If the slug does not match any folder, a warning is shown and you are prompted to pick from a QuickPick of existing folders.
3. If the `applications/` directory does not exist at all, an error tells you to run `/scaffold jobs` first, then `/interview-prep` to create an application folder.

### Shared guards

Every command that requires an application folder runs two guards before proceeding:

| Guard | What it checks | What happens if it fails |
|---|---|---|
| Agent idle check | No other agent workflow is running (WebSocket not open) | Error: "An agent workflow is already running." |
| Backend health check | Backend server is reachable | Error with server start instructions |

### File dependencies

Some commands depend on files created by `/interview-prep` or `/batch-prep`:

| Command | Requires |
|---|---|
| `/ats-check` | `resume.md` + `research.md` in the application folder |
| `/thank-you` | Application folder (any files) |
| `/negotiate` | Application folder (reads `resume.md` and `research.md`) |
| `/analyse-rejection` | Application folder (reads all available files) |
| `/log-applied` | Application folder |
| `/mock-interview` | `resume.md` + `interview_questions.md` in the application folder |
| `/recruiter-reply` | None (reads `applications/*/resume.md` if any exist for context) |

### Output location conventions

| Pattern | Meaning |
|---|---|
| `applications/{slug}/` | Per-application workspace — every command reads from and writes to this folder |
| `applications.md` | Master tracker at the workspace root (optional, created by `/scaffold jobs`) |
| `star_stories.md` | STAR story bank at the workspace root (optional, created by `/scaffold jobs`) |
| `templates/` | Outreach templates at the workspace root (optional, created by `/scaffold jobs`) |
| `recruiter_replies/` | Outbox for recruiter replies at the workspace root |
| `.job_queue.md` | Queue file for `/batch-prep` (created automatically if missing) |

## Tips

> **Tip** — Start with `/scaffold jobs my-hunt` to create a job-search workspace with a tracker, STAR story bank, and thank-you template. The job assistant commands will use these files automatically when they're present.

> **Tip** — For `/batch-prep`, keep batches to 5 or fewer jobs. Larger batches risk the request model running out of context or looping. Process multiple batches sequentially instead.

> **Tip** — The `/mock-interview` command uses the chat endpoint (not `/request`), so it scores your answers in real time. Reply to each question as you would in a real interview — no meta commentary, no asking for hints.

> **Tip** — Run `/ats-check` after `/interview-prep` but before you submit your application. The suggested rewrites are not auto-applied — review them carefully to make sure they are honest before updating your resume.

> **Note** — All job assistant commands are draft-only. They never send emails, messages, or make external calls on your behalf. You always review and send the output yourself.

## Troubleshooting

**Q: A command says "No `applications/` directory or folders found."**

The job assistant commands expect an `applications/` directory at the workspace root. Run `/scaffold jobs <name>` to bootstrap a job-search workspace, then run `/interview-prep` to create your first application folder.

**Q: `/ats-check` or `/mock-interview` says required files are missing.**

These commands need `resume.md` and `research.md` (for `/ats-check`) or `resume.md` and `interview_questions.md` (for `/mock-interview`). Run `/interview-prep` first to populate the application folder, or run `/batch-prep` to create multiple folders at once.

**Q: `/batch-prep` says the queue file has formatting issues.**

Each line in `.job_queue.md` must follow the format `company | role | url`. The pipe `|` character separates the three fields. Blank lines and lines starting with `#` are ignored. Fix the formatting and run `/batch-prep` again.

**Q: I want to use a different resume for one of the batch jobs.**

`/batch-prep` uses a single master resume for all jobs. If you need different resumes for different roles, use `/interview-prep` individually for each role instead.

**Q: The mock interview feels too easy or too harsh.**

The scoring rubric is calibrated to be strict by design — platitudes cap at 4, short non-answers score 1–2, and flattery is suppressed. If you want a different difficulty level, pick a harder round (e.g., "Executive / final round" instead of "Recruiter screening round").

**Q: Can I use these commands without the VSCode extension?**

No. The job assistant commands are implemented in the VSCode extension (`extension/src/slashCommandsWorkspace.ts` and `extension/src/jobSearchPrompts.ts`). They rely on VSCode's file dialogs, input boxes, and QuickPick UI.

## Related pages

- [Configuration Reference](configuration.md) — environment variables, model setup, and extension settings.
- [Extension Guide](extension.md) — VSCode/VSCodium setup, commands, and settings panel.
- [Architecture](architecture.md) — planning pipeline, workflow modes, tools, and internals.
- [Scaffold Recipes](configuration.md#scaffold-recipes) — the `jobs` scaffold that bootstraps a job-search workspace.
