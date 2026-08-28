package com.masterstrap.rbx.ui.custom

import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.widget.TextView
import androidx.fragment.app.DialogFragment
import com.masterstrap.rbx.R
import java.util.concurrent.atomic.AtomicBoolean

class LoadingDialogFragment : DialogFragment() {

    private var tvTaskCurrently: TextView? = null
    private var tvLoadingStatus: TextView? = null
    private var boostLaunchProgress: BoostLaunchProgressView? = null

    private var pendingText: String? = null
    private var pendingStatus: String? = null
    private var pendingProgress: Int = 0
    private var pendingLaunchCompletion: (() -> Unit)? = null
    private var launchCompletionStarted: Boolean = false

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View? {
        val view = inflater.inflate(R.layout.dialog_loading_launch, container, false)
        dialog?.window?.apply {
            setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
            requestFeature(Window.FEATURE_NO_TITLE)
        }
        isCancelable = false
        return view
    }

    override fun onStart() {
        super.onStart()
        dialog?.window?.apply {
            setLayout(android.view.WindowManager.LayoutParams.MATCH_PARENT, android.view.WindowManager.LayoutParams.MATCH_PARENT)
            setDimAmount(0.75f)
        }
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        tvTaskCurrently = view.findViewById(R.id.textview_taskcurrently)
        tvLoadingStatus = view.findViewById(R.id.textview_loadingstatus)
        boostLaunchProgress = view.findViewById(R.id.boost_launch_progress)

        pendingText?.let { tvTaskCurrently?.text = it }
        pendingStatus?.let { tvLoadingStatus?.text = it }
        boostLaunchProgress?.setProgress(pendingProgress)

        if (launchCompletionStarted) {
            startCompletionAnimationIfReady()
        }
    }

    fun setMessageText(text: String) {
        pendingText = text
        tvTaskCurrently?.text = text
    }

    fun setMessageStatus(text: String) {
        pendingStatus = text
        tvLoadingStatus?.text = text
        if (text.endsWith("%")) {
            try {
                setProgress(text.substring(0, text.length - 1).toInt())
            } catch (ignored: NumberFormatException) {}
        }
    }

    fun setProgress(progress: Int) {
        pendingProgress = progress.coerceIn(0, 100)
        boostLaunchProgress?.setProgress(pendingProgress)
    }

    fun completeAndLaunch(launchAction: () -> Unit) {
        if (launchCompletionStarted) return
        launchCompletionStarted = true
        val delivered = AtomicBoolean(false)
        pendingLaunchCompletion = {
            if (delivered.compareAndSet(false, true)) {
                launchAction.invoke()
            }
        }
        setProgress(100)
        setMessageStatus("100%")
        startCompletionAnimationIfReady()

        Handler(Looper.getMainLooper()).postDelayed({
            if (boostLaunchProgress == null && pendingLaunchCompletion != null) {
                val fallback = pendingLaunchCompletion
                pendingLaunchCompletion = null
                fallback?.invoke()
            }
        }, 700L)
    }

    private fun startCompletionAnimationIfReady() {
        val action = pendingLaunchCompletion ?: return
        boostLaunchProgress?.completeAndLaunch {
            pendingLaunchCompletion = null
            action.invoke()
        }
    }
}
