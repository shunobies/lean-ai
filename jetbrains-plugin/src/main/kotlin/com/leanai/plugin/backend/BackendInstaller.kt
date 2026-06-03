package com.leanai.plugin.backend

import com.intellij.ide.util.PropertiesComponent
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.components.Service
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.progress.ProgressIndicator
import com.intellij.openapi.progress.ProgressManager
import com.intellij.openapi.progress.Task
import com.intellij.openapi.project.Project
import com.intellij.execution.configurations.GeneralCommandLine
import com.intellij.execution.process.CapturingProcessHandler
import com.leanai.plugin.settings.LeanAiSettings
import com.leanai.plugin.util.PythonDetector
import java.net.JarURLConnection
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths
import java.nio.file.StandardCopyOption
import java.util.jar.JarFile

/**
 * Manages backend installation: venv creation, pip install, version tracking, upgrades.
 * Port of extension/src/backendInstaller.ts.
 */
@Service(Service.Level.APP)
class BackendInstaller {
    private val log = Logger.getInstance(BackendInstaller::class.java)

    companion object {
        private const val VERSION_KEY = "leanai.installedBackendVersion"
        private const val BACKEND_DIR_KEY = "leanai.managedBackendDir"
        private const val CURRENT_VERSION = "0.1.0"

        private const val VENV_DIR_NAME = ".venv"

        /** Standard fallback location for managed installs. */
        private val CACHE_ROOT: Path = Paths.get(
            System.getProperty("user.home"), ".cache", "JetBrains", "lean-ai"
        )

        fun getInstance(): BackendInstaller =
            ApplicationManager.getApplication().getService(BackendInstaller::class.java)
    }

    /** True if using managed mode (auto-installed venv). */
    fun isManagedMode(): Boolean {
        val settings = LeanAiSettings.getInstance().state
        return settings.pythonPath.isEmpty() && settings.backendDir.isEmpty()
    }

    /** Get the Python interpreter path (managed venv or user-configured). */
    fun getPythonPath(): String {
        val settings = LeanAiSettings.getInstance().state
        if (settings.pythonPath.isNotEmpty()) return settings.pythonPath

        // Managed mode: use venv python
        val isWindows = System.getProperty("os.name").lowercase().contains("win")
        val venvDir = getVenvDir()
        val venvPython = if (isWindows) {
            venvDir.resolve("Scripts").resolve("python.exe")
        } else {
            venvDir.resolve("bin").resolve("python")
        }
        return venvPython.toString()
    }

    /** Get the backend source directory (bundled in plugin resources or user-configured). */
    fun getBackendDir(): String {
        val settings = LeanAiSettings.getInstance().state
        if (settings.backendDir.isNotEmpty()) return settings.backendDir

        return getManagedBackendDir().toString()
    }

    /**
     * Ensure the backend is installed and up to date.
     * In managed mode: creates venv, installs pip packages, verifies imports.
     * In manual mode: skips installation entirely.
     */
    fun ensureInstalled(project: Project) {
        if (!isManagedMode()) {
            log.info("Manual mode — skipping managed installation")
            return
        }

        val props = PropertiesComponent.getInstance()
        val installedVersion = props.getValue(VERSION_KEY)

        if (installedVersion == CURRENT_VERSION && Files.exists(Paths.get(getPythonPath()))) {
            log.info("Backend already installed at version $CURRENT_VERSION")
            return
        }

        ProgressManager.getInstance().run(object : Task.WithResult<Unit, Exception>(
            project, "Installing Lean AI Backend", true
        ) {
            override fun compute(indicator: ProgressIndicator) {
                indicator.isIndeterminate = false

                // Step 1: Find system Python
                indicator.text = "Detecting Python interpreter..."
                indicator.fraction = 0.1
                val systemPython = PythonDetector.detect()
                    ?: throw RuntimeException(
                        "Python 3 not found. Please install Python 3.10+ and ensure it's on your PATH."
                    )

                // Step 2: Resolve/extract backend source
                indicator.text = "Extracting backend source..."
                indicator.fraction = 0.2
                extractBundledBackend()

                // Step 3: Create venv
                indicator.text = "Creating virtual environment..."
                indicator.fraction = 0.3
                createVenv(systemPython)

                // Step 4: pip install
                indicator.text = "Installing Python dependencies..."
                indicator.fraction = 0.4
                pipInstall()

                indicator.text = "Upgrading Python packaging tools..."
                indicator.fraction = 0.75
                upgradePackagingTools()

                // Step 5: Verify imports
                indicator.text = "Verifying installation..."
                indicator.fraction = 0.9
                verifyImports()

                // Step 6: Record version
                props.setValue(VERSION_KEY, CURRENT_VERSION)
                indicator.fraction = 1.0
                log.info("Backend installation complete (v$CURRENT_VERSION)")
            }
        })
    }

    private fun createVenv(systemPython: String) {
        val venvDir = getVenvDir()
        if (Files.exists(venvDir.resolve("pyvenv.cfg"))) {
            log.info("Venv already exists at $venvDir")
            return
        }

        Files.createDirectories(venvDir.parent)
        val cmd = GeneralCommandLine(systemPython, "-m", "venv", venvDir.toString())
        val handler = CapturingProcessHandler(cmd)
        val result = handler.runProcess(120_000)

        if (result.exitCode != 0) {
            throw RuntimeException("Failed to create venv: ${result.stderr}")
        }
        log.info("Created venv at $venvDir")
    }

    private fun extractBundledBackend() {
        val backendDir = getManagedBackendDir()
        if (Files.exists(backendDir.resolve("pyproject.toml"))) {
            log.info("Backend source already present at $backendDir")
            return
        }

        // Extract from plugin JAR resources
        val resourcePath = "/backend"
        val resourceUrl = javaClass.getResource(resourcePath)
        if (resourceUrl != null) {
            log.info("Extracting bundled backend to $backendDir")
            Files.createDirectories(backendDir)
            extractResourceDirectory(resourcePath, backendDir)
        } else {
            throw RuntimeException("No bundled backend found in plugin resources.")
        }
    }

    private fun extractResourceDirectory(resourcePath: String, targetDir: Path) {
        val url = javaClass.getResource(resourcePath) ?: return

        if (url.protocol == "file") {
            val sourceDir = Paths.get(url.toURI())
            sourceDir.toFile().copyRecursively(targetDir.toFile(), overwrite = true)
            return
        }

        if (url.protocol == "jar") {
            val connection = url.openConnection() as JarURLConnection
            val jarFile: JarFile = connection.jarFile
            val prefix = connection.entryName.trimEnd('/') + "/"
            jarFile.entries().asSequence()
                .filter { !it.isDirectory && it.name.startsWith(prefix) }
                .forEach { entry ->
                    val relative = entry.name.removePrefix(prefix)
                    if (shouldExclude(relative)) return@forEach
                    val out = targetDir.resolve(relative)
                    Files.createDirectories(out.parent)
                    jarFile.getInputStream(entry).use { input ->
                        Files.copy(input, out, StandardCopyOption.REPLACE_EXISTING)
                    }
                }
        } else {
            throw RuntimeException("Unsupported backend resource protocol: ${url.protocol}")
        }
    }

    private fun pipInstall() {
        val pythonPath = getPythonPath()
        val backendDir = getBackendDir()

        val cmd = GeneralCommandLine(
            pythonPath, "-m", "pip", "install", "-e", "$backendDir[dev]",
            "--disable-pip-version-check"
        )
        val handler = CapturingProcessHandler(cmd)
        val result = handler.runProcess(600_000) // 10 min timeout for pip

        if (result.exitCode != 0) {
            throw RuntimeException("pip install failed:\n${result.stderr}")
        }
        log.info("pip install completed successfully")
    }

    private fun upgradePackagingTools() {
        val pythonPath = getPythonPath()
        val cmd = GeneralCommandLine(
            pythonPath, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel",
            "--disable-pip-version-check"
        )
        val handler = CapturingProcessHandler(cmd)
        val result = handler.runProcess(300_000)

        if (result.exitCode != 0) {
            throw RuntimeException("pip packaging tool upgrade failed:\n${result.stderr}")
        }
        log.info("Python packaging tools upgraded successfully")
    }

    private fun verifyImports() {
        val pythonPath = getPythonPath()
        val cmd = GeneralCommandLine(
            pythonPath, "-c",
            "import lean_ai; import tree_sitter; import fastapi; import uvicorn; import ollama; print('OK')"
        )
        val handler = CapturingProcessHandler(cmd)
        val result = handler.runProcess(30_000)

        if (result.exitCode != 0 || !result.stdout.contains("OK")) {
            throw RuntimeException("Import verification failed:\n${result.stderr}")
        }
        log.info("Import verification passed")
    }

    private fun getVenvDir(): Path = getManagedBackendDir().resolve(VENV_DIR_NAME)

    private fun getManagedBackendDir(): Path {
        val props = PropertiesComponent.getInstance()
        props.getValue(BACKEND_DIR_KEY)?.let { return Paths.get(it) }

        val resourcePath = "/backend"
        val resourceUrl = javaClass.getResource(resourcePath)
        val backendDir = if (resourceUrl?.protocol == "file") {
            val sourceDir = Paths.get(resourceUrl.toURI())
            if (Files.exists(sourceDir.resolve("pyproject.toml")) && isWritableDirectory(sourceDir)) {
                sourceDir
            } else {
                CACHE_ROOT.resolve("backend")
            }
        } else {
            CACHE_ROOT.resolve("backend")
        }

        props.setValue(BACKEND_DIR_KEY, backendDir.toString())
        return backendDir
    }

    private fun isWritableDirectory(dir: Path): Boolean {
        return try {
            Files.createDirectories(dir)
            val probe = dir.resolve(".lean-ai-write-test-${System.nanoTime()}")
            Files.writeString(probe, "")
            Files.deleteIfExists(probe)
            true
        } catch (_: Exception) {
            false
        }
    }

    private fun shouldExclude(relativePath: String): Boolean {
        val parts = relativePath.split('/', '\\')
        val excludedNames = setOf(".env", ".git", ".venv", "venv")
        if (parts.any { it in excludedNames }) return true
        val excludedPatterns = listOf(
            "__pycache__",
            ".egg-info",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            "node_modules",
        )
        return parts.any { part -> excludedPatterns.any { pattern -> part.contains(pattern) } }
    }
}
