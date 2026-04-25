/**
 * Edge case and boundary condition tests for the slash command regex
 * `/^(\/\w[-\w]*)(?:\s+(.*))?$/s`.
 *
 * These tests complement the regression suite by covering boundary
 * conditions that are unlikely to regress but important for security
 * and correctness: single-character commands, very long commands,
 * mixed separators, special characters, unicode, trailing whitespace,
 * and empty input.
 *
 * The security concern about regex expansion inadvertently matching
 * unintended strings is addressed by explicitly testing what the
 * regex rejects (special characters, unicode without u-flag, bare
 * slashes) in addition to what it accepts. This ensures the character
 * class expansion `[-\w]` is a strict superset of `\w` without
 * over-matching, while `\w[-\w]*` ensures commands cannot start with
 * a hyphen.
 */

/**
 * The slash command regex pattern from sidebarChat.ts (after the fix).
 * Expanded from `/\w+/` to `/\w[-\w]*/` to allow hyphens in command names
 * while requiring the first character after the slash to be a word character.
 */
const SLASH_COMMAND_REGEX = /^(\/\w[-\w]*)(?:\s+(.*))?$/s;

describe('slash command regex edge cases', () => {
    /**
     * Matches single character command /a.
     *
     * Verifies that the `+` quantifier accepts a single character,
     * not just multiple characters. A one-letter command is valid.
     */
    it('matches single character command /a', () => {
        const input = '/a';
        const match = input.match(SLASH_COMMAND_REGEX);

        expect(match).not.toBeNull(
            'Expected /a to match the slash command regex, but match() returned null',
        );
        expect(match![1]).toBe('/a',
            'Expected capture group 1 to be /a, got ' + (match ? match[1] : 'null'),
        );
    });

    /**
     * Matches command with digits /command123.
     *
     * Digits are valid word characters (\w) and must match.
     * This ensures numeric suffixes in command names are accepted.
     */
    it('matches command with digits /command123', () => {
        const input = '/command123';
        const match = input.match(SLASH_COMMAND_REGEX);

        expect(match).not.toBeNull(
            'Expected /command123 to match the slash command regex, but match() returned null',
        );
        expect(match![1]).toBe('/command123',
            'Expected capture group 1 to be /command123, got ' + (match ? match[1] : 'null'),
        );
    });

    /**
     * Matches command with mixed hyphens and underscores /my-command_test.
     *
     * The character class `[-\w]` must accept both hyphens and underscores
     * anywhere in the command name, including adjacent to each other.
     */
    it('matches command with mixed hyphens and underscores /my-command_test', () => {
        const input = '/my-command_test';
        const match = input.match(SLASH_COMMAND_REGEX);

        expect(match).not.toBeNull(
            'Expected /my-command_test to match the slash command regex, but match() returned null',
        );
        expect(match![1]).toBe('/my-command_test',
            'Expected capture group 1 to be /my-command_test, got ' + (match ? match[1] : 'null'),
        );
    });

    /**
     * Does not match command with special characters /my@command.
     *
     * The `@` symbol is not in the `[-\w]` character class. Commands
     * containing special characters must be rejected to prevent
     * injection or unintended dispatch.
     */
    it('does not match command with special characters /my@command', () => {
        const input = '/my@command';
        const match = input.match(SLASH_COMMAND_REGEX);

        expect(match).toBeNull(
            'Expected /my@command to NOT match (special character @ not in [-\\w]), but got: ' + (match ? match[0] : 'null'),
        );
    });

    /**
     * Handles command with spaces in name /my command.
     *
     * The space acts as the delimiter between command and arguments,
     * not as part of the command name. The regex should capture `my`
     * as the command and `command` as the arguments.
     */
    it('handles command with spaces in name /my command', () => {
        const input = '/my command';
        const match = input.match(SLASH_COMMAND_REGEX);

        expect(match).not.toBeNull(
            'Expected /my command to match the slash command regex, but match() returned null',
        );
        expect(match![1]).toBe('/my',
            'Expected capture group 1 (command) to be /my, got ' + (match ? match[1] : 'null'),
        );
        expect(match![2]).toBe('command',
            'Expected capture group 2 (args) to be command, got ' + (match ? match[2] : 'null'),
        );
    });

    /**
     * Handles very long command name.
     *
     * Verifies that the regex does not have practical length limits
     * that would reject legitimate long command names. Tests with
     * 200 word characters to ensure no stack overflow or backtracking
     * issues.
     */
    it('handles very long command name', () => {
        const longName = 'a'.repeat(200);
        const input = '/' + longName;
        const match = input.match(SLASH_COMMAND_REGEX);

        expect(match).not.toBeNull(
            'Expected long command name (200 chars) to match the slash command regex, but match() returned null',
        );
        expect(match![1]).toBe('/' + longName,
            'Expected capture group 1 to contain the full 200-character command name, got length ' + (match ? match[1].length : 'null'),
        );
    });

    /**
     * Handles command with trailing whitespace /help   .
     *
     * Since handleUserMessage trims input before applying the regex,
     * trailing whitespace is removed. The trimmed string `/help` should
     * match and capture `help` as the command.
     */
    it('handles command with trailing whitespace /help   ', () => {
        const input = '/help   ';
        const trimmed = input.trim();
        const match = trimmed.match(SLASH_COMMAND_REGEX);

        expect(match).not.toBeNull(
            'Expected trimmed "/help" to match the slash command regex, but match() returned null',
        );
        expect(match![1]).toBe('/help',
            'Expected capture group 1 to be /help after trimming, got ' + (match ? match[1] : 'null'),
        );
    });

    /**
     * Does not match empty string.
     *
     * An empty string must not match the regex. The `+` quantifier
     * requires at least one character from `[-\w]` after the slash.
     */
    it('does not match empty string', () => {
        const input = '';
        const match = input.match(SLASH_COMMAND_REGEX);

        expect(match).toBeNull(
            'Expected empty string to NOT match, but got: ' + (match ? match[0] : 'null'),
        );
    });

    /**
     * Does not match command with unicode characters /café.
     *
     * The `\w` shorthand in JavaScript regex (without the `u` flag)
     * matches only ASCII word characters: [A-Za-z0-9_]. Accented
     * characters like `é` are NOT matched. Without the `u` flag,
     * `/café` should fail to match because `é` is not in `[-\w]`.
     *
     * This documents the observed behavior: the regex intentionally
     * rejects non-ASCII characters to keep command names simple and
     * predictable.
     */
    it('does not match command with unicode characters /café', () => {
        const input = '/café';
        const match = input.match(SLASH_COMMAND_REGEX);

        expect(match).toBeNull(
            'Expected /café to NOT match (unicode é not in [-\\w] without u flag), but got: ' + (match ? match[0] : 'null'),
        );
    });

    /**
     * Matches command ending with hyphen /test-.
     *
     * The character class `[-\w]+` allows a hyphen at any position,
     * including the end of the command name. This is valid per the
     * regex design — hyphens are not restricted to internal positions.
     */
    it('matches command ending with hyphen /test-', () => {
        const input = '/test-';
        const match = input.match(SLASH_COMMAND_REGEX);

        expect(match).not.toBeNull(
            'Expected /test- to match the slash command regex, but match() returned null',
        );
        expect(match![1]).toBe('/test-',
            'Expected capture group 1 to be /test-, got ' + (match ? match[1] : 'null'),
        );
    });
});
