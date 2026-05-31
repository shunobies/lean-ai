export {};

const SLASH_COMMAND_REGEX = /^(\/\w[-\w]*)(?:\s+(.*))?$/s;

describe('slash command regex regression', () => {
    it('matches hyphenated command /interview-prep and captures command name', () => {
        const match = '/interview-prep'.match(SLASH_COMMAND_REGEX);
        expect(match).not.toBeNull();
        expect(match![1]).toBe('/interview-prep');
    });

    it('matches simple command /help and captures command name', () => {
        const match = '/help'.match(SLASH_COMMAND_REGEX);
        expect(match).not.toBeNull();
        expect(match![1]).toBe('/help');
    });

    it('matches command with arguments /scaffold my-project', () => {
        const match = '/scaffold my-project'.match(SLASH_COMMAND_REGEX);
        expect(match).not.toBeNull();
        expect(match![1]).toBe('/scaffold');
        expect(match![2]).toBe('my-project');
    });

    it('matches command with underscores /my_custom_command', () => {
        const match = '/my_custom_command'.match(SLASH_COMMAND_REGEX);
        expect(match).not.toBeNull();
        expect(match![1]).toBe('/my_custom_command');
    });

    it('matches hyphenated command with arguments /interview-prep software-engineer', () => {
        const match = '/interview-prep software-engineer'.match(SLASH_COMMAND_REGEX);
        expect(match).not.toBeNull();
        expect(match![1]).toBe('/interview-prep');
        expect(match![2]).toBe('software-engineer');
    });

    it('does not match plain text without leading slash', () => {
        expect('interview-prep'.match(SLASH_COMMAND_REGEX)).toBeNull();
    });

    it('does not match slash followed by empty string', () => {
        expect('/'.match(SLASH_COMMAND_REGEX)).toBeNull();
    });

    it('does not match slash followed only by whitespace', () => {
        expect('/ '.match(SLASH_COMMAND_REGEX)).toBeNull();
    });

    it('matches command with multiple hyphens /analyse-rejection-feedback', () => {
        const match = '/analyse-rejection-feedback'.match(SLASH_COMMAND_REGEX);
        expect(match).not.toBeNull();
        expect(match![1]).toBe('/analyse-rejection-feedback');
    });

    it('does not match command starting with hyphen after slash', () => {
        expect('/-bad-command'.match(SLASH_COMMAND_REGEX)).toBeNull();
    });
});
