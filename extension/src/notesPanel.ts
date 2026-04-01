/**
 * Notes panel — a singleton WebviewPanel for viewing, searching, and
 * managing global notes and TODOs. Communicates with the backend
 * /api/notes endpoints.
 */

import * as vscode from "vscode";
import { BackendClient } from "./backendClient";
import { getNotesPanelHtml } from "./notesPanelHtml";

export class NotesPanel {
    static currentPanel: NotesPanel | undefined;
    private static readonly viewType = "lean-ai.notesPanel";

    private readonly _panel: vscode.WebviewPanel;
    private _disposables: vscode.Disposable[] = [];

    static createOrShow(context: vscode.ExtensionContext): void {
        const column = vscode.window.activeTextEditor
            ? vscode.window.activeTextEditor.viewColumn
            : undefined;

        if (NotesPanel.currentPanel) {
            NotesPanel.currentPanel._panel.reveal(column);
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            NotesPanel.viewType,
            "Notes & TODOs",
            column || vscode.ViewColumn.One,
            {
                enableScripts: true,
                retainContextWhenHidden: true,
                localResourceRoots: [],
            },
        );

        NotesPanel.currentPanel = new NotesPanel(panel);
    }

    private constructor(panel: vscode.WebviewPanel) {
        this._panel = panel;
        this._panel.webview.html = getNotesPanelHtml();

        this._panel.webview.onDidReceiveMessage(
            async (message: Record<string, unknown>) => {
                await this._handleMessage(message);
            },
            null,
            this._disposables,
        );

        this._panel.onDidDispose(() => this._dispose(), null, this._disposables);

        setTimeout(() => {
            void this._loadNotes();
        }, 200);
    }

    private async _handleMessage(msg: Record<string, unknown>): Promise<void> {
        const client = BackendClient.getInstance();

        switch (msg.type) {
            case "webviewReady":
                await this._loadNotes();
                break;

            case "loadNotes":
                await this._loadNotes(msg.project as string | undefined);
                break;

            case "searchNotes": {
                const query = msg.query as string;
                try {
                    const results = await client.searchNotes(query);
                    await this._panel.webview.postMessage({
                        type: "searchResults",
                        notes: results,
                    });
                } catch (err) {
                    this._showError("search notes", err);
                }
                break;
            }

            case "updateNote": {
                const noteId = msg.noteId as string;
                const data = msg.data as { content?: string; project?: string; tags?: string[] };
                try {
                    await client.updateNote(noteId, data);
                    await this._loadNotes();
                } catch (err) {
                    this._showError("update note", err);
                }
                break;
            }

            case "deleteNote": {
                const noteId = msg.noteId as string;
                try {
                    await client.deleteNote(noteId);
                    await this._loadNotes();
                } catch (err) {
                    this._showError("delete note", err);
                }
                break;
            }

            case "toggleTodo": {
                const todoId = msg.todoId as number;
                const completed = msg.completed as boolean;
                try {
                    await client.updateTodo(todoId, { completed });
                    await this._loadNotes();
                } catch (err) {
                    this._showError("update todo", err);
                }
                break;
            }

            case "addTodo": {
                const noteId = msg.noteId as string;
                const description = msg.description as string;
                try {
                    await client.addTodo(noteId, description);
                    await this._loadNotes();
                } catch (err) {
                    this._showError("add todo", err);
                }
                break;
            }

            case "deleteTodo": {
                const todoId = msg.todoId as number;
                try {
                    await client.deleteTodo(todoId);
                    await this._loadNotes();
                } catch (err) {
                    this._showError("delete todo", err);
                }
                break;
            }

            case "createNote": {
                const content = msg.content as string;
                const workspace = this._getRepoRoot();
                try {
                    await client.createNote(content, workspace);
                    await this._loadNotes();
                } catch (err) {
                    this._showError("create note", err);
                }
                break;
            }
        }
    }

    private _getRepoRoot(): string {
        const folders = vscode.workspace.workspaceFolders;
        return folders?.[0]?.uri.fsPath || "";
    }

    private async _loadNotes(project?: string): Promise<void> {
        try {
            const client = BackendClient.getInstance();
            const [notes, projects] = await Promise.all([
                client.listNotes(project),
                client.listNoteProjects(),
            ]);
            await this._panel.webview.postMessage({
                type: "loadNotes",
                notes,
                projects,
                activeProject: project || null,
            });
        } catch (err) {
            this._showError("load notes", err);
        }
    }

    private _showError(action: string, err: unknown): void {
        const errMsg = err instanceof Error ? err.message : String(err);
        void vscode.window.showErrorMessage(
            `Lean AI: failed to ${action} — ${errMsg}`,
        );
    }

    private _dispose(): void {
        NotesPanel.currentPanel = undefined;
        this._panel.dispose();
        while (this._disposables.length) {
            const d = this._disposables.pop();
            if (d) { d.dispose(); }
        }
    }
}
