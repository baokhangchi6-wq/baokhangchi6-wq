package com.masterstrap.rbx.data

/** Flags that the patched Roblox process must own exclusively. */
object PatchedFlagPolicy {
    const val FILE_CACHE_RESERVE_SIZE = "DFIntFileCacheReserveSize"

    private val prefixes = arrayOf(
        "DFString", "DFFloat", "DFInt", "DFFlag",
        "FString", "FFloat", "FInt", "FFlag"
    )

    fun isBlocked(name: String?): Boolean {
        if (RobloxGlobalSettingsPolicy.excludesFpsOverride(name)) return true
        if (name.isNullOrBlank()) return false
        val trimmed = name.trim()
        val bare = prefixes.firstOrNull {
            trimmed.startsWith(it) && trimmed.length > it.length
        }?.let { trimmed.substring(it.length) } ?: trimmed
        return bare.equals("FileCacheReserveSize", ignoreCase = true)
    }
}
