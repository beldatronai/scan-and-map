"""Load a capture session folder (see docs/data-format.md)."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Frames:
    names: list[str]
    t_ns: np.ndarray        # (N,)
    position: np.ndarray    # (N, 3) camera centre in AR world
    quat_xyzw: np.ndarray   # (N, 4) camera-to-world rotation, OpenGL camera convention
    intrinsics: np.ndarray  # (N, 4) fx, fy, cx, cy
    size: np.ndarray        # (N, 2) width, height
    sharpness: np.ndarray   # (N,)


@dataclass
class Samples:
    t_ns: np.ndarray
    kind: np.ndarray        # str: wifi_conn | wifi_scan | ble
    id: np.ndarray          # str: BSSID or BLE address
    name: np.ndarray        # str: SSID or BLE name
    freq_mhz: np.ndarray
    rssi: np.ndarray        # float dBm
    pos: np.ndarray         # (N, 3)

    def __len__(self) -> int:
        return len(self.t_ns)

    def subset(self, mask: np.ndarray) -> "Samples":
        return Samples(*(getattr(self, f)[mask] for f in ("t_ns", "kind", "id", "name", "freq_mhz", "rssi", "pos")))


class Session:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        if not (self.root / "samples.csv").exists():
            raise FileNotFoundError(f"{self.root} does not look like a session folder (no samples.csv)")

    @property
    def meta(self) -> dict:
        p = self.root / "meta.json"
        return json.loads(p.read_text()) if p.exists() else {}

    @property
    def frames_dir(self) -> Path:
        return self.root / "frames"

    def frames(self) -> Frames:
        rows = list(csv.DictReader((self.root / "frames.csv").open()))
        f = lambda k: np.array([float(r[k]) for r in rows], dtype=np.float64)
        return Frames(
            names=[f"{int(r['frame_id']):06d}.jpg" for r in rows],
            t_ns=np.array([int(r["t_ns"]) for r in rows], dtype=np.int64),
            position=np.stack([f("tx"), f("ty"), f("tz")], axis=1) if rows else np.zeros((0, 3)),
            quat_xyzw=np.stack([f("qx"), f("qy"), f("qz"), f("qw")], axis=1) if rows else np.zeros((0, 4)),
            intrinsics=np.stack([f("fx"), f("fy"), f("cx"), f("cy")], axis=1) if rows else np.zeros((0, 4)),
            size=np.stack([f("width"), f("height")], axis=1).astype(int) if rows else np.zeros((0, 2), int),
            sharpness=f("sharpness"),
        )

    def samples(self) -> Samples:
        rows = list(csv.DictReader((self.root / "samples.csv").open()))
        return Samples(
            t_ns=np.array([int(r["t_ns"]) for r in rows], dtype=np.int64),
            kind=np.array([r["kind"] for r in rows], dtype=object),
            id=np.array([r["id"] for r in rows], dtype=object),
            name=np.array([r["name"] for r in rows], dtype=object),
            freq_mhz=np.array([int(r["freq_mhz"] or 0) for r in rows], dtype=np.int32),
            rssi=np.array([float(r["rssi_dbm"]) for r in rows], dtype=np.float64),
            pos=np.array([[float(r["x"]), float(r["y"]), float(r["z"])] for r in rows], dtype=np.float64).reshape(-1, 3),
        )

    def poses(self) -> tuple[np.ndarray, np.ndarray]:
        """Tracked poses only: (t_ns, positions)."""
        t, p = [], []
        with (self.root / "poses.csv").open() as fh:
            for r in csv.DictReader(fh):
                if r["tracking_state"] != "tracking":
                    continue
                t.append(int(r["t_ns"]))
                p.append((float(r["tx"]), float(r["ty"]), float(r["tz"])))
        return np.array(t, dtype=np.int64), np.array(p, dtype=np.float64).reshape(-1, 3)

    def status_path(self) -> Path:
        return self.root / "status.json"

    def write_status(self, **kw) -> None:
        cur = self.read_status()
        cur.update(kw)
        tmp = self.status_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, indent=2))
        tmp.replace(self.status_path())

    def read_status(self) -> dict:
        p = self.status_path()
        try:
            return json.loads(p.read_text()) if p.exists() else {}
        except json.JSONDecodeError:
            return {}
