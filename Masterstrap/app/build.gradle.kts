plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

val masterstrapReleaseKeystore = file("masterstrap-release-key.jks")
val masterstrapStorePassword = providers.gradleProperty("MASTERSTRAP_STORE_PASSWORD")
    .orElse(providers.environmentVariable("MASTERSTRAP_STORE_PASSWORD"))
    .orNull
val masterstrapKeyPassword = providers.gradleProperty("MASTERSTRAP_KEY_PASSWORD")
    .orElse(providers.environmentVariable("MASTERSTRAP_KEY_PASSWORD"))
    .orNull
val masterstrapReleaseSigningAvailable = masterstrapReleaseKeystore.isFile &&
    !masterstrapStorePassword.isNullOrBlank() && !masterstrapKeyPassword.isNullOrBlank()

android {
    namespace = "com.masterstrap.rbx"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.masterstrap.app"
        minSdk = 26
        targetSdk = 36
        versionCode = 14
        // Keep the public release label at 1.3 for the current closed-test milestone.
        // versionCode remains 14 because Google Play requires every uploaded
        // artifact to use a new, previously unused code.
        versionName = "1.3"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        if (masterstrapReleaseSigningAvailable) create("release") {
            storeFile = masterstrapReleaseKeystore
            storePassword = masterstrapStorePassword
            keyAlias = "masterstrap_key"
            keyPassword = masterstrapKeyPassword
            enableV1Signing = true
            enableV2Signing = true
        }
    }

    buildTypes {
        release {
            isDebuggable = false
            isMinifyEnabled = true
            isShrinkResources = true
            if (masterstrapReleaseSigningAvailable) {
                signingConfig = signingConfigs.getByName("release")
            }
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
        debug {
            isDebuggable = true
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        viewBinding = true
    }

    lint {
        checkReleaseBuilds = false
        abortOnError = false
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.appcompat)
    implementation(libs.material)
    implementation(libs.androidx.activity)
    implementation(libs.androidx.constraintlayout)
    implementation(libs.androidx.recyclerview)

    // Official Google Play immediate-update flow. Availability is reported by
    // the Play Store for the user's active release track.
    implementation("com.google.android.play:app-update:2.1.0")
    implementation("com.google.android.play:app-update-ktx:2.1.0")

    // Unity LevelPlay Mediation & Unity Ads
    implementation("com.unity3d.ads-mediation:mediation-sdk:9.5.0")
    implementation("com.unity3d.ads-mediation:unityads-adapter:5.11.0")
    implementation("com.unity3d.ads:unity-ads:4.19.0")

}
