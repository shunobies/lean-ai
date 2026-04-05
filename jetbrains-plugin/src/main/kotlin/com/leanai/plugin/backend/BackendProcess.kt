package com.leanai.plugin.backend

import com.intellij.execution.configurations.GeneralCommandLine
import com.intellij.execution.process.KillableProcessHandler
import com.intellij.execution.process.ProcessAdapter
import com.intellij.execution.process.ProcessEvent
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.components.Service
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.Project
import com.intellij.openapi.util.Key
import com.leanai.plugin.notifications.NotificationManager
import com.leanai.plugin.settings.LeanAiSettings
import com.leanai.plugin.util.SettingsSync
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/**
 * Manages the backend Python subprocess lifecycle: start, stop, health monitoring, auto-restart.
 * Port of extension/src/backendProcess.ts.
 *
 * Application-level singleton — only one backend process across all project windows.
 */
@Service(Service.Level.APP)
class BackendProcess {
    private val log = Logger.getInstance(BackendProcess::class.java)

    private var processHandler: KillableProcessHandler? = null
    private var healthCheckFuture: ScheduledFuture<*>? = null
    private val scheduler = Executors.newSingleThreadScheduledExecutor { r ->
        Thread(r, "lean-ai-health-monitor").apply { isDaemon = true }
    }
    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(3, TimeUnit.SECONDS)
        .readTimeout(3, TimeUnit.SECONDS)
        .build()

    private val owned = AtomicBoolean(false)
    private val consecutiveFailures = AtomicInteger(0)
    private var ownerProject: Project? = null

    companion object {
        private const val HEALTH_INTERVAL_SECONDS = 20L
        private const val MAX_CONSECUTIVE_FAILURES = 3
        private const val STARTUP_POLL_INTERVAL_MS = 1000L
        private const val STARTUP_TIMEOUT_MS = 30_000L

        fun getInstance(): BackendProcess =
            ApplicationManager.getApplication().getService(BackendProcess::class.java)
    }

    /** Whether this instance owns (started) the backend process. */
    fun isOwned(): Boolean = owned.get()

    /** The backend base URL. */
    fun getBaseUrl(): String {
        val s = LeanAiSettings.getInstance().state
        return "http://${s.backendHost}:${s.port}"
    }

    /** The WebSocket base URL. */
    fun getWsUrl(): String {
        val s = LeanAiSettings.getInstance().state
        return "ws://${s.backendHost}:${s.port}"
    }

    /**
     * Start the backend subprocess. Blocks until the health check passes or timeout.
     */
    fun start(project: Project) {
        if (processHandler?.isProcessTerminated == false) {
            log.info("Backend process already running")
            return
        }

        val installer = BackendInstaller.getInstance()
        val settings = LeanAiSettings.getInstance().state

        // Kill any zombie processes on our port
        killProcessOnPort(settings.port)

        // Build the command
        val pythonPath = installer.getPythonPath()
        val cmd = GeneralCommandLine(
            pythonPath, "-m", "uvicorn",
            "lean_ai.main:app",
            "--host", settings.backendHost,
            "--port", settings.port.toString()
        )
        cmd.withEnvironment(SettingsSync.buildFullBackendEnv())
        cmd.withParentEnvironmentType(GeneralCommandLine.ParentEnvironmentType.CONSOLE)

        log.info("Starting backend: ${cmd.commandLineString}")

        val handler = KillableProcessHandler(cmd)
        handler.addProcessListener(object : ProcessAdapter() {
            override fun onTextAvailable(event: ProcessEvent, outputType: Key<*>) {
                log.debug("[backend] ${event.text.trimEnd()}")
            }

            override fun processTerminated(event: ProcessEvent) {
                log.info("Backend process terminated (exit code: ${event.exitCode})")
                processHandler = null
            }
        })

        handler.startNotify()
        processHandler = handler
        owned.set(true)
        ownerProject = project

        // Wait for the server to be ready
        waitForReady()

        // Start health monitoring
        startHealthMonitor(project)

        log.info("Backend started successfully at ${getBaseUrl()}")
    }

    /** Stop the backend subprocess and health monitor. */
    fun stop() {
        healthCheckFuture?.cancel(false)
        healthCheckFuture = null

        processHandler?.let { handler ->
            if (!handler.isProcessTerminated) {
                log.info("Stopping backend process")
                handler.destroyProcess()
            }
        }
        processHandler = null
        owned.set(false)
        ownerProject = null
    }

    /** Restart the backend (stop + start). */
    fun restart(project: Project) {
        stop()
        start(project)
    }

    /** Check if the backend is healthy (quick HTTP probe). */
    fun isHealthy(): Boolean {
        return try {
            val request = Request.Builder()
                .url("${getBaseUrl()}/api/health")
                .build()
            httpClient.newCall(request).execute().use { response ->
                response.isSuccessful
            }
        } catch (e: Exception) {
            false
        }
    }

    private fun waitForReady() {
        val startTime = System.currentTimeMillis()
        while (System.currentTimeMillis() - startTime < STARTUP_TIMEOUT_MS) {
            if (isHealthy()) return
            Thread.sleep(STARTUP_POLL_INTERVAL_MS)
        }
        throw RuntimeException("Backend failed to start within ${STARTUP_TIMEOUT_MS / 1000}s")
    }

    private fun startHealthMonitor(project: Project) {
        healthCheckFuture?.cancel(false)
        consecutiveFailures.set(0)

        healthCheckFuture = scheduler.scheduleAtFixedRate({
            try {
                if (isHealthy()) {
                    consecutiveFailures.set(0)
                } else {
                    val failures = consecutiveFailures.incrementAndGet()
                    log.warn("Health check failed ($failures/$MAX_CONSECUTIVE_FAILURES)")

                    if (failures >= MAX_CONSECUTIVE_FAILURES && owned.get()) {
                        log.warn("Backend unresponsive — attempting auto-restart")
                        ApplicationManager.getApplication().invokeLater {
                            try {
                                restart(project)
                                NotificationManager.notifyInfo(
                                    project, "Backend was unresponsive and has been restarted."
                                )
                            } catch (e: Exception) {
                                log.error("Auto-restart failed", e)
                                NotificationManager.notifyError(
                                    project, "Backend auto-restart failed: ${e.message}"
                                )
                            }
                        }
                    }
                }
            } catch (e: Exception) {
                log.debug("Health check error: ${e.message}")
            }
        }, HEALTH_INTERVAL_SECONDS, HEALTH_INTERVAL_SECONDS, TimeUnit.SECONDS)
    }

    /**
     * Kill any process listening on the given port (clean up zombies from previous runs).
     */
    private fun killProcessOnPort(port: Int) {
        try {
            val isWindows = System.getProperty("os.name").lowercase().contains("win")
            if (isWindows) {
                // Windows: netstat -ano | findstr :PORT
                val cmd = GeneralCommandLine("cmd", "/c", "netstat -ano | findstr :$port")
                val handler = com.intellij.execution.process.CapturingProcessHandler(cmd)
                val result = handler.runProcess(5000)
                if (result.exitCode == 0) {
                    // Parse PID from netstat output and kill
                    val lines = result.stdout.lines()
                    for (line in lines) {
                        val parts = line.trim().split("\\s+".toRegex())
                        if (parts.size >= 5) {
                            val pid = parts.last()
                            try {
                                GeneralCommandLine("taskkill", "/F", "/PID", pid)
                                    .let { com.intellij.execution.process.CapturingProcessHandler(it).runProcess(5000) }
                            } catch (_: Exception) {}
                        }
                    }
                }
            } else {
                // Unix: lsof -ti :PORT
                val cmd = GeneralCommandLine("lsof", "-ti", ":$port")
                val handler = com.intellij.execution.process.CapturingProcessHandler(cmd)
                val result = handler.runProcess(5000)
                if (result.exitCode == 0 && result.stdout.isNotBlank()) {
                    val pids = result.stdout.trim().lines()
                    for (pid in pids) {
                        try {
                            GeneralCommandLine("kill", "-9", pid.trim())
                                .let { com.intellij.execution.process.CapturingProcessHandler(it).runProcess(5000) }
                            log.info("Killed zombie process $pid on port $port")
                        } catch (_: Exception) {}
                    }
                }
            }
        } catch (e: Exception) {
            log.debug("Port cleanup failed (non-fatal): ${e.message}")
        }
    }
}
