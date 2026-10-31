package dev.scanmap.capture

/** Camera-to-world pose in the ARCore world frame (metres, Y up, OpenGL camera convention). */
data class TimedPose(
    val tNs: Long,
    val tx: Float, val ty: Float, val tz: Float,
    val qx: Float, val qy: Float, val qz: Float, val qw: Float,
)

enum class SampleKind(val csv: String) { WIFI_CONN("wifi_conn"), WIFI_SCAN("wifi_scan"), BLE("ble") }

/**
 * One radio reading. [tNs] is on the elapsedRealtimeNanos clock, taken from the reading
 * itself where the platform provides one (scan results, BLE advertisements).
 */
data class RadioSample(
    val tNs: Long,
    val kind: SampleKind,
    val id: String,
    val name: String,
    val freqMhz: Int,
    val rssiDbm: Int,
    val channelWidthMhz: Int = 0,
    val wifiStandard: Int = 0,
    val txPowerDbm: Int = Int.MIN_VALUE,
)
