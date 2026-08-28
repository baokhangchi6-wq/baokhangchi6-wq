package com.masterstrap.rbx.data

import android.content.Context
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.nio.charset.StandardCharsets

data class GamePresetItem(
    val id: String,
    val title: String,
    val assetJsonPath: String,
    val thumbPath: String? = null,
    val category: String,
    val isPopular: Boolean = false
)

object GamePresetManager {
    const val NONE = "none"

    private val PREFIXES = arrayOf(
        "DFInt", "DFFlag", "FFlag", "FInt", "DFString", "FString", "DFLog", "FLog",
        "SFFlag", "SFInt", "SFString", "SFLog", "FDouble", "DFDouble", "SFDouble"
    )

    private var windowsBlockedNames: Set<String>? = null

    fun getAllPresets(context: Context): List<GamePresetItem> {
        val list = mutableListOf<GamePresetItem>()

        // 1. POPULAR PRESETS (Strict Order with VIP Gold Crown)
        // Bloxfruits → The Strongest Battleground → Rivals → Volleyball Legends → Blade Ball
        list.add(
            GamePresetItem(
                id = "blox_fruits",
                title = "Blox Fruits",
                assetJsonPath = "game_presets/blox fruits.json",
                thumbPath = "preset_images/bloxfruits.png",
                category = "Popular",
                isPopular = true
            )
        )
        list.add(
            GamePresetItem(
                id = "the_strongest_battleground",
                title = "The Strongest Battleground",
                assetJsonPath = "game_presets/the strongest battleground.json",
                thumbPath = "preset_images/TheStrongestBattleground.png",
                category = "Popular",
                isPopular = true
            )
        )
        list.add(
            GamePresetItem(
                id = "rivals",
                title = "Rivals",
                assetJsonPath = "game_presets/rivals.json",
                thumbPath = "preset_images/rivals.png",
                category = "Popular",
                isPopular = true
            )
        )
        list.add(
            GamePresetItem(
                id = "volleyball_legend",
                title = "Volleyball Legends",
                assetJsonPath = "game_presets/volleyball legend.json",
                thumbPath = "preset_images/volleyball legend.png",
                category = "Popular",
                isPopular = true
            )
        )
        list.add(
            GamePresetItem(
                id = "blade_ball",
                title = "Blade Ball",
                assetJsonPath = "game_presets/blade ball.json",
                thumbPath = "preset_images/BladeBall.png",
                category = "Popular",
                isPopular = true
            )
        )

        // 2. HOT PRESETS (All other games from Preset folder)
        val hotGames = listOf(
            GamePresetItem("basketball_legends", "Basketball Legends", "game_presets/BasketballLegends.json", "preset_images/BasketballLegends.png", "Hot"),
            GamePresetItem("bedwars", "Bedwars", "game_presets/Bedwars.json", "preset_images/Bedwars.png", "Hot"),
            GamePresetItem("blue_lock_rivals", "Blue Lock Rivals", "game_presets/blue lock rivals.json", "preset_images/BlueLockRivals.png", "Hot"),
            GamePresetItem("boxing_beta", "Boxing Beta", "game_presets/Boxing Beta.json", "preset_images/Boxing Beta.png", "Hot"),
            GamePresetItem("cvr", "CVR", "game_presets/CVR.json", "preset_images/CVR.png", "Hot"),
            GamePresetItem("evade", "Evade", "game_presets/Evade.json", "preset_images/Evade.png", "Hot"),
            GamePresetItem("football_fusion_2", "Football Fusion 2", "game_presets/FootballFusion2.json", "preset_images/FootballFusion2.png", "Hot"),
            GamePresetItem("jujutsu_shenanigans", "Jujutsu Shenanigans", "game_presets/jujutsu shenanigans.json", "preset_images/jujutsu shenanigans.png", "Hot"),
            GamePresetItem("murder_mystery_2", "Murder Mystery 2", "game_presets/MurderMystery2.json", "preset_images/MurderMystery2.png", "Hot"),
            GamePresetItem("nfl_universe_football", "NFL Universe FootBall", "game_presets/NFL Universe FootBall.json", "preset_images/NFL Universe FootBall.png", "Hot"),
            GamePresetItem("pure_soccer", "Pure Soccer", "game_presets/PureSoccer.json", "preset_images/PureSoccer.png", "Hot"),
            GamePresetItem("timebomb_duels", "Timebomb Duels", "game_presets/timebomb duels.json", "preset_images/timebomb duels.png", "Hot"),
            GamePresetItem("touch_football", "Touch Football", "game_presets/touch football.json", "preset_images/touch football.png", "Hot"),
            GamePresetItem("tps_street_soccer", "TPS Street Soccer", "game_presets/TPSStreetSoccer.json", "preset_images/TPSStreetSoccer.png", "Hot"),
            GamePresetItem("volleyball_4_2", "Volleyball 4.2", "game_presets/volleyball 4.2.json", "preset_images/volleyball4.2.png", "Hot")
        )

        list.addAll(hotGames)
        return list
    }

    fun loadFlags(context: Context, presetId: String): Map<String, Any> {
        val flags = mutableMapOf<String, Any>()
        if (presetId == NONE) return flags

        val all = getAllPresets(context)
        val item = all.firstOrNull { it.id.equals(presetId, ignoreCase = true) } ?: return flags
        val path = item.assetJsonPath

        ensureWindowsBlocklist(context)
        try {
            context.assets.open(path).use { input ->
                val buffer = ByteArray(8192)
                val output = ByteArrayOutputStream()
                var read: Int
                while (input.read(buffer).also { read = it } >= 0) {
                    output.write(buffer, 0, read)
                }
                val json = JSONObject(output.toString(StandardCharsets.UTF_8.name()))
                val keys = json.keys()
                while (keys.hasNext()) {
                    val key = keys.next()
                    val value = json.opt(key)
                    if (value == null || value == JSONObject.NULL) continue
                    if (isWindowsOnly(key)) continue
                    flags[key] = value
                }
            }
        } catch (ignored: Exception) {}
        return flags
    }

    private fun isWindowsOnly(key: String): Boolean {
        val bare = stripPrefix(key)
        val blocked = windowsBlockedNames ?: return false
        return blocked.contains(key) || blocked.contains(bare)
    }

    private fun stripPrefix(key: String): String {
        for (prefix in PREFIXES) {
            if (key.startsWith(prefix)) return key.substring(prefix.length)
        }
        return key
    }

    private fun ensureWindowsBlocklist(context: Context) {
        if (windowsBlockedNames != null) return
        synchronized(this) {
            if (windowsBlockedNames != null) return
            val blocked = mutableSetOf<String>()
            try {
                context.assets.open("game_presets/FlagWindows.json").use { input ->
                    val buffer = ByteArray(8192)
                    val output = ByteArrayOutputStream()
                    var read: Int
                    while (input.read(buffer).also { read = it } >= 0) {
                        output.write(buffer, 0, read)
                    }
                    val json = JSONObject(output.toString(StandardCharsets.UTF_8.name()))
                    val keys = json.keys()
                    while (keys.hasNext()) {
                        val key = keys.next()
                        if (key.startsWith("_")) continue
                        blocked.add(key)
                        blocked.add(stripPrefix(key))
                    }
                }
            } catch (ignored: Exception) {}
            windowsBlockedNames = blocked
        }
    }
}
