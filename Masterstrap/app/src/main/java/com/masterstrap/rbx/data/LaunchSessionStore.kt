package com.masterstrap.rbx.data

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Binder
import android.os.SystemClock
import java.io.File
import java.security.MessageDigest
import java.util.UUID

/**
 * One-shot handoff owned by Masterstrap. Roblox may claim one session, read
 * both configuration files plus the native Turbo profile, then explicitly
 * consume it. Public Download files
 * are exports only and never participate in activation.
 */
object LaunchSessionStore {
    const val CLIENT_FILE = "ClientAppSettings.json"
    const val GLOBAL_FILE = "GlobalBasicSettings_13.xml"
    const val TURBO_FILE = ConfigExporter.TURBO_PROFILE_FILE
    private const val PREFS = "masterstrap_launch_session"
    private const val SESSION_DIR = "LaunchSession"
    private const val LIFETIME_MS = 90_000L

    data class Session(
        val id: String,
        val targetPackage: String,
        val targetUid: Int,
        val expiresAtElapsed: Long,
        val jsonSha256: String,
        val xmlSha256: String,
        val turboSha256: String
    )

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    private fun sessionDir(context: Context) = File(context.filesDir, SESSION_DIR)

    fun uri(context: Context, fileName: String): Uri = Uri.Builder()
        .scheme("content")
        .authority("${context.packageName}.flags")
        .appendPath("session")
        .appendPath(fileName)
        .build()

    @Synchronized
    fun create(
        context: Context,
        targetPackage: String,
        targetUid: Int,
        json: ByteArray,
        xml: ByteArray,
        turbo: ByteArray
    ): Session {
        clear(context)
        val dir = sessionDir(context).apply { mkdirs() }
        File(dir, CLIENT_FILE).writeBytes(json)
        File(dir, GLOBAL_FILE).writeBytes(xml)
        File(dir, TURBO_FILE).writeBytes(turbo)
        val session = Session(
            id = UUID.randomUUID().toString(),
            targetPackage = targetPackage,
            targetUid = targetUid,
            expiresAtElapsed = SystemClock.elapsedRealtime() + LIFETIME_MS,
            jsonSha256 = sha256(json),
            xmlSha256 = sha256(xml),
            turboSha256 = sha256(turbo)
        )
        prefs(context).edit()
            .putString("id", session.id)
            .putString("target_package", targetPackage)
            .putInt("target_uid", targetUid)
            .putLong("expires_elapsed", session.expiresAtElapsed)
            .putString("json_sha256", session.jsonSha256)
            .putString("xml_sha256", session.xmlSha256)
            .putString("turbo_sha256", session.turboSha256)
            .putInt("claimed_uid", -1)
            .putInt("claimed_pid", -1)
            .putBoolean("json_read", false)
            .putBoolean("xml_read", false)
            .putBoolean("turbo_read", false)
            .apply()
        return session
    }

    @Synchronized
    fun claimFile(context: Context, fileName: String): File? {
        if (fileName !in setOf(CLIENT_FILE, GLOBAL_FILE, TURBO_FILE)) return null
        val state = prefs(context)
        val id = state.getString("id", null) ?: return null
        if (id.isBlank() || SystemClock.elapsedRealtime() >
            state.getLong("expires_elapsed", 0L)
        ) {
            clear(context)
            return null
        }
        val callerUid = Binder.getCallingUid()
        val callerPid = Binder.getCallingPid()
        if (callerUid != state.getInt("target_uid", -1)) return null

        val claimedUid = state.getInt("claimed_uid", -1)
        val claimedPid = state.getInt("claimed_pid", -1)
        if (claimedUid >= 0 && (claimedUid != callerUid || claimedPid != callerPid)) {
            return null
        }
        val file = File(sessionDir(context), fileName)
        if (!file.isFile) return null
        state.edit()
            .putInt("claimed_uid", callerUid)
            .putInt("claimed_pid", callerPid)
            .putBoolean(
                when (fileName) {
                    CLIENT_FILE -> "json_read"
                    GLOBAL_FILE -> "xml_read"
                    else -> "turbo_read"
                },
                true
            )
            .apply()
        return file
    }

    @Synchronized
    fun consumeFromProvider(context: Context): Boolean {
        val state = prefs(context)
        if (Binder.getCallingUid() != state.getInt("claimed_uid", -1) ||
            Binder.getCallingPid() != state.getInt("claimed_pid", -1) ||
            !state.getBoolean("json_read", false) ||
            !state.getBoolean("xml_read", false) ||
            !state.getBoolean("turbo_read", false)
        ) return false
        clear(context)
        return true
    }

    @Synchronized
    fun consumeFromBridge(context: Context, sessionId: String): Boolean {
        if (prefs(context).getString("id", null) != sessionId) return false
        clear(context)
        return true
    }

    @Synchronized
    fun clear(context: Context) {
        runCatching {
            context.revokeUriPermission(
                uri(context, CLIENT_FILE),
                Intent.FLAG_GRANT_READ_URI_PERMISSION
            )
            context.revokeUriPermission(
                uri(context, GLOBAL_FILE),
                Intent.FLAG_GRANT_READ_URI_PERMISSION
            )
            context.revokeUriPermission(
                uri(context, TURBO_FILE),
                Intent.FLAG_GRANT_READ_URI_PERMISSION
            )
        }
        val dir = sessionDir(context)
        File(dir, CLIENT_FILE).delete()
        File(dir, GLOBAL_FILE).delete()
        File(dir, TURBO_FILE).delete()
        if (dir.isDirectory && dir.list().isNullOrEmpty()) dir.delete()
        prefs(context).edit().clear().apply()
    }

    private fun sha256(bytes: ByteArray): String = MessageDigest
        .getInstance("SHA-256")
        .digest(bytes)
        .joinToString("") { "%02x".format(it) }
}
