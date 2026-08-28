package com.masterstrap.rbx.licensing

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Log
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets
import java.util.concurrent.Executors

object MobileLicenseClient {
    private const val TAG = "MobileLicenseClient"
    private const val VALIDATE_ENDPOINT = "https://liscence-key-t0n3.onrender.com/api/mobile-licenses/validate"
    private const val CLIENT_INFO = "masterstrap-android/1.2"

    private val EXECUTOR = Executors.newSingleThreadExecutor()
    private val MAIN_HANDLER = Handler(Looper.getMainLooper())

    data class LicenseResult(
        val isValid: Boolean,
        val isNetworkError: Boolean,
        val plan: String,
        val expirationDate: String,
        val message: String
    )

    fun validate(context: Context, key: String, callback: (LicenseResult) -> Unit) {
        val trimmedKey = key.trim()
        if (trimmedKey.isEmpty()) {
            callback(LicenseResult(
                isValid = false,
                isNetworkError = false,
                plan = "Free",
                expirationDate = "",
                message = "Please enter a license key."
            ))
            return
        }

        val deviceId = try {
            Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID) ?: "masterstrap-android-device"
        } catch (e: Exception) {
            "masterstrap-android-device"
        }

        EXECUTOR.execute {
            var result: LicenseResult
            var connection: HttpURLConnection? = null
            try {
                val url = URL(VALIDATE_ENDPOINT)
                connection = url.openConnection() as HttpURLConnection
                connection.requestMethod = "POST"
                connection.connectTimeout = 12000
                connection.readTimeout = 12000
                connection.setRequestProperty("Content-Type", "application/json; charset=UTF-8")
                connection.setRequestProperty("Accept", "application/json")
                connection.doOutput = true
                connection.doInput = true

                val payload = JSONObject().apply {
                    put("key", trimmedKey)
                    put("deviceId", deviceId)
                    put("clientInfo", CLIENT_INFO)
                }

                OutputStreamWriter(connection.outputStream, StandardCharsets.UTF_8).use { writer ->
                    writer.write(payload.toString())
                    writer.flush()
                }

                val responseCode = connection.responseCode
                val stream = if (responseCode in 200..299) {
                    connection.inputStream
                } else {
                    connection.errorStream ?: connection.inputStream
                }

                val responseBody = BufferedReader(InputStreamReader(stream, StandardCharsets.UTF_8)).use { reader ->
                    reader.readText()
                }

                if (responseCode in 200..299) {
                    val json = JSONObject(responseBody)
                    val isValid = json.optBoolean("isValid", false)
                    val plan = json.optString("plan", if (isValid) "Plus" else "Free")
                    val expirationDate = json.optString("expirationDate", "")
                    val message = json.optString("message", if (isValid) "License activated successfully!" else "License validation failed.")

                    result = LicenseResult(
                        isValid = isValid,
                        isNetworkError = false,
                        plan = if (isValid) plan else "Free",
                        expirationDate = expirationDate,
                        message = message
                    )
                } else {
                    var errorMsg = "License validation failed."
                    try {
                        val json = JSONObject(responseBody)
                        errorMsg = json.optString("message", errorMsg)
                    } catch (ignored: Exception) {}

                    result = LicenseResult(
                        isValid = false,
                        isNetworkError = responseCode !in listOf(400, 401, 403, 404, 422),
                        plan = "Free",
                        expirationDate = "",
                        message = errorMsg
                    )
                }
            } catch (e: Exception) {
                Log.w(TAG, "License validation network error: ${e.message}")
                result = LicenseResult(
                    isValid = false,
                    isNetworkError = true,
                    plan = "Free",
                    expirationDate = "",
                    message = "Unable to reach the license server. Check your connection."
                )
            } finally {
                connection?.disconnect()
            }

            MAIN_HANDLER.post {
                callback(result)
            }
        }
    }
}
