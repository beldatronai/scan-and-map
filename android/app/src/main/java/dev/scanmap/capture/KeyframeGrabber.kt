package dev.scanmap.capture

import android.graphics.ImageFormat
import android.graphics.Rect
import android.graphics.YuvImage
import android.media.Image
import com.google.ar.core.Frame
import com.google.ar.core.exceptions.NotYetAvailableException
import java.io.File
import java.util.concurrent.atomic.AtomicInteger
import kotlin.math.abs
import kotlin.math.acos
import kotlin.math.min

/**
 * Picks photogrammetry keyframes: the phone must have moved [minMoveM] or turned [minTurnDeg]
 * since the last keyframe, at most [maxFps] per second, and the image must be sharp enough
 * (variance of the Laplacian on a downsampled luma plane). JPEG encoding runs on the
 * session I/O thread.
 */
class KeyframeGrabber(
    private val writer: SessionWriter,
    private val minMoveM: Float = 0.10f,
    private val minTurnDeg: Float = 10f,
    private val maxFps: Float = 3f,
    var minSharpness: Double = 60.0,
    private val jpegQuality: Int = 92,
) {
    private var last: TimedPose? = null
    private var lastAcceptNs = 0L
    private var nextId = 0
    private val pending = AtomicInteger(0)
    var rejectedBlurry = 0; private set
    var lastSharpness = 0.0; private set

    val count: Int get() = nextId

    fun maybeCapture(frame: Frame, pose: TimedPose) {
        if (pose.tNs - lastAcceptNs < (1e9 / maxFps).toLong()) return
        val prev = last
        if (prev != null && !movedEnough(prev, pose)) return
        if (pending.get() >= 4) return // encoder is behind; skip rather than stall the GL thread

        val image: Image = try { frame.acquireCameraImage() } catch (e: NotYetAvailableException) { return }
        val w = image.width
        val h = image.height
        val nv21: ByteArray
        try {
            lastSharpness = sharpness(image)
            if (lastSharpness < minSharpness) { rejectedBlurry++; return }
            nv21 = toNv21(image)
        } finally {
            image.close()
        }
        val intr = frame.camera.imageIntrinsics
        val f = intr.focalLength; val c = intr.principalPoint
        val intrinsics = floatArrayOf(f[0], f[1], c[0], c[1])
        val id = nextId++
        last = pose; lastAcceptNs = pose.tNs
        val sharp = lastSharpness
        pending.incrementAndGet()
        writer.submit {
            try {
                File(writer.framesDir, "%06d.jpg".format(id)).outputStream().buffered().use { out ->
                    YuvImage(nv21, ImageFormat.NV21, w, h, null).compressToJpeg(Rect(0, 0, w, h), jpegQuality, out)
                }
            } finally { pending.decrementAndGet() }
        }
        writer.frame(id, pose, intrinsics, w, h, sharp)
    }

    private fun movedEnough(a: TimedPose, b: TimedPose): Boolean {
        val d = PoseBuffer.distance(floatArrayOf(a.tx, a.ty, a.tz), floatArrayOf(b.tx, b.ty, b.tz))
        if (d >= minMoveM) return true
        val dot = abs(a.qx * b.qx + a.qy * b.qy + a.qz * b.qz + a.qw * b.qw).coerceAtMost(1f)
        val angleDeg = Math.toDegrees(2.0 * acos(dot.toDouble()))
        return angleDeg >= minTurnDeg
    }

    companion object {
        /** Variance of a 4-neighbour Laplacian over the luma plane, sampled every 4th pixel. */
        fun sharpness(img: Image): Double {
            val plane = img.planes[0]
            val buf = plane.buffer
            val rs = plane.rowStride
            val ps = plane.pixelStride
            val step = 4
            var sum = 0.0; var sumSq = 0.0; var n = 0
            var y = step
            while (y < img.height - step) {
                var x = step
                while (x < img.width - step) {
                    fun px(xx: Int, yy: Int) = buf.get(yy * rs + xx * ps).toInt() and 0xFF
                    val l = 4 * px(x, y) - px(x - step, y) - px(x + step, y) - px(x, y - step) - px(x, y + step)
                    sum += l; sumSq += l.toDouble() * l; n++
                    x += step
                }
                y += step
            }
            if (n == 0) return 0.0
            val mean = sum / n
            return sumSq / n - mean * mean
        }

        /** YUV_420_888 -> NV21 honouring row and pixel strides. */
        fun toNv21(img: Image): ByteArray {
            val w = img.width; val h = img.height
            val out = ByteArray(w * h * 3 / 2)
            val yP = img.planes[0]; val uP = img.planes[1]; val vP = img.planes[2]
            val yBuf = yP.buffer; val yRs = yP.rowStride; val yPs = yP.pixelStride
            var o = 0
            if (yPs == 1) {
                for (row in 0 until h) {
                    yBuf.position(row * yRs)
                    yBuf.get(out, o, w)
                    o += w
                }
            } else {
                for (row in 0 until h) for (col in 0 until w) out[o++] = yBuf.get(row * yRs + col * yPs)
            }
            val uBuf = uP.buffer; val vBuf = vP.buffer
            val cRs = uP.rowStride; val cPs = uP.pixelStride
            val cw = w / 2; val ch = h / 2
            for (row in 0 until ch) {
                val base = row * cRs
                for (col in 0 until cw) {
                    val i = base + col * cPs
                    out[o++] = vBuf.get(min(i, vBuf.limit() - 1))
                    out[o++] = uBuf.get(min(i, uBuf.limit() - 1))
                }
            }
            return out
        }
    }
}
