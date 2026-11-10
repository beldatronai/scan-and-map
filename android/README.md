# ScanMap capture app (Android)

Kotlin + ARCore, no third-party SDKs. Tuned for Pixel 10 (Android 16, arm64).

| File | Role |
|---|---|
| `MainActivity.kt` | Permissions, ARCore session and camera config, GL loop, record / upload / share |
| `Recorder.kt` | Ties AR frames to radio samples; holds samples until poses cover their timestamp |
| `PoseBuffer.kt` | Last ~60 s of poses, interpolates position at any timestamp, detects tracking gaps |
| `ArClock.kt` | Maps ARCore frame timestamps onto `elapsedRealtimeNanos` |
| `WifiSampler.kt` | Connected-network RSSI every 0.5 s plus continuous full scans |
| `BleSampler.kt` | Low-latency BLE scan, all PHYs, every advertisement |
| `KeyframeGrabber.kt` | Motion + sharpness gated keyframes, YUV to JPEG off the GL thread |
| `PointCloudAccumulator.kt` | Merges ARCore feature points into `depth_points.ply` |
| `SessionWriter.kt` | Writes the session folder ([format](../docs/data-format.md)) on an I/O thread |
| `SessionExport.kt` | Zips (JPEGs stored, CSVs deflated) and streams the upload to Fedora |
| `BackgroundRenderer.kt`, `SampleDotsRenderer.kt` | Camera background and the live coverage dots |

Build: `./gradlew assembleDebug` (JDK 17, Android SDK 36). CI builds the APK on every push.

Sessions are stored in `Android/data/dev.scanmap.capture/files/sessions/` and can also be pulled with
`adb pull /sdcard/Android/data/dev.scanmap.capture/files/sessions/`.

While recording, the status bar shows tracking state, the connected network's RSSI, keyframes,
samples, distinct APs and BLE devices, and a warning if Wi-Fi scan throttling is still on. The dots
in the camera view are coloured by the connected network's RSSI (red -90 dBm to green -30 dBm), so
you can see which areas you have covered.
