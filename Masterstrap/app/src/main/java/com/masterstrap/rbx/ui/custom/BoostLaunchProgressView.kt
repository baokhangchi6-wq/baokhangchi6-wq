package com.masterstrap.rbx.ui.custom

import android.animation.Animator
import android.animation.AnimatorListenerAdapter
import android.animation.ValueAnimator
import android.content.Context
import android.graphics.*
import android.util.AttributeSet
import android.view.View
import android.view.animation.AccelerateInterpolator
import android.view.animation.DecelerateInterpolator
import android.view.animation.LinearInterpolator
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.random.Random

/**
 * Hyper-detailed 3D Futuristic Rocket with dynamic warp space streaming stars,
 * converging FastFlags data text matrix, and full-screen upward trajectory launch animation.
 */
class BoostLaunchProgressView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val path = Path()

    private var progress: Float = 0f
    private var launchFraction: Float = 0f
    private var flightPhase: Float = 0f
    private var launchAnimationStarted: Boolean = false
    private var flightAnimator: ValueAnimator? = null

    private var entryFraction: Float = 0f
    private var entryAnimationStarted: Boolean = false

    private val convergingKeywords = arrayOf(
        "DFIntTargetFps",
        "FastFlagProfile",
        "VulkanEngine",
        "MemoryOptimized",
        "TaskScheduler",
        "GraphicsQuality",
        "GameBoostTurbo",
        "ClientAppSettings"
    )

    private val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
        textAlign = Paint.Align.CENTER
    }

    private val textBgPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = Color.parseColor("#E60F172A")
    }

    private val textBorderPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 2f
        color = Color.parseColor("#6638BDF8")
    }

    private val starPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#4D38BDF8")
        strokeCap = Paint.Cap.ROUND
    }

    // Warp speed streaming stars
    private val starCount = 28
    private val starX = FloatArray(starCount)
    private val starY = FloatArray(starCount)
    private val starSpeed = FloatArray(starCount)
    private val starLength = FloatArray(starCount)

    init {
        setLayerType(LAYER_TYPE_SOFTWARE, null)
        initStars()
    }

    private fun initStars() {
        val rand = Random(42)
        for (i in 0 until starCount) {
            starX[i] = rand.nextFloat()
            starY[i] = rand.nextFloat()
            starSpeed[i] = 0.008f + rand.nextFloat() * 0.018f
            starLength[i] = 12f + rand.nextFloat() * 24f
        }
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        startEntryAnimation()
    }

    override fun onDetachedFromWindow() {
        stopFlightAnimator()
        super.onDetachedFromWindow()
    }

    fun setProgress(value: Int) {
        progress = value.toFloat().coerceIn(0f, 100f)
        invalidate()
    }

    fun completeAndLaunch(onRocketExit: (() -> Unit)?) {
        if (launchAnimationStarted) return
        launchAnimationStarted = true
        progress = 100f

        val screenHeight = resources.displayMetrics.heightPixels.toFloat()
        val animator = ValueAnimator.ofFloat(0f, 1f).apply {
            duration = 1100L
            interpolator = AccelerateInterpolator(2.5f)
            addUpdateListener { valueAnimator ->
                launchFraction = valueAnimator.animatedValue as Float
                // Translate view across full screen to exit smoothly past the top of the display
                translationY = -launchFraction * (screenHeight * 0.95f)
                if (launchFraction > 0.85f) {
                    alpha = (1f - (launchFraction - 0.85f) / 0.15f).coerceIn(0f, 1f)
                }
                invalidate()
            }
            addListener(object : AnimatorListenerAdapter() {
                override fun onAnimationEnd(animation: Animator) {
                    stopFlightAnimator()
                    onRocketExit?.invoke()
                }
            })
        }
        animator.start()
    }

    private fun startEntryAnimation() {
        if (entryAnimationStarted) return
        entryAnimationStarted = true
        entryFraction = 0f
        val animator = ValueAnimator.ofFloat(0f, 1f).apply {
            duration = 550L
            interpolator = DecelerateInterpolator(1.8f)
            addUpdateListener { valueAnimator ->
                entryFraction = valueAnimator.animatedValue as Float
                invalidate()
            }
            addListener(object : AnimatorListenerAdapter() {
                override fun onAnimationEnd(animation: Animator) {
                    startFlightAnimator()
                }
            })
        }
        animator.start()
    }

    private fun startFlightAnimator() {
        if (launchAnimationStarted || flightAnimator != null) return
        flightAnimator = ValueAnimator.ofFloat(0f, 1f).apply {
            duration = 1800L
            repeatCount = ValueAnimator.INFINITE
            repeatMode = ValueAnimator.RESTART
            interpolator = LinearInterpolator()
            addUpdateListener { animator ->
                flightPhase = animator.animatedValue as Float
                invalidate()
            }
        }
        flightAnimator?.start()
    }

    private fun stopFlightAnimator() {
        flightAnimator?.cancel()
        flightAnimator = null
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        val width = width.toFloat()
        val height = height.toFloat()
        if (width <= 0 || height <= 0) return

        val size = minOf(width, height)
        val centerX = width / 2f
        val centerY = height / 2f
        val radius = size * 0.38f

        // 1. Draw Warp Speed Streaming Stars in background
        drawWarpStars(canvas, width, height)

        // 2. Draw converging text streams swirling into the rocket core
        if (launchFraction < 0.3f && entryFraction > 0.2f) {
            drawConvergingTextStreams(canvas, centerX, centerY, width, height)
        }

        // 3. Draw 3D Plasma Futuristic Rocket
        drawRocket(canvas, centerX, centerY, radius)
    }

    private fun drawWarpStars(canvas: Canvas, width: Float, height: Float) {
        val density = resources.displayMetrics.density
        val speedMultiplier = 1f + 3f * launchFraction

        for (i in 0 until starCount) {
            starY[i] = (starY[i] + starSpeed[i] * speedMultiplier) % 1.0f
            val px = starX[i] * width
            val py = starY[i] * height
            val len = starLength[i] * density * (1f + 2f * launchFraction)

            starPaint.strokeWidth = (1.5f + (i % 3) * 0.8f) * density
            starPaint.alpha = ((0.2f + (i % 4) * 0.2f) * entryFraction * 255).toInt().coerceIn(30, 220)

            canvas.drawLine(px, py, px, py + len, starPaint)
        }
    }

    private fun drawConvergingTextStreams(
        canvas: Canvas,
        centerX: Float,
        centerY: Float,
        viewWidth: Float,
        viewHeight: Float
    ) {
        val density = resources.displayMetrics.density
        textPaint.textSize = 10.5f * density
        val maxDist = minOf(viewWidth * 0.40f, viewHeight * 0.38f)
        val minDist = 30f * density

        val normProgress = (progress / 100f).coerceIn(0f, 1f)
        val convergeRatio = (1f - normProgress)

        for (i in convergingKeywords.indices) {
            val baseAngle = (i * (360f / convergingKeywords.size))
            val currentAngle = (baseAngle + flightPhase * 180f) * (PI / 180.0)
            val currentDist = minDist + maxDist * convergeRatio

            val tx = (centerX + currentDist * cos(currentAngle)).toFloat()
            val ty = (centerY + currentDist * sin(currentAngle) * 0.72f).toFloat()

            val text = convergingKeywords[i]
            val textWidth = textPaint.measureText(text)
            val textAlpha = ((0.35f + 0.65f * normProgress) * entryFraction * 255).toInt().coerceIn(40, 255)

            // Background pill strictly clamped to prevent screen edge clipping
            val pillPaddingH = 7f * density
            val pillPaddingV = 3.5f * density
            val halfPillWidth = textWidth / 2f + pillPaddingH
            val safeTx = tx.coerceIn(halfPillWidth + 10f * density, viewWidth - halfPillWidth - 10f * density)

            val rect = RectF(
                safeTx - halfPillWidth,
                ty - 10f * density - pillPaddingV,
                safeTx + halfPillWidth,
                ty + 4f * density + pillPaddingV
            )

            textBgPaint.alpha = (textAlpha * 0.92f).toInt()
            canvas.drawRoundRect(rect, 6f * density, 6f * density, textBgPaint)

            textBorderPaint.alpha = (textAlpha * 0.75f).toInt()
            canvas.drawRoundRect(rect, 6f * density, 6f * density, textBorderPaint)

            textPaint.color = Color.parseColor("#38BDF8")
            textPaint.alpha = textAlpha
            canvas.drawText(text, safeTx, ty, textPaint)
        }
    }

    private fun drawRocket(canvas: Canvas, centerX: Float, centerY: Float, radius: Float) {
        val flightWave = sin(flightPhase * PI * 2.0).toFloat()
        val entryOffset = (1f - entryFraction) * radius * 2.0f
        val baseY = centerY - radius * 0.08f + dp(6f) * flightWave + entryOffset

        var alpha = entryFraction
        val flamePulse = 0.5f + 0.5f * sin(flightPhase * PI * 4.0).toFloat()
        val scale = (1.60f + 0.15f * launchFraction) * resources.displayMetrics.density

        canvas.save()
        canvas.translate(centerX, baseY)
        canvas.scale(scale, scale)

        // 1. Massive Radial Plasma Aura behind rocket & engine
        val auraRadius = 65f + 45f * launchFraction
        paint.style = Paint.Style.FILL
        paint.shader = RadialGradient(
            0f, 25f,
            auraRadius,
            intArrayOf(
                withAlpha(Color.parseColor("#38BDF8"), alpha * (0.50f + 0.45f * launchFraction)),
                withAlpha(Color.parseColor("#0284C7"), alpha * 0.25f),
                Color.TRANSPARENT
            ),
            floatArrayOf(0f, 0.55f, 1f),
            Shader.TileMode.CLAMP
        )
        canvas.drawCircle(0f, 25f, auraRadius, paint)
        paint.shader = null

        // 2. Primary Outer Plasma Jet Exhaust Flame (Electric Neon Blue)
        path.reset()
        path.moveTo(-12f, 22f)
        path.cubicTo(
            -16f, 42f + 16f * flamePulse + 55f * launchFraction,
            -7f, 65f + 20f * flamePulse + 110f * launchFraction,
            0f, 78f + 24f * flamePulse + 150f * launchFraction
        )
        path.cubicTo(
            7f, 65f + 20f * flamePulse + 110f * launchFraction,
            16f, 42f + 16f * flamePulse + 55f * launchFraction,
            12f, 22f
        )
        path.close()
        paint.shader = LinearGradient(
            0f, 22f, 0f, 78f + 150f * launchFraction,
            withAlpha(Color.parseColor("#0284C7"), alpha * 0.95f),
            withAlpha(Color.parseColor("#38BDF8"), alpha * 0.20f),
            Shader.TileMode.CLAMP
        )
        paint.setShadowLayer(18f, 0f, 8f, withAlpha(Color.parseColor("#38BDF8"), alpha))
        canvas.drawPath(path, paint)
        paint.clearShadowLayer()

        // 3. Middle Intense Plasma Core (Cyan-Aqua)
        path.reset()
        path.moveTo(-8f, 22f)
        path.cubicTo(
            -10f, 36f + 12f * flamePulse + 40f * launchFraction,
            -4f, 52f + 14f * flamePulse + 80f * launchFraction,
            0f, 62f + 16f * flamePulse + 105f * launchFraction
        )
        path.cubicTo(
            4f, 52f + 14f * flamePulse + 80f * launchFraction,
            10f, 36f + 12f * flamePulse + 40f * launchFraction,
            8f, 22f
        )
        path.close()
        paint.shader = LinearGradient(
            0f, 22f, 0f, 62f + 105f * launchFraction,
            withAlpha(Color.parseColor("#67E8F9"), alpha * 0.95f),
            withAlpha(Color.parseColor("#38BDF8"), alpha * 0.15f),
            Shader.TileMode.CLAMP
        )
        canvas.drawPath(path, paint)

        // 4. White-Hot Nuclear Plasma Core Center
        path.reset()
        path.moveTo(-4f, 22f)
        path.cubicTo(
            -5f, 30f + 8f * flamePulse,
            -2f, 38f + 8f * flamePulse,
            0f, 44f + 10f * flamePulse
        )
        path.cubicTo(
            2f, 38f + 8f * flamePulse,
            5f, 30f + 8f * flamePulse,
            4f, 22f
        )
        path.close()
        paint.shader = LinearGradient(
            0f, 22f, 0f, 44f,
            withAlpha(Color.WHITE, alpha * 0.98f),
            withAlpha(Color.parseColor("#67E8F9"), alpha * 0.35f),
            Shader.TileMode.CLAMP
        )
        canvas.drawPath(path, paint)
        paint.shader = null

        // 5. Left & Right Thruster Fins
        // Left Fin
        path.reset()
        path.moveTo(-12f, 2f)
        path.lineTo(-24f, 18f)
        path.cubicTo(-26f, 22f, -22f, 24f, -14f, 20f)
        path.lineTo(-12f, 14f)
        path.close()
        paint.shader = LinearGradient(
            -24f, 2f, -12f, 24f,
            withAlpha(Color.parseColor("#1E293B"), alpha),
            withAlpha(Color.parseColor("#0F172A"), alpha),
            Shader.TileMode.CLAMP
        )
        canvas.drawPath(path, paint)

        // Left Fin Neon Cyan Edge
        paint.shader = null
        paint.style = Paint.Style.STROKE
        paint.strokeWidth = 1.5f
        paint.color = withAlpha(Color.parseColor("#38BDF8"), alpha * 0.9f)
        canvas.drawPath(path, paint)
        paint.style = Paint.Style.FILL

        // Right Fin
        path.reset()
        path.moveTo(12f, 2f)
        path.lineTo(24f, 18f)
        path.cubicTo(26f, 22f, 22f, 24f, 14f, 20f)
        path.lineTo(12f, 14f)
        path.close()
        paint.shader = LinearGradient(
            12f, 2f, 24f, 24f,
            withAlpha(Color.parseColor("#334155"), alpha),
            withAlpha(Color.parseColor("#1E293B"), alpha),
            Shader.TileMode.CLAMP
        )
        canvas.drawPath(path, paint)

        // Right Fin Neon Cyan Edge
        paint.shader = null
        paint.style = Paint.Style.STROKE
        paint.strokeWidth = 1.5f
        paint.color = withAlpha(Color.parseColor("#38BDF8"), alpha * 0.9f)
        canvas.drawPath(path, paint)
        paint.style = Paint.Style.FILL

        // 6. Rocket Fuselage Body (Aerodynamic Bullet Shape)
        path.reset()
        path.moveTo(0f, -44f) // Sharp nosecone tip
        path.cubicTo(-18f, -22f, -17f, 10f, -13f, 22f) // Left fuselage curve
        path.lineTo(13f, 22f) // Base
        path.cubicTo(17f, 10f, 18f, -22f, 0f, -44f) // Right fuselage curve
        path.close()

        // 3D Titanium Body Shader (Left shadow to right specular highlight)
        paint.shader = LinearGradient(
            -16f, 0f, 16f, 0f,
            intArrayOf(
                withAlpha(Color.parseColor("#0F172A"), alpha),
                withAlpha(Color.parseColor("#1E293B"), alpha),
                withAlpha(Color.parseColor("#334155"), alpha),
                withAlpha(Color.parseColor("#475569"), alpha),
                withAlpha(Color.parseColor("#1E293B"), alpha)
            ),
            floatArrayOf(0f, 0.25f, 0.55f, 0.8f, 1.0f),
            Shader.TileMode.CLAMP
        )
        canvas.drawPath(path, paint)

        // Fuselage Specular Highlight Line
        paint.shader = null
        paint.style = Paint.Style.STROKE
        paint.strokeWidth = 1.8f
        paint.color = withAlpha(Color.parseColor("#64748B"), alpha * 0.7f)
        canvas.drawPath(path, paint)
        paint.style = Paint.Style.FILL

        // 7. Futuristic Cockpit Window (Glowing Cyan Hexagon / Diamond)
        val cockpitRect = RectF(-6f, -22f, 6f, -10f)
        paint.shader = RadialGradient(
            0f, -16f,
            8f,
            withAlpha(Color.WHITE, alpha),
            withAlpha(Color.parseColor("#38BDF8"), alpha * 0.95f),
            Shader.TileMode.CLAMP
        )
        canvas.drawRoundRect(cockpitRect, 3f, 3f, paint)

        paint.shader = null
        paint.style = Paint.Style.STROKE
        paint.strokeWidth = 1.4f
        paint.color = withAlpha(Color.parseColor("#BAE6FD"), alpha)
        canvas.drawRoundRect(cockpitRect, 3f, 3f, paint)
        paint.style = Paint.Style.FILL

        // 8. Cyber Tech Trim Stripes (Horizontal Neon Bands)
        paint.color = withAlpha(Color.parseColor("#38BDF8"), alpha * 0.9f)
        canvas.drawRect(-10f, 0f, 10f, 2.5f, paint)
        canvas.drawRect(-12f, 8f, 12f, 10f, paint)

        // 9. Thruster Exhaust Nozzle (Dark Alloy)
        path.reset()
        path.moveTo(-11f, 22f)
        path.lineTo(-13f, 26f)
        path.lineTo(13f, 26f)
        path.lineTo(11f, 22f)
        path.close()
        paint.color = withAlpha(Color.parseColor("#0F172A"), alpha)
        canvas.drawPath(path, paint)

        canvas.restore()
    }

    private fun dp(value: Float): Float = value * resources.displayMetrics.density

    private fun withAlpha(color: Int, alphaRatio: Float): Int {
        val a = (Color.alpha(color) * alphaRatio.coerceIn(0f, 1f)).roundToInt()
        return (color and 0x00FFFFFF) or (a shl 24)
    }
}
