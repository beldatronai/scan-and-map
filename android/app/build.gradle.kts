plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "dev.scanmap.capture"
    compileSdk = 36

    defaultConfig {
        applicationId = "dev.scanmap.capture"
        // Tuned for Pixel 10 (Android 16), but runs down to Android 10 so older ARCore phones
        // such as the Galaxy A6 (2018) work too. Newer APIs are guarded with SDK_INT checks.
        minSdk = 29
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
        ndk { abiFilters += listOf("arm64-v8a") }
    }

    // Two variants of the same app:
    //  lan      - can upload a session to the Fedora receiver over HTTP on the home network.
    //  offline  - no INTERNET permission at all, so the OS guarantees nothing leaves the phone;
    //             sessions leave only via "Share zip" or `adb pull`. Installs side by side.
    flavorDimensions += "network"
    productFlavors {
        create("lan") {
            dimension = "network"
            buildConfigField("boolean", "ALLOW_UPLOAD", "true")
        }
        create("offline") {
            dimension = "network"
            applicationIdSuffix = ".offline"
            versionNameSuffix = "-offline"
            buildConfigField("boolean", "ALLOW_UPLOAD", "false")
            resValue("string", "app_name", "ScanMap offline")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    lint {
        // Fail the build on calls to APIs newer than minSdk without a version check.
        checkOnly += "NewApi"
        abortOnError = true
    }
    buildFeatures {
        viewBinding = true
        buildConfig = true
    }
}

kotlin { jvmToolchain(17) }

dependencies {
    implementation("com.google.ar:core:1.48.0")
    implementation("androidx.core:core-ktx:1.16.0")
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.9.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
}
