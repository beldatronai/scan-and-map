package dev.scanmap.capture

import android.content.Context
import android.os.SystemClock
import com.google.ar.core.Camera
import com.google.ar.core.Frame
import com.google.ar.core.TrackingState
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Glue between ARCore frames (GL thread) and radio callbacks (main thread). Radio samples are
 * held until the pose buffer covers their timestamp, then written with an interpolated
 * position. Samples that fall in a tracking gap are dropped rather than misplaced.
 */
class Recorder(private val context: Context, private val dots: SampleDotsRenderer) {
    private val poses = PoseBuffer()
    private val clock = ArClock()
    private val pending = ArrayList<RadioSample>()
    private val pendingLock = Any()
    private val frameLock = Any()

    private var writer: SessionWriter? = null
    private var grabber: KeyframeGrabber? = null
    private var cloud: PointCloudAccumulator? = null
    private var wifi: WifiSampler? = null
    private var ble: BleSampler? = null
    private var startRealtimeNs = 0L
    private var startWallMs = 0L

    @Volatile var recording = false; private set
    @Volatile var trackingState: TrackingState = TrackingState.STOPPED; private set
    @Volatile var lastConnectedRssi: Int? = null; private set
    @Volatile var lastConnectedSsid: String = ""; private set
    var droppedSamples = 0; private set
    private val wifiIds = HashSet<String>()
    private val bleIds = HashSet<String>()
    private var lastDotPos: FloatArray? = null

    fun start(root: File) {
        check(!recording)
        val w = SessionWriter(root)
        writer = w
        grabber = KeyframeGrabber(w)
        cloud = PointCloudAccumulator()
        poses.clear(); dots.clear()
        synchronized(pendingLock) { pending.clear() }
        wifiIds.clear(); bleIds.clear(); droppedSamples = 0; lastDotPos = null
        startRealtimeNs = SystemClock.elapsedRealtimeNanos()
        startWallMs = System.currentTimeMillis()
        recording = true
        wifi = WifiSampler(context, ::onSample).also { it.start() }
        ble = BleSampler(context, ::onSample).also { it.start() }
    }

    private fun onSample(s: RadioSample) {
        if (!recording || s.tNs < startRealtimeNs) return
        if (s.kind == SampleKind.WIFI_CONN) { lastConnectedRssi = s.rssiDbm; lastConnectedSsid = s.name }
        synchronized(pendingLock) { pending += s }
    }

    /** Called on the GL thread for every ARCore frame. */
    fun onFrame(frame: Frame, camera: Camera) = synchronized(frameLock) { onFrameLocked(frame, camera) }

    private fun onFrameLocked(frame: Frame, camera: Camera) {
        trackingState = camera.trackingState
        if (!recording) return
        val w = writer ?: return
        val t = clock.toRealtime(frame.timestamp)
        if (camera.trackingState == TrackingState.TRACKING) {
            val p = camera.pose
            val q = p.rotationQuaternion
            val tp = TimedPose(t, p.tx(), p.ty(), p.tz(), q[0], q[1], q[2], q[3])
            poses.add(tp)
            w.pose(tp, "tracking")
            grabber?.maybeCapture(frame, tp)
            frame.acquirePointCloud().use { cloud?.add(it) }
        } else {
            w.pose(TimedPose(t, 0f, 0f, 0f, 0f, 0f, 0f, 1f), camera.trackingFailureReason.name.lowercase())
        }
        flushSamples(w)
    }

    private fun flushSamples(w: SessionWriter) {
        val latest = poses.latestT()
        val earliest = poses.earliestT()
        val ready = ArrayList<RadioSample>()
        synchronized(pendingLock) {
            val it = pending.iterator()
            while (it.hasNext()) {
                val s = it.next()
                if (s.tNs <= latest || s.tNs < earliest) { ready += s; it.remove() }
                else if (SystemClock.elapsedRealtimeNanos() - s.tNs > 5_000_000_000L) { droppedSamples++; it.remove() }
            }
        }
        for (s in ready) {
            val pos = poses.positionAt(s.tNs)
            if (pos == null) { droppedSamples++; continue }
            w.sample(s, pos)
            when (s.kind) {
                SampleKind.WIFI_SCAN, SampleKind.WIFI_CONN -> wifiIds += s.id
                SampleKind.BLE -> bleIds += s.id
            }
            if (s.kind == SampleKind.WIFI_CONN) {
                val last = lastDotPos
                if (last == null || PoseBuffer.distance(last, pos) > 0.15f) { dots.add(pos, s.rssiDbm); lastDotPos = pos }
            }
        }
    }

    fun status(): String {
        val w = writer
        val sb = StringBuilder()
        sb.append("AR: ").append(trackingState.name.lowercase())
        lastConnectedRssi?.let { sb.append("   Wi-Fi ").append(lastConnectedSsid).append(": ").append(it).append(" dBm") }
        if (w != null && recording) {
            val secs = (SystemClock.elapsedRealtimeNanos() - startRealtimeNs) / 1_000_000_000L
            sb.append("\nREC ").append(secs / 60).append(':').append("%02d".format(secs % 60))
            sb.append("  frames ").append(grabber?.count ?: 0)
            sb.append("  samples ").append(w.sampleCount)
            sb.append("\nAPs ").append(wifiIds.size).append("  BLE ").append(bleIds.size)
            sb.append("  points ").append(cloud?.size ?: 0)
            wifi?.let {
                sb.append("\nscans ").append(it.scansReceived).append('/').append(it.scansRequested)
                if (it.throttlingEnabled != false) sb.append("  ⚠ turn off Wi-Fi scan throttling (Developer options)")
            }
            grabber?.let { if (it.rejectedBlurry > 0) sb.append("\nblurry skipped ").append(it.rejectedBlurry) }
        }
        return sb.toString()
    }

    /** Stops sensors, finalises files, returns the session folder. Blocks until written: call off the main thread. */
    fun stop(extraMeta: JSONObject.() -> Unit = {}): File? = synchronized(frameLock) { stopLocked(extraMeta) }

    private fun stopLocked(extraMeta: JSONObject.() -> Unit): File? {
        if (!recording) return null
        recording = false
        wifi?.stop(); ble?.stop()
        val w = writer ?: return null
        flushSamples(w)
        val meta = SessionWriter.baseMeta(w.id).apply {
            put("start_wall_ms", startWallMs)
            put("start_realtime_ns", startRealtimeNs)
            put("duration_s", (SystemClock.elapsedRealtimeNanos() - startRealtimeNs) / 1e9)
            put("ar_clock_offset_ns", clock.offsetNs)
            put("counts", JSONObject().apply {
                put("poses", w.poseCount); put("keyframes", w.frameCount); put("samples", w.sampleCount)
                put("dropped_samples", droppedSamples); put("wifi_ids", wifiIds.size); put("ble_ids", bleIds.size)
            })
            put("wifi_scan_throttling", wifi?.throttlingEnabled ?: JSONObject.NULL)
            put("android_release", android.os.Build.VERSION.RELEASE)
            put("files", JSONArray(listOf("poses.csv", "frames.csv", "frames/", "samples.csv", "depth_points.ply")))
            extraMeta()
        }
        val c = cloud
        w.finish(meta) { dir -> c?.writePly(File(dir, "depth_points.ply")) }
        writer = null; grabber = null; cloud = null; wifi = null; ble = null
        return w.dir
    }
}
