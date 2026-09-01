package com.masterstrap.rbx.ui

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.Rect
import android.graphics.drawable.ColorDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.text.Editable
import android.text.TextWatcher
import android.util.Log
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.InputMethodManager
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AppCompatDelegate
import androidx.core.content.ContextCompat
import androidx.core.os.LocaleListCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.recyclerview.widget.LinearLayoutManager
import com.google.android.play.core.appupdate.AppUpdateInfo
import com.google.android.play.core.appupdate.AppUpdateManager
import com.google.android.play.core.appupdate.AppUpdateManagerFactory
import com.google.android.play.core.appupdate.AppUpdateOptions
import com.google.android.play.core.install.model.AppUpdateType
import com.google.android.play.core.install.model.UpdateAvailability
import com.masterstrap.rbx.R
import com.masterstrap.rbx.ads.RewardAdManager
import com.masterstrap.rbx.data.ConfigExporter
import com.masterstrap.rbx.data.FlagItem
import com.masterstrap.rbx.data.FlagType
import com.masterstrap.rbx.data.GamePresetItem
import com.masterstrap.rbx.data.GamePresetManager
import com.masterstrap.rbx.data.NativeBridgeServer
import com.masterstrap.rbx.data.PatchedFlagPolicy
import com.masterstrap.rbx.data.LaunchSessionStore
import com.masterstrap.rbx.data.PresetItemUI
import com.masterstrap.rbx.data.PresetToggle
import com.masterstrap.rbx.data.PresetsRepository
import com.masterstrap.rbx.data.SafeBoostController
import com.masterstrap.rbx.data.SettingsPersistence
import com.masterstrap.rbx.data.ShaderPackage
import com.masterstrap.rbx.databinding.ActivityMainBinding
import org.json.JSONObject
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var flagAdapter: FlagAdapter
    private lateinit var fastFlagUIAdapter: PresetUIAdapter
    private lateinit var settingsUIAdapter: PresetUIAdapter
    private lateinit var gameModeUIAdapter: PresetUIAdapter
    private lateinit var gamePresetAdapter: GamePresetAdapter

    private var fastFlagUIItems: List<PresetItemUI> = emptyList()
    private var settingsUIItems: List<PresetItemUI> = emptyList()
    private var gameModeUIItems: List<PresetItemUI> = emptyList()
    private var gameModeToggles: List<PresetToggle> = emptyList()
    private var gamePresetUIItems: List<GamePresetUIItem> = emptyList()

    private var currentFpsCap: Int = 60
    private var currentSelectedPreset: String = GamePresetManager.NONE
    private lateinit var appUpdateManager: AppUpdateManager
    private var mandatoryUpdateDialog: AlertDialog? = null
    private var pendingAppUpdateInfo: AppUpdateInfo? = null
    private var updateCheckInFlight = false

    // File Picker for Import JSON
    private val importJsonLauncher = registerForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri: Uri? ->
        if (uri != null) {
            importJsonFromUri(uri)
        }
    }

    private val immediateUpdateLauncher = registerForActivityResult(
        ActivityResultContracts.StartIntentSenderForResult()
    ) { result ->
        if (result.resultCode != Activity.RESULT_OK) {
            // An immediate update can be declined or interrupted. Keep the app
            // locked, fetch a fresh AppUpdateInfo (instances are single-use),
            // and let the user retry from the mandatory dialog.
            pendingAppUpdateInfo = null
            showMandatoryUpdateDialog()
            checkForMandatoryUpdate()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        // Apply saved Theme before layout inflation
        val savedTheme = SettingsPersistence.loadAppTheme(this)
        if (savedTheme == "Dark") {
            AppCompatDelegate.setDefaultNightMode(AppCompatDelegate.MODE_NIGHT_YES)
        } else {
            AppCompatDelegate.setDefaultNightMode(AppCompatDelegate.MODE_NIGHT_NO)
        }

        super.onCreate(savedInstanceState)

        // Android 16 enforces edge-to-edge for targetSdk 36. Opt in on every
        // supported version and apply system-bar insets to the root view so the
        // header and bottom navigation never render under system controls.
        enableEdgeToEdge()
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { view, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.setPadding(
                systemBars.left,
                systemBars.top,
                systemBars.right,
                systemBars.bottom
            )
            insets
        }
        ViewCompat.requestApplyInsets(binding.root)

        // 1. Restore saved state
        currentFpsCap = SettingsPersistence.loadFps(this, 60)
        currentSelectedPreset = SettingsPersistence.loadSelectedPreset(this)

        setupHeaderSettings()
        setupEditorNavigation()
        setupDataGrid()
        setupAllUIItems()
        setupGamePresets()
        setupBottomNavigation()

        // Let the first frame render before LevelPlay starts its mediated
        // rewarded auction. Ad-gated controls can then show the winning ad
        // directly, while their payload still waits for onAdRewarded.
        RewardAdManager.scheduleStartupAuction(this)

        appUpdateManager = AppUpdateManagerFactory.create(this)
        if (isMandatoryUpdateCached()) {
            showMandatoryUpdateDialog()
        }
        checkForMandatoryUpdate()
    }

    override fun onResume() {
        super.onResume()
        RewardAdManager.refreshEntitlement(this)
        if (::appUpdateManager.isInitialized) {
            // Required by Play Core: resume an interrupted immediate update and
            // re-check whenever the app returns to the foreground.
            checkForMandatoryUpdate()
        }
    }

    private fun checkForMandatoryUpdate() {
        if (updateCheckInFlight || isFinishing) return
        updateCheckInFlight = true

        appUpdateManager.appUpdateInfo
            .addOnSuccessListener { info ->
                updateCheckInFlight = false
                when (info.updateAvailability()) {
                    UpdateAvailability.DEVELOPER_TRIGGERED_UPDATE_IN_PROGRESS -> {
                        rememberMandatoryUpdate(info.availableVersionCode())
                        pendingAppUpdateInfo = info
                        if (!startImmediateUpdate(info)) {
                            showMandatoryUpdateDialog()
                        }
                    }

                    UpdateAvailability.UPDATE_AVAILABLE -> {
                        rememberMandatoryUpdate(info.availableVersionCode())
                        pendingAppUpdateInfo = info
                        showMandatoryUpdateDialog()
                    }

                    UpdateAvailability.UPDATE_NOT_AVAILABLE -> {
                        clearMandatoryUpdate()
                        pendingAppUpdateInfo = null
                        mandatoryUpdateDialog?.dismiss()
                        mandatoryUpdateDialog = null
                    }

                    else -> {
                        // UNKNOWN does not prove that an update exists. Only a
                        // previously confirmed/cached requirement remains locked.
                        if (isMandatoryUpdateCached()) {
                            showMandatoryUpdateDialog()
                        }
                    }
                }
            }
            .addOnFailureListener { error ->
                updateCheckInFlight = false
                Log.w(UPDATE_LOG_TAG, "Google Play update check failed", error)
                if (isMandatoryUpdateCached()) {
                    showMandatoryUpdateDialog()
                }
            }
    }

    private fun showMandatoryUpdateDialog() {
        if (isFinishing || mandatoryUpdateDialog?.isShowing == true) return

        val content = layoutInflater.inflate(R.layout.dialog_mandatory_update, null)
        content.findViewById<TextView>(R.id.txtCurrentVersion).text = getString(
            R.string.dialog_update_current_version,
            currentVersionName()
        )
        val dialog = AlertDialog.Builder(this)
            .setView(content)
            .setCancelable(false)
            .create()

        content.findViewById<View>(R.id.btnUpgradeNow).setOnClickListener {
            val info = pendingAppUpdateInfo
            if (info == null || !startImmediateUpdate(info)) {
                openMasterstrapGooglePlayPage()
            }
        }

        dialog.setCanceledOnTouchOutside(false)
        dialog.setOnDismissListener {
            if (mandatoryUpdateDialog === dialog) {
                mandatoryUpdateDialog = null
            }
        }
        dialog.show()
        dialog.window?.apply {
            setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
            setLayout(
                (resources.displayMetrics.widthPixels * 0.90f).toInt(),
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        }
        mandatoryUpdateDialog = dialog
    }

    private fun startImmediateUpdate(info: AppUpdateInfo): Boolean {
        if (!info.isUpdateTypeAllowed(AppUpdateType.IMMEDIATE)) return false
        return try {
            appUpdateManager.startUpdateFlowForResult(
                info,
                immediateUpdateLauncher,
                AppUpdateOptions.newBuilder(AppUpdateType.IMMEDIATE).build()
            )
        } catch (error: Exception) {
            pendingAppUpdateInfo = null
            Log.w(UPDATE_LOG_TAG, "Unable to start immediate update", error)
            false
        }
    }

    private fun openMasterstrapGooglePlayPage() {
        val packageId = "com.masterstrap.app"
        val marketIntent = Intent(
            Intent.ACTION_VIEW,
            Uri.parse("market://details?id=$packageId")
        ).apply {
            setPackage("com.android.vending")
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }

        try {
            startActivity(marketIntent)
        } catch (_: ActivityNotFoundException) {
            startActivity(
                Intent(
                    Intent.ACTION_VIEW,
                    Uri.parse("https://play.google.com/store/apps/details?id=$packageId")
                )
            )
        }
    }

    private fun rememberMandatoryUpdate(targetVersionCode: Int) {
        getSharedPreferences(UPDATE_PREFS, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_UPDATE_REQUIRED, true)
            .putLong(KEY_TARGET_VERSION_CODE, targetVersionCode.toLong())
            .apply()
    }

    private fun clearMandatoryUpdate() {
        getSharedPreferences(UPDATE_PREFS, Context.MODE_PRIVATE)
            .edit()
            .remove(KEY_UPDATE_REQUIRED)
            .remove(KEY_TARGET_VERSION_CODE)
            .apply()
    }

    private fun isMandatoryUpdateCached(): Boolean {
        val preferences = getSharedPreferences(UPDATE_PREFS, Context.MODE_PRIVATE)
        if (!preferences.getBoolean(KEY_UPDATE_REQUIRED, false)) return false

        val targetVersionCode = preferences.getLong(KEY_TARGET_VERSION_CODE, Long.MAX_VALUE)
        if (currentVersionCode() >= targetVersionCode) {
            clearMandatoryUpdate()
            return false
        }
        return true
    }

    @Suppress("DEPRECATION")
    private fun currentVersionCode(): Long {
        val info = packageManager.getPackageInfo(packageName, 0)
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            info.longVersionCode
        } else {
            info.versionCode.toLong()
        }
    }

    @Suppress("DEPRECATION")
    private fun currentVersionName(): String =
        packageManager.getPackageInfo(packageName, 0).versionName ?: "?"

    private companion object {
        const val UPDATE_LOG_TAG = "MasterstrapUpdate"
        const val UPDATE_PREFS = "mandatory_update_state"
        const val KEY_UPDATE_REQUIRED = "update_required"
        const val KEY_TARGET_VERSION_CODE = "target_version_code"
    }

    private fun getLanguageTag(name: String): String {
        return when (name) {
            "Tiếng Việt" -> "vi"
            "中文 (简体)" -> "zh-CN"
            "한국어" -> "ko"
            "日本語" -> "ja"
            "Português (Brasil)" -> "pt-BR"
            "Русский" -> "ru"
            else -> "en"
        }
    }

    private fun getShaderPackageDisplayName(pkg: ShaderPackage): String {
        val stringRes = when (pkg.stableKey) {
            "default" -> R.string.shader_package_default
            "daytime" -> R.string.shader_package_daytime
            "rtx_ultra" -> R.string.shader_package_rtx_ultra
            "sunset" -> R.string.shader_package_sunset
            "night" -> R.string.shader_package_night
            "cloudy" -> R.string.shader_package_cloudy
            "desert" -> R.string.shader_package_desert
            "arctic" -> R.string.shader_package_arctic
            "blood_moon" -> R.string.shader_package_blood_moon
            "cyberpunk" -> R.string.shader_package_cyberpunk
            "cherry_blossom" -> R.string.shader_package_cherry_blossom
            "ghibli_anime" -> R.string.shader_package_ghibli_anime
            "synthwave" -> R.string.shader_package_synthwave
            "noir" -> R.string.shader_package_noir
            else -> R.string.shader_package_default
        }
        return getString(stringRes)
    }

    private fun setupHeaderSettings() {
        // 1. Open Full Screen Settings & About Page
        binding.btnSettingsAbout.setOnClickListener {
            binding.layoutSettingsAboutPage.visibility = View.VISIBLE
        }

        binding.btnBackFromSettings.setOnClickListener {
            binding.layoutSettingsAboutPage.visibility = View.GONE
        }

        binding.cardAdvancedSettings.setOnClickListener {
            binding.layoutAdvancedSettingsPage.visibility = View.VISIBLE
        }

        binding.btnBackFromAdvancedSettings.setOnClickListener {
            binding.layoutAdvancedSettingsPage.visibility = View.GONE
        }

        // Display Settings: Shader Package Spinner
        val shaderPackages = ShaderPackage.ordered
        val shaderDisplayNames = shaderPackages.map { pkg ->
            getShaderPackageDisplayName(pkg)
        }
        val shaderAdapter = ArrayAdapter(this, R.layout.item_custom_spinner_item, shaderDisplayNames).apply {
            setDropDownViewResource(R.layout.item_custom_spinner_dropdown)
        }
        binding.spinnerShaderPackage.adapter = shaderAdapter
        val currentShader = SettingsPersistence.loadShaderPackage(this)
        val currentShaderIdx = shaderPackages.indexOf(currentShader).coerceAtLeast(0)
        binding.spinnerShaderPackage.setSelection(currentShaderIdx, false)

        binding.spinnerShaderPackage.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                if (position in shaderPackages.indices) {
                    val selectedPkg = shaderPackages[position]
                    val currentSaved = SettingsPersistence.loadShaderPackage(this@MainActivity)
                    if (selectedPkg != currentSaved) {
                        SettingsPersistence.saveShaderPackage(this@MainActivity, selectedPkg)
                    }
                }
            }
            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }

        binding.switchShaderWetFloor.isChecked = SettingsPersistence.loadShaderWetFloor(this)
        binding.switchShaderWetFloor.setOnCheckedChangeListener { _, isChecked ->
            SettingsPersistence.saveShaderWetFloor(this, isChecked)
        }

        val stretchPercent = SettingsPersistence.loadStretchScreenPercent(this)
        binding.sliderStretchScreen.value = stretchPercent.toFloat()
        binding.tvStretchScreenValue.text = getString(
            R.string.value_stretch_screen_percent,
            stretchPercent
        )
        binding.sliderStretchScreen.addOnChangeListener { _, value, fromUser ->
            val percent = value.toInt()
            binding.tvStretchScreenValue.text = getString(
                R.string.value_stretch_screen_percent,
                percent
            )
            if (fromUser) {
                SettingsPersistence.saveStretchScreenPercent(this, percent)
            }
        }

        binding.btnBackFromEditor.setOnClickListener {
            binding.layoutFastFlagEditorPage.visibility = View.GONE
            binding.layoutFastFlagDashboard.visibility = View.VISIBLE
        }

        // 2. Preferences: Theme Spinner ("Dark", "Light")
        val themeOptions = listOf("Dark", "Light")
        val savedTheme = SettingsPersistence.loadAppTheme(this)
        val themeAdapter = ArrayAdapter(this, R.layout.item_custom_spinner_item, themeOptions).apply {
            setDropDownViewResource(R.layout.item_custom_spinner_dropdown)
        }
        binding.spinnerAppTheme.adapter = themeAdapter
        val themeIdx = themeOptions.indexOf(savedTheme).coerceAtLeast(0)
        binding.spinnerAppTheme.setSelection(themeIdx, false)

        var isThemeUserTouch = false
        binding.spinnerAppTheme.setOnTouchListener { _, _ ->
            isThemeUserTouch = true
            false
        }

        binding.spinnerAppTheme.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                if (!isThemeUserTouch) return
                isThemeUserTouch = false

                val selected = themeOptions[position]
                val currentSaved = SettingsPersistence.loadAppTheme(this@MainActivity)
                if (selected != currentSaved) {
                    SettingsPersistence.saveAppTheme(this@MainActivity, selected)
                    if (selected == "Dark") {
                        AppCompatDelegate.setDefaultNightMode(AppCompatDelegate.MODE_NIGHT_YES)
                    } else {
                        AppCompatDelegate.setDefaultNightMode(AppCompatDelegate.MODE_NIGHT_NO)
                    }
                }
            }
            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }

        // 3. Preferences: Language Spinner
        val languageOptions = listOf(
            "English",
            "Tiếng Việt",
            "中文 (简体)",
            "한국어",
            "日本語",
            "Português (Brasil)",
            "Русский",
            "Español",
            "Bahasa Indonesia",
            "ไทย"
        )
        val savedLang = SettingsPersistence.loadAppLanguage(this)
        val langAdapter = ArrayAdapter(this, R.layout.item_custom_spinner_item, languageOptions).apply {
            setDropDownViewResource(R.layout.item_custom_spinner_dropdown)
        }
        binding.spinnerAppLanguage.adapter = langAdapter
        val langIdx = languageOptions.indexOf(savedLang).coerceAtLeast(0)
        binding.spinnerAppLanguage.setSelection(langIdx, false)

        var isLangUserTouch = false
        binding.spinnerAppLanguage.setOnTouchListener { _, _ ->
            isLangUserTouch = true
            false
        }

        binding.spinnerAppLanguage.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                if (!isLangUserTouch) return
                isLangUserTouch = false

                val selected = languageOptions[position]
                val currentSaved = SettingsPersistence.loadAppLanguage(this@MainActivity)
                if (selected != currentSaved) {
                    SettingsPersistence.saveAppLanguage(this@MainActivity, selected)
                    val tag = getLanguageTag(selected)
                    AppCompatDelegate.setApplicationLocales(LocaleListCompat.forLanguageTags(tag))
                }
            }
            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }

        // 4. Preferences: Preferred Roblox App Spinner (Global vs VNG)
        val robloxAppOptions = listOf("Global", "VNG")
        val savedRobloxApp = SettingsPersistence.loadPreferredRobloxApp(this)
        val robloxAppAdapter = ArrayAdapter(this, R.layout.item_custom_spinner_item, robloxAppOptions).apply {
            setDropDownViewResource(R.layout.item_custom_spinner_dropdown)
        }
        binding.spinnerRobloxApp.adapter = robloxAppAdapter
        val robloxAppIdx = if (savedRobloxApp == "vng") 1 else 0
        binding.spinnerRobloxApp.setSelection(robloxAppIdx, false)

        var isRobloxAppUserTouch = false
        binding.spinnerRobloxApp.setOnTouchListener { _, _ ->
            isRobloxAppUserTouch = true
            false
        }

        binding.spinnerRobloxApp.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                if (!isRobloxAppUserTouch) return
                isRobloxAppUserTouch = false

                val chosen = if (position == 1) "vng" else "global"
                SettingsPersistence.savePreferredRobloxApp(this@MainActivity, chosen)
            }
            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }

        // 5. Official Channels & Links
        binding.cardFullGitHub.setOnClickListener {
            openBrowser("https://github.com/Masterstrap/Masterstrap-Android")
        }

        binding.cardFullYouTube.setOnClickListener {
            openBrowser("https://www.youtube.com/@Dank1ngs")
        }

        binding.cardFullDiscord.setOnClickListener {
            openBrowser("https://discord.gg/masterstrap")
        }

        binding.cardFullLicense.setOnClickListener {
            AlertDialog.Builder(this)
                .setTitle("MIT License")
                .setMessage(
                    "MIT License\n\nCopyright (c) 2026 Masterstrap Team\n\n" +
                    "Permission is hereby granted, free of charge, to any person obtaining a copy " +
                    "of this software and associated documentation files (the \"Software\"), to deal " +
                    "in the Software without restriction, including without limitation the rights " +
                    "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell " +
                    "copies of the Software, and to permit persons to whom the Software is " +
                    "furnished to do so, subject to the following conditions:\n\n" +
                    "The above copyright notice and this permission notice shall be included in all " +
                    "copies or substantial portions of the Software."
                )
                .setPositiveButton("Close", null)
                .show()
        }

        // 5. Activation: License Key Card
        val savedKey = SettingsPersistence.loadLicenseKey(this)
        val savedPlan = SettingsPersistence.loadLicensePlan(this)
        if (savedKey.isNotEmpty()) {
            binding.etFullLicenseKey.setText(savedKey)
            binding.tvFullLicenseBadge.text = savedPlan
            binding.tvFullLicenseBadge.setBackgroundResource(R.drawable.bg_badge_green)
            binding.tvFullLicenseBadge.setTextColor(ContextCompat.getColor(this, R.color.accent_green))
        } else {
            binding.tvFullLicenseBadge.text = getString(R.string.plan_free)
            binding.tvFullLicenseBadge.setBackgroundResource(R.drawable.bg_badge_type)
            binding.tvFullLicenseBadge.setTextColor(ContextCompat.getColor(this, R.color.brand_primary))
        }

        binding.btnFullActivateLicense.setOnClickListener {
            val key = binding.etFullLicenseKey.text.toString().trim()
            if (key.isEmpty()) {
                Toast.makeText(this, R.string.msg_invalid_license, Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            binding.btnFullActivateLicense.isEnabled = false
            Toast.makeText(this, "Validating license...", Toast.LENGTH_SHORT).show()

            com.masterstrap.rbx.licensing.MobileLicenseClient.validate(this, key) { result ->
                binding.btnFullActivateLicense.isEnabled = true
                if (result.isValid) {
                    SettingsPersistence.saveLicenseKey(this, key)
                    SettingsPersistence.saveLicensePlan(this, result.plan)
                    RewardAdManager.refreshEntitlement(this)
                    binding.tvFullLicenseBadge.text = result.plan
                    binding.tvFullLicenseBadge.setBackgroundResource(R.drawable.bg_badge_green)
                    binding.tvFullLicenseBadge.setTextColor(ContextCompat.getColor(this, R.color.accent_green))
                    Toast.makeText(this, result.message, Toast.LENGTH_LONG).show()
                } else {
                    Toast.makeText(this, result.message, Toast.LENGTH_LONG).show()
                }
            }
        }
    }

    private fun openBrowser(url: String) {
        try {
            val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
            startActivity(intent)
        } catch (e: Exception) {
            Toast.makeText(this, "Cannot open link: ${e.message}", Toast.LENGTH_SHORT).show()
        }
    }

    private fun setupEditorNavigation() {
        binding.cardOpenFastFlagEditor.setOnClickListener {
            binding.layoutFastFlagDashboard.visibility = View.GONE
            binding.layoutFastFlagEditorPage.visibility = View.VISIBLE
        }

        // Add Flag Button
        binding.btnAddFlag.setOnClickListener {
            showAddFlagDialog()
        }

        // Import JSON File Picker
        binding.btnImportJson.setOnClickListener {
            try {
                importJsonLauncher.launch("*/*")
            } catch (e: Exception) {
                Toast.makeText(this, "Cannot open file picker: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }

        // Export JSON File
        binding.btnExportJson.setOnClickListener {
            val saved = saveConfigurations(showToast = true)
            if (saved) {
                Toast.makeText(this, "Exported to /sdcard/Masterstrap/ClientAppSettings.json", Toast.LENGTH_LONG).show()
            }
        }

        // Clear All Flags
        binding.btnClearAllFlags.setOnClickListener {
            AlertDialog.Builder(this)
                .setTitle(R.string.btn_clear_all)
                .setMessage("Are you sure you want to remove all custom FastFlags?")
                .setPositiveButton("Clear") { _, _ ->
                    val flags = flagAdapter.getAllFlags().toMutableList()
                    for (f in flags) {
                        flagAdapter.removeFlag(f)
                    }
                    SettingsPersistence.saveCustomFlags(this, flagAdapter.getAllFlags())
                    Toast.makeText(this, "Cleared all custom flags", Toast.LENGTH_SHORT).show()
                }
                .setNegativeButton("Cancel", null)
                .show()
        }
    }

    private fun showAddFlagDialog() {
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(40, 20, 40, 10)
        }

        val etInput = EditText(this).apply {
            hint = "Paste flag name or JSON snippet here...\nExample:\n{\n  \"FFlagDebugDisplayFPS\": \"True\"\n}"
            minLines = 4
            maxLines = 8
            setPadding(20, 20, 20, 20)
            setBackgroundResource(R.drawable.bg_edittext)
            textSize = 13f
        }
        container.addView(etInput)

        AlertDialog.Builder(this)
            .setTitle("Add FastFlag")
            .setView(container)
            .setPositiveButton("Apply") { _, _ ->
                val text = etInput.text.toString().trim()
                if (text.isNotEmpty()) {
                    parseAndAddFlags(text)
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun parseAndAddFlags(rawText: String) {
        try {
            if (rawText.startsWith("{") && rawText.endsWith("}")) {
                val obj = JSONObject(rawText)
                var count = 0
                for (key in obj.keys()) {
                    if (PatchedFlagPolicy.isBlocked(key)) continue
                    val value = obj.get(key)
                    val type = when (value) {
                        is Boolean -> FlagType.BOOLEAN
                        is Int, is Long, is Double -> FlagType.INTEGER
                        else -> FlagType.STRING
                    }
                    flagAdapter.addFlag(FlagItem(key, type, value.toString()))
                    count++
                }
                SettingsPersistence.saveCustomFlags(this, flagAdapter.getAllFlags())
                Toast.makeText(this, "Added $count flags from JSON", Toast.LENGTH_SHORT).show()
                return
            }

            if (rawText.contains("=") || rawText.contains(":")) {
                val lines = rawText.split("\n")
                var count = 0
                for (line in lines) {
                    val trimmed = line.trim().trimEnd(',')
                    if (trimmed.isEmpty()) continue
                    val delimiter = if (trimmed.contains("=")) "=" else ":"
                    val parts = trimmed.split(delimiter, limit = 2)
                    if (parts.size == 2) {
                        val key = parts[0].trim().replace("\"", "")
                        if (PatchedFlagPolicy.isBlocked(key)) continue
                        val value = parts[1].trim().replace("\"", "")
                        val type = when {
                            value.equals("true", true) || value.equals("false", true) -> FlagType.BOOLEAN
                            value.toIntOrNull() != null -> FlagType.INTEGER
                            else -> FlagType.STRING
                        }
                        flagAdapter.addFlag(FlagItem(key, type, value))
                        count++
                    }
                }
                if (count > 0) {
                    SettingsPersistence.saveCustomFlags(this, flagAdapter.getAllFlags())
                    Toast.makeText(this, "Added $count flags", Toast.LENGTH_SHORT).show()
                    return
                }
            }

            val flagName = rawText.trim()
            if (PatchedFlagPolicy.isBlocked(flagName)) {
                Toast.makeText(
                    this,
                    "This flag is managed by Roblox and cannot be added.",
                    Toast.LENGTH_LONG
                ).show()
                return
            }
            flagAdapter.addFlag(FlagItem(flagName, FlagType.BOOLEAN, "true"))
            SettingsPersistence.saveCustomFlags(this, flagAdapter.getAllFlags())
            Toast.makeText(this, "Added $flagName", Toast.LENGTH_SHORT).show()
        } catch (e: Exception) {
            Toast.makeText(this, "Parse error: ${e.message}", Toast.LENGTH_SHORT).show()
        }
    }

    private fun importJsonFromUri(uri: Uri) {
        try {
            contentResolver.openInputStream(uri)?.use { inputStream ->
                val reader = BufferedReader(InputStreamReader(inputStream))
                val jsonString = reader.readText().trim()

                if (!jsonString.startsWith("{") || !jsonString.endsWith("}")) {
                    Toast.makeText(this, "Invalid flag file: Must be a valid JSON object", Toast.LENGTH_LONG).show()
                    return
                }

                val obj = JSONObject(jsonString)
                var count = 0
                for (key in obj.keys()) {
                    if (PatchedFlagPolicy.isBlocked(key)) continue
                    val value = obj.get(key)
                    val type = when (value) {
                        is Boolean -> FlagType.BOOLEAN
                        is Int, is Long, is Double -> FlagType.INTEGER
                        else -> FlagType.STRING
                    }
                    flagAdapter.addFlag(FlagItem(key, type, value.toString()))
                    count++
                }
                SettingsPersistence.saveCustomFlags(this, flagAdapter.getAllFlags())
                Toast.makeText(this, "Successfully imported $count flags from JSON file!", Toast.LENGTH_LONG).show()
            }
        } catch (e: Exception) {
            Toast.makeText(this, "Failed to read JSON: ${e.message}", Toast.LENGTH_SHORT).show()
        }
    }

    private fun setupBottomNavigation() {
        // Tab 1: FastFlag
        binding.navTabFastFlag.setOnClickListener {
            switchTab(0)
        }

        // Tab 2: FF Settings
        binding.navTabFFSettings.setOnClickListener {
            switchTab(1)
        }

        // Tab 3: START (Floating Circular Button with Rewarded Ad Gate & Claim Dialog)
        binding.navTabStart.setOnClickListener {
            RewardAdManager.showRewardedAdWithClaimDialog(
                activity = this,
                adPurposeTitle = getString(R.string.dialog_claim_ad_title),
                adPurposeMsg = getString(R.string.dialog_claim_ad_msg),
                onRewarded = {
                    saveAndLaunch()
                },
                onCanceled = {
                    Toast.makeText(this, "Launch canceled.", Toast.LENGTH_SHORT).show()
                }
            )
        }

        // Tab 4: Game Mode
        binding.navTabGameMode.setOnClickListener {
            switchTab(2)
        }

        // Tab 5: Preset
        binding.navTabPreset.setOnClickListener {
            switchTab(3)
        }
    }

    private fun switchTab(index: Int) {
        val primaryColor = ContextCompat.getColor(this, R.color.brand_primary)
        val secondaryColor = ContextCompat.getColor(this, R.color.text_secondary)

        // Reset all icon tints and text colors
        binding.ivNavFastFlag.setColorFilter(secondaryColor)
        binding.tvNavFastFlag.setTextColor(secondaryColor)

        binding.ivNavFFSettings.setColorFilter(secondaryColor)
        binding.tvNavFFSettings.setTextColor(secondaryColor)

        binding.ivNavGameMode.setColorFilter(secondaryColor)
        binding.tvNavGameMode.setTextColor(secondaryColor)

        binding.ivNavPreset.setColorFilter(secondaryColor)
        binding.tvNavPreset.setTextColor(secondaryColor)

        // Hide all views
        binding.layoutFastFlagDashboard.visibility = View.GONE
        binding.layoutFastFlagEditorPage.visibility = View.GONE
        binding.layoutFastFlagSettingsTab.visibility = View.GONE
        binding.layoutGameModeTab.visibility = View.GONE
        binding.layoutPresetTab.visibility = View.GONE
        binding.layoutSettingsAboutPage.visibility = View.GONE
        binding.layoutAdvancedSettingsPage.visibility = View.GONE

        when (index) {
            0 -> {
                binding.layoutFastFlagDashboard.visibility = View.VISIBLE
                binding.ivNavFastFlag.setColorFilter(primaryColor)
                binding.tvNavFastFlag.setTextColor(primaryColor)
            }
            1 -> {
                binding.layoutFastFlagSettingsTab.visibility = View.VISIBLE
                binding.ivNavFFSettings.setColorFilter(primaryColor)
                binding.tvNavFFSettings.setTextColor(primaryColor)
            }
            2 -> {
                binding.layoutGameModeTab.visibility = View.VISIBLE
                binding.ivNavGameMode.setColorFilter(primaryColor)
                binding.tvNavGameMode.setTextColor(primaryColor)
            }
            3 -> {
                binding.layoutPresetTab.visibility = View.VISIBLE
                binding.ivNavPreset.setColorFilter(primaryColor)
                binding.tvNavPreset.setTextColor(primaryColor)
            }
        }
    }

    private fun setupDataGrid() {
        val savedFlags = SettingsPersistence.loadCustomFlags(this)
        val initialFlags = (savedFlags ?: PresetsRepository.getDefaultEditorFlags())
            .filterNot { PatchedFlagPolicy.isBlocked(it.name) }
            .toMutableList()
        if (savedFlags != null && initialFlags.size != savedFlags.size) {
            SettingsPersistence.saveCustomFlags(this, initialFlags)
        }
        flagAdapter = FlagAdapter(
            flags = initialFlags,
            onDeleteClick = { flagToDelete ->
                flagAdapter.removeFlag(flagToDelete)
                SettingsPersistence.saveCustomFlags(this, flagAdapter.getAllFlags())
            },
            onValueChanged = {
                // Persist an explicit empty FString as soon as it is edited.
                // Empty is a valid value and must survive process recreation.
                SettingsPersistence.saveCustomFlags(this, flagAdapter.getAllFlags())
            }
        )

        binding.rvFlagsDataGrid.apply {
            layoutManager = LinearLayoutManager(this@MainActivity)
            adapter = flagAdapter
        }

        binding.etSearchFlag.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {
                flagAdapter.filter(s.toString())
            }
            override fun afterTextChanged(s: Editable?) {}
        })
    }

    private fun setupAllUIItems() {
        // Restore saved dropdown values (Defaults to Automatic)
        PresetsRepository.msaaDropdown.selectedOption = SettingsPersistence.loadDropdown(
            this, PresetsRepository.msaaDropdown.id, "Automatic"
        )
        PresetsRepository.frmDropdown.selectedOption = SettingsPersistence.loadDropdown(
            this, PresetsRepository.frmDropdown.id, "Automatic"
        )
        PresetsRepository.renderingModeDropdown.selectedOption = SettingsPersistence.loadDropdown(
            this, PresetsRepository.renderingModeDropdown.id, "Automatic"
        )
        PresetsRepository.dynamicResolutionDropdown.selectedOption = SettingsPersistence.loadDropdown(
            this, PresetsRepository.dynamicResolutionDropdown.id, "Automatic"
        )

        fastFlagUIItems = PresetsRepository.getFastFlagUIItems(this)
        settingsUIItems = PresetsRepository.getFastFlagSettingsUIItems(this)
        gameModeUIItems = PresetsRepository.getGameModeUIItems(this)
        gameModeToggles = PresetsRepository.getGameModeSettings(this)

        // Restore saved toggle states
        for (item in fastFlagUIItems) {
            if (item is PresetItemUI.Toggle) {
                item.toggle.isEnabled = SettingsPersistence.loadToggle(this, item.toggle.id, item.toggle.isEnabled)
            }
        }
        for (item in settingsUIItems) {
            if (item is PresetItemUI.Toggle) {
                item.toggle.isEnabled = SettingsPersistence.loadToggle(this, item.toggle.id, item.toggle.isEnabled)
            }
        }
        val licensePlan = SettingsPersistence.loadLicensePlan(this)
        val isPremium = licensePlan.equals("Plus", ignoreCase = true) ||
                        licensePlan.equals("Max", ignoreCase = true) ||
                        licensePlan.equals("Active", ignoreCase = true) ||
                        SettingsPersistence.loadLicenseKey(this).isNotEmpty()

        for (item in gameModeUIItems) {
            if (item is PresetItemUI.Toggle) {
                val toggle = item.toggle
                if (toggle.id == "game_mode_master") {
                    if (isPremium) {
                        toggle.isEnabled = SettingsPersistence.loadToggle(this, toggle.id, false)
                    } else {
                        // Free users: Always defaults to OFF
                        toggle.isEnabled = false
                        SettingsPersistence.saveToggle(this, toggle.id, false)
                    }
                } else {
                    toggle.isEnabled = SettingsPersistence.loadToggle(this, toggle.id, toggle.isEnabled)
                }
            }
        }

        val turboMasterInitial = gameModeUIItems
            .filterIsInstance<PresetItemUI.Toggle>()
            .map { it.toggle }
            .firstOrNull { it.id == "game_mode_master" }
        val isInitialMasterOn = turboMasterInitial?.isEnabled == true
        updateTurboBoostLockState(isInitialMasterOn)

        // 1. Tab 1: FastFlag Structured UI Items
        fastFlagUIAdapter = PresetUIAdapter(
            items = fastFlagUIItems,
            currentFps = currentFpsCap,
            onToggleChanged = { toggle, isChecked ->
                toggle.isEnabled = isChecked
                SettingsPersistence.saveToggle(this, toggle.id, isChecked)
            },
            onDropdownChanged = { dropdown, newOption ->
                SettingsPersistence.saveDropdown(this, dropdown.id, newOption)
            },
            onFpsChanged = { newFps ->
                currentFpsCap = newFps
                SettingsPersistence.saveFps(this, newFps)
            }
        )
        binding.rvFastFlagUI.apply {
            layoutManager = LinearLayoutManager(this@MainActivity)
            adapter = fastFlagUIAdapter
        }

        // 2. Tab 2: FF Settings Structured UI Items
        settingsUIAdapter = PresetUIAdapter(
            items = settingsUIItems,
            onToggleChanged = { toggle, isChecked ->
                if (toggle.id == "desync" && isChecked) {
                    // Desync is a crowned rewarded feature. Keep the saved and
                    // in-memory state OFF until LevelPlay reports a completed
                    // reward so ConfigExporter can never apply it early.
                    toggle.isEnabled = false
                    SettingsPersistence.saveToggle(this, toggle.id, false)
                    settingsUIAdapter.notifyDataSetChanged()
                    RewardAdManager.showRewardedAdWithClaimDialog(
                        activity = this,
                        adPurposeTitle = getString(R.string.ffs_desync_title),
                        adPurposeMsg = getString(R.string.dialog_claim_ad_msg),
                        onRewarded = {
                            toggle.isEnabled = true
                            SettingsPersistence.saveToggle(this, toggle.id, true)
                            settingsUIAdapter.notifyDataSetChanged()
                            Toast.makeText(this, R.string.msg_desync_enabled, Toast.LENGTH_SHORT).show()
                        },
                        onCanceled = {
                            toggle.isEnabled = false
                            SettingsPersistence.saveToggle(this, toggle.id, false)
                            settingsUIAdapter.notifyDataSetChanged()
                        }
                    )
                } else {
                    toggle.isEnabled = isChecked
                    SettingsPersistence.saveToggle(this, toggle.id, isChecked)
                }
            }
        )
        binding.rvSettingsUI.apply {
            layoutManager = LinearLayoutManager(this@MainActivity)
            adapter = settingsUIAdapter
        }

        // 3. Tab 4: Game Mode 2-Flow (Safe Boost & Turbo Boost 👑)
        gameModeUIAdapter = PresetUIAdapter(
            items = gameModeUIItems,
            onToggleChanged = { toggle, isChecked ->
                if (toggle.id == "game_mode_master") {
                    if (isChecked) {
                        val licenseKey = SettingsPersistence.loadLicenseKey(this)
                        if (licenseKey.isEmpty()) {
                            toggle.isEnabled = false
                            updateTurboBoostLockState(false)
                            gameModeUIAdapter.notifyDataSetChanged()
                            RewardAdManager.showRewardedAdWithClaimDialog(
                                activity = this,
                                adPurposeTitle = getString(R.string.gm_master_title),
                                adPurposeMsg = getString(R.string.dialog_claim_ad_msg),
                                onRewarded = {
                                    toggle.isEnabled = true
                                    updateTurboBoostLockState(true)
                                    gameModeUIAdapter.notifyDataSetChanged()
                                    SettingsPersistence.saveToggle(this, toggle.id, true)
                                    Toast.makeText(this, "Game Mode Turbo Enabled!", Toast.LENGTH_SHORT).show()
                                },
                                onCanceled = {
                                    toggle.isEnabled = false
                                    updateTurboBoostLockState(false)
                                    gameModeUIAdapter.notifyDataSetChanged()
                                    SettingsPersistence.saveToggle(this, toggle.id, false)
                                }
                            )
                        } else {
                            toggle.isEnabled = true
                            updateTurboBoostLockState(true)
                            SettingsPersistence.saveToggle(this, toggle.id, true)
                            gameModeUIAdapter.notifyDataSetChanged()
                        }
                    } else {
                        toggle.isEnabled = false
                        updateTurboBoostLockState(false)
                        SettingsPersistence.saveToggle(this, toggle.id, false)
                        gameModeUIAdapter.notifyDataSetChanged()
                    }
                } else if (toggle.isLocked) {
                    Toast.makeText(this, R.string.msg_enable_master_first, Toast.LENGTH_SHORT).show()
                } else {
                    toggle.isEnabled = isChecked
                    SettingsPersistence.saveToggle(this, toggle.id, isChecked)
                }
            }
        )
        binding.rvGameModeToggles.apply {
            layoutManager = LinearLayoutManager(this@MainActivity)
            adapter = gameModeUIAdapter
        }
    }

    private fun updateTurboBoostLockState(isTurboMasterOn: Boolean) {
        val turboCategory = getString(R.string.section_turbo_boost)
        for (item in gameModeUIItems) {
            if (item is PresetItemUI.Toggle) {
                val toggle = item.toggle
                if (toggle.id != "game_mode_master" && (toggle.category.contains("Turbo", ignoreCase = true) || toggle.category == turboCategory)) {
                    toggle.isLocked = !isTurboMasterOn
                }
            }
        }
    }

    private fun setupGamePresets() {
        val allPresets = GamePresetManager.getAllPresets(this)
        val uiList = mutableListOf<GamePresetUIItem>()

        // 1. Popular Group (Bloxfruits → The Strongest Battleground → Rivals → Volleyball Legends → Blade Ball)
        uiList.add(GamePresetUIItem.Header(getString(R.string.category_popular), hasCrown = true))
        for (p in allPresets.filter { it.category == "Popular" }) {
            uiList.add(GamePresetUIItem.Preset(p))
        }

        // 2. Hot Group (All remaining games)
        uiList.add(GamePresetUIItem.Header(getString(R.string.category_hot), hasCrown = false))
        for (p in allPresets.filter { it.category == "Hot" }) {
            uiList.add(GamePresetUIItem.Preset(p))
        }

        gamePresetUIItems = uiList
        gamePresetAdapter = GamePresetAdapter(
            items = gamePresetUIItems,
            selectedPresetId = currentSelectedPreset,
            onPresetBoosted = { preset ->
                if (preset.isPopular) {
                    // Popular Presets require Rewarded Ad (unless VIP license)
                    RewardAdManager.showRewardedAdWithClaimDialog(
                        activity = this,
                        adPurposeTitle = "Boost ${preset.title}",
                        adPurposeMsg = getString(R.string.dialog_claim_ad_msg),
                        onRewarded = {
                            val boostDialog = com.masterstrap.rbx.ui.custom.GameBoostDialogFragment.newInstance(preset) {
                                applyPresetToDataGrid(preset)
                            }
                            boostDialog.show(supportFragmentManager, "GameBoostDialog")
                        },
                        onCanceled = {
                            Toast.makeText(this, "Boost canceled.", Toast.LENGTH_SHORT).show()
                        }
                    )
                } else {
                    // Hot Presets: Free 100% direct boost without ads
                    val boostDialog = com.masterstrap.rbx.ui.custom.GameBoostDialogFragment.newInstance(preset) {
                        applyPresetToDataGrid(preset)
                    }
                    boostDialog.show(supportFragmentManager, "GameBoostDialog")
                }
            }
        )

        binding.rvGamePresets.apply {
            layoutManager = LinearLayoutManager(this@MainActivity)
            adapter = gamePresetAdapter
        }
    }

    private fun applyPresetToDataGrid(preset: GamePresetItem) {
        currentSelectedPreset = preset.id
        SettingsPersistence.saveSelectedPreset(this, preset.id)
        gamePresetAdapter.setSelectedPreset(preset.id)

        // Real System Cache Cleanup
        try {
            System.gc()
            Runtime.getRuntime().gc()
        } catch (ignored: Exception) {}

        val presetFlags = GamePresetManager.loadFlags(this, preset.id)
        for ((key, value) in presetFlags) {
            if (PatchedFlagPolicy.isBlocked(key)) continue
            val type = when (value) {
                is Boolean -> FlagType.BOOLEAN
                is Int, is Long, is Double -> FlagType.INTEGER
                else -> FlagType.STRING
            }
            val existing = flagAdapter.getAllFlags().firstOrNull { it.name == key }
            if (existing != null) {
                existing.value = value.toString()
                existing.type = type
                existing.isEnabled = true
            } else {
                flagAdapter.addFlag(FlagItem(key, type, value.toString(), isEnabled = true))
            }
        }

        flagAdapter.notifyDataSetChanged()
        SettingsPersistence.saveCustomFlags(this, flagAdapter.getAllFlags())

        // Materialize the selected preset immediately instead of merely
        // changing the card state. Roblox still receives it through the
        // authorized Start/Save & Launch session, but the exported files and
        // app-private staging are now ready as soon as Boost completes.
        val exported = saveConfigurations(showToast = false)
        Log.i(
            "MasterstrapPreset",
            "preset_applied id=${preset.id} flags=${presetFlags.size} exported=$exported"
        )

        Toast.makeText(
            this,
            if (exported) {
                getString(R.string.msg_game_preset_applied, preset.title, presetFlags.size)
            } else {
                getString(R.string.msg_save_failed)
            },
            Toast.LENGTH_SHORT
        ).show()
    }

    private fun saveAndLaunch() {
        val loadingDialog = com.masterstrap.rbx.ui.custom.LoadingDialogFragment()
        loadingDialog.setMessageText("Analyzing configurations & FastFlags…")
        loadingDialog.setMessageStatus("0%")
        loadingDialog.show(supportFragmentManager, "launch_dialog")

        val handler = Handler(Looper.getMainLooper())
        val progressValues = intArrayOf(18, 38, 58, 78, 92)
        val stageTexts = arrayOf(
            "Analyzing configurations & FastFlags…",
            "Optimizing memory pipeline & FPS cap…",
            "Injecting ClientAppSettings profile…",
            "Turbo engine & GameMode active…",
            "Launch environment ready…"
        )

        var idx = 0
        fun stepProgress() {
            if (idx < progressValues.size) {
                loadingDialog.setProgress(progressValues[idx])
                loadingDialog.setMessageText(stageTexts[idx])
                loadingDialog.setMessageStatus("${progressValues[idx]}%")
                idx++
                handler.postDelayed({ stepProgress() }, 550L)
            } else {
                Thread {
                    val saved = saveConfigurations(showToast = false)
                    handler.post {
                        if (saved) {
                            loadingDialog.setMessageText("Launching Roblox…")
                            loadingDialog.completeAndLaunch {
                                handler.postDelayed({
                                    try {
                                        if (loadingDialog.isAdded) loadingDialog.dismissAllowingStateLoss()
                                    } catch (ignored: Exception) {}
                                    launchRobloxApp()
                                }, 250L)
                            }
                        } else {
                            try {
                                if (loadingDialog.isAdded) loadingDialog.dismissAllowingStateLoss()
                            } catch (ignored: Exception) {}
                            Toast.makeText(this@MainActivity, R.string.msg_save_failed, Toast.LENGTH_SHORT).show()
                        }
                    }
                }.start()
            }
        }

        handler.postDelayed({ stepProgress() }, 300L)
    }

    private fun saveConfigurations(showToast: Boolean): Boolean {
        val flags = flagAdapter.getAllFlags()

        // Save before exporting/launching. This is especially important for
        // FString values whose intentional value is the empty string.
        SettingsPersistence.saveCustomFlags(this, flags)

        val allToggles = mutableListOf<PresetToggle>()
        for (item in fastFlagUIItems) {
            if (item is PresetItemUI.Toggle) allToggles.add(item.toggle)
        }
        for (item in settingsUIItems) {
            if (item is PresetItemUI.Toggle) allToggles.add(item.toggle)
        }
        for (item in gameModeUIItems) {
            if (item is PresetItemUI.Toggle) allToggles.add(item.toggle)
        }

        val dropdowns = listOf(
            PresetsRepository.msaaDropdown,
            PresetsRepository.frmDropdown,
            PresetsRepository.renderingModeDropdown,
            PresetsRepository.dynamicResolutionDropdown
        )

        val isGameMode = gameModeUIItems
            .filterIsInstance<PresetItemUI.Toggle>()
            .map { it.toggle }
            .firstOrNull { it.id == "game_mode_master" }?.isEnabled ?: false

        val success = ConfigExporter.exportConfiguration(
            this,
            flags,
            allToggles,
            dropdowns,
            currentSelectedPreset,
            currentFpsCap,
            isGameMode
        )

        if (showToast) {
            if (success) {
                Toast.makeText(this, R.string.msg_saved_success, Toast.LENGTH_LONG).show()
            } else {
                Toast.makeText(this, R.string.msg_save_failed, Toast.LENGTH_LONG).show()
            }
        }
        return success
    }

    private fun launchRobloxApp() {
        val gameModeStates = gameModeUIItems
            .filterIsInstance<PresetItemUI.Toggle>()
            .associate { it.toggle.id to it.toggle.isEnabled }
        val safeBoostReport = SafeBoostController.prepareBeforeLaunch(
            this,
            SafeBoostController.Selection(
                releaseLauncherMemory = gameModeStates["safe_ram_cleanup"] == true,
                lowLatencyWifi = gameModeStates["safe_network_opt"] == true,
                stableLaunchPower = gameModeStates["safe_power_opt"] == true,
                thermalSafetyGuard = gameModeStates["safe_thermal_guard"] == true
            )
        )
        val preferredApp = SettingsPersistence.loadPreferredRobloxApp(this)
        val packages = if (preferredApp == "vng") {
            listOf("com.roblox.client.vnggames", "com.roblox.client")
        } else {
            listOf("com.roblox.client", "com.roblox.client.vnggames")
        }
        var launched = false

        for (pkg in packages) {
            try {
                var launchIntent = packageManager.getLaunchIntentForPackage(pkg)

                if (launchIntent == null) {
                    val query = Intent(Intent.ACTION_MAIN)
                        .addCategory(Intent.CATEGORY_LAUNCHER)
                        .setPackage(pkg)
                    val activities = packageManager.queryIntentActivities(query, 0)
                    if (activities.isNotEmpty()) {
                        val resolved = activities[0]
                        if (resolved.activityInfo != null && resolved.activityInfo.name != null) {
                            val comp = android.content.ComponentName(
                                resolved.activityInfo.packageName,
                                resolved.activityInfo.name
                            )
                            launchIntent = Intent.makeMainActivity(comp)
                        }
                    }
                }

                if (launchIntent == null) {
                    launchIntent = Intent(Intent.ACTION_MAIN).apply {
                        addCategory(Intent.CATEGORY_LAUNCHER)
                        setPackage(pkg)
                    }
                }

                launchIntent.addFlags(
                    Intent.FLAG_ACTIVITY_NEW_TASK or
                    Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED or
                    Intent.FLAG_ACTIVITY_CLEAR_TOP or
                    Intent.FLAG_GRANT_READ_URI_PERMISSION
                )
                val turboMasterEnabled = gameModeUIItems
                    .filterIsInstance<PresetItemUI.Toggle>()
                    .any { it.toggle.id == "game_mode_master" && it.toggle.isEnabled }
                val fasterLaunchEnabled = gameModeUIItems
                    .filterIsInstance<PresetItemUI.Toggle>()
                    .any { it.toggle.id == "faster_game_launch" && it.toggle.isEnabled }
                if (turboMasterEnabled && fasterLaunchEnabled) {
                    launchIntent.addFlags(Intent.FLAG_ACTIVITY_NO_ANIMATION)
                }

                // Android 11+ prevents Roblox from opening another app's raw
                // /sdcard/Download file. Grant the patched client read access
                // to the same freshly-exported payload through FileProvider.
                // Download remains unchanged for MuMu and legacy clients.
                try {
                    val privateSettings = File(filesDir, "Inject/ClientAppSettings.json")
                    val privateGlobalSettings = File(filesDir, "GlobalBasicSettings_13.xml")
                    val privateTurboProfile = File(
                        filesDir,
                        "Inject/${ConfigExporter.TURBO_PROFILE_FILE}"
                    )
                    val privateShaderProfile = File(
                        filesDir,
                        "Inject/${ConfigExporter.SHADER_PROFILE_FILE}"
                    )
                    if (privateSettings.isFile && privateGlobalSettings.isFile &&
                        privateTurboProfile.isFile && privateShaderProfile.isFile
                    ) {
                        val jsonPayload = privateSettings.readBytes()
                        val xmlPayload = privateGlobalSettings.readBytes()
                        val turboPayload = privateTurboProfile.readBytes()
                        val shaderPayload = privateShaderProfile.readBytes()
                        val targetUid = packageManager.getApplicationInfo(pkg, 0).uid
                        val session = LaunchSessionStore.create(
                            this,
                            pkg,
                            targetUid,
                            jsonPayload,
                            xmlPayload,
                            turboPayload,
                            shaderPayload
                        )
                        val bridgeStarted = NativeBridgeServer.start(
                            this,
                            session.id,
                            jsonPayload,
                            xmlPayload,
                            turboPayload,
                            shaderPayload
                        )
                        val settingsUri = LaunchSessionStore.uri(
                            this,
                            LaunchSessionStore.CLIENT_FILE
                        )
                        val globalSettingsUri = LaunchSessionStore.uri(
                            this,
                            LaunchSessionStore.GLOBAL_FILE
                        )
                        val turboProfileUri = LaunchSessionStore.uri(
                            this,
                            LaunchSessionStore.TURBO_FILE
                        )
                        val shaderProfileUri = LaunchSessionStore.uri(
                            this,
                            LaunchSessionStore.SHADER_FILE
                        )
                        grantUriPermission(pkg, settingsUri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
                        grantUriPermission(pkg, globalSettingsUri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
                        grantUriPermission(pkg, turboProfileUri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
                        grantUriPermission(pkg, shaderProfileUri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
                        launchIntent.clipData = ClipData.newRawUri(
                            "Masterstrap ClientAppSettings",
                            settingsUri
                        ).apply {
                            addItem(ClipData.Item(globalSettingsUri))
                            addItem(ClipData.Item(turboProfileUri))
                            addItem(ClipData.Item(shaderProfileUri))
                        }
                        ConfigExporter.writeAndroidLaunchDiagnostic(
                            this,
                            buildString {
                                val targetInfo = packageManager.getApplicationInfo(pkg, 0)
                                val targetVersion = packageManager.getPackageInfo(pkg, 0)
                                val appVersion = packageManager.getPackageInfo(packageName, 0)
                                @Suppress("DEPRECATION")
                                val targetVersionCode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                                    targetVersion.longVersionCode
                                } else targetVersion.versionCode.toLong()
                                @Suppress("DEPRECATION")
                                val appVersionCode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                                    appVersion.longVersionCode
                                } else appVersion.versionCode.toLong()
                                appendLine("timestamp=${System.currentTimeMillis()}")
                                appendLine("elapsed_realtime=${android.os.SystemClock.elapsedRealtime()}")
                                appendLine("masterstrap_version=${appVersion.versionName}/$appVersionCode")
                                appendLine("masterstrap_pid=${android.os.Process.myPid()}")
                                appendLine("masterstrap_uid=${android.os.Process.myUid()}")
                                appendLine("android_sdk=${Build.VERSION.SDK_INT}")
                                appendLine("android_release=${Build.VERSION.RELEASE}")
                                appendLine("device=${Build.MANUFACTURER}/${Build.MODEL}/${Build.DEVICE}")
                                appendLine("supported_abis=${Build.SUPPORTED_ABIS.joinToString(",")}")
                                appendLine("target_package=$pkg")
                                appendLine("target_uid=$targetUid")
                                appendLine("target_version=${targetVersion.versionName}/$targetVersionCode")
                                appendLine("target_native_lib_dir=${targetInfo.nativeLibraryDir}")
                                appendLine("target_source_dir=${targetInfo.sourceDir}")
                                appendLine("session_id=${session.id}")
                                appendLine("session_expires_elapsed=${session.expiresAtElapsed}")
                                appendLine(
                                    "session_remaining_ms=${session.expiresAtElapsed - android.os.SystemClock.elapsedRealtime()}"
                                )
                                appendLine("settings_bytes=${privateSettings.length()}")
                                appendLine("global_settings_bytes=${privateGlobalSettings.length()}")
                                appendLine("turbo_profile_bytes=${privateTurboProfile.length()}")
                                appendLine("shader_profile_bytes=${privateShaderProfile.length()}")
                                appendLine("settings_uri=$settingsUri")
                                appendLine("global_settings_uri=$globalSettingsUri")
                                appendLine("turbo_profile_uri=$turboProfileUri")
                                appendLine("shader_profile_uri=$shaderProfileUri")
                                appendLine("json_sha256=${session.jsonSha256}")
                                appendLine("xml_sha256=${session.xmlSha256}")
                                appendLine("turbo_sha256=${session.turboSha256}")
                                appendLine("shader_sha256=${session.shaderSha256}")
                                appendLine("grant_read_uri=true")
                                appendLine(
                                    "grant_check_settings=" + checkUriPermission(
                                        settingsUri, -1, targetUid,
                                        Intent.FLAG_GRANT_READ_URI_PERMISSION
                                    )
                                )
                                appendLine(
                                    "grant_check_global=" + checkUriPermission(
                                        globalSettingsUri, -1, targetUid,
                                        Intent.FLAG_GRANT_READ_URI_PERMISSION
                                    )
                                )
                                appendLine(
                                    "grant_check_turbo=" + checkUriPermission(
                                        turboProfileUri, -1, targetUid,
                                        Intent.FLAG_GRANT_READ_URI_PERMISSION
                                    )
                                )
                                appendLine(
                                    "grant_check_shader=" + checkUriPermission(
                                        shaderProfileUri, -1, targetUid,
                                        Intent.FLAG_GRANT_READ_URI_PERMISSION
                                    )
                                )
                                appendLine("launch_clip_data=true")
                                appendLine("launch_component=${launchIntent.component}")
                                appendLine("launch_flags=0x${launchIntent.flags.toString(16)}")
                                appendLine("loopback_bridge_started=$bridgeStarted")
                                append(safeBoostReport.toDiagnostic())
                            }
                        )
                        Log.i("MasterstrapProvider", "Granted ClientAppSettings URI to $pkg")
                    } else {
                        ConfigExporter.writeAndroidLaunchDiagnostic(
                            this,
                            "timestamp=${System.currentTimeMillis()}\n" +
                                "target_package=$pkg\n" +
                                "error=private_settings_missing\n"
                        )
                        Log.w("MasterstrapProvider", "Private launch configuration is missing")
                    }
                } catch (providerError: Exception) {
                    // Never turn the Android scoped-storage fallback into a
                    // launch blocker. MuMu can still consume the Download file.
                    ConfigExporter.writeAndroidLaunchDiagnostic(
                        this,
                        "timestamp=${System.currentTimeMillis()}\n" +
                            "target_package=$pkg\n" +
                            "error=${providerError.javaClass.name}: ${providerError.message}\n"
                    )
                    Log.w("MasterstrapProvider", "URI grant unavailable", providerError)
                }
                startActivity(launchIntent)
                launched = true
                break
            } catch (e: Exception) {
                // Try next package
            }
        }

        if (launched) {
            // Replicate original codebase behavior: Free users automatically have Game Mode reset back to disabled after launching Roblox
            val licensePlan = SettingsPersistence.loadLicensePlan(this)
            val isPremium = licensePlan.equals("Plus", ignoreCase = true) ||
                            licensePlan.equals("Max", ignoreCase = true) ||
                            licensePlan.equals("Active", ignoreCase = true) ||
                            SettingsPersistence.loadLicenseKey(this).isNotEmpty()
            if (!isPremium) {
                val gmMaster = gameModeUIItems
                    .filterIsInstance<PresetItemUI.Toggle>()
                    .map { it.toggle }
                    .firstOrNull { it.id == "game_mode_master" }
                if (gmMaster != null && gmMaster.isEnabled) {
                    gmMaster.isEnabled = false
                    SettingsPersistence.saveToggle(this, "game_mode_master", false)
                    gameModeUIAdapter.notifyDataSetChanged()
                }
            }
            Toast.makeText(this, "discord.gg/masterstrap", Toast.LENGTH_LONG).show()
        } else {
            SafeBoostController.releaseNow("roblox_not_found")
            Toast.makeText(this, R.string.msg_roblox_not_found, Toast.LENGTH_SHORT).show()
        }
    }

    override fun dispatchTouchEvent(ev: MotionEvent?): Boolean {
        if (ev?.action == MotionEvent.ACTION_DOWN) {
            val v = currentFocus
            if (v is EditText) {
                val outRect = Rect()
                v.getGlobalVisibleRect(outRect)
                if (!outRect.contains(ev.rawX.toInt(), ev.rawY.toInt())) {
                    v.clearFocus()
                    val imm = getSystemService(Context.INPUT_METHOD_SERVICE) as? InputMethodManager
                    imm?.hideSoftInputFromWindow(v.windowToken, 0)
                }
            }
        }
        return super.dispatchTouchEvent(ev)
    }
}
