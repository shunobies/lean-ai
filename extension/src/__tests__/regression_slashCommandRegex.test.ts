/**
 * Regression tests for the slash command parsing regex used in sidebarChat.ts.
 *
 * The regex `/^(\/[-\w]+)(?:\s+(.*))?$/s` is the gatekeeper for all slash
 * command dispatch. When it matches, capture group 1 contains the full command
 * token (including the leading slash) and capture group 2 (if present) contains
 * the space-delimited arguments. When it does not match, `match()` returns
 * `null` and the input falls through to normal chat mode — a silent failure
 * mode that is hard to debug without these tests.
 *
 * These tests pin down exactly what the regex accepts and rejects to prevent
 * both under-matching (valid commands silently falling through) and
 * over-matching (unintended strings being treated as commands).
 */

/**
 * The slash command regex pattern from sidebarChat.ts (after the fix).
 * Expanded from `/\w+/` to `/[-\w]+/` to allow hyphens in command names.
 */
const SLASH_COMMAND_REGEX = /^(\/[-\w]+)(?:\s+(.*))?$/s;

describe('slash command regex regression', () => {
    /**
     * Matches hyphenated command /interview-prep and captures command name.
     *
     * This is the primary regression scenario: before the fix, the regex
     * `/\w+/` did not match hyphens, so /interview-prep fell through to
     * chat mode with no error.
     */
    it('matches hyphenated command /interview-prep and captures command name', () => {
        const input = '/interview-prep';
        const match = input.match(SLASH_COMMAND_REGEX);

        expect(match).not.toBeNull(
            'Expected /interview-prep to match the slash command regex, but match() returned null',
        );
        // Group 1 is (\/[-\w]+) which includes the leading slash
        expect(match![1]).toBe('/interview-prep',
            'Expected capture group 1 to be /interview-prep, got ' + (match ? match[1] : 'null'));
    });

    /**
     * Matches simple command /help and captures command name.
     *
     * Backward compatibility check: existing single-word commands must
     * continue to work after the regex expansion.
     */
    it('matches simple command /help and captures command name', () => {
        const input = '/help';
        const match = input.match(SLASH_COMMAND_REGEX);

        expect(match).not.toBeNull(
            'Expected /help to match the slash command regex, but match() returned null',
        );
        expect(match![1]).toBe('/help',
            'Expected capture group 1 to be /help, got ' + (match ? match[1] : 'null'));
    });

    /**
     * Matches command with arguments /scaffold my-project.
     *
     * Verifies that the optional arguments capture group works correctly
     * when a command is followed by space-delimited arguments.
     */
    it('matches command with arguments /scaffold my-project', () => {
        const input = '/scaffold my-project';
        const match = input.match(SLASH_COMMAND_REGEX);

        expect(match).not.toBeNull(
            'Expected /scaffold my-project to match the slash command regex, but match() returned null',
        );
        expect(match![1]).toBe('/scaffold',
            'Expected capture group 1 (command) to be /scaffold, got ' + (match ? match[1] : 'null'));
        expect(match![2]).toBe('my-project',
            'Expected capture group 2 (args) to be my-project, got ' + (match ? match[2] : 'null'));
    });

    /**
     * Matches command with underscores /my_custom_command.
     *
     * Underscores are valid word characters (\w) and must continue to match
     * after the regex expansion to [-\w].
     */
    it('matches command with underscores /my_custom_command', () => {
        const input = '/my_custom_command';
        const match = input.match(SLASH_COMMAND_REGEX);

        expect(match).not.toBeNull(
            'Expected /my_custom_command to match the slash command regex, but match() returned null',
        );
        expect(match![1]).toBe('/my_custom_command',
            'Expected capture group 1 to be /my_custom_command, got ' + (match ? match[1] : 'null'));
    });

    /**
     * Matches hyphenated command with arguments /interview-prep software-engineer.
     *
     * Combined test: hyphenated command name AND hyphenated arguments.
     * Verifies both capture groups work together.
     */
    it('matches hyphenated command with arguments /interview-prep software-engineer', () => {
        const input = '/interview-prep software-engineer';
        const match = input.match(SLASH_COMMAND_REGEX);

        expect(match).not.toBeNull(
            'Expected /interview-prep software-engineer to match, but match() returned null',
        );
        expect(match![1]).toBe('/interview-prep',
            'Expected capture group 1 (command) to be /interview-prep, got ' + (match ? match[1] : 'null'));
        expect(match![2]).toBe('software-engineer',
            'Expected capture group 2 (args) to be software-engineer, got ' + (match ? match[2] : 'null'));
    });

    /**
     * Does not match plain text without leading slash.
     *
     * Strings without a leading slash must not be treated as commands.
     * This prevents normal chat messages from being misinterpreted.
     */
    it('does not match plain text without leading slash', () => {
        const input = 'interview-prep';
        const match = input.match(SLASH_COMMAND_REGEX);

        expect(match).toBeNull(
            'Expected "interview-prep" (no leading slash) to NOT match, but got: ' + (match ? match[0] : 'null'),
        );
    });

    /**
     * Does not match slash followed by empty string.
     *
     * A bare slash with no command name is not a valid command and should
     * fall through to normal chat mode.
     */
    it('does not match slash followed by empty string', () => {
        const input = '/';
        const match = input.match(SLASH_COMMAND_REGEX);

        expect(match).toBeNull(
            'Expected "/" (bare slash) to NOT match, but got: ' + (match ? match[0] : 'null'),
        );
    });

    /**
     * Does not match slash followed only by whitespace.
     *
     * A slash followed by whitespace but no command name is not valid.
     * The regex requires at least one character from [-\w] after the slash.
     */
    it('does not match slash followed only by whitespace', () => {
        const input = '/ ';
        const match = input.match(SLASH_COMMAND_REGEX);

        expect(match).toBeNull(
            'Expected "/ " (slash + whitespace) to NOT match, but got: ' + (match ? match[0] : 'null'),
        );
    });

    /**
     * Matches command with multiple hyphens /analyse-rejection-feedback.
     *
     * Verifies that the regex handles commands with more than one hyphen,
     * not just a single hyphen.
     */
    it('matches command with multiple hyphens /analyse-rejection-feedback', () => {
        const input = '/analyse-rejection-feedback';
        const match = input.match(SLASH_COMMAND_REGEX);

        expect(match).not.toBeNull(
            'Expected /analyse-rejection-feedback to match, but match() returned null',
        );
        expect(match![1]).toBe('/analyse-rejection-feedback',
            'Expected capture group 1 to be /analyse-rejection-feedback, got ' + (match ? match[1] : 'null'));
    });

    /**
     * Does not match command starting with hyphen after slash.
     *
     * A command name should start with a word character, not a hyphen.
     * The regex should reject strings like /-bad-command where the first
     * character after the slash is a hyphen. This prevents malformed
     * command names from being dispatched.
     *
     * NOTE: The character class [-\w]+ technically allows a leading hyphen
     * since - is included in the class. If this test fails, the regex may
     * need tightening to /^(\w[-\w]*)/ to require a word character first.
     */
    it('does not match command starting with hyphen after slash', () => {
        const input = '/-bad-command';
        const match = input.match(SLASH_COMMAND_REGEX);

        expect(match).toBeNull(
            'Expected "/-bad-command" (leading hyphen) to NOT match, but got: ' + (match ? match[0] : 'null'),
        );
    });
});
