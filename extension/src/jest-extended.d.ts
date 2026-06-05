/**
 * Type augmentation for jest-extended two-argument expect(value, message) pattern.
 *
 * Standard Jest's expect() only accepts one argument. The test suites use the
 * extended form from jest-extended which allows a custom failure message as the
 * second parameter. This declaration augments the global namespace so ts-jest
 * type-checks pass without modifying individual test files.
 */

declare namespace jest {
    interface Expect {
        (actual: any, message?: string): ReturnType<Expect>;
    }

    // Re-export all standard Jest matchers with optional message parameter support.
    interface Matchers<R = void, T = {}> {
        toBe(expected: unknown, message?: string): R;
        toEqual(expected: unknown, message?: string): R;
        toStrictEqual(expected: unknown, message?: string): R;
        toHaveLength(length: number, message?: string): R;
        toContain(expected: string | object, message?: string): R;
        toMatch(regexpOrString: RegExp | string, message?: string): R;
        toBeDefined(message?: string): R;
        toBeUndefined(message?: string): R;
        toBeNull(message?: string): R;
        toBeNaN(message?: string): R;
        toBeTruthy(message?: string): R;
        toBeFalsy(message?: string): R;
        toBeGreaterThan(numericBound: number | bigint, message?: string): R;
        toBeGreaterThanOrEqual(numericBound: number | bigint, message?: string): R;
        toBeLessThan(numericBound: number | bigint, message?: string): R;
        toBeLessThanOrEqual(numericBound: number | bigint, message?: string): R;
        toHaveBeenCalled(message?: string): R;
        toHaveBeenCalledTimes(expected: number, message?: string): R;
        toHaveBeenCalledWith(...args: unknown[]): R;
        not: Matchers<R>;
    }

    function expect(actual: any, message?: string): ReturnType<Expect>;
}
