# ScanMap: 3D walkthrough maps of Wi-Fi and Bluetooth signal

Walk through your apartment with an Android phone. ScanMap records where the phone is (ARCore),
what it hears (Wi-Fi and Bluetooth LE signal strength), and keyframe photos, all on one clock and
in one coordinate frame. Your Fedora box then turns the photos into a photorealistic 3D model
(COLMAP + Gaussian splatting on the RTX card), aligns it to the phone's frame at true metric scale,
and builds a 3D signal field per transmitter that you explore in a browser.

```
 Pixel 10 (android/)                         Fedora box (fedora/ + viewer/)
 ┌──────────────────────────┐   zip over    ┌───────────────────────────────────────────┐
 │ ARCore poses  30 Hz      │   home LAN    │ scanmap serve  :8765                       │
 │ Wi-Fi RSSI (conn + scan) │ ────────────▶ │  colmap ─▶ align (Sim3) ─▶ splatfacto      │
 │ BLE advertisements       │  "Send to     │  signal field (GP per transmitter)         │
 │ keyframes + intrinsics   │   Fedora"     │  three.js + Spark viewer                   │
 │ AR feature point cloud   │               └───────────────────────────────────────────┘
 └──────────────────────────┘
```

Status: **first prototype**. The Fedora pipeline and viewer are tested end to end with a synthetic
session; the Android app builds in CI but has not yet been run on a Pixel 10. COLMAP and nerfstudio
commands follow their documented CLIs but have not been run on your RTX box yet.

## Layout

| Folder | What |
|---|---|
| [`android/`](android/) | Kotlin + ARCore capture app, tuned for Pixel 10 (Android 16, arm64 only) |
| [`fedora/`](fedora/) | Python receiver (`scanmap serve`), pipeline (`scanmap process`), tests |
| [`viewer/`](viewer/) | three.js + Spark web viewer: splat, point cloud, heatmap slice, sample dots |
| [`docs/data-format.md`](docs/data-format.md) | The session folder format shared by all parts |

## Quick start

**Phone**

1. Download `scanmap-debug-apk` from the latest CI run (Actions tab) or build with
   `cd android && ./gradlew assembleDebug`, then `adb install app/build/outputs/apk/debug/app-debug.apk`.
2. On the Pixel: Settings → System → Developer options → turn **off "Wi-Fi scan throttling"**.
   Without this Android allows only 4 full Wi-Fi scans per 2 minutes (connected-network RSSI still
   works either way).
3. Open ScanMap, grant camera, location, nearby devices. Tap **Record**, walk slowly through every
   room (2 to 4 minutes per room, lights on, aim at furniture and corners), tap **Stop**.
4. Tap **Send to Fedora** (first time: enter `http://<fedora-ip>:8765`; long-press to change), or
   **Share zip** to copy it any other way.

**Fedora**: see [fedora/README.md](fedora/README.md) for the one-time GPU container setup, then:

```bash
cd fedora && python3 -m venv .venv && .venv/bin/pip install -e .
(cd ../viewer && npm ci && npm run build)
.venv/bin/scanmap serve            # receiver + viewer on http://<fedora-ip>:8765
```

Uploads are processed automatically (colmap → align → splat → signal). Open
`http://<fedora-ip>:8765` on the desktop or on the phone.

**Try it without a phone**

```bash
cd fedora
.venv/bin/python tools/fake_session.py ~/scanmap/sessions/demo
.venv/bin/scanmap signal ~/scanmap/sessions/demo
.venv/bin/scanmap serve   # open http://localhost:8765/?session=demo
```

## Why Pixel 10 specifically

* Pixel cameras report REALTIME sensor timestamps, so ARCore frames, Wi-Fi scan results and BLE
  advertisements share `elapsedRealtimeNanos` with no offset; the app still measures the offset
  and records it in `meta.json`.
* The app picks the back-camera config with the largest CPU image at 30 fps (1920x1080 on recent
  Pixels) because photogrammetry keyframes come from that image, enables continuous autofocus,
  and uses the ARCore Depth API when available.
* Wi-Fi 6E/7 radio: 6 GHz access points show up in scans; the app logs channel width (up to 320 MHz)
  and Wi-Fi standard per reading.
* BLE 5 extended advertising is scanned on all PHYs, so newer beacons and trackers are included.
* arm64-v8a only, targetSdk 36 (Android 16).

## Other phones

minSdk is 29 (Android 10), so any arm64 phone on [Google's ARCore device list](https://developers.google.com/ar/devices)
running Android 10 or newer should work, for example the Samsung Galaxy A6 (2018). Expect lower
quality there: older phones usually give ARCore only a 640x480 CPU image (fewer usable keyframes for
photogrammetry), Android 10 cannot switch off Wi-Fi scan throttling programmatically or report its
state (turn it off in Developer options if the menu has it), and Wi-Fi standard is not logged below
Android 11. CI runs Android lint's NewApi check so newer calls stay behind version checks.

## Roadmap

1. Run on a real Pixel 10 and tune keyframe thresholds and scan cadence.
2. Validate COLMAP + splatfacto on the RTX box; compare `photogrammetry` vs `ar_poses` modes.
3. Coverage surface at a threshold (for example -67 dBm) in the viewer.
4. Optional full-video capture (ARCore Recording API) for denser photogrammetry on Fedora.
5. Multiple walks of the same apartment registered into one model.
