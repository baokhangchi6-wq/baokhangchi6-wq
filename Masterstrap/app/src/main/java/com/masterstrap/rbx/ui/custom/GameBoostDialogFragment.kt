package com.masterstrap.rbx.ui.custom

import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.view.WindowManager
import android.widget.TextView
import androidx.fragment.app.DialogFragment
import com.masterstrap.rbx.R
import com.masterstrap.rbx.data.GamePresetItem

class GameBoostDialogFragment : DialogFragment() {

    private var preset: GamePresetItem? = null
    private var onBoostComplete: (() -> Unit)? = null
    private val handler = Handler(Looper.getMainLooper())

    companion object {
        fun newInstance(preset: GamePresetItem, onComplete: () -> Unit): GameBoostDialogFragment {
            return GameBoostDialogFragment().apply {
                this.preset = preset
                this.onBoostComplete = onComplete
            }
        }
    }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View? {
        val view = inflater.inflate(R.layout.dialog_game_boost, container, false)
        dialog?.window?.apply {
            setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
            requestFeature(Window.FEATURE_NO_TITLE)
            setDimAmount(0.80f)
            addFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND)
        }
        isCancelable = false
        return view
    }

    override fun onStart() {
        super.onStart()
        dialog?.window?.apply {
            setLayout(
                android.view.WindowManager.LayoutParams.MATCH_PARENT,
                android.view.WindowManager.LayoutParams.MATCH_PARENT
            )
            setDimAmount(0.75f)
        }
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        val tvTitle = view.findViewById<TextView>(R.id.tvBoostDialogTitle)
        val tvStatus = view.findViewById<TextView>(R.id.tvBoostDialogStatus)
        val tvStat = view.findViewById<TextView>(R.id.tvBoostDialogStat)
        val boostCircle = view.findViewById<BoostCircleProgressView>(R.id.boostCircleView)

        preset?.let { p ->
            tvTitle.text = "Boosting ${p.title}..."
            if (!p.thumbPath.isNullOrEmpty()) {
                try {
                    requireContext().assets.open(p.thumbPath).use { stream ->
                        val bmp = BitmapFactory.decodeStream(stream)
                        boostCircle.setGameBitmap(bmp)
                    }
                } catch (e: Exception) {
                    val fallback = BitmapFactory.decodeResource(resources, R.drawable.masterstrap)
                    boostCircle.setGameBitmap(fallback)
                }
            } else {
                val fallback = BitmapFactory.decodeResource(resources, R.drawable.masterstrap)
                boostCircle.setGameBitmap(fallback)
            }
        }

        // Multi-stage Cyber Game Boost sequence (~3.2s)
        handler.postDelayed({
            if (isAdded) {
                tvStatus.text = "⚡ Analyzing Engine & RAM Allocation..."
                tvStat.text = "INITIALIZING ENGINE"
            }
        }, 300L)

        handler.postDelayed({
            if (isAdded) {
                tvStatus.text = "🧹 Purging Memory Cache & Optimizing Pipeline..."
                tvStat.text = "MEMORY CACHE OPTIMIZED"
            }
        }, 1100L)

        handler.postDelayed({
            if (isAdded) {
                tvStatus.text = "🚀 Injecting ${preset?.title ?: "Game"} FastFlags..."
                tvStat.text = "GPU TURBO ENGAGED"
            }
        }, 2000L)

        handler.postDelayed({
            if (isAdded) {
                tvStatus.text = "✨ Engine Boosted & Profile Ready!"
                tvStat.text = "STATUS: 100% READY"
            }
        }, 2800L)

        handler.postDelayed({
            if (isAdded) {
                dismissAllowingStateLoss()
                onBoostComplete?.invoke()
            }
        }, 3200L)
    }

    override fun onDestroyView() {
        handler.removeCallbacksAndMessages(null)
        super.onDestroyView()
    }
}
