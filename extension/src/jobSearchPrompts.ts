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
// Helpers private to this module
// ---------------------------------------------------------------------------

function slugifyForFilename(input: string): string {
    return (input || "unknown")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "_")
        .replace(/^_+|_+$/g, "")
        .slice(0, 40) || "unknown";
}
