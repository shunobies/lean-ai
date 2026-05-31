export {};

const SLASH_COMMAND_REGEX = /^(\/\w[-\w]*)(?:\s+(.*))?$/s;

describe('slash command regex edge cases', () => {
    it('matches single character command /a', () => {
        const match = '/a'.match(SLASH_COMMAND_REGEX);
        expect(match).not.toBeNull();
        expect(match![1]).toBe('/a');
    });

    it('matches command with digits /command123', () => {
        const match = '/command123'.match(SLASH_COMMAND_REGEX);
        expect(match).not.toBeNull();
        expect(match![1]).toBe('/command123');
    });

    it('matches command with mixed hyphens and underscores /my-command_test', () => {
        const match = '/my-command_test'.match(SLASH_COMMAND_REGEX);
        expect(match).not.toBeNull();
        expect(match![1]).toBe('/my-command_test');
    });

    it('does not match command with special characters /my@command', () => {
        expect('/my@command'.match(SLASH_COMMAND_REGEX)).toBeNull();
    });

    it('handles command with spaces in name /my command', () => {
        const match = '/my command'.match(SLASH_COMMAND_REGEX);
        expect(match).not.toBeNull();
        expect(match![1]).toBe('/my');
        expect(match![2]).toBe('command');
    });

    it('handles very long command name', () => {
        const longName = 'a'.repeat(200);
        const match = `/${longName}`.match(SLASH_COMMAND_REGEX);
        expect(match).not.toBeNull();
        expect(match![1]).toBe(`/${longName}`);
    });

    it('handles command with trailing whitespace after trim', () => {
        const match = '/help   '.trim().match(SLASH_COMMAND_REGEX);
        expect(match).not.toBeNull();
        expect(match![1]).toBe('/help');
    });

    it('does not match empty string', () => {
        expect(''.match(SLASH_COMMAND_REGEX)).toBeNull();
    });

    it('does not match command with unicode characters /café', () => {
        expect('/café'.match(SLASH_COMMAND_REGEX)).toBeNull();
    });

    it('matches command ending with hyphen /test-', () => {
        const match = '/test-'.match(SLASH_COMMAND_REGEX);
        expect(match).not.toBeNull();
        expect(match![1]).toBe('/test-');
    });
});
