package com.masterstrap.rbx.data

/** Shared export decision for the XML preference and the final flag override. */
object RobloxGraphicsPolicy {
    fun forceLowest(toggles: List<PresetToggle>): Boolean {
        val managePreferences = toggles.firstOrNull {
            it.id == "allow_manage_preferences"
        }?.isEnabled ?: true
        return managePreferences && toggles.any {
            it.id == "force_lowest_graphics" && it.isEnabled
        }
    }

    fun xmlProperties(enabled: Boolean): String {
        // A comment carries the explicit toggle state without adding an
        // unsupported property to Roblox's UserGameSettings schema.
        val marker = "<!-- MasterstrapForceLowestGraphics: $enabled -->"
        if (!enabled) return marker
        return """
            $marker
            <token name="GraphicsOptimizationMode">1</token>
            <int name="GraphicsQualityLevel">1</int>
            <int name="QualityResetLevel">1</int>
            <token name="SavedQualityLevel">1</token>
            <bool name="MaxQualityEnabled">false</bool>
        """.trimIndent()
    }
}
