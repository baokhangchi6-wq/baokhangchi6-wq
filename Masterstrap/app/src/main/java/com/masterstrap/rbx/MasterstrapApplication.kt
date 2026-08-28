package com.masterstrap.rbx

import android.app.Application
import androidx.appcompat.app.AppCompatDelegate
import com.masterstrap.rbx.data.ConfigExporter
import com.masterstrap.rbx.data.SettingsPersistence

class MasterstrapApplication : Application() {

    override fun onCreate() {
        super.onCreate()
        
        // Strictly default to Dark Mode on fresh install or reboot unless user explicitly chose Light
        val savedTheme = SettingsPersistence.loadAppTheme(this)
        if (savedTheme == "Light") {
            AppCompatDelegate.setDefaultNightMode(AppCompatDelegate.MODE_NIGHT_NO)
        } else {
            AppCompatDelegate.setDefaultNightMode(AppCompatDelegate.MODE_NIGHT_YES)
        }

        // MediaStore from older builds may have auto-renamed raced writes to
        // " (n)". Clean only Masterstrap-owned duplicates off the UI thread;
        // subsequent exports overwrite their canonical rows in place.
        Thread({
            ConfigExporter.cleanupDuplicateOutputs(this)
        }, "MasterstrapOutputCleanup").apply {
            isDaemon = true
            start()
        }
        // LevelPlay is deliberately initialized only after the user requests
        // a rewarded action. Starting it here forces Android WebView/Chromium
        // to initialize while the first activity (and possibly the mandatory
        // update dialog) is drawing. On storage-constrained devices that can
        // block the main thread long enough to trigger an input ANR.
    }
}
