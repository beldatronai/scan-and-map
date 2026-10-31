# Session folder format (version 1)

One walk = one folder, zipped on the phone (`<session-id>/...`) and unpacked on Fedora.

**Clock**: `SystemClock.elapsedRealtimeNanos` on Android. ARCore frame timestamps are mapped onto it
(`ar_clock_offset_ns` in `meta.json`, 0 on Pixels).
**Frame**: the ARCore world frame: metres, right-handed, +Y up (gravity aligned), origin where the
session started. Poses are camera-to-world in the OpenGL camera convention (camera looks down -Z).
COLMAP uses world-to-camera with the OpenCV convention; see `fedora/scanmap/geometry.py`.

| File | Contents | Rate |
|---|---|---|
| `meta.json` | device, SoC, Android SDK, app version, start time, counts, clock offset, depth mode, CPU image size | once |
| `poses.csv` | `t_ns,tx,ty,tz,qx,qy,qz,qw,tracking_state` (non-tracking rows carry the failure reason and an identity pose) | every AR frame (30 Hz) |
| `frames/000123.jpg` | keyframe photos in sensor orientation (landscape) | about 2 to 3 per second, motion and sharpness gated |
| `frames.csv` | `frame_id,t_ns,tx,ty,tz,qx,qy,qz,qw,fx,fy,cx,cy,width,height,sharpness` | per keyframe |
| `samples.csv` | `t_ns,kind,id,name,freq_mhz,rssi_dbm,x,y,z,channel_width_mhz,wifi_standard,tx_power_dbm` | per reading |
| `depth_points.ply` | ARCore feature points merged on a 4 cm voxel grid (ASCII PLY, `x y z confidence observations`) | end of session |

`kind` is `wifi_conn` (connected network, polled every 0.5 s), `wifi_scan` (every AP in a full scan)
or `ble` (every advertisement). `x, y, z` is the phone position interpolated from the AR poses **at the
reading's own timestamp**, not when the app received it. That matters for Wi-Fi scans, which take
2 to 4 s to sweep all channels: every AP gets its own time and so its own position. Readings that fall
in a tracking gap are dropped rather than misplaced.

## Added on Fedora

| Path | Made by |
|---|---|
| `colmap/` | `colmap` step: database, sparse models, `aligned/` (TXT model in the AR frame) |
| `alignment.json` | `align` step: Sim3, matched/inlier counts, median and p90 camera-centre error |
| `splat_runs/`, `splat/splat.ply` | `splat` step (nerfstudio splatfacto, trained in the AR frame) |
| `mesh/mesh.ply` | optional `mesh` step (COLMAP dense + Poisson) |
| `signal/index.json`, `signal/*.bin` | `signal` step: Float32 grid per layer, x fastest, NaN = unknown |
| `status.json`, `process.log` | pipeline progress and tool output |
