package com.masterstrap.rbx.data

/** Explicit modes own both preferences; Automatic removes stale editor copies. */
object RendererModePolicy {
    const val OPENGL = "FFlagDebugGraphicsPreferOpenGL"
    const val VULKAN = "FFlagDebugGraphicsPreferVulkan"
    val ownedKeys = setOf(OPENGL, VULKAN)

    fun flags(mode: String): Map<String, Boolean> = when (mode) {
        "OpenGL" -> mapOf(OPENGL to true, VULKAN to false)
        "Vulkan" -> mapOf(OPENGL to false, VULKAN to true)
        else -> emptyMap()
    }
}
