package com.masterstrap.rbx.data

import android.content.Context
import android.util.Log
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.SocketTimeoutException
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Scoped-storage-safe localhost bridge used while Save and Launch starts
 * Roblox. Socket binding is synchronous: start() returns true only after the
 * bridge is ready, so a fast physical device cannot outrun the listener.
 */
object NativeBridgeServer {
    private const val TAG = "MasterstrapBridge"
    private const val PORT = 39671
    private const val REQUEST_SESSION = "MASTERSTRAP_GET_SESSION_V4"
    private const val PROBE_SESSION = "MASTERSTRAP_PROBE_SESSION_V1"
    private const val SESSION_AVAILABLE = "MASTERSTRAP_SESSION_AVAILABLE_V1"
    private const val CONSUME_SESSION = "MASTERSTRAP_CONSUME_SESSION_V4 "
    private const val MAX_JSON_BYTES = 60 * 1024
    private const val MAX_XML_BYTES = 16 * 1024
    private const val MAX_TURBO_BYTES = 8 * 1024
    private const val MAX_SHADER_BYTES = 8 * 1024
    private const val LIFETIME_MS = 90_000L
    // A freshly installed Roblox APK can create a short-lived bootstrap
    // process and restart itself once native extraction/profile setup finishes.
    // Keep only the already-authorized in-memory session available long enough
    // for that replacement process; storage is still consumed immediately.
    private const val PROCESS_RESTART_REPLAY_MS = 45_000L

    private val generation = AtomicInteger(0)
    @Volatile private var activeSocket: DatagramSocket? = null

    fun start(
        context: Context,
        sessionId: String,
        jsonPayload: ByteArray,
        xmlPayload: ByteArray,
        turboPayload: ByteArray,
        shaderPayload: ByteArray
    ): Boolean {
        if (sessionId.isBlank() || jsonPayload.isEmpty() ||
            jsonPayload.size > MAX_JSON_BYTES || xmlPayload.isEmpty() ||
            xmlPayload.size > MAX_XML_BYTES || turboPayload.isEmpty() ||
            turboPayload.size > MAX_TURBO_BYTES || shaderPayload.isEmpty() ||
            shaderPayload.size > MAX_SHADER_BYTES
        ) {
            ConfigExporter.writeNativeBridgeDiagnostic(
                context,
                "bridge_start_error=invalid_payload json_bytes=${jsonPayload.size} " +
                    "xml_bytes=${xmlPayload.size} turbo_bytes=${turboPayload.size} " +
                    "shader_bytes=${shaderPayload.size}\n"
            )
            return false
        }

        val appContext = context.applicationContext
        val runId = generation.incrementAndGet()
        activeSocket?.close()

        // Bind before returning to MainActivity. Roblox is launched only after
        // this succeeds, eliminating the old thread-start/socket-ready race.
        val socket = try {
            DatagramSocket(null).apply {
                reuseAddress = true
                bind(InetSocketAddress(InetAddress.getByName("127.0.0.1"), PORT))
                soTimeout = 500
            }
        } catch (error: Exception) {
            Log.e(TAG, "Native bridge bind failed", error)
            ConfigExporter.writeNativeBridgeDiagnostic(
                appContext,
                "bridge_run=$runId\nbridge_bind_error=${error.javaClass.name}: ${error.message}\n"
            )
            return false
        }
        activeSocket = socket

        val header = buildString {
            appendLine("MASTERSTRAP_SESSION_V4")
            appendLine(sessionId)
            appendLine(jsonPayload.size)
            appendLine(xmlPayload.size)
            appendLine(turboPayload.size)
            appendLine(shaderPayload.size)
        }.toByteArray(Charsets.UTF_8)
        val sessionPayload = header + jsonPayload + xmlPayload + turboPayload + shaderPayload
        val turboOwnsWifi = turboPayload.toString(Charsets.UTF_8)
            .contains("\"wifiLowLatency\":true")
        if (sessionPayload.size > 64 * 1024 - 512) {
            socket.close()
            activeSocket = null
            ConfigExporter.writeNativeBridgeDiagnostic(
                appContext,
                "bridge_start_error=session_datagram_too_large bytes=${sessionPayload.size}\n"
            )
            return false
        }

        val events = mutableListOf<String>()
        val logDirty = AtomicBoolean(false)
        val inspection = AtomicReference(
            "=== installed_roblox_inspection ===\n" +
                "status=pending\n" +
                "=== installed_roblox_inspection_end ===\n"
        )
        fun snapshot(): String = synchronized(events) {
            buildString {
                appendLine("bridge_run=$runId")
                appendLine("port=$PORT")
                appendLine("session_id=$sessionId")
                appendLine("json_bytes=${jsonPayload.size}")
                appendLine("xml_bytes=${xmlPayload.size}")
                appendLine("turbo_bytes=${turboPayload.size}")
                appendLine("shader_bytes=${shaderPayload.size}")
                events.forEach { appendLine(it) }
                append(inspection.get())
            }
        }
        fun flushLog(force: Boolean = false) {
            if (!force && !logDirty.compareAndSet(true, false)) return
            if (force) logDirty.set(false)
            ConfigExporter.writeNativeBridgeDiagnostic(appContext, snapshot())
        }
        fun record(message: String) {
            val safe = message.replace('\r', ' ').replace('\n', ' ').take(1500)
            synchronized(events) {
                events += "${System.currentTimeMillis()} $safe"
                // Keep the complete startup sequence but put a hard ceiling on
                // pathological native spam so one session cannot grow forever.
                if (events.size > 3000) {
                    events.subList(0, events.size - 3000).clear()
                }
            }
            logDirty.set(true)
        }

        val logWriter = Executors.newSingleThreadScheduledExecutor { task ->
            Thread(task, "MasterstrapBridgeLogWriter").apply { isDaemon = true }
        }
        logWriter.scheduleAtFixedRate(
            {
                if (generation.get() == runId) flushLog()
            },
            0,
            500,
            TimeUnit.MILLISECONDS
        )

        record("bridge_listening")

        // APK inspection is diagnostic-only and must never delay UDP receives.
        Thread({
            inspection.set(RobloxInstallInspector.inspect(appContext))
            record("installed_roblox_inspection_ready")
        }, "MasterstrapInstallInspector").apply {
            isDaemon = true
            start()
        }

        Thread({
            try {
                DatagramSocket().use { probe ->
                    val selfTest = "MASTERSTRAP_APP_V1 self_test".toByteArray()
                    probe.send(
                        DatagramPacket(
                            selfTest,
                            selfTest.size,
                            InetAddress.getByName("127.0.0.1"),
                            PORT
                        )
                    )
                }

                var deadline = System.currentTimeMillis() + LIFETIME_MS
                var claimed = false
                var consumed = false
                var replayUntil = 0L
                var probeCount = 0
                var replyCount = 0
                var replayReplyCount = 0
                var replayConsumeCount = 0
                var receiveCount = 0
                var safeHandoffReleased = false
                fun completeSafeHandoff(reason: String) {
                    if (safeHandoffReleased) return
                    safeHandoffReleased = true
                    SafeBoostController.releaseNow(reason)
                    record("safe_handoff_release=true reason=$reason")
                }
                val receiveBuffer = ByteArray(64 * 1024)
                while (generation.get() == runId && System.currentTimeMillis() < deadline) {
                    val packet = DatagramPacket(receiveBuffer, receiveBuffer.size)
                    try {
                        socket.receive(packet)
                    } catch (_: SocketTimeoutException) {
                        continue
                    }
                    if (!packet.address.isLoopbackAddress) {
                        record("rejected_non_loopback")
                        continue
                    }
                    val request = packet.data.decodeToString(
                        packet.offset,
                        packet.offset + packet.length
                    )
                    receiveCount++
                    if (request == PROBE_SESSION) {
                        val reply = SESSION_AVAILABLE.toByteArray(Charsets.UTF_8)
                        socket.send(DatagramPacket(reply, reply.size, packet.address, packet.port))
                        probeCount++
                        record(
                            "session_probe_reply attempt=$probeCount peer_port=${packet.port} " +
                                "receive_count=$receiveCount"
                        )
                    } else if (request == REQUEST_SESSION) {
                        // GET is idempotent until consume. Physical Android can
                        // drop the first localhost datagram while both apps are
                        // changing process state; never lock out a retry.
                        val now = System.currentTimeMillis()
                        val replayActive = consumed && now <= replayUntil
                        if (!consumed || replayActive) {
                            socket.send(
                                DatagramPacket(
                                    sessionPayload,
                                    sessionPayload.size,
                                    packet.address,
                                    packet.port
                                )
                            )
                            claimed = true
                            replyCount++
                            if (replayActive) replayReplyCount++
                            record(
                                "session_reply attempt=$replyCount replay=$replayActive " +
                                    "replay_attempt=$replayReplyCount bytes=${sessionPayload.size} " +
                                    "peer_port=${packet.port} receive_count=$receiveCount"
                            )
                        } else {
                            record("session_reply_rejected reason=replay_grace_expired")
                        }
                    } else if (request.startsWith(CONSUME_SESSION)) {
                        val requestedId = request.removePrefix(CONSUME_SESSION).trim()
                        val now = System.currentTimeMillis()
                        val firstConsume = !consumed && claimed && requestedId == sessionId &&
                            LaunchSessionStore.consumeFromBridge(appContext, sessionId)
                        val replayConsume = consumed && now <= replayUntil && requestedId == sessionId
                        val consumeAccepted = firstConsume || replayConsume
                        val reply = if (consumeAccepted) {
                            "MASTERSTRAP_SESSION_CONSUMED_V4"
                        } else {
                            "MASTERSTRAP_SESSION_REJECTED_V4"
                        }.toByteArray(Charsets.UTF_8)
                        socket.send(DatagramPacket(reply, reply.size, packet.address, packet.port))
                        if (firstConsume) {
                            consumed = true
                            replayUntil = now + PROCESS_RESTART_REPLAY_MS
                            // End the bridge when the restart window closes,
                            // rather than keeping an already-consumed launch
                            // available for the full initial lifetime.
                            deadline = minOf(deadline, replayUntil)
                            record(
                                "session_consume_first=true restart_replay_ms=$PROCESS_RESTART_REPLAY_MS " +
                                    "replay_until=$replayUntil"
                            )
                        } else if (replayConsume) {
                            replayConsumeCount++
                            record(
                                "session_consume_replay=true attempt=$replayConsumeCount " +
                                    "replay_until=$replayUntil"
                            )
                        }
                        record(
                            "session_consume=$consumeAccepted peer_port=${packet.port} " +
                                "replay_active=${consumed && now <= replayUntil}"
                        )
                    } else if (request.startsWith("MASTERSTRAP_")) {
                        val pipelineReady = request.contains("stage=pipeline-ready")
                        val turboWifiReady = request.contains("turbo_wifi=applied") &&
                            request.contains("held=1")
                        if (turboWifiReady) {
                            completeSafeHandoff("turbo_wifi_ready")
                        } else if (pipelineReady && !turboOwnsWifi) {
                            completeSafeHandoff("roblox_pipeline_ready")
                        }
                        record(request)
                    } else {
                        record("unknown_message bytes=${packet.length}")
                    }
                }
                record(
                    "bridge_finished claimed=$claimed consumed=$consumed probes=$probeCount replies=$replyCount " +
                        "replay_replies=$replayReplyCount replay_consumes=$replayConsumeCount " +
                        "receives=$receiveCount"
                )
            } catch (error: Exception) {
                // Closing an older generation is normal when Save and Launch is
                // pressed again; only the current bridge reports a real error.
                if (generation.get() == runId) {
                    Log.e(TAG, "Native bridge failed", error)
                    record("bridge_error=${error.javaClass.name}: ${error.message}")
                }
            } finally {
                if (generation.get() == runId) flushLog(force = true)
                logWriter.shutdownNow()
                socket.close()
                if (activeSocket === socket) activeSocket = null
            }
        }, "MasterstrapNativeBridge").apply {
            isDaemon = false
            start()
        }
        return true
    }
}
