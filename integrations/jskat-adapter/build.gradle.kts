plugins {
    java
}

repositories {
    mavenCentral()
}

val jskatBaseJar = providers.gradleProperty("jskatBaseJar").orNull
    ?: throw GradleException("Pass -PjskatBaseJar=/path/to/jskat-base.jar")

dependencies {
    compileOnly(files(jskatBaseJar))
    implementation("com.fasterxml.jackson.core:jackson-databind:2.22.2")

    testImplementation(files(jskatBaseJar))
    testImplementation("org.junit.jupiter:junit-jupiter:5.13.4")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher:1.13.4")

    val jskatRuntimeClasspath = providers.gradleProperty("jskatRuntimeClasspath").orNull
    if (!jskatRuntimeClasspath.isNullOrBlank()) {
        testRuntimeOnly(files(jskatRuntimeClasspath.split(File.pathSeparator)))
    }
}

java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(25)
    }
}

tasks.test {
    useJUnitPlatform()
}

tasks.jar {
    archiveFileName.set("skatai-jskat-adapter.jar")
    duplicatesStrategy = DuplicatesStrategy.EXCLUDE
    from({
        configurations.runtimeClasspath.get().map {
            if (it.isDirectory) it else zipTree(it)
        }
    })
    isPreserveFileTimestamps = false
    isReproducibleFileOrder = true
}
