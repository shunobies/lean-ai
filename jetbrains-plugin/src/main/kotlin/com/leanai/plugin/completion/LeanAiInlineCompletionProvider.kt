package com.leanai.plugin.completion

import com.intellij.codeInsight.inline.completion.*
import com.intellij.codeInsight.inline.completion.elements.InlineCompletionGrayTextElement
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.editor.Document
import com.intellij.openapi.fileEditor.FileDocumentManager
import com.intellij.openapi.util.TextRange
import com.leanai.plugin.backend.BackendClient
import com.leanai.plugin.settings.LeanAiSettings
import kotlinx.coroutines.flow.channelFlow

/**
 * Inline completion provider — Copilot-style FIM predictions.
 * Port of extension/src/inlineProvider.ts (~90 lines).
 *
 * The IntelliJ platform handles debouncing natively, so we don't need
 * the manual 300ms timer from the VSCode version.
 */
class LeanAiInlineCompletionProvider : InlineCompletionProvider {

    private val log = Logger.getInstance(LeanAiInlineCompletionProvider::class.java)

    override val id: InlineCompletionProviderID =
        InlineCompletionProviderID("LeanAI")

    companion object {
        private const val PREFIX_LINES = 50
        private const val SUFFIX_LINES = 20
    }

    override suspend fun getSuggestion(request: InlineCompletionRequest): InlineCompletionSuggestion {
        // Check if inline predictions are enabled
        if (!LeanAiSettings.getInstance().state.enableInlinePredictions) {
            return InlineCompletionSuggestion.empty()
        }

        val editor = request.editor
        val document = editor.document
        val offset = request.endOffset

        // Build prefix (lines before cursor) and suffix (lines after cursor)
        val cursorLine = document.getLineNumber(offset)

        val prefixStartLine = maxOf(0, cursorLine - PREFIX_LINES)
        val prefixStartOffset = document.getLineStartOffset(prefixStartLine)
        val prefix = document.getText(TextRange(prefixStartOffset, offset))

        val suffixEndLine = minOf(document.lineCount - 1, cursorLine + SUFFIX_LINES)
        val suffixEndOffset = document.getLineEndOffset(suffixEndLine)
        val suffix = document.getText(TextRange(offset, suffixEndOffset))

        // Get file info
        val virtualFile = FileDocumentManager.getInstance().getFile(document)
            ?: return InlineCompletionSuggestion.empty()

        val language = virtualFile.fileType.name.lowercase()
        val filePath = virtualFile.path
        val cursorCharacter = offset - document.getLineStartOffset(cursorLine)

        // Call backend prediction endpoint
        val client = BackendClient.getInstance()
        val result = try {
            client.predict(
                BackendClient.PredictRequest(
                    file_path = filePath,
                    language = language,
                    prefix = prefix,
                    suffix = suffix,
                    cursor_line = cursorLine,
                    cursor_character = cursorCharacter
                )
            )
        } catch (e: Exception) {
            log.debug("Prediction request failed: ${e.message}")
            return InlineCompletionSuggestion.empty()
        }

        val completion = result?.completion
        if (completion.isNullOrEmpty()) {
            return InlineCompletionSuggestion.empty()
        }

        return InlineCompletionSuggestion.withFlow {
            emit(InlineCompletionGrayTextElement(completion))
        }
    }

    override fun isEnabled(event: InlineCompletionEvent): Boolean {
        return event is InlineCompletionEvent.DocumentChange
    }
}
