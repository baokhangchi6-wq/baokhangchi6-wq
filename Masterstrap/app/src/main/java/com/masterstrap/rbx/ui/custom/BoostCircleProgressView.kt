package com.masterstrap.rbx.ui.custom

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.*
import android.util.AttributeSet
import android.view.View
import android.view.animation.AccelerateDecelerateInterpolator
import android.view.animation.LinearInterpolator
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin

class BoostCircleProgressView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private var gameBitmap: Bitmap? = null
    private var circularBitmapShader: BitmapShader? = null
    private val shaderMatrix = Matrix()

    private var rotationAngle = 0f
    private var pulseScale = 1.0f
    private var sweepAngle = 0f

    private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 5f
        strokeCap = Paint.Cap.ROUND
    }

    private val outerDashPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 3f
        pathEffect = DashPathEffect(floatArrayOf(15f, 15f), 0f)
        color = Color.parseColor("#38BDF8")
    }

    private val glowPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 12f
        color = Color.parseColor("#4D38BDF8")
    }

    private val avatarPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        isFilterBitmap = true
    }

    private val particlePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = Color.parseColor("#67E8F9")
    }

    private var rotateAnimator: ValueAnimator? = null
    private var pulseAnimator: ValueAnimator? = null

    init {
        startAnimations()
    }

    fun setGameBitmap(bitmap: Bitmap?) {
        this.gameBitmap = bitmap
        updateShader()
        invalidate()
    }

    private fun updateShader() {
        val bmp = gameBitmap ?: return
        circularBitmapShader = BitmapShader(bmp, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP)
        avatarPaint.shader = circularBitmapShader
    }

    private fun startAnimations() {
        rotateAnimator = ValueAnimator.ofFloat(0f, 360f).apply {
            duration = 2000L
            repeatCount = ValueAnimator.INFINITE
            interpolator = LinearInterpolator()
            addUpdateListener {
                rotationAngle = it.animatedValue as Float
                outerDashPaint.pathEffect = DashPathEffect(floatArrayOf(18f, 18f), rotationAngle)
                invalidate()
            }
            start()
        }

        pulseAnimator = ValueAnimator.ofFloat(0.95f, 1.06f).apply {
            duration = 800L
            repeatCount = ValueAnimator.INFINITE
            repeatMode = ValueAnimator.REVERSE
            interpolator = AccelerateDecelerateInterpolator()
            addUpdateListener {
                pulseScale = it.animatedValue as Float
                invalidate()
            }
            start()
        }
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        val ringGradient = SweepGradient(
            w / 2f, h / 2f,
            intArrayOf(
                Color.parseColor("#0038BDF8"),
                Color.parseColor("#2563EB"),
                Color.parseColor("#38BDF8"),
                Color.parseColor("#A855F7"),
                Color.parseColor("#38BDF8")
            ),
            floatArrayOf(0f, 0.25f, 0.5f, 0.75f, 1.0f)
        )
        ringPaint.shader = ringGradient
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        val cx = width / 2f
        val cy = height / 2f
        val radius = (min(width, height) / 2f) - 16f
        if (radius <= 0) return

        canvas.save()
        canvas.scale(pulseScale, pulseScale, cx, cy)

        // 1. Draw glowing outer halo
        glowPaint.strokeWidth = 14f * pulseScale
        canvas.drawCircle(cx, cy, radius + 4f, glowPaint)

        // 2. Draw outer dashed rotating tech ring
        canvas.save()
        canvas.rotate(rotationAngle, cx, cy)
        canvas.drawCircle(cx, cy, radius + 6f, outerDashPaint)
        canvas.restore()

        // 3. Draw high-speed gradient turbine ring
        canvas.save()
        canvas.rotate(-rotationAngle * 1.5f, cx, cy)
        canvas.drawCircle(cx, cy, radius, ringPaint)
        canvas.restore()

        // 4. Draw orbiting cyber-energy particles
        val particleRadius = radius + 6f
        for (i in 0 until 4) {
            val angleRad = Math.toRadians((rotationAngle + i * 90).toDouble())
            val px = (cx + particleRadius * cos(angleRad)).toFloat()
            val py = (cy + particleRadius * sin(angleRad)).toFloat()
            particlePaint.alpha = (180 + 75 * sin(angleRad)).toInt().coerceIn(80, 255)
            canvas.drawCircle(px, py, 4.5f, particlePaint)
        }

        // 5. Draw Perfect Circular Avatar
        val avatarRadius = radius - 8f
        val bmp = gameBitmap
        if (bmp != null && circularBitmapShader != null) {
            val scale = (avatarRadius * 2f) / min(bmp.width, bmp.height).toFloat()
            val dx = cx - (bmp.width * scale) / 2f
            val dy = cy - (bmp.height * scale) / 2f

            shaderMatrix.reset()
            shaderMatrix.setScale(scale, scale)
            shaderMatrix.postTranslate(dx, dy)
            circularBitmapShader?.setLocalMatrix(shaderMatrix)

            canvas.drawCircle(cx, cy, avatarRadius, avatarPaint)
        } else {
            val fallbackPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                color = Color.parseColor("#0F172A")
            }
            canvas.drawCircle(cx, cy, avatarRadius, fallbackPaint)
        }

        // 6. Draw inner glowing circular rim
        val rimPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            strokeWidth = 2.5f
            color = Color.parseColor("#8038BDF8")
        }
        canvas.drawCircle(cx, cy, avatarRadius, rimPaint)

        canvas.restore()
    }

    override fun onDetachedFromWindow() {
        rotateAnimator?.cancel()
        pulseAnimator?.cancel()
        super.onDetachedFromWindow()
    }
}
