package dev.scanmap.capture

import kotlin.math.sqrt

/**
 * Keeps the last [capacity] AR poses so radio samples can be placed at the moment they were
 * measured rather than when the callback arrived (Wi-Fi scans arrive seconds late).
 */
class PoseBuffer(private val capacity: Int = 30 * 60) {
    private val buf = ArrayDeque<TimedPose>(capacity)

    /** Poses further apart than this are treated as a tracking gap; samples inside it are dropped. */
    var maxGapNs: Long = 250_000_000L

    @Synchronized fun add(p: TimedPose) {
        if (buf.size == capacity) buf.removeFirst()
        buf.addLast(p)
    }

    @Synchronized fun clear() = buf.clear()

    @Synchronized fun latestT(): Long = buf.lastOrNull()?.tNs ?: Long.MIN_VALUE
    @Synchronized fun earliestT(): Long = buf.firstOrNull()?.tNs ?: Long.MAX_VALUE

    /** Linear position interpolation at [tNs], or null if outside the buffer or in a gap. */
    @Synchronized fun positionAt(tNs: Long): FloatArray? {
        if (buf.isEmpty()) return null
        if (tNs < buf.first().tNs || tNs > buf.last().tNs) return null
        // binary search for the first pose with t >= tNs
        var lo = 0
        var hi = buf.size - 1
        while (lo < hi) {
            val mid = (lo + hi) ushr 1
            if (buf[mid].tNs < tNs) lo = mid + 1 else hi = mid
        }
        val b = buf[lo]
        if (b.tNs == tNs || lo == 0) return floatArrayOf(b.tx, b.ty, b.tz)
        val a = buf[lo - 1]
        val dt = b.tNs - a.tNs
        if (dt > maxGapNs) return null
        val f = (tNs - a.tNs).toFloat() / dt.toFloat()
        return floatArrayOf(a.tx + (b.tx - a.tx) * f, a.ty + (b.ty - a.ty) * f, a.tz + (b.tz - a.tz) * f)
    }

    companion object {
        fun distance(a: FloatArray, b: FloatArray): Float {
            val dx = a[0] - b[0]; val dy = a[1] - b[1]; val dz = a[2] - b[2]
            return sqrt(dx * dx + dy * dy + dz * dz)
        }
    }
}
