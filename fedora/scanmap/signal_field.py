"""Phase 4a: turn radio samples into 3D signal grids, one per transmitter.

Output (in <session>/signal/):
  index.json    list of layers with name, kind, sample count, strongest reading, grid header,
                and an optional estimated transmitter position
  <layer>.bin   Float32 grid, x fastest then y then z; NaN = unknown (too far from any sample)
"""
from __future__ import annotations

import json
import re
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial import cKDTree
from sklearn.exceptions import ConvergenceWarning
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel, WhiteKernel

from .session import Samples, Session


@dataclass
class Grid:
    origin: np.ndarray  # (3,) min corner, metres
    voxel: float
    dims: tuple[int, int, int]

    def centers(self) -> np.ndarray:
        nx, ny, nz = self.dims
        xs = self.origin[0] + (np.arange(nx) + 0.5) * self.voxel
        ys = self.origin[1] + (np.arange(ny) + 0.5) * self.voxel
        zs = self.origin[2] + (np.arange(nz) + 0.5) * self.voxel
        Z, Y, X = np.meshgrid(zs, ys, xs, indexing="ij")  # x fastest in C order
        return np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1)

    def header(self) -> dict:
        return {"origin": self.origin.tolist(), "voxel": self.voxel, "dims": list(self.dims), "order": "x-fastest", "dtype": "float32"}


def make_grid(points: np.ndarray, voxel: float, pad: float = 0.5, max_voxels: int = 600_000) -> Grid:
    lo = points.min(0) - pad
    hi = points.max(0) + pad
    while True:
        dims = tuple(int(np.ceil(v)) for v in (hi - lo) / voxel)
        if np.prod(dims) <= max_voxels:
            return Grid(lo, voxel, dims)  # type: ignore[arg-type]
        voxel *= 1.25


def smooth_ble(t_ns: np.ndarray, rssi: np.ndarray, window_s: float = 1.0) -> np.ndarray:
    """Running median over a time window: BLE RSSI jumps 10 dB+ between packets."""
    order = np.argsort(t_ns)
    t = t_ns[order] / 1e9
    r = rssi[order]
    out = np.empty_like(r)
    j0 = 0
    for i in range(len(t)):
        while t[i] - t[j0] > window_s:
            j0 += 1
        out[i] = np.median(r[j0:i + 1])
    res = np.empty_like(out)
    res[order] = out
    return res


def voxel_average(pos: np.ndarray, val: np.ndarray, size: float) -> tuple[np.ndarray, np.ndarray]:
    """Average samples that fall in the same small voxel (reduces noise and GP size)."""
    keys = np.floor(pos / size).astype(np.int64)
    _, inv, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    P = np.zeros((len(counts), 3)); V = np.zeros(len(counts))
    np.add.at(P, inv, pos); np.add.at(V, inv, val)
    return P / counts[:, None], V / counts


def fit_field(pos: np.ndarray, rssi: np.ndarray, grid: Grid, max_dist: float, max_train: int = 1500,
              seed: int = 0) -> np.ndarray:
    P, V = voxel_average(pos, rssi, 0.15)
    if len(P) > max_train:
        idx = np.random.default_rng(seed).choice(len(P), max_train, replace=False)
        P, V = P[idx], V[idx]
    kernel = ConstantKernel(36.0, (1.0, 400.0)) * RBF(length_scale=1.5, length_scale_bounds=(0.3, 8.0)) \
        + WhiteKernel(noise_level=9.0, noise_level_bounds=(0.5, 100.0))
    gp = GaussianProcessRegressor(kernel=kernel, normalize_y=True, n_restarts_optimizer=0, random_state=seed)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        gp.fit(P, V)
    C = grid.centers()
    dist, _ = cKDTree(pos).query(C, k=1)
    known = dist <= max_dist
    out = np.full(len(C), np.nan, dtype=np.float32)
    idx = np.flatnonzero(known)
    for s in range(0, len(idx), 50_000):
        chunk = idx[s:s + 50_000]
        out[chunk] = gp.predict(C[chunk]).astype(np.float32)
    return out


def locate_transmitter(pos: np.ndarray, rssi: np.ndarray) -> dict | None:
    """Fit RSSI = P0 - 10 n log10(d) for an unknown transmitter position (rough!)."""
    if len(pos) < 20 or np.ptp(pos, axis=0).max() < 1.0:
        return None
    order = np.argsort(-rssi)
    x0 = np.concatenate([pos[order[:5]].mean(0), [rssi.max() + 5.0, 2.5]])

    def resid(p):
        d = np.maximum(np.linalg.norm(pos - p[:3], axis=1), 0.3)
        return p[3] - 10 * p[4] * np.log10(d) - rssi

    lo = np.concatenate([pos.min(0) - 3, [-80, 1.2]])
    hi = np.concatenate([pos.max(0) + 3, [0, 6.0]])
    x0 = np.clip(x0, lo + 1e-6, hi - 1e-6)
    r = least_squares(resid, x0, bounds=(lo, hi), loss="soft_l1", f_scale=4.0)
    rms = float(np.sqrt(np.mean(r.fun ** 2)))
    return {"position": r.x[:3].tolist(), "p0_dbm": float(r.x[3]), "path_loss_exponent": float(r.x[4]), "rms_db": rms}


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("_")[:80] or "x"


def build(session: Session, voxel: float = 0.2, max_dist: float = 1.0, min_samples: int = 15,
          max_wifi: int = 25, max_ble: int = 25, extra_points: np.ndarray | None = None) -> dict:
    S = session.samples()
    out_dir = session.root / "signal"
    out_dir.mkdir(exist_ok=True)
    for old in out_dir.glob("*.bin"):
        old.unlink()
    if len(S) == 0:
        index = {"grid": None, "layers": []}
        (out_dir / "index.json").write_text(json.dumps(index, indent=2))
        return index

    bbox_pts = S.pos if extra_points is None or len(extra_points) == 0 else np.vstack([S.pos, extra_points])
    # keep the grid around where we walked, not the whole reconstruction
    lo, hi = S.pos.min(0) - 1.5, S.pos.max(0) + 1.5
    bbox_pts = bbox_pts[np.all((bbox_pts >= lo) & (bbox_pts <= hi), axis=1)]
    grid = make_grid(bbox_pts, voxel)

    layers = []
    wifi_mask = np.isin(S.kind, ["wifi_conn", "wifi_scan"])
    ble_mask = S.kind == "ble"

    def emit(layer_id: str, kind: str, name: str, sub: Samples, rssi: np.ndarray, values: np.ndarray | None = None,
             members: list[str] | None = None):
        if values is None:
            values = fit_field(sub.pos, rssi, grid, max_dist)
        fname = f"{slug(layer_id)}.bin"
        values.astype(np.float32).tofile(out_dir / fname)
        finite = values[np.isfinite(values)]
        layers.append({
            "id": layer_id, "kind": kind, "name": name, "file": fname,
            "samples": int(len(sub)), "max_rssi": float(rssi.max()) if len(rssi) else None,
            "median_rssi": float(np.median(rssi)) if len(rssi) else None,
            "grid_min": float(finite.min()) if len(finite) else None,
            "grid_max": float(finite.max()) if len(finite) else None,
            "freq_mhz": int(np.median(sub.freq_mhz)) if len(sub) else None,
            "estimate": locate_transmitter(sub.pos, rssi) if len(sub) else None,
            "members": members or [],
        })
        return values

    # Wi-Fi: per BSSID (scan + connected readings of the same BSSID together)
    ids, counts = np.unique(S.id[wifi_mask], return_counts=True)
    order = np.argsort(-counts)
    per_ssid: dict[str, list[tuple[str, np.ndarray]]] = {}
    connected = set(S.id[S.kind == "wifi_conn"])
    chosen = [ids[i] for i in order if counts[i] >= min_samples]
    chosen = sorted(chosen, key=lambda b: (b not in connected, -int(counts[list(ids).index(b)])))[:max_wifi]
    for bssid in chosen:
        sub = S.subset(wifi_mask & (S.id == bssid))
        ssid = next((n for n in sub.name if n), "") or "(hidden)"
        band = "2.4 GHz" if np.median(sub.freq_mhz) < 3000 else ("5 GHz" if np.median(sub.freq_mhz) < 5925 else "6 GHz")
        label = f"{ssid} · {band} · {bssid}" + (" · connected" if bssid in connected else "")
        vals = emit(f"wifi_{bssid}", "wifi", label, sub, sub.rssi)
        per_ssid.setdefault(ssid, []).append((bssid, vals))

    # "best of" layer per SSID that has several BSSIDs (mesh systems, dual band)
    for ssid, members in per_ssid.items():
        if len(members) < 2:
            continue
        stack = np.stack([v for _, v in members])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            best = np.nanmax(stack, axis=0).astype(np.float32)
        bss = [b for b, _ in members]
        sub = S.subset(wifi_mask & np.isin(S.id, bss))
        emit(f"ssid_{ssid}", "wifi_ssid", f"{ssid} · best of {len(bss)} APs", sub, sub.rssi, values=best, members=bss)

    # BLE: per address, smoothed
    ids, counts = np.unique(S.id[ble_mask], return_counts=True)
    for i in np.argsort(-counts)[:max_ble]:
        if counts[i] < min_samples:
            break
        addr = ids[i]
        sub = S.subset(ble_mask & (S.id == addr))
        rssi = smooth_ble(sub.t_ns, sub.rssi)
        name = next((n for n in sub.name if n), "") or "(unnamed)"
        emit(f"ble_{addr}", "ble", f"{name} · {addr}", sub, rssi)

    index = {"grid": grid.header(), "max_dist_m": max_dist, "layers": layers}
    (out_dir / "index.json").write_text(json.dumps(index, indent=2))
    return index
