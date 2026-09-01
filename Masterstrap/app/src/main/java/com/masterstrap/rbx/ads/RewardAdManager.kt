package com.masterstrap.rbx.ads

import android.app.Activity
import android.app.Application
import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Choreographer
import android.widget.Toast
import com.masterstrap.rbx.R
import com.masterstrap.rbx.data.SettingsPersistence
import com.unity3d.mediation.LevelPlay
import com.unity3d.mediation.LevelPlayAdError
import com.unity3d.mediation.LevelPlayAdInfo
import com.unity3d.mediation.LevelPlayConfiguration
import com.unity3d.mediation.LevelPlayInitError
import com.unity3d.mediation.LevelPlayInitListener
import com.unity3d.mediation.LevelPlayInitRequest
import com.unity3d.mediation.rewarded.LevelPlayReward
import com.unity3d.mediation.rewarded.LevelPlayRewardedAd
import com.unity3d.mediation.rewarded.LevelPlayRewardedAdListener
import java.lang.ref.WeakReference

object RewardAdManager {
    private const val TAG = "MasterstrapAds"

    const val LEVELPLAY_APP_KEY = "274610415"
    const val LEVELPLAY_REWARDED_ID = "y3rmxilnttlub1ij"

    private val MAIN_HANDLER = Handler(Looper.getMainLooper())
    private const val REWARDED_WAIT_MS = 20000L

    @Volatile private var isInitialized = false
    @Volatile private var isInitializing = false
    @Volatile private var isLoading = false
    @Volatile private var isShowing = false
    @Volatile private var startupAuctionScheduled = false

    private var rewardedAd: LevelPlayRewardedAd? = null

    private var pendingAction: (() -> Unit)? = null
    private var pendingCancelAction: (() -> Unit)? = null
    private var pendingActivityRef: WeakReference<Activity>? = null
    private var timeoutRunnable: Runnable? = null
    private var startupRunnable: Runnable? = null
    private var startupFrame: Choreographer.FrameCallback? = null
    private var appContext: Context? = null

    private fun access(context: Context) = AdEligibility.resolve(
        SettingsPersistence.loadLicensePlan(context),
        SettingsPersistence.loadLicenseKey(context).isNotBlank()
    )

    private fun allowAdWork(context: Context, stage: String): Boolean {
        val state = access(context)
        if (state == AdEligibility.Access.FREE) return true
        startupRunnable?.let { MAIN_HANDLER.removeCallbacks(it) }
        startupRunnable = null
        startupFrame?.let { Choreographer.getInstance().removeFrameCallback(it) }
        startupFrame = null
        startupAuctionScheduled = false
        cancelTimeout()
        isLoading = false
        Log.i(TAG, "ad_policy=$state stage=$stage sdk_work=blocked")
        if (pendingAction != null) finishPending(allowed = state == AdEligibility.Access.AD_FREE)
        return false
    }

    /** Call after activation and on resume to cancel queued work immediately. */
    fun refreshEntitlement(activity: Activity) {
        appContext = activity.applicationContext
        if (allowAdWork(activity, "entitlement_refresh")) {
            if (isInitialized) createAndPreloadRewarded(activity.applicationContext)
            else scheduleStartupAuction(activity)
        }
    }

    /**
     * Starts the first LevelPlay auction after the Activity has rendered one
     * frame, then waits 200 ms so SDK/WebView startup cannot block the first
     * layout pass. LevelPlay still selects the highest eligible mediated bid.
     */
    fun scheduleStartupAuction(activity: Activity) {
        appContext = activity.applicationContext
        if (!allowAdWork(activity, "startup")) return
        if (startupAuctionScheduled || isInitialized || isInitializing) return
        startupAuctionScheduled = true
        val activityRef = WeakReference(activity)
        startupFrame = Choreographer.FrameCallback {
            startupFrame = null
            startupRunnable = Runnable {
                startupRunnable = null
                startupAuctionScheduled = false
                val host = activityRef.get() ?: return@Runnable
                if (!host.isFinishing && !host.isDestroyed && allowAdWork(host, "startup_200ms")) {
                    Log.i(TAG, "ad_policy=FREE startup_delay_ms=200 sdk_init=requested")
                    initialize(host.application)
                }
            }
            MAIN_HANDLER.postDelayed(startupRunnable!!, 200L)
        }
        Choreographer.getInstance().postFrameCallback(startupFrame!!)
    }

    fun initialize(application: Application) {
        appContext = application
        if (!allowAdWork(application, "initialize")) return
        if (isInitialized || isInitializing) return
        isInitializing = true

        Thread {
            if (!isNetworkAvailable(application)) {
                Log.d(TAG, "LevelPlay init deferred while offline")
                isInitializing = false
                return@Thread
            }

            Log.i(TAG, "Initializing LevelPlay with key $LEVELPLAY_APP_KEY asynchronously")
            val initRequest = LevelPlayInitRequest.Builder(LEVELPLAY_APP_KEY).build()

            MAIN_HANDLER.post {
                if (!allowAdWork(application, "before_sdk_init")) {
                    isInitializing = false
                    return@post
                }
                try {
                    LevelPlay.init(application, initRequest, object : LevelPlayInitListener {
                        override fun onInitSuccess(configuration: LevelPlayConfiguration) {
                            isInitializing = false
                            isInitialized = true
                            if (!allowAdWork(application, "init_success")) return
                            Log.i(TAG, "LevelPlay init successful. Preloading live rewarded auction...")
                            createAndPreloadRewarded(application)
                        }

                        override fun onInitFailed(error: LevelPlayInitError) {
                            isInitializing = false
                            isInitialized = false
                            Log.w(TAG, "LevelPlay init failed: $error")
                        }
                    })
                } catch (e: Exception) {
                    isInitializing = false
                    Log.w(TAG, "LevelPlay init error: ${e.message}")
                }
            }
        }.start()
    }

    private fun createAndPreloadRewarded(context: Context) {
        if (!allowAdWork(context, "create_rewarded")) return
        if (rewardedAd == null) {
            rewardedAd = LevelPlayRewardedAd(LEVELPLAY_REWARDED_ID)
            rewardedAd?.setListener(object : LevelPlayRewardedAdListener {
                private var isRewarded = false
                private var isClosed = false
                private var isResolved = false

                override fun onAdLoaded(adInfo: LevelPlayAdInfo) {
                    isLoading = false
                    if (!allowAdWork(context, "ad_loaded")) return
                    Log.i(TAG, "LevelPlay live rewarded loaded: ${adInfo.adNetwork}")
                    tryShowPendingRewarded()
                }

                override fun onAdLoadFailed(error: LevelPlayAdError) {
                    isLoading = false
                    Log.w(TAG, "LevelPlay rewarded load failed: $error")
                }

                override fun onAdDisplayed(adInfo: LevelPlayAdInfo) {
                    isRewarded = false
                    isClosed = false
                    isResolved = false
                    Log.i(TAG, "LevelPlay live rewarded impression: ${adInfo.adNetwork}")
                }

                override fun onAdDisplayFailed(error: LevelPlayAdError, adInfo: LevelPlayAdInfo) {
                    Log.w(TAG, "LevelPlay rewarded display failed: $error")
                    finishPending(allowed = false, message = "Ad display failed. Action canceled.")
                    preload()
                }

                override fun onAdRewarded(reward: LevelPlayReward, adInfo: LevelPlayAdInfo) {
                    isRewarded = true
                    Log.i(TAG, "LevelPlay rewarded completion received!")
                    if (isClosed) {
                        resolve(true)
                    }
                }

                override fun onAdClosed(adInfo: LevelPlayAdInfo) {
                    isClosed = true
                    Log.i(TAG, "LevelPlay rewarded closed")
                    if (isRewarded) {
                        resolve(true)
                    } else {
                        MAIN_HANDLER.postDelayed({ resolve(isRewarded) }, 600L)
                    }
                }

                private fun resolve(completed: Boolean) {
                    if (isResolved) return
                    isResolved = true
                    finishPending(
                        allowed = completed,
                        message = if (completed) null else "Ad not completed. Action canceled."
                    )
                    preload()
                }

                override fun onAdClicked(adInfo: LevelPlayAdInfo) {}
                override fun onAdInfoChanged(adInfo: LevelPlayAdInfo) {}
            })
        }
        preload()
    }

    fun preload() {
        val context = appContext ?: return
        if (!allowAdWork(context, "preload")) return
        if (!isInitialized || isLoading) return
        if (rewardedAd?.isAdReady == true) return

        isLoading = true
        Log.i(TAG, "Preloading next live Rewarded Ad auction...")
        rewardedAd?.loadAd()
    }

    /** Shows the already-preloaded rewarded ad immediately. */
    fun showRewardedAdWithClaimDialog(
        activity: Activity,
        adPurposeTitle: String? = null,
        adPurposeMsg: String? = null,
        onRewarded: () -> Unit,
        onCanceled: () -> Unit = {}
    ) {
        if (access(activity) == AdEligibility.Access.AD_FREE) {
            allowAdWork(activity, "claim_bypass")
            onRewarded()
            return
        }
        if (access(activity) != AdEligibility.Access.FREE) { onCanceled(); return }

        val title = adPurposeTitle ?: activity.getString(R.string.dialog_claim_ad_title)
        Log.d(TAG, "Direct rewarded request: $title (${adPurposeMsg.orEmpty()})")
        showRewardedAd(
            activity = activity,
            adPurposeTitle = title,
            onRewarded = onRewarded,
            onCanceled = onCanceled
        )
    }

    fun showRewardedAd(
        activity: Activity,
        adPurposeTitle: String = "Watch Ad to Unlock",
        onRewarded: () -> Unit,
        onCanceled: () -> Unit = {}
    ) {
        appContext = activity.applicationContext
        if (access(activity) == AdEligibility.Access.AD_FREE) {
            allowAdWork(activity, "show_bypass")
            onRewarded()
            return
        }
        if (access(activity) != AdEligibility.Access.FREE) { onCanceled(); return }

        if (isShowing) {
            onCanceled()
            return
        }

        // Register the request before lazy initialization. A very fast init
        // callback can otherwise load an ad before there is a pending action
        // to consume it.
        pendingAction = onRewarded
        pendingCancelAction = onCanceled
        pendingActivityRef = WeakReference(activity)

        if (!isInitialized) {
            Log.i(TAG, "Lazy LevelPlay initialization requested by rewarded action: $adPurposeTitle")
            initialize(activity.application)
        } else if (rewardedAd == null) {
            createAndPreloadRewarded(activity.applicationContext)
        }

        if (tryShowPendingRewarded()) return

        preload()
        scheduleTimeout(activity)
    }

    private fun tryShowPendingRewarded(): Boolean {
        val activity = pendingActivityRef?.get() ?: return false
        if (!allowAdWork(activity, "before_show")) return true
        if (pendingAction == null || isShowing || activity.isFinishing || activity.isDestroyed) {
            return false
        }

        if (rewardedAd?.isAdReady == true) {
            isShowing = true
            cancelTimeout()
            Log.i(TAG, "Showing live LevelPlay Rewarded Ad")
            rewardedAd?.showAd(activity)
            return true
        }

        return false
    }

    private fun scheduleTimeout(activity: Activity) {
        if (!allowAdWork(activity, "schedule_timeout")) return
        cancelTimeout()
        timeoutRunnable = Runnable {
            if (tryShowPendingRewarded()) return@Runnable
            Log.w(TAG, "Rewarded ad wait timeout: No live inventory filled within ${REWARDED_WAIT_MS}ms")
            finishPending(
                allowed = false,
                message = "Live ad currently unavailable. Please check your internet connection."
            )
        }
        MAIN_HANDLER.postDelayed(timeoutRunnable!!, REWARDED_WAIT_MS)
    }

    private fun cancelTimeout() {
        timeoutRunnable?.let { MAIN_HANDLER.removeCallbacks(it) }
        timeoutRunnable = null
    }

    private fun finishPending(allowed: Boolean, message: String? = null) {
        cancelTimeout()
        isShowing = false
        val activity = pendingActivityRef?.get()
        val action = if (allowed) pendingAction else null
        val cancelAction = if (allowed) null else pendingCancelAction

        pendingAction = null
        pendingCancelAction = null
        pendingActivityRef = null

        MAIN_HANDLER.post {
            if (message != null && activity != null && !activity.isFinishing) {
                Toast.makeText(activity, message, Toast.LENGTH_SHORT).show()
            }
            if (allowed) {
                action?.invoke()
            } else {
                cancelAction?.invoke()
            }
        }
    }

    private fun isNetworkAvailable(context: Context): Boolean {
        return try {
            val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager ?: return false
            val network = cm.activeNetwork ?: return false
            val caps = cm.getNetworkCapabilities(network) ?: return false
            caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
        } catch (e: Exception) {
            false
        }
    }
}
