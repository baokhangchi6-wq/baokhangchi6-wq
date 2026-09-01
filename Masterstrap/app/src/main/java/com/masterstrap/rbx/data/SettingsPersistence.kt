package com.masterstrap.rbx.data

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONArray
import org.json.JSONObject

object SettingsPersistence {
    private const val PREFS_NAME = "masterstrap_preferences"
    private const val KEY_FPS = "key_fps_cap"
    private const val KEY_SELECTED_PRESET = "key_selected_preset"
    private const val KEY_CUSTOM_FLAGS = "key_custom_flags"
    private const val KEY_LICENSE_KEY = "key_license_key"
    private const val KEY_LICENSE_PLAN = "key_license_plan"
    private const val KEY_APP_THEME = "key_app_theme"
    private const val KEY_APP_LANGUAGE = "key_app_language"
    private const val KEY_STRETCH_SCREEN_PERCENT = "key_stretch_screen_percent"
    private const val KEY_SHADER_PACKAGE = "key_shader_package"
    private const val KEY_SHADER_WET_FLOOR = "key_shader_wet_floor"

    private fun normalizeStretchScreenPercent(percent: Int): Int {
        val clamped = percent.coerceIn(10, 200)
        return ((clamped + 5) / 10) * 10
    }

    private fun getPrefs(context: Context): SharedPreferences {
        return context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    fun saveFps(context: Context, fps: Int) {
        getPrefs(context).edit().putInt(KEY_FPS, fps).apply()
    }

    fun loadFps(context: Context, defaultFps: Int = 120): Int {
        return getPrefs(context).getInt(KEY_FPS, defaultFps)
    }

    fun saveSelectedPreset(context: Context, presetId: String) {
        getPrefs(context).edit().putString(KEY_SELECTED_PRESET, presetId).apply()
    }

    fun loadSelectedPreset(context: Context): String {
        return getPrefs(context).getString(KEY_SELECTED_PRESET, GamePresetManager.NONE) ?: GamePresetManager.NONE
    }

    fun saveToggle(context: Context, toggleId: String, isEnabled: Boolean) {
        getPrefs(context).edit().putBoolean("toggle_$toggleId", isEnabled).apply()
    }

    fun loadToggle(context: Context, toggleId: String, defaultValue: Boolean): Boolean {
        return getPrefs(context).getBoolean("toggle_$toggleId", defaultValue)
    }

    fun saveDropdown(context: Context, dropdownId: String, selectedOption: String) {
        getPrefs(context).edit().putString("dropdown_$dropdownId", selectedOption).apply()
    }

    fun loadDropdown(context: Context, dropdownId: String, defaultOption: String): String {
        return getPrefs(context).getString("dropdown_$dropdownId", defaultOption) ?: defaultOption
    }

    fun saveCustomFlags(context: Context, flags: List<FlagItem>) {
        val array = JSONArray()
        for (f in flags) {
            val obj = JSONObject()
            obj.put("name", f.name)
            obj.put("type", f.type.name)
            obj.put("value", f.value)
            obj.put("isEnabled", f.isEnabled)
            obj.put("category", f.category)
            array.put(obj)
        }
        getPrefs(context).edit().putString(KEY_CUSTOM_FLAGS, array.toString()).apply()
    }

    fun loadCustomFlags(context: Context): MutableList<FlagItem>? {
        val jsonStr = getPrefs(context).getString(KEY_CUSTOM_FLAGS, null) ?: return null
        return try {
            val array = JSONArray(jsonStr)
            val list = mutableListOf<FlagItem>()
            for (i in 0 until array.length()) {
                val obj = array.getJSONObject(i)
                val name = obj.getString("name")
                val typeName = obj.optString("type", FlagType.BOOLEAN.name)
                val type = try { FlagType.valueOf(typeName) } catch (e: Exception) { FlagType.BOOLEAN }
                val value = obj.optString("value", "true")
                val isEnabled = obj.optBoolean("isEnabled", true)
                val category = obj.optString("category", "Custom")
                list.add(FlagItem(name, type, value, isEnabled, category))
            }
            list
        } catch (e: Exception) {
            null
        }
    }

    fun saveLicenseKey(context: Context, key: String) {
        getPrefs(context).edit().putString(KEY_LICENSE_KEY, key).apply()
    }

    fun loadLicenseKey(context: Context): String {
        return getPrefs(context).getString(KEY_LICENSE_KEY, "") ?: ""
    }

    fun saveLicensePlan(context: Context, plan: String) {
        getPrefs(context).edit().putString(KEY_LICENSE_PLAN, plan).apply()
    }

    fun loadLicensePlan(context: Context): String {
        return getPrefs(context).getString(KEY_LICENSE_PLAN, "Free") ?: "Free"
    }

    fun saveAppTheme(context: Context, theme: String) {
        getPrefs(context).edit().putString(KEY_APP_THEME, theme).apply()
    }

    fun loadAppTheme(context: Context): String {
        return getPrefs(context).getString(KEY_APP_THEME, "Dark") ?: "Dark"
    }

    fun saveAppLanguage(context: Context, language: String) {
        getPrefs(context).edit().putString(KEY_APP_LANGUAGE, language).apply()
    }

    fun loadAppLanguage(context: Context): String {
        return getPrefs(context).getString(KEY_APP_LANGUAGE, "English") ?: "English"
    }

    fun savePreferredRobloxApp(context: Context, appType: String) {
        getPrefs(context).edit().putString("key_preferred_roblox_app", appType).apply()
    }

    fun loadPreferredRobloxApp(context: Context): String {
        return getPrefs(context).getString("key_preferred_roblox_app", "global") ?: "global"
    }

    fun saveStretchScreenPercent(context: Context, percent: Int) {
        getPrefs(context).edit()
            .putInt(KEY_STRETCH_SCREEN_PERCENT, normalizeStretchScreenPercent(percent))
            .apply()
    }

    fun loadStretchScreenPercent(context: Context, defaultPercent: Int = 100): Int {
        return getPrefs(context)
            .getInt(KEY_STRETCH_SCREEN_PERCENT, defaultPercent)
            .let(::normalizeStretchScreenPercent)
    }

    fun saveShaderPackage(context: Context, shaderPackage: ShaderPackage) {
        getPrefs(context).edit()
            .putString(KEY_SHADER_PACKAGE, shaderPackage.stableKey)
            .apply()
    }

    fun loadShaderPackage(context: Context): ShaderPackage =
        ShaderPackage.fromStableKey(
            getPrefs(context).getString(KEY_SHADER_PACKAGE, ShaderPackage.DEFAULT.stableKey)
        )

    fun saveShaderWetFloor(context: Context, enabled: Boolean) {
        getPrefs(context).edit().putBoolean(KEY_SHADER_WET_FLOOR, enabled).apply()
    }

    fun loadShaderWetFloor(context: Context): Boolean =
        getPrefs(context).getBoolean(KEY_SHADER_WET_FLOOR, false)
}
