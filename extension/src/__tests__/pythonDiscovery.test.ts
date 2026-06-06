import {
    discoverSupportedPython,
    getPythonCandidates,
    isSupportedPythonVersion,
    normalizeConfiguredPythonPath,
    parsePythonVersion,
    pythonDownloadUrl,
} from "../pythonDiscovery";

describe("Python discovery", () => {
    it.each([undefined, "", "   "])(
        "treats a blank configured path as automatic discovery",
        (configured) => {
            expect(normalizeConfiguredPythonPath(configured)).toBe("python");
        },
    );

    it("trims an explicitly configured interpreter path", () => {
        expect(normalizeConfiguredPythonPath(" /opt/python3.13 ")).toBe("/opt/python3.13");
    });

    it("orders Windows launcher candidates from Python 3.13 down", () => {
        expect(getPythonCandidates("win32").slice(0, 4)).toEqual([
            { command: "py", args: ["-3.13"], label: "py -3.13" },
            { command: "py", args: ["-3.12"], label: "py -3.12" },
            { command: "py", args: ["-3.11"], label: "py -3.11" },
            { command: "py", args: ["-3.10"], label: "py -3.10" },
        ]);
    });

    it("checks common Homebrew locations before PATH on macOS", () => {
        expect(getPythonCandidates("darwin").slice(0, 2).map((item) => item.command))
            .toEqual([
                "/opt/homebrew/bin/python3.13",
                "/usr/local/bin/python3.13",
            ]);
    });

    it("links to a Python 3.13 installer rather than the latest unsupported release", () => {
        expect(pythonDownloadUrl()).toContain("python-313");
    });

    it("prefers Python 3.13 and skips an unavailable candidate", () => {
        const result = discoverSupportedPython("linux", (candidate) => {
            if (candidate.command === "python3.13") {
                return "Python 3.13.5";
            }
            throw new Error("not installed");
        });

        expect(result.selected?.command).toBe("python3.13");
        expect(result.selected?.version.display).toBe("3.13.5");
    });

    it("rejects Python 3.14 even when it is the generic python3", () => {
        const result = discoverSupportedPython("linux", (candidate) => {
            if (candidate.command === "python3") {
                return "Python 3.14.1";
            }
            throw new Error("not installed");
        });

        expect(result.selected).toBeUndefined();
        expect(result.detected.map((item) => item.version.display)).toEqual(["3.14.1"]);
    });

    it.each([
        ["Python 3.9.18", false],
        ["Python 3.10.0", true],
        ["Python 3.11.9", true],
        ["Python 3.12.8", true],
        ["Python 3.13.4", true],
        ["Python 3.14.0", false],
    ])("validates supported range for %s", (output, supported) => {
        const version = parsePythonVersion(output);
        expect(version).not.toBeNull();
        expect(isSupportedPythonVersion(version!)).toBe(supported);
    });
});
