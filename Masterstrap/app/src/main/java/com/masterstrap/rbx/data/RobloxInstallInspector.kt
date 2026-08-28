package com.masterstrap.rbx.data

import android.content.Context
import android.os.Build
import java.io.BufferedInputStream
import java.io.File
import java.io.InputStream
import java.security.MessageDigest
import java.util.zip.ZipEntry
import java.util.zip.ZipFile

/** Read-only evidence about the exact Roblox APK/splits installed on-device. */
object RobloxInstallInspector {
    private const val ROBLOX_PACKAGE = "com.roblox.client"
    private val nativeEntries = listOf(
        "lib/arm64-v8a/libroblox.so",
        "lib/arm64-v8a/libmsboot.so",
        "lib/arm64-v8a/libmshook.so"
    )

    fun inspect(context: Context): String = buildString {
        appendLine("=== installed_roblox_inspection ===")
        appendLine("device_abis=${Build.SUPPORTED_ABIS.joinToString(",")}")
        try {
            val pm = context.packageManager
            val packageInfo = pm.getPackageInfo(ROBLOX_PACKAGE, 0)
            val appInfo = pm.getApplicationInfo(ROBLOX_PACKAGE, 0)
            appendLine("package=${packageInfo.packageName}")
            appendLine("version_name=${packageInfo.versionName}")
            appendLine("version_code=${packageInfo.longVersionCode}")
            appendLine("first_install_time=${packageInfo.firstInstallTime}")
            appendLine("last_update_time=${packageInfo.lastUpdateTime}")
            appendLine("source_dir=${appInfo.sourceDir}")
            appendLine("native_library_dir=${appInfo.nativeLibraryDir}")
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                try {
                    appendLine("installer=${pm.getInstallSourceInfo(ROBLOX_PACKAGE).installingPackageName}")
                } catch (error: Exception) {
                    appendLine("installer_error=${error.javaClass.simpleName}:${error.message}")
                }
            }

            val sourcePaths = buildList {
                add(appInfo.sourceDir)
                appInfo.splitSourceDirs?.forEach { add(it) }
            }
            appendLine("source_count=${sourcePaths.size}")
            sourcePaths.forEachIndexed { index, path -> inspectSource(index, path, this) }

            val nativeDir = File(appInfo.nativeLibraryDir ?: "")
            appendLine("native_dir_exists=${nativeDir.isDirectory}")
            for (name in listOf("libroblox.so", "libmsboot.so", "libmshook.so")) {
                val file = File(nativeDir, name)
                appendLine("native_file_$name exists=${file.isFile} bytes=${if (file.isFile) file.length() else -1}")
                if (file.isFile && name != "libroblox.so") {
                    try {
                        appendLine("native_sha256_$name=${file.inputStream().use(::sha256)}")
                    } catch (error: Exception) {
                        appendLine("native_hash_error_$name=${error.javaClass.simpleName}:${error.message}")
                    }
                }
            }
        } catch (error: Exception) {
            appendLine("inspection_error=${error.javaClass.name}:${error.message}")
        }
        appendLine("=== installed_roblox_inspection_end ===")
    }

    private fun inspectSource(index: Int, path: String, output: StringBuilder) {
        output.appendLine("source[$index]=$path")
        val sourceFile = File(path)
        output.appendLine("source[$index]_exists=${sourceFile.isFile} bytes=${if (sourceFile.isFile) sourceFile.length() else -1}")
        if (!sourceFile.isFile) return
        try {
            ZipFile(sourceFile).use { zip ->
                nativeEntries.forEach { name ->
                    val entry = zip.getEntry(name)
                    output.appendLine(
                        "source[$index]_entry=$name present=${entry != null}" +
                            if (entry != null) " size=${entry.size} compressed=${entry.compressedSize} crc=${entry.crc} method=${entry.method}" else ""
                    )
                    if (entry != null && name.endsWith("libmsboot.so")) {
                        output.appendLine("source[$index]_msboot_sha256=${zip.getInputStream(entry).use(::sha256)}")
                        val markers = zip.getInputStream(entry).use {
                            containsPatterns(it, listOf("MASTERSTRAP_BOOT_V1", "hook_load=success path="))
                        }
                        output.appendLine("source[$index]_msboot_markers=$markers")
                    } else if (entry != null && name.endsWith("libmshook.so")) {
                        output.appendLine("source[$index]_mshook_sha256=${zip.getInputStream(entry).use(::sha256)}")
                        val markers = zip.getInputStream(entry).use {
                            containsPatterns(
                                it,
                                listOf(
                                    "053-embedded-dynamic-android-debug",
                                    "MASTERSTRAP_GET_CONFIG_V1",
                                    "MASTERSTRAP_EMBEDDED_FLAGS_V1"
                                )
                            )
                        }
                        output.appendLine("source[$index]_mshook_markers=$markers")
                    } else if (entry != null && name.endsWith("libroblox.so")) {
                        val markers = zip.getInputStream(entry).use {
                            containsPatterns(it, listOf("libmsboot.so", "libOpenMAXAL.so"))
                        }
                        output.appendLine("source[$index]_libroblox_needed_strings=$markers")
                    }
                }
            }
        } catch (error: Exception) {
            output.appendLine("source[$index]_zip_error=${error.javaClass.name}:${error.message}")
        }
    }

    private fun sha256(input: InputStream): String {
        val digest = MessageDigest.getInstance("SHA-256")
        val buffer = ByteArray(64 * 1024)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            if (count > 0) digest.update(buffer, 0, count)
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    private fun containsPatterns(input: InputStream, patterns: List<String>): String {
        val needles = patterns.associateWith { it.toByteArray(Charsets.UTF_8) }
        val found = needles.keys.associateWith { false }.toMutableMap()
        val maxNeedle = needles.values.maxOf { it.size }
        val stream = BufferedInputStream(input, 64 * 1024)
        var carry = ByteArray(0)
        val chunk = ByteArray(64 * 1024)
        while (found.values.any { !it }) {
            val count = stream.read(chunk)
            if (count < 0) break
            val data = ByteArray(carry.size + count)
            carry.copyInto(data)
            chunk.copyInto(data, carry.size, 0, count)
            for ((label, needle) in needles) {
                if (found[label] == false && data.indexOfSubArray(needle) >= 0) found[label] = true
            }
            val keep = minOf(maxNeedle - 1, data.size)
            carry = data.copyOfRange(data.size - keep, data.size)
        }
        return found.entries.joinToString(",") { "${it.key}:${it.value}" }
    }

    private fun ByteArray.indexOfSubArray(needle: ByteArray): Int {
        if (needle.isEmpty() || needle.size > size) return -1
        outer@ for (start in 0..size - needle.size) {
            for (offset in needle.indices) {
                if (this[start + offset] != needle[offset]) continue@outer
            }
            return start
        }
        return -1
    }
}
