/**
 * Seeded prompt builders + shared helpers for the job-search command
 * family (/thank-you, /recruiter-reply, /negotiate, /analyse-rejection,
 * /ats-check, /batch-prep, /mock-interview).
 *
 * Keeping the prompts in a separate module makes them diff-friendly and
 * keeps the slash-command handlers focused on VS Code UI plumbing.
 */

import * as fs from "fs";
import * as path from "path";

import * as vscode from "vscode";

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

/**
 * Resolve an ``applications/{slug}/`` directory — either from a user-provided
 * slug (validated against the filesystem) or by showing a QuickPick over
 * existing application folders.
 *
 * Returns the resolved slug (no prefix) or ``undefined`` if the user
 * cancelled, or ``null`` if the ``applications/`` directory does not
 * exist at all.
 */
export async function pickApplicationSlug(
    repoRoot: string,
    providedSlug?: string,
): Promise<string | undefined | null> {
    const appsDir = path.join(repoRoot, "applications");
    if (!fs.existsSync(appsDir) || !fs.statSync(appsDir).isDirectory()) {
        return null;
    }

    const candidates = fs
        .readdirSync(appsDir, { withFileTypes: true })
        .filter((d) => d.isDirectory() && !d.name.startsWith("."))
        .map((d) => d.name)
        .sort();

    if (providedSlug) {
        const trimmed = providedSlug.trim();
        if (candidates.includes(trimmed)) {
            return trimmed;
        }
        const msg = `No applications/${trimmed}/ folder found. Pick one below or run /interview-prep first.`;
        vscode.window.showWarningMessage(msg);
    }

    if (candidates.length === 0) {
        return null;
    }

    const pick = await vscode.window.showQuickPick(candidates, {
        title: "Pick an application",
        placeHolder: "Select the application folder to operate on",
        ignoreFocusOut: true,
    });
    return pick ?? undefined;
}

// ---------------------------------------------------------------------------
// /thank-you
// ---------------------------------------------------------------------------

export function thankYouPrompt(opts: {
    slug: string;
    interviewers: string;
    discussionNotes: string;
}): string {
    const { slug, interviewers, discussionNotes } = opts;
    return [
        `I just finished an interview for the application at \`applications/${slug}/\`.`,
        "",
        "Context to read BEFORE drafting:",
        `- \`applications/${slug}/research.md\` — company research, tone, talking points`,
        `- \`applications/${slug}/resume.md\` — my tailored resume for this role`,
        "- `templates/thank_you.md` at the workspace root (use its format if present)",
        "",
        `Interviewer(s): ${interviewers}`,
        "",
        `What we discussed:`,
        discussionNotes,
        "",
        "Task:",
        `1. Read the three files above (any that exist).`,
        `2. Draft a thank-you note and save it to \`applications/${slug}/thank_you_sent.md\`.`,
        "3. Personalise it by referencing something SPECIFIC from what we discussed — not a generic 'I enjoyed our chat'.",
        "4. Tie ONE sentence back to relevant experience from my resume that matches a topic we discussed.",
        "5. Keep it under 150 words.",
        "6. If there were multiple interviewers, produce one variant per person separated by a `---` divider in the same file.",
        "",
        "DO NOT send the note — I'll review and send it myself. This is a draft only.",
    ].join("\n");
}

// ---------------------------------------------------------------------------
// /recruiter-reply
// ---------------------------------------------------------------------------

export type RecruiterReplyIntent =
    | "interested-details"
    | "interested-times"
    | "decline-polite"
    | "decline-stay-in-touch";

const INTENT_INSTRUCTIONS: Record<RecruiterReplyIntent, string> = {
    "interested-details":
        "I am interested but need more information — ask for compensation range, tech stack, team size, remote/hybrid policy, and interview process. Express genuine interest so they take me seriously.",
    "interested-times":
        "I am interested and want to move forward — propose two to three concrete times to talk this week (use generic placeholders like 'Tuesday 10am-12pm PT' that I will customise before sending).",
    "decline-polite":
        "I am not interested right now — decline politely in two to three sentences. Don't give detailed reasons, don't bash the role.",
    "decline-stay-in-touch":
        "I am not interested right now, but leave the door open — decline politely and say I would welcome them reaching out about senior/more aligned roles in the future.",
};

export function recruiterReplyPrompt(opts: {
    recruiterMessage: string;
    intent: RecruiterReplyIntent;
}): string {
    const { recruiterMessage, intent } = opts;
    const today = new Date().toISOString().slice(0, 10);
    return [
        "The following message is from a recruiter who contacted me. Draft my reply.",
        "",
        `Intent: ${INTENT_INSTRUCTIONS[intent]}`,
        "",
        "Steps:",
        "1. If the workspace has any `applications/*/resume.md` files, list the directory and read the most recently modified resume to ground your reply in my real background.",
        "2. Identify the specific role or topic the recruiter is pitching.",
        "3. Draft a reply under 120 words. Courteous, specific enough to show I read their message. Mention ONE concrete overlap with my experience if the role is a genuine fit; if it isn't a fit, decline without trashing the role.",
        `4. Save the reply to \`recruiter_replies/${today}_${slugifyForFilename("recruiter")}.md\` — create the \`recruiter_replies/\` directory if it does not exist yet. If a file at that path already exists, append \`_2\`, \`_3\`, etc.`,
        "5. DO NOT send. Draft only.",
        "",
        "Recruiter message:",
        "```",
        recruiterMessage,
        "```",
    ].join("\n");
}

// ---------------------------------------------------------------------------
// /negotiate
// ---------------------------------------------------------------------------

export function negotiatePrompt(opts: {
    slug: string;
    currentComp?: string;
    location: string;
    currentOffer?: string;
}): string {
    const { slug, currentComp, location, currentOffer } = opts;
    return [
        `I need negotiation prep for the application at \`applications/${slug}/\`.`,
        "",
        "Context to read:",
        `- \`applications/${slug}/resume.md\` — my background and seniority signals`,
        `- \`applications/${slug}/research.md\` — company overview, size, funding stage, recent news`,
        "",
        "Inputs:",
        `- Location I'll be working from: ${location}`,
        currentComp ? `- My current/last total comp: ${currentComp}` : "- My current comp: not provided (target a market-competitive anchor)",
        currentOffer ? `- Their offer so far: ${currentOffer}` : "- Their offer so far: none / pre-offer prep",
        "",
        "Task:",
        "1. Research market compensation for this role at this company in this location. Use search_internet and fetch_url. Prefer Levels.fyi, Glassdoor, BuiltIn, recent tech-comp news, state pay-transparency listings. Note that data staleness matters — cite each source with its date.",
        `2. Save a negotiation brief to \`applications/${slug}/negotiation.md\` containing:`,
        "   - **Market range** — low, target, stretch (base + equity + bonus if applicable)",
        "   - **Anchor number** — the first number I should state, with a one-sentence justification",
        "   - **Walk-away number** — the minimum I'd accept",
        "   - **Five negotiating phrases** tied to specific strengths from my resume (e.g. 'Given my track record of X, I was targeting Y')",
        "   - **Three non-salary asks** — signing bonus, equity refresh, additional PTO, remote flexibility, professional development budget, start date",
        "   - **Sources** — bulleted list of URLs with publication dates",
        "3. Be concrete with numbers. Ranges, not vague descriptions.",
        "4. If the data you can find is thin or contradictory, SAY SO in the brief — don't fabricate confidence.",
    ].join("\n");
}

// ---------------------------------------------------------------------------
// /analyse-rejection
// ---------------------------------------------------------------------------

export function analyseRejectionPrompt(opts: {
    slug: string;
    rejectionContext: string;
}): string {
    const { slug, rejectionContext } = opts;
    return [
        `I got rejected (or ghosted) on the application at \`applications/${slug}/\`. Help me do a post-mortem.`,
        "",
        "Context to read:",
        `- \`applications/${slug}/resume.md\``,
        `- \`applications/${slug}/research.md\``,
        `- \`applications/${slug}/cover_letter.md\``,
        `- \`applications/${slug}/ats_report.md\` if it exists`,
        `- \`applications/${slug}/interview_questions.md\` if it exists`,
        "",
        "Outcome / signal:",
        rejectionContext,
        "",
        "Task:",
        "1. Read every file above that exists.",
        "2. Hypothesise what most likely went wrong. Rank by likelihood (most to least):",
        "   - ATS keyword mismatch — the resume didn't hit the right terms",
        "   - Over- or under-qualification framing",
        "   - Missing quantified results — the resume read as responsibilities, not achievements",
        "   - Cover letter didn't differentiate me",
        "   - Role was filled internally or pulled",
        "   - Interview-specific factors if we got that far",
        "3. For each of the TOP 3 hypotheses: cite specific evidence from the application artefacts. Don't generalise — quote the actual lines or bullet points.",
        `4. Write \`applications/${slug}/post_mortem.md\` with the hypotheses + evidence + **three concrete changes** to try on the NEXT similar application. Be candid — flattering me doesn't help me iterate.`,
    ].join("\n");
}

// ---------------------------------------------------------------------------
// /ats-check
// ---------------------------------------------------------------------------

export function atsCheckPrompt(opts: { slug: string }): string {
    const { slug } = opts;
    return [
        `Produce an ATS keyword gap report for the application at \`applications/${slug}/\`.`,
        "",
        "Context to read:",
        `- \`applications/${slug}/resume.md\` — my tailored resume`,
        `- \`applications/${slug}/research.md\` — company + role research (contains the job description text)`,
        "",
        "Task:",
        `1. Read the two files above. Locate the job description inside \`research.md\`.`,
        "2. Extract 15-25 ATS-relevant keywords from the job description. Cover:",
        "   - Must-have technical skills (languages, frameworks, tools)",
        "   - Required certifications or formal qualifications",
        "   - Domain / industry terms the description uses repeatedly",
        "   - Soft-skill buzzwords the company uses verbatim (leadership, ownership, mentorship, etc.)",
        "3. For each keyword, classify its presence in my resume:",
        "   - **Verbatim** — appears exactly as written",
        "   - **Synonym** — present as a close equivalent (e.g. 'Postgres' vs 'PostgreSQL')",
        "   - **Missing** — not present at all",
        `4. Write \`applications/${slug}/ats_report.md\` containing:`,
        "   - **Summary** — one paragraph: the biggest risks for getting past the ATS.",
        "   - **Keyword table** — markdown table with columns: `Keyword | Status | Evidence`. Evidence is the line/phrase in my resume (or '—' if missing).",
        "   - **Top 5 changes** — prioritised list. For each, quote the current resume line verbatim and propose a specific rewrite that works the keyword in naturally. DO NOT auto-apply the rewrites to resume.md.",
        "5. Be honest: if a keyword is missing because I genuinely lack the experience, say so — don't suggest inserting terms I can't back up.",
    ].join("\n");
}

// ---------------------------------------------------------------------------
// /batch-prep
// ---------------------------------------------------------------------------

export interface BatchJob {
    company: string;
    role: string;
    url: string;
}

export function batchPrepPrompt(opts: {
    resumePath: string;
    jobs: BatchJob[];
}): string {
    const { resumePath, jobs } = opts;
    const jobList = jobs
        .map((j, i) => `${i + 1}. **${j.company}** — ${j.role} — ${j.url}`)
        .join("\n");

    const slugFor = (j: BatchJob) =>
        `${_slug(j.company)}_${_slug(j.role)}`;

    return [
        `Batch interview-prep for ${jobs.length} applications. Process them SEQUENTIALLY — finish one completely before moving on to the next. Do not attempt parallel work.`,
        "",
        `Master resume (read with read_file — .docx is supported now): \`${resumePath}\``,
        "",
        "Workspace root artefacts to use if present:",
        "- `star_stories.md` — reusable STAR story bank",
        "- `applications.md` — master tracker; append a row for each job as you complete it",
        "- `templates/` — outreach templates (don't modify, just aware they exist)",
        "",
        "Jobs to process:",
        jobList,
        "",
        "For EACH job, in order:",
        "",
        `A. Create the folder \`applications/{slug}/\` where slug follows \`{company_snake}_{role_snake}\`. Example slugs: ${jobs.slice(0, Math.min(2, jobs.length)).map(slugFor).map(s => `\`${s}\``).join(", ")}.`,
        "B. Fetch the job posting URL with fetch_url. Save the JD text + company research to `applications/{slug}/research.md` (company overview, why it matters for the role, 3-5 talking points, red flags).",
        "C. Read my master resume with read_file. Compare against the JD. Identify gaps and ATS-relevant keywords.",
        "D. Write `applications/{slug}/resume.md` — a tailored markdown version of my resume for THIS role. Preserve every factual claim from the master; re-order and re-weight sections to emphasise what matters for this role. Work in ATS keywords where honestly supported.",
        "E. Write `applications/{slug}/cover_letter.md` — 200-300 words, uses the STAR method where fitting, pulls from `star_stories.md` if present.",
        "F. Write `applications/{slug}/interview_questions.md` — at least 20 questions total: role-specific (5+), behavioural (5+), technical (5+), company-specific (3+), questions to ask them (5+). One-paragraph suggested-answer outline per question.",
        "G. If `applications.md` exists, append a row: today's date, company, role, source 'batch', status 'applied', last contact '—', next action 'Follow up in 7 days', folder link.",
        "",
        "IMPORTANT constraints:",
        "- DO NOT ask me clarifying questions in batch mode — work with what's in my resume.",
        "- If the JD URL returns an error or anti-bot wall, skip that job with a WARNING note at the TOP of `applications/{slug}/research.md` saying you couldn't reach the posting — don't fabricate JD content.",
        "- Announce progress before each job: 'Starting 3/10: Acme Corp — Senior Engineer'. This helps me track progress and interrupt if something's wrong.",
        "- DO NOT fabricate any experience from the resume. If a gap exists, note it in the research file's talking points section as something to address later, not by inventing facts.",
        "",
        "When all jobs are done, post a final summary listing how many succeeded, any that were skipped, and the folder paths for each one.",
    ].join("\n");
}

// ---------------------------------------------------------------------------
// Helpers private to this module
// ---------------------------------------------------------------------------

function slugifyForFilename(input: string): string {
    return _slug(input);
}

function _slug(input: string): string {
    return (input || "unknown")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "_")
        .replace(/^_+|_+$/g, "")
        .slice(0, 40) || "unknown";
}

/**
 * Parse a ``.job_queue.md`` file body into batch jobs.
 *
 * Each job is one non-comment, non-blank line with pipe-separated
 * fields: ``company|role|url``. Returns ``{jobs, errors}`` — caller
 * decides whether to proceed if errors is non-empty.
 */
export function parseBatchQueue(body: string): { jobs: BatchJob[]; errors: string[] } {
    const jobs: BatchJob[] = [];
    const errors: string[] = [];
    const lines = body.split(/\r?\n/);
    lines.forEach((raw, i) => {
        const line = raw.trim();
        if (!line || line.startsWith("#")) return;
        const parts = line.split("|").map((p) => p.trim());
        if (parts.length < 3) {
            errors.push(`Line ${i + 1}: expected 'company|role|url', got '${line}'`);
            return;
        }
        const [company, role, url] = parts;
        if (!company || !role || !url) {
            errors.push(`Line ${i + 1}: one of company/role/url is empty`);
            return;
        }
        if (!/^https?:\/\//i.test(url)) {
            errors.push(`Line ${i + 1}: url '${url}' doesn't look like an http(s) URL`);
            return;
        }
        jobs.push({ company, role, url });
    });
    return { jobs, errors };
}

export const BATCH_QUEUE_TEMPLATE = `# Batch interview-prep queue
#
# One job per line, format:  company | role | url
# Lines starting with '#' and blank lines are ignored.
# Recommended: keep batches to 5 jobs or fewer for best output quality.
#
# Example:
# Acme Corp | Senior Software Engineer | https://acme.example/careers/123
# BigCo | Backend Engineer | https://bigco.example/jobs/456

`;

