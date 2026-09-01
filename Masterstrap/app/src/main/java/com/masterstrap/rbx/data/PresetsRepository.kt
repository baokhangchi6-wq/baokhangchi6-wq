package com.masterstrap.rbx.data

import android.content.Context
import com.masterstrap.rbx.R

data class PresetToggle(
    val id: String,
    var title: String,
    var description: String,
    var isEnabled: Boolean,
    var category: String,
    val flags: Map<String, Any>,
    var isLocked: Boolean = false,
    var hasCrown: Boolean = false
)

data class PresetDropdown(
    val id: String,
    var title: String,
    var description: String,
    val options: List<String>,
    var selectedOption: String,
    var category: String,
    val flagMapper: (String) -> Map<String, Any>
)

sealed class PresetItemUI {
    data class Header(val title: String, val hasCrown: Boolean = false) : PresetItemUI()
    data class Toggle(val toggle: PresetToggle) : PresetItemUI()
    data class Dropdown(val dropdown: PresetDropdown) : PresetItemUI()
    data class FpsSlider(
        var title: String = "Frame rate",
        var description: String = "Choose an FPS level that suits the display and device performance. Higher levels can make motion feel smoother."
    ) : PresetItemUI()
}

object PresetsRepository {

    fun getDefaultEditorFlags(): MutableList<FlagItem> {
        // Structured controls below own the built-in flags. Starting the custom
        // editor with duplicate enabled entries made an OFF toggle ineffective.
        return mutableListOf()
    }

    // Dropdowns for FastFlag tab
    val msaaDropdown = PresetDropdown(
        id = "msaa_quality",
        title = "Anti-aliasing quality (MSAA)",
        description = "Overrides Roblox MSAA with an Android-compatible FastFlag.",
        options = listOf("Automatic", "Off / 1x", "2x", "4x", "8x"),
        selectedOption = "Automatic",
        category = "Graphics quality",
        flagMapper = { quality ->
            when (quality) {
                "Off / 1x", "Off" -> mapOf("FIntDebugForceMSAASamples" to 1)
                "2x" -> mapOf("FIntDebugForceMSAASamples" to 2)
                "4x" -> mapOf("FIntDebugForceMSAASamples" to 4)
                "8x" -> mapOf("FIntDebugForceMSAASamples" to 8)
                else -> emptyMap()
            }
        }
    )

    val frmDropdown = PresetDropdown(
        id = "frm_quality",
        title = "FRM quality override",
        description = "Selects a fixed Roblox render-quality level from 1 to 21.",
        options = listOf("Automatic", "Level 1 (lowest)", "Level 10 (balanced)", "Level 21 (highest)"),
        selectedOption = "Automatic",
        category = "Graphics quality",
        flagMapper = { level ->
            when {
                level.contains("1 (lowest)") || level == "Lowest" -> mapOf("DFIntDebugFRMQualityLevelOverride" to 1)
                level.contains("10 (balanced)") || level == "Balanced" -> mapOf("DFIntDebugFRMQualityLevelOverride" to 10)
                level.contains("21 (highest)") || level == "Highest" -> mapOf("DFIntDebugFRMQualityLevelOverride" to 21)
                else -> emptyMap()
            }
        }
    )

    val renderingModeDropdown = PresetDropdown(
        id = "rendering_mode",
        title = "Rendering mode",
        description = "Choose the renderer for your next launch. The other preference is disabled; unsupported drivers may fall back safely.",
        options = listOf("Automatic", "OpenGL", "Vulkan"),
        selectedOption = "Automatic",
        category = "Graphics quality",
        flagMapper = RendererModePolicy::flags
    )

    val dynamicResolutionDropdown = PresetDropdown(
        id = "dynamic_resolution",
        title = "Dynamic render resolution",
        description = "Caps the internal 16:9 render pixel budget while keeping the Android UI at native size.",
        options = listOf("Automatic", "144p", "240p", "360p", "480p", "720p", "1080p"),
        selectedOption = "Automatic",
        category = "Graphics quality",
        flagMapper = { res ->
            when (res) {
                "144p" -> mapOf("DFIntDebugDynamicRenderKiloPixels" to 36)
                "240p" -> mapOf("DFIntDebugDynamicRenderKiloPixels" to 102)
                "360p" -> mapOf("DFIntDebugDynamicRenderKiloPixels" to 230)
                "480p" -> mapOf("DFIntDebugDynamicRenderKiloPixels" to 410)
                "720p" -> mapOf("DFIntDebugDynamicRenderKiloPixels" to 922)
                "1080p" -> mapOf("DFIntDebugDynamicRenderKiloPixels" to 2074)
                else -> emptyMap()
            }
        }
    )

    // Master Switch toggle
    val masterSwitchToggle = PresetToggle(
        "allow_manage_preferences",
        "Apply Roblox optimizations",
        "Applies your selected optimizations whenever you save and launch. Turn this off to start Roblox with its normal configuration.",
        true,
        "Master Switch",
        emptyMap()
    )

    // Tab 1: Full FastFlag UI Items (Localized using context)
    fun getFastFlagUIItems(context: Context): List<PresetItemUI> {
        masterSwitchToggle.title = context.getString(R.string.ff_master_switch_title)
        masterSwitchToggle.description = context.getString(R.string.ff_master_switch_desc)

        msaaDropdown.title = context.getString(R.string.ff_msaa_title)
        msaaDropdown.description = context.getString(R.string.ff_msaa_desc)

        frmDropdown.title = context.getString(R.string.ff_frm_title)
        frmDropdown.description = context.getString(R.string.ff_frm_desc)

        renderingModeDropdown.title = context.getString(R.string.ff_rendering_mode_title)
        renderingModeDropdown.description = context.getString(R.string.ff_rendering_mode_desc)

        dynamicResolutionDropdown.title = context.getString(R.string.ff_dynamic_res_title)
        dynamicResolutionDropdown.description = context.getString(R.string.ff_dynamic_res_desc)

        return listOf(
            PresetItemUI.Toggle(masterSwitchToggle),

            // Section 1: Graphics quality
            PresetItemUI.Header(context.getString(R.string.section_graphics_quality)),
            PresetItemUI.Dropdown(msaaDropdown),
            PresetItemUI.Dropdown(frmDropdown),
            PresetItemUI.Dropdown(renderingModeDropdown),
            PresetItemUI.Dropdown(dynamicResolutionDropdown),

            // Section 2: Verified Roblox preferences
            PresetItemUI.Header(context.getString(R.string.section_verified_preferences)),
            PresetItemUI.Toggle(
                PresetToggle(
                    "force_lowest_graphics",
                    context.getString(R.string.ff_force_lowest_title),
                    context.getString(R.string.ff_force_lowest_desc),
                    false,
                    context.getString(R.string.section_verified_preferences),
                    mapOf("DFIntDebugFRMQualityLevelOverride" to 1)
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "reduced_motion",
                    context.getString(R.string.ff_reduced_motion_title),
                    context.getString(R.string.ff_reduced_motion_desc),
                    false,
                    context.getString(R.string.section_verified_preferences),
                    // This is a UserGameSettings property, not a reliable
                    // Android FastFlag. ConfigExporter writes ReducedMotion to
                    // GlobalBasicSettings_13.xml for both ON and OFF states.
                    emptyMap()
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "haptics",
                    context.getString(R.string.ff_haptics_title),
                    context.getString(R.string.ff_haptics_desc),
                    true,
                    context.getString(R.string.section_verified_preferences),
                    // HapticStrength is exported as a real Roblox preference;
                    // do not submit an unverified registry alias.
                    emptyMap()
                )
            ),

            // Section 3: Mobile performance
            PresetItemUI.Header(context.getString(R.string.section_mobile_performance)),
            PresetItemUI.FpsSlider(
                title = context.getString(R.string.ff_fps_title),
                description = context.getString(R.string.ff_fps_desc)
            ),
            // FPS is automatic via GlobalBasicSettings, not a FastFlag bundle.
            PresetItemUI.Toggle(
                PresetToggle(
                    "fps_boost",
                    context.getString(R.string.ff_fps_boost_title),
                    context.getString(R.string.ff_fps_boost_desc),
                    true,
                    context.getString(R.string.section_mobile_performance),
                    mapOf(
                        "DFIntDebugFRMQualityLevelOverride" to 1,
                        "FIntDebugForceMSAASamples" to 1,
                        "FFlagAdaptiveMeshCacheSizeForAndroid" to "True",
                        "DFIntRenderPostFxBasePixelCount" to 0,
                        "FIntFRMMaxGrassDistance" to 0,
                        "FIntFRMMinGrassDistance" to 0
                    )
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "low_poly",
                    context.getString(R.string.ff_low_poly_title),
                    context.getString(R.string.ff_low_poly_desc),
                    false,
                    context.getString(R.string.section_mobile_performance),
                    mapOf(
                        "DFIntCSGLevelOfDetailSwitchingDistanceL12" to 0,
                        "DFIntCSGLevelOfDetailSwitchingDistanceL23" to 0,
                        "DFIntCSGLevelOfDetailSwitchingDistanceL34" to 0
                    )
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "low_texture_mode",
                    context.getString(R.string.ff_low_texture_title),
                    context.getString(R.string.ff_low_texture_desc),
                    false,
                    context.getString(R.string.section_mobile_performance),
                    mapOf(
                        "DFIntDebugTextureManagerSkipMips" to 12,
                        "DFIntDebugLimitMinTextureResolutionWhenSkipMips" to 64
                    )
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "android_mesh_cache",
                    context.getString(R.string.ff_android_mesh_title),
                    context.getString(R.string.ff_android_mesh_desc),
                    true,
                    context.getString(R.string.section_mobile_performance),
                    mapOf("FFlagAdaptiveMeshCacheSizeForAndroid" to "True")
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "mesh_preloading",
                    context.getString(R.string.ff_mesh_preload_title),
                    context.getString(R.string.ff_mesh_preload_desc),
                    true,
                    context.getString(R.string.section_mobile_performance),
                    mapOf("DFFlagEnableMeshPreloading2" to "True")
                )
            ),
            // Section 4: Rendering and visual effects
            PresetItemUI.Header(context.getString(R.string.section_rendering_fx)),
            PresetItemUI.Toggle(
                PresetToggle(
                    "disable_shadows",
                    context.getString(R.string.ff_disable_shadows_title),
                    context.getString(R.string.ff_disable_shadows_desc),
                    false,
                    context.getString(R.string.section_rendering_fx),
                    mapOf(
                        "FFlagDebugForceDisableShadows" to true,
                        "FIntRenderShadowIntensity" to 0
                    )
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "disable_post_fx",
                    context.getString(R.string.ff_disable_postfx_title),
                    context.getString(R.string.ff_disable_postfx_desc),
                    false,
                    context.getString(R.string.section_rendering_fx),
                    mapOf("DFIntRenderPostFxBasePixelCount" to 0)
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "disable_grass",
                    context.getString(R.string.ff_disable_grass_title),
                    context.getString(R.string.ff_disable_grass_desc),
                    false,
                    context.getString(R.string.section_rendering_fx),
                    mapOf(
                        "FIntFRMMinGrassDistance" to 0,
                        "FIntFRMMaxGrassDistance" to 0,
                        "FIntRenderGrassDetailStrands" to 0,
                        "FIntRenderGrassHeightScaler" to 0
                    )
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "gray_sky",
                    context.getString(R.string.ff_skygray_title),
                    context.getString(R.string.ff_skygray_desc),
                    false,
                    context.getString(R.string.section_rendering_fx),
                    mapOf("FFlagDebugSkyGray" to "True")
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "display_fps",
                    context.getString(R.string.ff_display_fps_title),
                    context.getString(R.string.ff_display_fps_desc),
                    true,
                    context.getString(R.string.section_rendering_fx),
                    mapOf("FFlagDebugDisplayFPS" to "True")
                )
            ),

            // Section 5: Interface and camera
            PresetItemUI.Header(context.getString(R.string.section_interface_camera)),
            PresetItemUI.Toggle(
                PresetToggle(
                    "disable_gui_blur",
                    context.getString(R.string.ff_disable_gui_blur_title),
                    context.getString(R.string.ff_disable_gui_blur_desc),
                    true,
                    context.getString(R.string.section_interface_camera),
                    mapOf(
                        "FIntRobloxGuiBlurIntensity" to 0,
                        "FFlagDisablePostFx" to true
                    )
                )
            )
        )
    }

    // Tab 2: Full FastFlag Settings Items (Localized using context)
    fun getFastFlagSettingsUIItems(context: Context): List<PresetItemUI> {
        return listOf(
            // Section 1: Graphics & frame rate
            PresetItemUI.Header(context.getString(R.string.section_graphics_framerate)),
            PresetItemUI.Toggle(
                PresetToggle(
                    "automatic_drs",
                    context.getString(R.string.ffs_automatic_drs_title),
                    context.getString(R.string.ffs_automatic_drs_desc),
                    true,
                    context.getString(R.string.section_graphics_framerate),
                    mapOf("FFlagAutomaticDRS" to true)
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "animation_lod",
                    context.getString(R.string.ffs_animation_lod_title),
                    context.getString(R.string.ffs_animation_lod_desc),
                    true,
                    context.getString(R.string.section_graphics_framerate),
                    mapOf("FFlagAnimationLodBoneEnabled" to true)
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "drs_basic_management",
                    context.getString(R.string.ffs_drs_basic_title),
                    context.getString(R.string.ffs_drs_basic_desc),
                    true,
                    context.getString(R.string.section_graphics_framerate),
                    mapOf("FFlagDRSBasicManagement" to true)
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "drs_gpu_time",
                    context.getString(R.string.ffs_drs_gpu_time_title),
                    context.getString(R.string.ffs_drs_gpu_time_desc),
                    true,
                    context.getString(R.string.section_graphics_framerate),
                    mapOf("FFlagAutomaticDRSUseGpuTime" to true)
                )
            ),

            // Section 2: Network
            PresetItemUI.Header(context.getString(R.string.section_network)),
            PresetItemUI.Toggle(
                PresetToggle(
                    "batch_net_asset",
                    context.getString(R.string.ffs_batch_net_title),
                    context.getString(R.string.ffs_batch_net_desc),
                    true,
                    context.getString(R.string.section_network),
                    mapOf("FFlagBatchNetAssetJoinBlobEnable" to true)
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "desync",
                    context.getString(R.string.ffs_desync_title),
                    context.getString(R.string.ffs_desync_desc),
                    false,
                    context.getString(R.string.section_network),
                    mapOf(
                        "DFFlagClampIncomingReplicationLag" to true,
                        "DFIntReplicationLagCompensation" to 1,
                        "DFIntClientPacketMaxDelayMs" to 1,
                        "DFIntNetworkLatencyTolerance" to 1,
                        "DFIntDataSenderRate" to 38760,
                        "DFIntS2PhysicsSenderRate" to 38760,
                        "DFIntDataSenderMaxBandwidthBps" to 100000000
                    ),
                    hasCrown = true
                )
            ),

            // Section 3: Power & game session
            PresetItemUI.Header(context.getString(R.string.section_power_session)),
            PresetItemUI.Toggle(
                PresetToggle(
                    "android_power_manager",
                    context.getString(R.string.ffs_power_mgr_title),
                    context.getString(R.string.ffs_power_mgr_desc),
                    true,
                    context.getString(R.string.section_power_session),
                    mapOf("FFlagEnableAndroidPowerManager" to true)
                )
            ),
            // Section 4: Memory & assets
            PresetItemUI.Header(context.getString(R.string.section_memory_assets)),
            PresetItemUI.Toggle(
                PresetToggle(
                    "unified_mesh_cache",
                    context.getString(R.string.ffs_unified_mesh_title),
                    context.getString(R.string.ffs_unified_mesh_desc),
                    true,
                    context.getString(R.string.section_memory_assets),
                    mapOf("FFlagUnifiedMeshCacheSizeComputation" to true)
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "no_texture",
                    context.getString(R.string.ffs_no_texture_title),
                    context.getString(R.string.ffs_no_texture_desc),
                    false,
                    context.getString(R.string.section_memory_assets),
                    // The empty string is the requested value. It must remain a
                    // present JSON entry instead of being treated as missing.
                    mapOf(
                        "FStringTerrainMaterialTable2022" to "",
                        "FStringTerrainMaterialTablePre2022" to "",
                        "FStringPartTexturePackTable2022" to "",
                        "FStringPartTexturePackTablePre2022" to "",
                        "FFlagDebugDisableTelemetryV2Counter" to true,
                        "FFlagDebugDisableTelemetryV2Event" to true
                    )
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "animation_clip_cache",
                    context.getString(R.string.ffs_anim_cache_title),
                    context.getString(R.string.ffs_anim_cache_desc),
                    true,
                    context.getString(R.string.section_memory_assets),
                    mapOf("FFlagAnimationClipMemCacheEnabled" to true)
                )
            ),
            PresetItemUI.Header(context.getString(R.string.section_interface_camera)),
            PresetItemUI.Toggle(
                PresetToggle(
                    "hide_player_names",
                    context.getString(R.string.ffs_hide_player_names_title),
                    context.getString(R.string.ffs_hide_player_names_desc),
                    false,
                    context.getString(R.string.section_interface_camera),
                    // A UserGameSettings XML preference, not a fabricated FFlag.
                    emptyMap()
                )
            )
        )
    }

    // Tab 4: Game Booster with 2 Distinct Boost Flows (Safe Boost vs Turbo Boost 👑)
    fun getGameModeUIItems(context: Context): List<PresetItemUI> {
        return listOf(
            // LUỒNG 1: Safe Boost (Non-Root GitHub Research)
            PresetItemUI.Header(context.getString(R.string.section_safe_boost)),
            PresetItemUI.Toggle(
                PresetToggle(
                    "safe_ram_cleanup",
                    context.getString(R.string.gm_safe_ram_title),
                    context.getString(R.string.gm_safe_ram_desc),
                    true,
                    context.getString(R.string.section_safe_boost),
                    emptyMap()
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "safe_network_opt",
                    context.getString(R.string.gm_safe_network_title),
                    context.getString(R.string.gm_safe_network_desc),
                    true,
                    context.getString(R.string.section_safe_boost),
                    emptyMap()
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "safe_power_opt",
                    context.getString(R.string.gm_safe_power_title),
                    context.getString(R.string.gm_safe_power_desc),
                    true,
                    context.getString(R.string.section_safe_boost),
                    emptyMap()
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "safe_thermal_guard",
                    context.getString(R.string.gm_safe_thermal_title),
                    context.getString(R.string.gm_safe_thermal_desc),
                    true,
                    context.getString(R.string.section_safe_boost),
                    emptyMap()
                )
            ),

            // LUỒNG 2: Turbo Boost 👑 (Existing Full Turbo Features with Crown & Ad Gate)
            PresetItemUI.Header(context.getString(R.string.section_turbo_boost), hasCrown = true),
            PresetItemUI.Toggle(
                PresetToggle(
                    "game_mode_master",
                    context.getString(R.string.gm_master_title),
                    context.getString(R.string.gm_master_desc),
                    false,
                    context.getString(R.string.section_turbo_boost),
                    emptyMap(),
                    hasCrown = true
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "system_perf_boost",
                    context.getString(R.string.gm_system_perf_title),
                    context.getString(R.string.gm_system_perf_desc),
                    true,
                    context.getString(R.string.section_turbo_boost),
                    emptyMap(),
                    hasCrown = true
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "cpu_priority",
                    context.getString(R.string.gm_cpu_priority_title),
                    context.getString(R.string.gm_cpu_priority_desc),
                    true,
                    context.getString(R.string.section_turbo_boost),
                    emptyMap(),
                    hasCrown = true
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "smart_ram_cleanup",
                    context.getString(R.string.gm_smart_ram_title),
                    context.getString(R.string.gm_smart_ram_desc),
                    true,
                    context.getString(R.string.section_turbo_boost),
                    emptyMap(),
                    hasCrown = true
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "faster_game_launch",
                    context.getString(R.string.gm_fast_launch_title),
                    context.getString(R.string.gm_fast_launch_desc),
                    true,
                    context.getString(R.string.section_turbo_boost),
                    emptyMap(),
                    hasCrown = true
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "keep_display_active",
                    context.getString(R.string.gm_keep_display_title),
                    context.getString(R.string.gm_keep_display_desc),
                    true,
                    context.getString(R.string.section_turbo_boost),
                    emptyMap(),
                    hasCrown = true
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "unrestricted_power",
                    context.getString(R.string.gm_unrestricted_power_title),
                    context.getString(R.string.gm_unrestricted_power_desc),
                    true,
                    context.getString(R.string.section_turbo_boost),
                    emptyMap(),
                    hasCrown = true
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "turbo_wifi_latency",
                    context.getString(R.string.gm_turbo_wifi_title),
                    context.getString(R.string.gm_turbo_wifi_desc),
                    true,
                    context.getString(R.string.section_turbo_boost),
                    emptyMap(),
                    hasCrown = true
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "turbo_thermal_guard",
                    context.getString(R.string.gm_turbo_thermal_title),
                    context.getString(R.string.gm_turbo_thermal_desc),
                    true,
                    context.getString(R.string.section_turbo_boost),
                    emptyMap(),
                    hasCrown = true
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "turbo_refresh_contract",
                    context.getString(R.string.gm_turbo_refresh_title),
                    context.getString(R.string.gm_turbo_refresh_desc),
                    true,
                    context.getString(R.string.section_turbo_boost),
                    emptyMap(),
                    hasCrown = true
                )
            ),
            PresetItemUI.Toggle(
                PresetToggle(
                    "turbo_sustained_performance",
                    context.getString(R.string.gm_turbo_sustained_title),
                    context.getString(R.string.gm_turbo_sustained_desc),
                    false,
                    context.getString(R.string.section_turbo_boost),
                    emptyMap(),
                    hasCrown = true
                )
            )
        )
    }

    fun getGameModeSettings(context: Context): List<PresetToggle> {
        val list = mutableListOf<PresetToggle>()
        for (item in getGameModeUIItems(context)) {
            if (item is PresetItemUI.Toggle) list.add(item.toggle)
        }
        return list
    }
}
