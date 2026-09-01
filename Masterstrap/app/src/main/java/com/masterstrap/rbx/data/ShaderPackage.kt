package com.masterstrap.rbx.data

/** Stable launcher-to-native shader identifiers. */
enum class ShaderPackage(val stableKey: String) {
    DEFAULT("default"),
    DAYTIME("daytime"),
    RTX_ULTRA("rtx_ultra"),
    SUNSET("sunset"),
    NIGHT("night"),
    CLOUDY("cloudy"),
    DESERT("desert"),
    ARCTIC("arctic"),
    BLOOD_MOON("blood_moon"),
    CYBERPUNK("cyberpunk"),
    CHERRY_BLOSSOM("cherry_blossom"),
    GHIBLI_ANIME("ghibli_anime"),
    SYNTHWAVE("synthwave"),
    NOIR("noir");

    companion object {
        val ordered: List<ShaderPackage> = entries

        fun fromStableKey(value: String?): ShaderPackage =
            entries.firstOrNull { it.stableKey == value } ?: DEFAULT
    }
}
