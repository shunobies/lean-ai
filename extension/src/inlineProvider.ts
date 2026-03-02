/**
 * Inline Completion Provider — Copilot-style predictions.
 *
 * TASK_TYPE=INLINE_PREDICTION: No Bundle mutation, no plan, no review, no file modification.
 */

import * as vscode from "vscode";
import { BackendClient } from "./backendClient";
import { INLINE_DEBOUNCE_MS, INLINE_PREFIX_LINES, INLINE_SUFFIX_LINES } from "./constants";

export class LeanAIInlineProvider implements vscode.InlineCompletionItemProvider {
    private debounceTimer: ReturnType<typeof setTimeout> | undefined;
    private client: BackendClient;

    constructor() {
        this.client = BackendClient.getInstance();
    }

    async provideInlineCompletionItems(
        document: vscode.TextDocument,
        position: vscode.Position,
        context: vscode.InlineCompletionContext,
        token: vscode.CancellationToken,
    ): Promise<vscode.InlineCompletionItem[]> {
        // Check if inline predictions are enabled
        const config = vscode.workspace.getConfiguration("lean-ai");
        if (!config.get<boolean>("enableInlinePredictions", true)) {
            return [];
        }

        // Debounce rapid keystrokes
        if (this.debounceTimer) {
            clearTimeout(this.debounceTimer);
        }

        return new Promise((resolve) => {
            this.debounceTimer = setTimeout(async () => {
                if (token.isCancellationRequested) {
                    resolve([]);
                    return;
                }

                // Build context: prefix (lines before cursor) and suffix (lines after cursor)
                const prefixStartLine = Math.max(0, position.line - INLINE_PREFIX_LINES);
                const prefix = document.getText(
                    new vscode.Range(
                        new vscode.Position(prefixStartLine, 0),
                        position,
                    ),
                );

                const suffixEndLine = Math.min(
                    document.lineCount - 1,
                    position.line + INLINE_SUFFIX_LINES,
                );
                const suffix = document.getText(
                    new vscode.Range(
                        position,
                        new vscode.Position(suffixEndLine, document.lineAt(suffixEndLine).text.length),
                    ),
                );

                try {
                    const result = await this.client.predict({
                        file_path: document.uri.fsPath,
                        language: document.languageId,
                        prefix,
                        suffix,
                        cursor_line: position.line,
                        cursor_character: position.character,
                    });

                    if (result.completion && !token.isCancellationRequested) {
                        resolve([
                            new vscode.InlineCompletionItem(
                                result.completion,
                                new vscode.Range(position, position),
                            ),
                        ]);
                    } else {
                        resolve([]);
                    }
                } catch {
                    resolve([]);
                }
            }, INLINE_DEBOUNCE_MS);
        });
    }
}
