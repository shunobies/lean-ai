/**
 * Convert a user-provided string (company name, job title, etc.) into a
 * filename-safe slug.
 *
 * - Lowercased.
 * - Non-alphanumeric characters collapse into a single underscore.
 * - Leading/trailing underscores trimmed.
 * - Capped at 60 characters so combined slugs stay short.
 */
export function slugify(input: string): string {
    return (input || "")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "_")
        .replace(/^_+|_+$/g, "")
        .slice(0, 60);
}
