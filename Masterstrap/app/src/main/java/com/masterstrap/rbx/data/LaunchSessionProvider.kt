package com.masterstrap.rbx.data

import android.content.ContentProvider
import android.content.ContentValues
import android.database.Cursor
import android.net.Uri
import android.os.Bundle
import android.os.ParcelFileDescriptor
import android.util.Log
import java.io.FileNotFoundException

/** Read-only, UID/PID-bound provider for one Save-and-Launch session. */
class LaunchSessionProvider : ContentProvider() {
    companion object {
        private const val TAG = "MasterstrapProvider"
    }

    override fun onCreate(): Boolean = true

    override fun getType(uri: Uri): String? = when (uri.lastPathSegment) {
        LaunchSessionStore.CLIENT_FILE -> "application/json"
        LaunchSessionStore.GLOBAL_FILE -> "text/xml"
        LaunchSessionStore.TURBO_FILE -> "application/json"
        LaunchSessionStore.SHADER_FILE -> "application/json"
        else -> null
    }

    override fun openFile(uri: Uri, mode: String): ParcelFileDescriptor {
        val callerUid = android.os.Binder.getCallingUid()
        val callerPid = android.os.Binder.getCallingPid()
        if (mode != "r" || uri.pathSegments.size != 2 ||
            uri.pathSegments[0] != "session"
        ) {
            Log.w(TAG, "open rejected invalid uri=$uri mode=$mode uid=$callerUid pid=$callerPid")
            throw FileNotFoundException("Invalid launch-session request")
        }
        val appContext = context ?: throw FileNotFoundException("Provider unavailable")
        val file = LaunchSessionStore.claimFile(appContext, uri.lastPathSegment.orEmpty())
            ?: run {
                Log.w(
                    TAG,
                    "open rejected unavailable file=${uri.lastPathSegment} uid=$callerUid pid=$callerPid"
                )
                throw FileNotFoundException("Launch session unavailable")
            }
        Log.i(
            TAG,
            "open granted file=${file.name} bytes=${file.length()} uid=$callerUid pid=$callerPid"
        )
        return ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY)
    }

    override fun call(method: String, arg: String?, extras: Bundle?): Bundle {
        val appContext = context
        val consumed = appContext != null && method == "consume" &&
            LaunchSessionStore.consumeFromProvider(appContext)
        Log.i(
            TAG,
            "consume method=$method result=$consumed uid=${android.os.Binder.getCallingUid()} " +
                "pid=${android.os.Binder.getCallingPid()}"
        )
        return Bundle().apply { putBoolean("consumed", consumed) }
    }

    override fun query(
        uri: Uri, projection: Array<out String>?, selection: String?,
        selectionArgs: Array<out String>?, sortOrder: String?
    ): Cursor? = null

    override fun insert(uri: Uri, values: ContentValues?): Uri? = null
    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?): Int = 0
    override fun update(
        uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<out String>?
    ): Int = 0
}
