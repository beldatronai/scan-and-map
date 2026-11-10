"""Generate a synthetic session (two-room apartment, one router, two BLE devices) for testing
the pipeline and viewer without a phone:  python tools/fake_session.py /tmp/demo-session"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROUTER = np.array([4.2, 1.8, -1.0])
MESH_NODE = np.array([0.5, 0.9, -5.5])
BLE = {"AA:BB:CC:00:00:01": ("Speaker", np.array([1.0, 0.8, -1.5])),
       "AA:BB:CC:00:00:02": ("TV", np.array([5.0, 0.6, -5.0]))}


def rssi(src: np.ndarray, p: np.ndarray, p0: float, n: float, rng, noise: float) -> float:
    d = max(np.linalg.norm(p - src), 0.3)
    wall = 6.0 if (p[0] < 3.0) != (src[0] < 3.0) else 0.0  # interior wall at x = 3 m
    return p0 - 10 * n * np.log10(d) - wall + rng.normal(0, noise)


def yaw_quat(yaw: float) -> tuple[float, float, float, float]:
    return 0.0, float(np.sin(yaw / 2)), 0.0, float(np.cos(yaw / 2))


def make(root: Path, seed: int = 1) -> Path:
    rng = np.random.default_rng(seed)
    root.mkdir(parents=True, exist_ok=True)
    (root / "frames").mkdir(exist_ok=True)
    # lawnmower walk through a 6 m x 6 m flat at hand height (~1.2 m above floor at y = -1.2... keep floor at y=0)
    waypoints = []
    for i, z in enumerate(np.linspace(-0.5, -5.5, 6)):
        xs = (0.5, 5.5) if i % 2 == 0 else (5.5, 0.5)
        waypoints += [(xs[0], z), (xs[1], z)]
    path = []
    for (x0, z0), (x1, z1) in zip(waypoints, waypoints[1:]):
        n = int(np.hypot(x1 - x0, z1 - z0) / 0.6 * 30) + 1  # 0.6 m/s at 30 Hz
        for f in np.linspace(0, 1, n, endpoint=False):
            path.append((x0 + (x1 - x0) * f, z0 + (z1 - z0) * f))
    t0 = 10_000_000_000
    dt = 33_333_333
    poses, frames, samples = [], [], []
    last_kf = None
    for i, (x, z) in enumerate(path):
        t = t0 + i * dt
        y = 1.3 + 0.03 * np.sin(i / 15)
        p = np.array([x, y, z])
        yaw = np.arctan2(-(path[min(i + 1, len(path) - 1)][0] - x), -(path[min(i + 1, len(path) - 1)][1] - z) + 1e-9)
        q = yaw_quat(yaw)
        poses.append(f"{t},{x:.4f},{y:.4f},{z:.4f},{q[0]:.6f},{q[1]:.6f},{q[2]:.6f},{q[3]:.6f},tracking")
        if last_kf is None or np.linalg.norm(p - last_kf) > 0.2:
            fid = len(frames)
            frames.append(f"{fid:06d},{t},{x:.4f},{y:.4f},{z:.4f},{q[0]:.6f},{q[1]:.6f},{q[2]:.6f},{q[3]:.6f},1450.0,1450.0,960.0,540.0,1920,1080,180.0")
            last_kf = p
        if i % 15 == 0:
            samples.append(f"{t},wifi_conn,02:11:22:33:44:55,HomeNet,5180,{round(rssi(ROUTER, p, -32, 2.6, rng, 2.5))},{x:.4f},{y:.4f},{z:.4f},80,6,")
        if i % 60 == 0:
            for bssid, src, freq, ssid in (("02:11:22:33:44:55", ROUTER, 5180, "HomeNet"),
                                           ("02:11:22:33:44:54", ROUTER, 2437, "HomeNet"),
                                           ("06:aa:bb:cc:dd:ee", MESH_NODE, 5500, "HomeNet"),
                                           ("0a:00:00:00:00:99", np.array([-8.0, 2.0, -3.0]), 2412, "Neighbour")):
                samples.append(f"{t - 1_500_000_000},wifi_scan,{bssid},{ssid},{freq},{round(rssi(src, p, -36 if freq > 3000 else -30, 2.8, rng, 3))},{x:.4f},{y:.4f},{z:.4f},80,6,")
        if i % 3 == 0:
            for addr, (name, src) in BLE.items():
                if rng.random() < 0.5:
                    samples.append(f"{t},ble,{addr},{name},2440,{round(rssi(src, p, -55, 2.2, rng, 6))},{x:.4f},{y:.4f},{z:.4f},0,0,")
    (root / "poses.csv").write_text("t_ns,tx,ty,tz,qx,qy,qz,qw,tracking_state\n" + "\n".join(poses) + "\n")
    (root / "frames.csv").write_text("frame_id,t_ns,tx,ty,tz,qx,qy,qz,qw,fx,fy,cx,cy,width,height,sharpness\n" + "\n".join(frames) + "\n")
    (root / "samples.csv").write_text("t_ns,kind,id,name,freq_mhz,rssi_dbm,x,y,z,channel_width_mhz,wifi_standard,tx_power_dbm\n" + "\n".join(sorted(samples)) + "\n")
    # walls and floor as a point cloud
    pts = []
    for _ in range(6000):
        u, v = rng.random(2)
        kind = rng.integers(0, 6)
        if kind == 0: pts.append((u * 6, 0.0, -v * 6))            # floor
        elif kind == 1: pts.append((0.0, v * 2.6, -u * 6))        # west wall
        elif kind == 2: pts.append((6.0, v * 2.6, -u * 6))        # east wall
        elif kind == 3: pts.append((u * 6, v * 2.6, 0.0))         # south wall
        elif kind == 4: pts.append((u * 6, v * 2.6, -6.0))        # north wall
        else:
            z = -u * 6
            if not (-3.4 < z < -2.4):                             # doorway
                pts.append((3.0, v * 2.6, z))
    with (root / "depth_points.ply").open("w") as fh:
        fh.write(f"ply\nformat ascii 1.0\nelement vertex {len(pts)}\nproperty float x\nproperty float y\nproperty float z\nproperty float confidence\nproperty uchar observations\nend_header\n")
        for p in pts:
            fh.write(f"{p[0]:.3f} {p[1]:.3f} {p[2]:.3f} 0.9 3\n")
    (root / "meta.json").write_text(json.dumps({
        "format_version": 1, "session_id": root.name, "platform": "synthetic", "device": "fake_session.py",
        "duration_s": len(path) / 30, "counts": {"poses": len(poses), "keyframes": len(frames), "samples": len(samples)},
        "truth": {"router": ROUTER.tolist(), "mesh_node": MESH_NODE.tolist(), "ble": {k: v[1].tolist() for k, v in BLE.items()}},
    }, indent=2))
    return root


if __name__ == "__main__":
    print(make(Path(sys.argv[1] if len(sys.argv) > 1 else "demo-session")))
