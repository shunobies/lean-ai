plugins {
    id("java")
    id("org.jetbrains.kotlin.jvm") version "1.9.25"
    id("org.jetbrains.intellij.platform") version "2.1.0"
}

group = providers.gradleProperty("pluginGroup").get()
version = providers.gradleProperty("pluginVersion").get()

repositories {
    mavenCentral()
    intellijPlatform {
        defaultRepositories()
    }
}

dependencies {
    intellijPlatform {
        create(
            providers.gradleProperty("platformType").get(),
            providers.gradleProperty("platformVersion").get()
        )
        bundledPlugins(listOf<String>())
        instrumentationTools()
    }

    // HTTP + WebSocket client
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    // JSON serialization
    implementation("com.google.code.gson:gson:2.11.0")

    // Coroutines for async operations
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.8.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-swing:1.8.1")

    // Testing
    testImplementation("org.jetbrains.kotlin:kotlin-test")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
}

kotlin {
    jvmToolchain(17)
}

intellijPlatform {
    pluginConfiguration {
        name = providers.gradleProperty("pluginName")
        version = providers.gradleProperty("pluginVersion")
        ideaVersion {
            sinceBuild = providers.gradleProperty("pluginSinceBuild")
        }
    }

    publishing {
        token = providers.environmentVariable("PUBLISH_TOKEN")
    }
}

// Copy backend source into plugin resources for bundling
tasks.register<Copy>("copyBackend") {
    from("../backend")
    into("src/main/resources/backend")
    exclude(
        "**/__pycache__/**",
        "**/*.pyc",
        "**/*.egg-info/**",
        "**/.venv/**",
        "**/.pytest_cache/**",
        "**/.ruff_cache/**",
        "**/node_modules/**",
        "**/.mypy_cache/**"
    )
}

tasks.named("processResources") {
    dependsOn("copyBackend")
}

tasks {
    wrapper {
        gradleVersion = "8.10"
    }
}
