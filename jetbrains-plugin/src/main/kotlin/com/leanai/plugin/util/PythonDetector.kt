package com.leanai.plugin.util

import com.intellij.execution.configurations.GeneralCommandLine
import com.intellij.execution.process.CapturingProcessHandler
import com.intellij.openapi.diagnostic.Logger

/**
 * Detect a usable Python 3 interpreter on the system.
 * Probes common interpreter names in order of preference.
 */
object PythonDetector {
    private val log = Logger.getInstance(PythonDetector::class.java)

    private val CANDIDATES = when {
        System.getProperty("os.name").lowercase().contains("win") ->
            listOf("python", "python3", "py")
        else ->
            listOf("python3", "python")
    }

    /**
     * Find a working Python 3 interpreter.
     * @return Absolute path to the interpreter, or null if none found.
     */
    fun detect(): String? {
        for (candidate in CANDIDATES) {
            try {
                val cmd = GeneralCommandLine(candidate, "--version")
                    .withParentEnvironmentType(GeneralCommandLine.ParentEnvironmentType.CONSOLE)
                val handler = CapturingProcessHandler(cmd)
                val result = handler.runProcess(5000)

                if (result.exitCode == 0) {
                    val version = (result.stdout + result.stderr).trim()
                    // Ensure it's Python 3.x
                    if (version.startsWith("Python 3.")) {
                        log.info("Detected Python: $candidate → $version")
                        return candidate
                    }
                }
            } catch (e: Exception) {
                // Candidate not found, try next
                log.debug("Python candidate '$candidate' not found: ${e.message}")
            }
        }

        log.warn("No Python 3 interpreter found")
        return null
    }

    /**
     * Get the Python version string from a given interpreter path.
     */
    fun getVersion(pythonPath: String): String? {
        return try {
            val cmd = GeneralCommandLine(pythonPath, "--version")
            val handler = CapturingProcessHandler(cmd)
            val result = handler.runProcess(5000)
            if (result.exitCode == 0) (result.stdout + result.stderr).trim() else null
        } catch (e: Exception) {
            null
        }
    }
}
