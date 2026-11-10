package dev.scanmap.capture

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.opengl.GLES20
import android.opengl.GLSurfaceView
import android.opengl.Matrix
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.util.Log
import android.view.WindowManager
import android.widget.EditText
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.lifecycle.lifecycleScope
import com.google.ar.core.ArCoreApk
import com.google.ar.core.CameraConfig
import com.google.ar.core.CameraConfigFilter
import com.google.ar.core.Config
import com.google.ar.core.Session
import com.google.ar.core.exceptions.CameraNotAvailableException
import com.google.ar.core.exceptions.UnavailableException
import dev.scanmap.capture.databinding.ActivityMainBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.util.EnumSet
import javax.microedition.khronos.egl.EGLConfig
import javax.microedition.khronos.opengles.GL10

class MainActivity : AppCompatActivity(), GLSurfaceView.Renderer {
    private lateinit var ui: ActivityMainBinding
    private var session: Session? = null
    private var installRequested = false
    private val background = BackgroundRenderer()
    private val dots = SampleDotsRenderer()
    private lateinit var recorder: Recorder
    private var viewportW = 1; private var viewportH = 1
    private var displayRotation = 0
    private var lastSession: File? = null
    private var statusJob: Job? = null
    private var cameraInfo = ""

    private val sessionsRoot by lazy { File(getExternalFilesDir(null), "sessions").apply { mkdirs() } }
    private val prefs by lazy { getSharedPreferences("scanmap", MODE_PRIVATE) }

    private val permissions = buildList {
        add(Manifest.permission.CAMERA)
        add(Manifest.permission.ACCESS_FINE_LOCATION)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            add(Manifest.permission.BLUETOOTH_SCAN)
            add(Manifest.permission.BLUETOOTH_CONNECT)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) add(Manifest.permission.NEARBY_WIFI_DEVICES)
    }.toTypedArray()
    private val permissionLauncher = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { res ->
        if (res.values.any { !it }) toast("Camera, location, nearby Wi-Fi and Bluetooth permissions are all needed")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ui = ActivityMainBinding.inflate(layoutInflater)
        setContentView(ui.root)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        recorder = Recorder(this, dots)

        ui.surface.preserveEGLContextOnPause = true
        ui.surface.setEGLContextClientVersion(2)
        ui.surface.setEGLConfigChooser(8, 8, 8, 8, 16, 0)
        ui.surface.setRenderer(this)
        ui.surface.renderMode = GLSurfaceView.RENDERMODE_CONTINUOUSLY

        ui.record.setOnClickListener { if (recorder.recording) stopRecording() else startRecording() }
        ui.share.setOnClickListener { lastSession?.let(::shareZip) }
        ui.upload.setOnClickListener { lastSession?.let(::uploadToFedora) }
        ui.upload.setOnLongClickListener { askServer(); true }
        ui.status.setOnLongClickListener { askServer(); true }

        lastSession = sessionsRoot.listFiles()?.filter { File(it, "meta.json").exists() }?.maxByOrNull { it.name }
        updateButtons()
    }

    override fun onResume() {
        super.onResume()
        if (permissions.any { ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED }) {
            permissionLauncher.launch(permissions)
            return
        }
        if (session == null) {
            try {
                when (ArCoreApk.getInstance().requestInstall(this, !installRequested)) {
                    ArCoreApk.InstallStatus.INSTALL_REQUESTED -> { installRequested = true; return }
                    else -> {}
                }
                session = Session(this).also(::configure)
            } catch (e: UnavailableException) {
                toast("ARCore unavailable: ${e.javaClass.simpleName}")
                return
            }
        }
        try {
            session?.resume()
        } catch (e: CameraNotAvailableException) {
            toast("Camera not available"); session = null; return
        }
        ui.surface.onResume()
        statusJob = lifecycleScope.launch {
            while (isActive) { ui.status.text = cameraInfo + recorder.status(); delay(250) }
        }
    }

    /**
     * Pixel 10 tuning: back camera at 30 fps with the largest CPU image the device offers
     * (photogrammetry keyframes come from the CPU image), autofocus on, depth if supported.
     */
    private fun configure(s: Session) {
        val filter = CameraConfigFilter(s)
            .setFacingDirection(CameraConfig.FacingDirection.BACK)
            .setTargetFps(EnumSet.of(CameraConfig.TargetFps.TARGET_FPS_30))
        val configs = s.getSupportedCameraConfigs(filter)
        configs.maxByOrNull { it.imageSize.width * it.imageSize.height }?.let {
            s.cameraConfig = it
            cameraInfo = "cam ${it.imageSize.width}x${it.imageSize.height}  "
        }
        val config = Config(s).apply {
            focusMode = Config.FocusMode.AUTO
            updateMode = Config.UpdateMode.LATEST_CAMERA_IMAGE
            planeFindingMode = Config.PlaneFindingMode.HORIZONTAL_AND_VERTICAL
            lightEstimationMode = Config.LightEstimationMode.DISABLED
            instantPlacementMode = Config.InstantPlacementMode.DISABLED
            depthMode = if (s.isDepthModeSupported(Config.DepthMode.AUTOMATIC)) Config.DepthMode.AUTOMATIC else Config.DepthMode.DISABLED
        }
        s.configure(config)
    }

    override fun onPause() {
        super.onPause()
        statusJob?.cancel()
        if (recorder.recording) stopRecording()
        ui.surface.onPause()
        session?.pause()
    }

    override fun onDestroy() {
        session?.close(); session = null
        super.onDestroy()
    }

    private fun startRecording() {
        if (session == null) { toast("AR session not ready"); return }
        recorder.start(sessionsRoot)
        ui.record.text = "Stop"
        updateButtons()
    }

    private fun stopRecording() {
        ui.record.isEnabled = false
        ui.record.text = "Saving…"
        lifecycleScope.launch {
            val depth = session?.config?.depthMode?.name ?: "unknown"
            val cam = session?.cameraConfig?.imageSize?.let { "${it.width}x${it.height}" } ?: ""
            val dir = withContext(Dispatchers.IO) {
                recorder.stop { put("depth_mode", depth); put("cpu_image_size", cam) }
            }
            lastSession = dir
            ui.record.isEnabled = true
            ui.record.text = "Record"
            updateButtons()
            dir?.let { toast("Saved ${it.name}") }
        }
    }

    private fun updateButtons() {
        val ready = lastSession != null && !recorder.recording
        ui.share.isEnabled = ready
        ui.upload.isEnabled = ready
    }

    private fun exportsDir() = File(cacheDir, "exports")

    private fun shareZip(dir: File) = lifecycleScope.launch {
        val zip = withContext(Dispatchers.IO) { SessionExport.zip(dir, exportsDir()) }
        val uri = FileProvider.getUriForFile(this@MainActivity, "$packageName.files", zip)
        startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).apply {
            type = "application/zip"
            putExtra(Intent.EXTRA_STREAM, uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }, "Share session"))
    }

    private fun uploadToFedora(dir: File) {
        val server = prefs.getString("server", null)
        if (server.isNullOrBlank()) { askServer { uploadToFedora(dir) }; return }
        ui.upload.isEnabled = false
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching {
                    val zip = SessionExport.zip(dir, exportsDir())
                    SessionExport.upload(zip, server, process = true) { sent, total ->
                        runOnUiThread { ui.upload.text = "${sent * 100 / total}%" }
                    }
                }
            }
            ui.upload.text = "Send to Fedora"
            ui.upload.isEnabled = true
            result.onSuccess { toast("Uploaded. Processing on $server") }
                .onFailure { Log.w("Upload", it); toast("Upload failed: ${it.message}") }
        }
    }

    private fun askServer(then: (() -> Unit)? = null) {
        val input = EditText(this).apply {
            inputType = InputType.TYPE_TEXT_VARIATION_URI
            setText(prefs.getString("server", "http://fedora.local:8765"))
        }
        AlertDialog.Builder(this)
            .setTitle("Fedora receiver URL")
            .setMessage("Where scanmap-server runs, e.g. http://192.168.1.20:8765")
            .setView(input)
            .setPositiveButton("Save") { _, _ ->
                prefs.edit().putString("server", input.text.toString().trim()).apply()
                then?.invoke()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun toast(msg: String) = runOnUiThread { Toast.makeText(this, msg, Toast.LENGTH_LONG).show() }

    // ---- GL thread ----

    override fun onSurfaceCreated(gl: GL10?, config: EGLConfig?) {
        GLES20.glClearColor(0f, 0f, 0f, 1f)
        background.create()
        dots.create()
    }

    override fun onSurfaceChanged(gl: GL10?, width: Int, height: Int) {
        viewportW = width; viewportH = height
        GLES20.glViewport(0, 0, width, height)
        @Suppress("DEPRECATION")
        displayRotation = windowManager.defaultDisplay.rotation
        session?.setDisplayGeometry(displayRotation, width, height)
    }

    private val proj = FloatArray(16)
    private val view = FloatArray(16)
    private val viewProj = FloatArray(16)

    override fun onDrawFrame(gl: GL10?) {
        GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT or GLES20.GL_DEPTH_BUFFER_BIT)
        val s = session ?: return
        s.setCameraTextureName(background.textureId)
        val frame = try { s.update() } catch (e: Exception) { Log.w(TAG, "update", e); return }
        background.draw(frame)
        val camera = frame.camera
        recorder.onFrame(frame, camera)
        if (camera.trackingState == com.google.ar.core.TrackingState.TRACKING) {
            camera.getProjectionMatrix(proj, 0, 0.05f, 50f)
            camera.getViewMatrix(view, 0)
            Matrix.multiplyMM(viewProj, 0, proj, 0, view, 0)
            dots.draw(viewProj)
        }
    }

    companion object { private const val TAG = "ScanMap" }
}
