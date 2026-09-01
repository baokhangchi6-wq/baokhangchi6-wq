package com.masterstrap.rbx.data

import android.app.ActivityManager
import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Debug
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.util.Log

/**
 * Bounded, non-root preparation performed by the Play-distributed launcher.
 *
 * Every action affects either Masterstrap's own process or a public Android
 * session lock. It never kills another app, changes a system property, invokes
 * a privileged shell, or writes a FastFlag.
 */
object SafeBoostController {
    private const val TAG = "MasterstrapSafeBoost"
    private const val LAUNCH_BOOST_DURATION_MS = 60_000L
    private const val THERMAL_UNKNOWN = -1

    data class Selection(
        val releaseLauncherMemory: Boolean,
        val lowLatencyWifi: Boolean,
        val stableLaunchPower: Boolean,
        val thermalSafetyGuard: Boolean
    )

    data class Report(
        val enabled: Boolean,
        val thermalGuardEnabled: Boolean,
        val thermalStatus: Int,
        val thermalHeadroom: Float?,
        val powerSaveMode: Boolean,
        val memoryResult: String,
        val networkResult: String,
        val powerResult: String
    ) {
        fun toDiagnostic(): String = buildString {
            appendLine("safe_boost_enabled=$enabled")
            appendLine("safe_thermal_guard_enabled=$thermalGuardEnabled")
            appendLine("safe_thermal_status=$thermalStatus")
            appendLine("safe_thermal_headroom=${thermalHeadroom ?: "unavailable"}")
            appendLine("safe_power_save=$powerSaveMode")
            appendLine("safe_memory=$memoryResult")
            appendLine("safe_network=$networkResult")
            appendLine("safe_launch_power=$powerResult")
            appendLine("safe_duration_ms=$LAUNCH_BOOST_DURATION_MS")
        }
    }

    private val mainHandler = Handler(Looper.getMainLooper())
    private var wifiLock: WifiManager.WifiLock? = null
    private var launchWakeLock: PowerManager.WakeLock? = null
    private val releaseRunnable = Runnable { release("timeout") }

    @Synchronized
    fun prepareBeforeLaunch(context: Context, selection: Selection): Report {
        release("replace")
        val appContext = context.applicationContext
        val powerManager = appContext.getSystemService(PowerManager::class.java)
        val thermalStatus = readThermalStatus(powerManager)
        val thermalHeadroom = readThermalHeadroom(powerManager)
        val thermalLimited = selection.thermalSafetyGuard &&
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q &&
            thermalStatus >= PowerManager.THERMAL_STATUS_SEVERE

        val memoryResult = if (selection.releaseLauncherMemory) {
            releaseOwnMemory(appContext)
        } else {
            "disabled"
        }
        val networkResult = if (selection.lowLatencyWifi) {
            if (thermalLimited) "skipped_thermal_guard" else acquireLowLatencyWifi(appContext)
        } else {
            "disabled"
        }
        val powerResult = if (selection.stableLaunchPower) {
            if (thermalLimited) "skipped_thermal_guard" else acquireLaunchWakeLock(powerManager)
        } else {
            "disabled"
        }

        if (wifiLock != null || launchWakeLock != null) {
            mainHandler.removeCallbacks(releaseRunnable)
            mainHandler.postDelayed(releaseRunnable, LAUNCH_BOOST_DURATION_MS)
        }

        val report = Report(
            enabled = selection.releaseLauncherMemory || selection.lowLatencyWifi ||
                selection.stableLaunchPower || selection.thermalSafetyGuard,
            thermalGuardEnabled = selection.thermalSafetyGuard,
            thermalStatus = thermalStatus,
            thermalHeadroom = thermalHeadroom,
            powerSaveMode = powerManager?.isPowerSaveMode == true,
            memoryResult = memoryResult,
            networkResult = networkResult,
            powerResult = powerResult
        )
        Log.i(TAG, report.toDiagnostic().replace('\n', ' ').trim())
        return report
    }

    @Synchronized
    fun releaseNow(reason: String) {
        release(reason)
    }

    private fun readThermalStatus(powerManager: PowerManager?): Int {
        if (powerManager == null || Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            return THERMAL_UNKNOWN
        }
        return runCatching { powerManager.currentThermalStatus }.getOrDefault(THERMAL_UNKNOWN)
    }

    private fun readThermalHeadroom(powerManager: PowerManager?): Float? {
        if (powerManager == null || Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return null
        return runCatching { powerManager.getThermalHeadroom(0) }
            .getOrNull()
            ?.takeIf { it.isFinite() && it >= 0f }
    }

    /** Releases only memory owned by Masterstrap; other app processes are never touched. */
    private fun releaseOwnMemory(context: Context): String {
        val activityManager = context.getSystemService(ActivityManager::class.java)
        val memoryInfo = ActivityManager.MemoryInfo()
        runCatching { activityManager?.getMemoryInfo(memoryInfo) }
        val runtime = Runtime.getRuntime()
        val javaBefore = runtime.totalMemory() - runtime.freeMemory()
        val nativeBefore = Debug.getNativeHeapAllocatedSize()
        runtime.gc()
        val javaAfter = runtime.totalMemory() - runtime.freeMemory()
        val nativeAfter = Debug.getNativeHeapAllocatedSize()
        val reclaimed = (javaBefore - javaAfter).coerceAtLeast(0L) +
            (nativeBefore - nativeAfter).coerceAtLeast(0L)
        val availableMb = memoryInfo.availMem / (1024L * 1024L)
        return "applied_own_process mode=gc_only reclaimed_bytes=$reclaimed available_mb=$availableMb " +
            "system_low_memory=${memoryInfo.lowMemory}"
    }

    private fun acquireLowLatencyWifi(context: Context): String {
        val connectivity = context.getSystemService(ConnectivityManager::class.java)
        val network = connectivity?.activeNetwork
        val capabilities = network?.let { connectivity.getNetworkCapabilities(it) }
        if (capabilities?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) != true) {
            return "skipped_not_wifi"
        }
        val wifiManager = context.getSystemService(WifiManager::class.java)
            ?: return "unavailable_wifi_service"
        if (!wifiManager.isWifiEnabled) return "skipped_wifi_disabled"
        return runCatching {
            val mode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                WifiManager.WIFI_MODE_FULL_LOW_LATENCY
            } else {
                @Suppress("DEPRECATION")
                WifiManager.WIFI_MODE_FULL_HIGH_PERF
            }
            wifiLock = wifiManager.createWifiLock(mode, "masterstrap:safe-launch-wifi").apply {
                setReferenceCounted(false)
                acquire()
            }
            val modeName = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                "low_latency"
            } else {
                "high_performance"
            }
            "applied mode=$modeName held=${wifiLock?.isHeld == true} " +
                "scope=launcher_foreground_handoff"
        }.getOrElse { "failed_${it.javaClass.simpleName}" }
    }

    private fun acquireLaunchWakeLock(powerManager: PowerManager?): String {
        if (powerManager == null) return "unavailable_power_service"
        return runCatching {
            launchWakeLock = powerManager.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK,
                "masterstrap:safe-launch-cpu"
            ).apply {
                setReferenceCounted(false)
                acquire(LAUNCH_BOOST_DURATION_MS)
            }
            "applied held=${launchWakeLock?.isHeld == true}"
        }.getOrElse { "failed_${it.javaClass.simpleName}" }
    }

    @Synchronized
    private fun release(reason: String) {
        mainHandler.removeCallbacks(releaseRunnable)
        val wifiReleased = runCatching {
            wifiLock?.takeIf { it.isHeld }?.release()
            true
        }.getOrDefault(false)
        val wakeReleased = runCatching {
            launchWakeLock?.takeIf { it.isHeld }?.release()
            true
        }.getOrDefault(false)
        if (wifiLock != null || launchWakeLock != null) {
            Log.i(TAG, "released reason=$reason wifi=$wifiReleased power=$wakeReleased")
        }
        wifiLock = null
        launchWakeLock = null
    }
}
