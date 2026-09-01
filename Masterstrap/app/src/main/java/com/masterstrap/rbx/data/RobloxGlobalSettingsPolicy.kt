package com.masterstrap.rbx.data

import java.util.Locale

/** XML owns the FPS ceiling. This does not raise physical display refresh rate. */
object RobloxGlobalSettingsPolicy {
    const val AUTOMATIC_FPS_TARGET = 9999
    const val MIN_FPS_TARGET = 30
    const val MAX_FPS_TARGET = 9999

    fun normalizeFpsTarget(value: Int): Int = value.coerceIn(MIN_FPS_TARGET, MAX_FPS_TARGET)

    private val prefixes = listOf(
        "dfstring", "dffloat", "dfint", "dfflag", "fstring", "ffloat", "fint", "fflag"
    )
    private val excludedFpsNames = setOf(
        "taskschedulertargetfps", "taskschedulerlimittargetfpsto2402",
        "taskschedulerlimittargetfpsto500", "taskschedulerlimittargetfpsto750",
        "taskschedulerlimittargetfpsto1000", "taskschedulerlimittargetfpstoscreenrefreshrate",
        "targetrefreshrate",
        "gamebasicsettingsframeratecap", "gamebasicsettingsframeratecap5",
        "enableandroidvsync", "enableandroidframepacing",
        "debugexperimentalvsyncframepacingforce", "debugexperimentalvsyncframepacing"
    )

    fun excludesFpsOverride(name: String?): Boolean {
        val normalized = name?.trim()?.lowercase(Locale.ROOT) ?: return false
        val prefix = prefixes.firstOrNull { normalized.startsWith(it) && normalized.length > it.length }
        return (if (prefix == null) normalized else normalized.removePrefix(prefix)) in excludedFpsNames
    }

    fun playerNamesEnabled(toggles: List<PresetToggle>): Boolean {
        val manage = toggles.firstOrNull { it.id == "allow_manage_preferences" }?.isEnabled ?: true
        return !manage || toggles.none { it.id == "hide_player_names" && it.isEnabled }
    }
}
