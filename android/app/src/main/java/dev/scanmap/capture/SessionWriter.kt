package dev.scanmap.capture

import android.os.Build
import org.json.JSONObject
import java.io.BufferedWriter
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Writes one session folder in the format described in docs/data-format.md.
 * All file I/O happens on a single background thread so the GL thread never blocks on disk.
 */
class SessionWriter(root: File) {
    val id: String = SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(Date())
    val dir = File(root, id).apply { mkdirs() }
    val framesDir = File(dir, "frames").apply { mkdirs() }
    private val io = Executors.newSingleThreadExecutor { Thread(it, "session-io") }

    private val poses = writer("poses.csv", "t_ns,tx,ty,tz,qx,qy,qz,qw,tracking_state")
    private val frames = writer("frames.csv", "frame_id,t_ns,tx,ty,tz,qx,qy,qz,qw,fx,fy,cx,cy,width,height,sharpness")
    private val samples = writer(
        "samples.csv",
        "t_ns,kind,id,name,freq_mhz,rssi_dbm,x,y,z,channel_width_mhz,wifi_standard,tx_power_dbm",
    )

    var poseCount = 0; private set
    var frameCount = 0; private set
    var sampleCount = 0; private set

    private fun writer(name: String, header: String): BufferedWriter =
        File(dir, name).bufferedWriter(bufferSize = 1 shl 16).apply { write(header); newLine() }

    fun pose(p: TimedPose, state: String) {
        poseCount++
        io.execute {
            poses.write("${p.tNs},${p.tx},${p.ty},${p.tz},${p.qx},${p.qy},${p.qz},${p.qw},$state")
            poses.newLine()
        }
    }

    fun frame(id: Int, p: TimedPose, intr: FloatArray, w: Int, h: Int, sharpness: Double) {
        frameCount++
        io.execute {
            frames.write(
                "%06d,${p.tNs},${p.tx},${p.ty},${p.tz},${p.qx},${p.qy},${p.qz},${p.qw},${intr[0]},${intr[1]},${intr[2]},${intr[3]},$w,$h,%.2f"
                    .format(Locale.US, id, sharpness)
            )
            frames.newLine()
        }
    }

    fun sample(s: RadioSample, pos: FloatArray) {
        sampleCount++
        io.execute {
            val tx = if (s.txPowerDbm == Int.MIN_VALUE) "" else s.txPowerDbm.toString()
            samples.write(
                "${s.tNs},${s.kind.csv},${csv(s.id)},${csv(s.name)},${s.freqMhz},${s.rssiDbm}," +
                    "${pos[0]},${pos[1]},${pos[2]},${s.channelWidthMhz},${s.wifiStandard},$tx"
            )
            samples.newLine()
        }
    }

    /** Run arbitrary work (JPEG writes, PLY) on the I/O thread. */
    fun submit(block: () -> Unit) = io.execute(block)

    fun finish(meta: JSONObject, extra: (File) -> Unit = {}) {
        io.execute {
            poses.close(); frames.close(); samples.close()
            extra(dir)
            File(dir, "meta.json").writeText(meta.toString(2))
        }
        io.shutdown()
        io.awaitTermination(60, TimeUnit.SECONDS)
    }

    companion object {
        fun csv(v: String): String =
            if (v.any { it == ',' || it == '"' || it == '\n' || it == '\r' }) "\"" + v.replace("\"", "\"\"").replace('\n', ' ').replace('\r', ' ') + "\""
            else v

        fun baseMeta(id: String): JSONObject = JSONObject().apply {
            put("format_version", 1)
            put("session_id", id)
            put("platform", "android")
            put("device", "${Build.MANUFACTURER} ${Build.MODEL}")
            put("device_codename", Build.DEVICE)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) put("soc", "${Build.SOC_MANUFACTURER} ${Build.SOC_MODEL}")
            else put("soc", Build.HARDWARE)
            put("android_sdk", Build.VERSION.SDK_INT)
            put("app_version", BuildConfig.VERSION_NAME)
            put("clock", "elapsedRealtimeNanos")
            put("frame", "arcore_world: metres, right-handed, +Y up (gravity), camera-to-world, OpenGL camera (looks down -Z)")
            put("image_orientation", "sensor (landscape), intrinsics match the stored JPEGs")
        }
    }
}
