package dev.scanmap.capture

import android.os.SystemClock
import kotlin.math.abs

/**
 * Maps ARCore frame timestamps onto SystemClock.elapsedRealtimeNanos, the clock that Wi-Fi
 * ScanResult.timestamp and BLE ScanResult.timestampNanos use. On Pixels the camera reports
 * REALTIME timestamps so the offset is ~0, but we measure it rather than assume it.
 */
class ArClock {
    @Volatile var offsetNs: Long = 0L
        private set
    private var calibrated = false
    private val diffs = ArrayList<Long>()

    fun toRealtime(frameTimestampNs: Long): Long {
        if (!calibrated) {
            val diff = SystemClock.elapsedRealtimeNanos() - frameTimestampNs
            diffs += diff
            if (diffs.size >= 30) {
                diffs.sort()
                val median = diffs[diffs.size / 2]
                // Within ~0.5 s means same time base (diff is just pipeline latency).
                offsetNs = if (abs(median) < 500_000_000L) 0L else median
                calibrated = true
            }
            return frameTimestampNs + (if (abs(diff) < 500_000_000L) 0L else diff)
        }
        return frameTimestampNs + offsetNs
    }

    val isSameBase: Boolean get() = calibrated && offsetNs == 0L
}
