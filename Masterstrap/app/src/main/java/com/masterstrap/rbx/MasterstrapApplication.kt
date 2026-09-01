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
        // No SDK init here. The Activity restores the saved entitlement first;
        // only Free schedules LevelPlay 200 ms after the first frame. Paid or
        // unresolved tiers do not initialize/preload/show advertising.
    }
}
