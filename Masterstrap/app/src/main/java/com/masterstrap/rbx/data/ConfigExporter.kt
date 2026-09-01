package com.masterstrap.rbx.data

import android.content.ContentUris
import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.io.OutputStream

object ConfigExporter {
    private const val TAG = "ConfigExporter"
    private const val MASTERSTRAP_DIR = "Masterstrap"
    private const val CLIENT_SETTINGS_FILE = "ClientAppSettings.json"
    private const val GLOBAL_SETTINGS_FILE = "GlobalBasicSettings_13.xml"
    const val TURBO_PROFILE_FILE = "MasterstrapTurboProfile.json"
    const val SHADER_PROFILE_FILE = "MasterstrapShaderProfile.json"
    private const val ANDROID_LAUNCH_LOG = "Masterstrap_Android_Launch.log.txt"
    private const val NATIVE_BRIDGE_LOG = "Masterstrap_Android_NativeBridge.log.txt"
    private const val PATCHED_CLIENT_LOG = "Masterstrap_RobloxPatched.log.txt"
    private val MANAGED_OUTPUTS = setOf(
        CLIENT_SETTINGS_FILE,
        GLOBAL_SETTINGS_FILE,
        ANDROID_LAUNCH_LOG,
        NATIVE_BRIDGE_LOG,
        PATCHED_CLIENT_LOG,
        TURBO_PROFILE_FILE,
        SHADER_PROFILE_FILE
    )

    private val ROBLOX_PACKAGES = listOf(
        "com.roblox.client",
        "com.roblox.client.vnggames"
    )

    fun exportConfiguration(
        context: Context,
        editorFlags: List<FlagItem>,
        presetToggles: List<PresetToggle>,
        dropdowns: List<PresetDropdown>,
        selectedGamePreset: String,
        fpsCap: Int,
        gameModeEnabled: Boolean
    ): Boolean {
        return try {
            cleanupDuplicateOutputs(context)
            val jsonContent = generateClientAppSettingsJson(
                context,
                editorFlags,
                presetToggles,
                dropdowns,
                selectedGamePreset,
                fpsCap,
                gameModeEnabled
            )
            val managePreferences = presetToggles.firstOrNull {
                it.id == "allow_manage_preferences"
            }?.isEnabled ?: true
            val xmlContent = generateGlobalBasicSettingsXml(
                fpsTarget = RobloxGlobalSettingsPolicy.normalizeFpsTarget(fpsCap),
                playerNamesEnabled = RobloxGlobalSettingsPolicy.playerNamesEnabled(presetToggles),
                forceLowestGraphics = RobloxGraphicsPolicy.forceLowest(presetToggles),
                reducedMotion = managePreferences && presetToggles.any {
                    it.id == "reduced_motion" && it.isEnabled
                },
                hapticsEnabled = !managePreferences || (presetToggles
                    .firstOrNull { it.id == "haptics" }?.isEnabled ?: true)
            )
            val turboProfileContent = generateTurboProfileJson(
                context, presetToggles,
                RobloxGlobalSettingsPolicy.normalizeFpsTarget(fpsCap)
            )
            Log.i(TAG, "graphics_policy=launch_toggle force_lowest=" +
                RobloxGraphicsPolicy.forceLowest(presetToggles))
            val shaderProfileContent = generateShaderProfileJson(context)

            // 1. Primary: Save via MediaStore API into /sdcard/Download/Masterstrap/ (Standard on Android 10+ / Q to 15+)
            // MuMu exposes Download as a writable shared folder but its
            // MediaStore provider can point at a different numbered backing
            // file. Prefer a real canonical overwrite when raw access is
            // available; stock scoped-storage devices fall back to MediaStore.
            val jsonStoredDirect = writeCanonicalDownloadFileDirect(
                CLIENT_SETTINGS_FILE, jsonContent
            )
            val xmlStoredDirect = writeCanonicalDownloadFileDirect(
                GLOBAL_SETTINGS_FILE, xmlContent
            )
            val turboStoredDirect = writeCanonicalDownloadFileDirect(
                TURBO_PROFILE_FILE, turboProfileContent
            )
            val shaderStoredDirect = writeCanonicalDownloadFileDirect(
                SHADER_PROFILE_FILE, shaderProfileContent
            )
            val jsonStored = jsonStoredDirect || writeViaMediaStore(
                context, CLIENT_SETTINGS_FILE, jsonContent, "application/json"
            )
            val xmlStored = xmlStoredDirect || writeViaMediaStore(
                context, GLOBAL_SETTINGS_FILE, xmlContent, "text/xml"
            )
            val turboStored = turboStoredDirect || writeViaMediaStore(
                context, TURBO_PROFILE_FILE, turboProfileContent, "application/json"
            )
            val shaderStored = shaderStoredDirect || writeViaMediaStore(
                context, SHADER_PROFILE_FILE, shaderProfileContent, "application/json"
            )

            // 2. Direct File I/O for Android 9 & direct access fallback
            val sharedDirs = listOfNotNull(
                // On Android 10+, do not race a successful MediaStore write
                // with raw I/O to the very same Download row. That race was
                // responsible for the repeated " (n)" output files.
                File(
                    Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
                    MASTERSTRAP_DIR
                ).takeUnless {
                    Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q &&
                        jsonStored && xmlStored && turboStored && shaderStored
                },
                File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOCUMENTS), MASTERSTRAP_DIR),
                File(Environment.getExternalStorageDirectory(), MASTERSTRAP_DIR)
            ).distinctBy { it.absolutePath.lowercase() }
            for (dir in sharedDirs) {
                try {
                    if (!dir.exists()) dir.mkdirs()
                    File(dir, CLIENT_SETTINGS_FILE).writeText(jsonContent, Charsets.UTF_8)
                    File(dir, GLOBAL_SETTINGS_FILE).writeText(xmlContent, Charsets.UTF_8)
                    File(dir, TURBO_PROFILE_FILE).writeText(turboProfileContent, Charsets.UTF_8)
                    File(dir, SHADER_PROFILE_FILE).writeText(shaderProfileContent, Charsets.UTF_8)
                    Log.i(TAG, "Direct export successful to: ${dir.absolutePath}")
                } catch (e: Exception) {
                    Log.w(TAG, "Direct export skipped for ${dir.absolutePath}: ${e.message}")
                }
            }

            // 3. Roblox App Data Directories
            for (pkg in ROBLOX_PACKAGES) {
                try {
                    val clientSettingsDirs = listOf(
                        File(Environment.getExternalStorageDirectory(), "Android/data/$pkg/files/ClientSettings"),
                        File(Environment.getExternalStorageDirectory(), "Android/data/$pkg/files/exe/ClientSettings")
                    )
                    for (csDir in clientSettingsDirs) {
                        if (csDir.exists() || csDir.mkdirs()) {
                            File(csDir, CLIENT_SETTINGS_FILE).writeText(jsonContent, Charsets.UTF_8)
                        }
                    }

                    val appDataDirs = listOf(
                        File(Environment.getExternalStorageDirectory(), "Android/data/$pkg/files/appData"),
                        File(Environment.getExternalStorageDirectory(), "Android/data/$pkg/files")
                    )
                    for (adDir in appDataDirs) {
                        if (adDir.exists() || adDir.mkdirs()) {
                            File(adDir, GLOBAL_SETTINGS_FILE).writeText(xmlContent, Charsets.UTF_8)
                        }
                    }
                } catch (ignored: Exception) {}
            }

            // 4. App Internal and External Files Staging
            val extDir = context.getExternalFilesDir(MASTERSTRAP_DIR)
            if (extDir != null) {
                if (!extDir.exists()) extDir.mkdirs()
                File(extDir, CLIENT_SETTINGS_FILE).writeText(jsonContent, Charsets.UTF_8)
                File(extDir, GLOBAL_SETTINGS_FILE).writeText(xmlContent, Charsets.UTF_8)
            }

            val internalClientSettingsDir = File(context.filesDir, "ClientSettings")
            if (!internalClientSettingsDir.exists()) internalClientSettingsDir.mkdirs()
            File(internalClientSettingsDir, CLIENT_SETTINGS_FILE).writeText(jsonContent, Charsets.UTF_8)

            val injectStagingDir = File(context.filesDir, "Inject")
            if (!injectStagingDir.exists()) injectStagingDir.mkdirs()
            File(injectStagingDir, CLIENT_SETTINGS_FILE).writeText(jsonContent, Charsets.UTF_8)
            File(injectStagingDir, TURBO_PROFILE_FILE)
                .writeText(turboProfileContent, Charsets.UTF_8)
            File(injectStagingDir, SHADER_PROFILE_FILE)
                .writeText(shaderProfileContent, Charsets.UTF_8)

            File(context.filesDir, GLOBAL_SETTINGS_FILE).writeText(xmlContent, Charsets.UTF_8)

            cleanupDuplicateOutputs(context)
            Log.i(
                TAG,
                "All configuration exports completed successfully " +
                    "media_json=$jsonStored media_xml=$xmlStored media_turbo=$turboStored " +
                    "media_shader=$shaderStored"
            )
            true
        } catch (e: Exception) {
            Log.e(TAG, "Configuration export failed: ${e.message}", e)
            false
        }
    }

    /**
     * Launcher-side evidence that is always visible without root. The patched
     * Roblox process writes its own Masterstrap_RobloxPatched_*.log beside it.
     */
    fun writeAndroidLaunchDiagnostic(context: Context, content: String) {
        writeDiagnosticOutput(context, ANDROID_LAUNCH_LOG, content)
    }

    fun writeNativeBridgeDiagnostic(context: Context, content: String) {
        writeDiagnosticOutput(context, NATIVE_BRIDGE_LOG, content)
    }

    /**
     * Diagnostics are first replaced in app-private storage. MuMu exposes a
     * writable shared Download directory, so use a single raw canonical file
     * there and avoid its non-standard MediaStore row/file mismatch. Stock
     * Android rejects raw scoped-storage writes and cleanly falls back to one
     * MediaStore row.
     */
    private fun writeDiagnosticOutput(context: Context, fileName: String, content: String) {
        runCatching {
            val diagnosticsDir = File(context.filesDir, "Diagnostics").apply { mkdirs() }
            File(diagnosticsDir, fileName).writeText(content, Charsets.UTF_8)
        }.onFailure {
            Log.w(TAG, "Private diagnostic write failed for $fileName: ${it.message}")
        }

        val publicDir = File(
            Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
            MASTERSTRAP_DIR
        )
        val directWritten = runCatching {
            if (!publicDir.exists() && !publicDir.mkdirs()) return@runCatching false
            File(publicDir, fileName).writeText(content, Charsets.UTF_8)
            true
        }.getOrDefault(false)
        if (!directWritten) {
            writeViaMediaStore(context, fileName, content, "text/plain")
        }
    }

    private fun writeCanonicalDownloadFileDirect(fileName: String, content: String): Boolean {
        return runCatching {
            val dir = File(
                Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
                MASTERSTRAP_DIR
            )
            if (!dir.exists() && !dir.mkdirs()) return@runCatching false
            File(dir, fileName).writeText(content, Charsets.UTF_8)
            Log.i(TAG, "Canonical direct overwrite successful for $fileName")
            true
        }.onFailure {
            Log.i(TAG, "Canonical direct overwrite unavailable for $fileName: ${it.message}")
        }.getOrDefault(false)
    }

    private fun isLogicalDuplicate(candidate: String, canonical: String): Boolean {
        if (candidate.equals(canonical, ignoreCase = true)) return true
        val dot = canonical.lastIndexOf('.')
        val stem = if (dot > 0) canonical.substring(0, dot) else canonical
        val extension = if (dot > 0) canonical.substring(dot) else ""
        if (Regex(
            "^${Regex.escape(stem)}(?: \\(\\d+\\))*${Regex.escape(extension)}$",
            RegexOption.IGNORE_CASE
        ).matches(candidate)) return true

        // Android commonly changes a requested *.log into *.log.txt. Match
        // both forms, including multiple historical MediaStore suffixes.
        if (canonical.endsWith(".log.txt", ignoreCase = true)) {
            val legacyLogName = canonical.dropLast(4)
            return Regex(
                "^${Regex.escape(legacyLogName)}(?: \\(\\d+\\))*(?:\\.txt)?$",
                RegexOption.IGNORE_CASE
            ).matches(candidate)
        }
        return false
    }

    /**
     * Some Android MediaStore implementations expose unfinished rows on disk
     * as `.pending-<epoch>-<display name>`. Older bridge builds created one of
     * these for every diagnostic update. Only accept the prefix when the
     * remaining display name belongs to a managed Masterstrap output, so this
     * cleanup can never match an unrelated hidden Download file.
     */
    private fun isManagedPendingOutput(candidate: String): Boolean {
        val displayName = Regex(
            "^\\.pending-\\d+-(.+)$",
            RegexOption.IGNORE_CASE
        ).matchEntire(candidate)?.groupValues?.getOrNull(1) ?: return false
        return MANAGED_OUTPUTS.any { canonical ->
            isLogicalDuplicate(displayName, canonical)
        }
    }

    /**
     * Removes only Masterstrap-owned numbered duplicates left by older
     * MediaStore inserts. Canonical files are retained and overwritten in
     * place; unrelated files in Download/Masterstrap are never touched.
     */
    @Synchronized
    fun cleanupDuplicateOutputs(context: Context) {
        // MuMu/shared-folder files may exist without a usable MediaStore row.
        // Remove only known Masterstrap aliases from the exact app folder.
        runCatching {
            val directDir = File(
                Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
                MASTERSTRAP_DIR
            )
            directDir.listFiles()?.forEach { file ->
                if (!file.isFile) return@forEach
                val numberedDuplicate = MANAGED_OUTPUTS.any { canonical ->
                    !file.name.equals(canonical, ignoreCase = true) &&
                        isLogicalDuplicate(file.name, canonical)
                }
                val legacyNativeLog = Regex(
                    "^Masterstrap_RobloxPatched_\\d+_\\d+\\.log(?:\\.txt)?$",
                    RegexOption.IGNORE_CASE
                ).matches(file.name)
                val pendingOutput = isManagedPendingOutput(file.name)
                if ((numberedDuplicate || legacyNativeLog || pendingOutput) && !file.delete()) {
                    Log.w(TAG, "Cannot remove raw stale output ${file.absolutePath}")
                }
            }
        }.onFailure { Log.w(TAG, "Raw duplicate cleanup skipped: ${it.message}") }

        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return
        try {
            val resolver = context.contentResolver
            val collection = MediaStore.Downloads.EXTERNAL_CONTENT_URI
            val canonicalPath =
                (Environment.DIRECTORY_DOWNLOADS + "/$MASTERSTRAP_DIR").trimEnd('/')
            val projection = arrayOf(
                MediaStore.MediaColumns._ID,
                MediaStore.MediaColumns.DISPLAY_NAME,
                MediaStore.MediaColumns.RELATIVE_PATH
            )
            val stale = mutableListOf<Uri>()
            resolver.query(
                collection,
                projection,
                "${MediaStore.MediaColumns.RELATIVE_PATH} LIKE ?",
                arrayOf("$canonicalPath%"),
                null
            )?.use { cursor ->
                val idColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns._ID)
                val nameColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.DISPLAY_NAME)
                val pathColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.RELATIVE_PATH)
                while (cursor.moveToNext()) {
                    val path = cursor.getString(pathColumn)?.trimEnd('/') ?: continue
                    if (!path.equals(canonicalPath, ignoreCase = true)) continue
                    val name = cursor.getString(nameColumn) ?: continue
                    val numberedDuplicate = MANAGED_OUTPUTS.any { canonical ->
                        name != canonical && isLogicalDuplicate(name, canonical)
                    }
                    val legacyNativeLog = Regex(
                        "^Masterstrap_RobloxPatched_\\d+_\\d+\\.log$",
                        RegexOption.IGNORE_CASE
                    ).matches(name)
                    val pendingOutput = isManagedPendingOutput(name)
                    if (numberedDuplicate || legacyNativeLog || pendingOutput) {
                        stale += ContentUris.withAppendedId(
                            collection,
                            cursor.getLong(idColumn)
                        )
                    }
                }
            }
            stale.distinct().forEach { uri ->
                runCatching { resolver.delete(uri, null, null) }
                    .onFailure { Log.w(TAG, "Cannot remove stale output $uri: ${it.message}") }
            }
            if (stale.isNotEmpty()) {
                Log.i(TAG, "Removed ${stale.distinct().size} stale duplicate output(s)")
            }
        } catch (error: Exception) {
            Log.w(TAG, "Duplicate output cleanup skipped: ${error.message}")
        }
    }

    @Synchronized
    private fun writeViaMediaStore(
        context: Context,
        fileName: String,
        content: String,
        mimeType: String
    ): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            try {
                val resolver = context.contentResolver
                val relativePathWithSlash = Environment.DIRECTORY_DOWNLOADS + "/$MASTERSTRAP_DIR/"
                val canonicalRelativePath = relativePathWithSlash.trimEnd('/')
                val queryUri = MediaStore.Downloads.EXTERNAL_CONTENT_URI
                var targetUri: Uri? = null
                val alternateRows = mutableListOf<Uri>()
                val duplicateUris = mutableListOf<Uri>()

                // Query the folder instead of querying only the exact display
                // name. MediaStore may have renamed a raced insert to " (n)";
                // those rows still represent the same Masterstrap-owned file.
                val projection = arrayOf(
                    MediaStore.MediaColumns._ID,
                    MediaStore.MediaColumns.DISPLAY_NAME,
                    MediaStore.MediaColumns.RELATIVE_PATH
                )
                val selection = "${MediaStore.MediaColumns.RELATIVE_PATH} LIKE ?"
                val selectionArgs = arrayOf("$canonicalRelativePath%")
                try {
                    resolver.query(queryUri, projection, selection, selectionArgs, null)?.use { cursor ->
                        val idColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns._ID)
                        val nameColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.DISPLAY_NAME)
                        val pathColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.RELATIVE_PATH)
                        while (cursor.moveToNext()) {
                            val path = cursor.getString(pathColumn)?.trimEnd('/') ?: continue
                            val candidate = cursor.getString(nameColumn) ?: continue
                            if (!path.equals(canonicalRelativePath, ignoreCase = true) ||
                                !isLogicalDuplicate(candidate, fileName)
                            ) continue
                            val id = cursor.getLong(idColumn)
                            val uri = ContentUris.withAppendedId(queryUri, id)
                            if (candidate == fileName && targetUri == null) {
                                targetUri = uri
                            } else {
                                alternateRows += uri
                            }
                        }
                    }
                } catch (e: Exception) {
                    Log.w(TAG, "MediaStore query error for $fileName: ${e.message}")
                }

                // If a provider previously renamed the canonical file, reuse
                // that existing row rather than inserting another numbered
                // copy. Remaining aliases are removed only after overwrite.
                if (targetUri == null && alternateRows.isNotEmpty()) {
                    targetUri = alternateRows.removeAt(0)
                }
                duplicateUris += alternateRows

                var inserted = false
                if (targetUri == null) {
                    val values = ContentValues().apply {
                        put(MediaStore.MediaColumns.DISPLAY_NAME, fileName)
                        put(MediaStore.MediaColumns.MIME_TYPE, mimeType)
                        put(MediaStore.MediaColumns.RELATIVE_PATH, relativePathWithSlash)
                        put(MediaStore.MediaColumns.IS_PENDING, 1)
                    }
                    targetUri = resolver.insert(queryUri, values)
                    inserted = targetUri != null
                }

                if (targetUri != null) {
                    val stream = resolver.openOutputStream(targetUri!!, "rwt")
                        ?: throw IllegalStateException("MediaStore returned a null stream")
                    stream.use { output: OutputStream ->
                        output.write(content.toByteArray(Charsets.UTF_8))
                        output.flush()
                    }
                    if (inserted) {
                        val finishValues = ContentValues().apply {
                            put(MediaStore.MediaColumns.IS_PENDING, 0)
                        }
                        resolver.update(targetUri!!, finishValues, null, null)
                    }

                    // Cleanup only numbered duplicates of this exact managed
                    // filename and only after the canonical write succeeded.
                    duplicateUris.distinct().forEach { duplicate ->
                        try {
                            resolver.delete(duplicate, null, null)
                        } catch (deleteError: Exception) {
                            Log.w(TAG, "Cannot remove duplicate $duplicate: ${deleteError.message}")
                        }
                    }
                    Log.i(TAG, "MediaStore in-place overwrite successful for $fileName at $targetUri")
                    return true
                }
            } catch (e: Exception) {
                Log.w(TAG, "MediaStore export warning for $fileName: ${e.message}")
            }
        }
        return false
    }

    private fun generateClientAppSettingsJson(
        context: Context,
        editorFlags: List<FlagItem>,
        presetToggles: List<PresetToggle>,
        dropdowns: List<PresetDropdown>,
        selectedGamePreset: String,
        fpsCap: Int,
        gameModeEnabled: Boolean
    ): String {
        val root = JSONObject()
        val managePreferences = presetToggles.firstOrNull {
            it.id == "allow_manage_preferences"
        }?.isEnabled ?: true

        // 1. Apply Selected Game Preset Flags (e.g. Blox Fruits, Rivals, Inferium)
        if (managePreferences && selectedGamePreset != GamePresetManager.NONE) {
            val gamePresetFlags = GamePresetManager.loadFlags(context, selectedGamePreset)
            for ((key, value) in gamePresetFlags) {
                if (PatchedFlagPolicy.isBlocked(key)) continue
                root.put(key, value)
            }
        }

        // 2. Merge Custom Editor Flags
        for (flag in editorFlags) {
            if (!managePreferences) break
            if (!flag.isEnabled) continue
            if (PatchedFlagPolicy.isBlocked(flag.name)) continue
            when (flag.type) {
                FlagType.BOOLEAN -> {
                    root.put(flag.name, flag.value.equals("true", ignoreCase = true))
                }
                FlagType.INTEGER -> {
                    flag.value.toIntOrNull()?.let { intVal ->
                        root.put(flag.name, intVal)
                    } ?: root.put(flag.name, flag.value)
                }
                FlagType.STRING -> {
                    root.put(flag.name, flag.value)
                }
            }
        }

        // Turbo Boost is transported as a separate, one-shot native profile.
        // Never turn its UI controls into FastFlags: the patched process owns
        // these runtime optimizations and a direct Roblox launch must stay inert.
        val gameModeControlIds = setOf(
            "safe_ram_cleanup",
            "safe_network_opt",
            "safe_power_opt",
            "safe_thermal_guard",
            "game_mode_master",
            "system_perf_boost",
            "cpu_priority",
            "smart_ram_cleanup",
            "faster_game_launch",
            "keep_display_active",
            "unrestricted_power",
            "turbo_wifi_latency",
            "turbo_thermal_guard",
            "turbo_refresh_contract",
            "turbo_sustained_performance"
        )

        // Structured controls are authoritative over stale custom-editor or
        // game-preset copies of the same key. Enabled toggles are merged after
        // the editor and therefore force their advertised values. The only
        // deliberate exception is DFIntS2PhysicsSenderRate while Desync is ON;
        // that one numeric value is exposed as an advanced editor tuning knob.
        val editorOwnedKeys = editorFlags.asSequence()
            .filter { it.isEnabled && it.name.isNotBlank() }
            .map { it.name }
            .toSet()
        presetToggles.asSequence()
            .filterNot { it.id in gameModeControlIds }
            .flatMap { it.flags.keys.asSequence() }
            .distinct()
            .filterNot { it in editorOwnedKeys }
            .forEach { root.remove(it) }

        // 3. Apply Preset Toggles (Strictly overrides matching flags from Preset/DataGrid when ON)
        for (preset in presetToggles) {
            if (preset.id in gameModeControlIds) continue
            if (preset.id == "allow_manage_preferences") continue
            if (!managePreferences) continue
            if (preset.isEnabled) {
                for ((key, value) in preset.flags) {
                    when (value) {
                        is Boolean -> root.put(key, value)
                        is Int -> root.put(key, value)
                        is Long -> root.put(key, value)
                        is Double -> root.put(key, value)
                        is String -> {
                            val lower = value.trim().lowercase()
                            if (lower == "true") root.put(key, true)
                            else if (lower == "false") root.put(key, false)
                            else value.toIntOrNull()?.let { root.put(key, it) } ?: root.put(key, value)
                        }
                        else -> root.put(key, value)
                    }
                }
            }
        }

        // Dropdowns likewise own their mapped keys even when Automatic is
        // selected; this prevents an old custom value from keeping a visibly
        // disabled combobox active.
        dropdowns.asSequence()
            .flatMap { dropdown ->
                dropdown.options.asSequence().flatMap {
                    dropdown.flagMapper(it).keys.asSequence()
                }
            }
            .distinct()
            .filterNot { it in editorOwnedKeys }
            .forEach { root.remove(it) }

        // 4. Apply Preset Dropdowns (MSAA, FRM, Rendering Mode, Dynamic Resolution)
        // Renderer mode must never inherit a contradictory editor preference,
        // including when Automatic is selected or managed preferences are off.
        RendererModePolicy.ownedKeys.forEach { root.remove(it) }
        for (dropdown in dropdowns) {
            if (!managePreferences) break
            val flags = dropdown.flagMapper(dropdown.selectedOption)
            for ((key, value) in flags) {
                when (value) {
                    is Boolean -> root.put(key, value)
                    is Int -> root.put(key, value)
                    is Long -> root.put(key, value)
                    is Double -> root.put(key, value)
                    is String -> {
                        val lower = value.trim().lowercase()
                        if (lower == "true") root.put(key, true)
                        else if (lower == "false") root.put(key, false)
                        else value.toIntOrNull()?.let { root.put(key, it) } ?: root.put(key, value)
                    }
                    else -> root.put(key, value)
                }
            }
        }

        // ON owns level 1 even when an FRM dropdown is processed after toggles.
        // OFF adds/removes nothing here: retain the existing editor/dropdown policy.
        if (RobloxGraphicsPolicy.forceLowest(presetToggles)) {
            root.put("DFIntDebugFRMQualityLevelOverride", 1)
        }

        // Desync defaults to 38760, matching v3.lua/v4.lua. If the user has an
        // enabled, valid integer entry with this exact canonical name in the
        // FastFlag Editor, restore that value after every structured control is
        // merged. No other editor flag receives this privilege.
        val desyncEnabled = presetToggles.any { it.id == "desync" && it.isEnabled }
        val editableSenderRate = FlagPrecedencePolicy.editableS2PhysicsSenderRate(
            editorFlags = editorFlags,
            desyncEnabled = desyncEnabled
        )
        if (editableSenderRate != null) {
            root.put(
                FlagPrecedencePolicy.EDITABLE_S2_PHYSICS_SENDER_RATE,
                editableSenderRate
            )
            Log.i(
                TAG,
                "Resolved DFIntS2PhysicsSenderRate=$editableSenderRate " +
                    "source=fastflag_editor desync=true"
            )
        } else if (desyncEnabled) {
            Log.i(
                TAG,
                "Resolved DFIntS2PhysicsSenderRate=" +
                    root.opt(FlagPrecedencePolicy.EDITABLE_S2_PHYSICS_SENDER_RATE) +
                    " source=desync_toggle"
            )
        }

        // Global XML owns the user-selected FPS target. Remove competing flag
        // overrides after EVERY input source; omitting an override is different
        // from forcing its value to false.
        root.keys().asSequence().toList().forEach { key ->
            if (PatchedFlagPolicy.isBlocked(key)) root.remove(key)
        }
        Log.i(TAG, "fps_policy=global_xml requested=$fpsCap " +
            "target=${RobloxGlobalSettingsPolicy.normalizeFpsTarget(fpsCap)} " +
            "flag_overrides=excluded")

        return root.toString(4)
    }

    private fun generateGlobalBasicSettingsXml(
        fpsTarget: Int,
        playerNamesEnabled: Boolean,
        forceLowestGraphics: Boolean,
        reducedMotion: Boolean,
        hapticsEnabled: Boolean
    ): String {
        val framerate = RobloxGlobalSettingsPolicy.normalizeFpsTarget(fpsTarget)
        val graphicsSettings = RobloxGraphicsPolicy.xmlProperties(forceLowestGraphics)
        return """
<roblox version="4">
  <Item class="UserGameSettings" referent="RBX0">
    <Properties>
$graphicsSettings
      <bool name="ReducedMotion">$reducedMotion</bool>
      <float name="HapticStrength">${if (hapticsEnabled) "1.000000" else "0.000000"}</float>
      <int name="FramerateCap">$framerate</int>
      <bool name="PlayerNamesEnabled">$playerNamesEnabled</bool>
    </Properties>
  </Item>
</roblox>
        """.trimIndent()
    }

    /**
     * Native-only launch profile. It is intentionally stored in app-private
     * staging and handed to the patched client only through a one-shot launch
     * session; it is never exported as a public FastFlag file.
     */
    private fun generateTurboProfileJson(
        context: Context,
        presetToggles: List<PresetToggle>,
        fpsTarget: Int
    ): String {
        val states = presetToggles.associate { it.id to it.isEnabled }
        val master = states["game_mode_master"] == true
        return JSONObject().apply {
            put("schema", 4)
            put("enabled", master)
            put("systemPerformanceBoost", master && states["system_perf_boost"] == true)
            put("cpuPerformancePriority", master && states["cpu_priority"] == true)
            put("smartRamCleanup", master && states["smart_ram_cleanup"] == true)
            put("fasterGameLaunch", master && states["faster_game_launch"] == true)
            put("keepDisplayActive", master && states["keep_display_active"] == true)
            put("unrestrictedPower", master && states["unrestricted_power"] == true)
            put("wifiLowLatency", master && states["turbo_wifi_latency"] == true)
            put("thermalGuard", master && states["turbo_thermal_guard"] == true)
            put("displayRefreshContract", master && states["turbo_refresh_contract"] == true)
            put(
                "sustainedPerformance",
                master && states["turbo_sustained_performance"] == true
            )
            // The native performance-hint deadline must represent a physical
            // display workload, not an intentionally unbounded Roblox FPS cap.
            put("targetFrameRate", fpsTarget.coerceIn(30, 480))
            // A launcher-owned native display setting, not a FastFlag. Keeping
            // it in the one-shot private profile makes direct patched launches
            // inert and avoids adding another bridge payload/file.
            put("stretchScreenPercent", SettingsPersistence.loadStretchScreenPercent(context))
            put(
                "stretchBufferPreallocation",
                master && SettingsPersistence.loadStretchScreenPercent(context) != 100
            )
        }.toString()
    }

    /**
     * Launcher-owned visual profile consumed once by patched Roblox. It stays
     * separate from public FastFlags and explicitly forbids the reference Lua
     * script's whole-workspace material scan and periodic workers.
     */
    private fun generateShaderProfileJson(context: Context): String {
        val selected = SettingsPersistence.loadShaderPackage(context)
        val wetFloor = SettingsPersistence.loadShaderWetFloor(context)
        return JSONObject().apply {
            put("schema", 1)
            put("enabled", selected != ShaderPackage.DEFAULT)
            put("package", selected.stableKey)
            put("applyMode", "once_after_datamodel")
            put("maxPostEffects", 5)
            put("wetFloor", wetFloor)
            put("wetFloorStrategy", "bounded_nearby_once")
            put("wetFloorMaxParts", 1024)
            put("wetFloorRadiusStuds", 384)
            put("allowWorkspaceMaterialScan", false)
            put("allowPeriodicWorker", false)
        }.toString()
    }
}
