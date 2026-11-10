"""Minimal COLMAP text-model reader/writer (cameras.txt, images.txt, points3D.txt)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class Image:
    id: int
    qvec: np.ndarray  # w, x, y, z  (world-to-camera)
    tvec: np.ndarray
    camera_id: int
    name: str
    points2d: str = ""  # raw second line, kept verbatim

    @property
    def center(self) -> np.ndarray:
        from .geometry import quat_wxyz_to_R
        R = quat_wxyz_to_R(self.qvec)
        return -R.T @ self.tvec


@dataclass
class Model:
    cameras: list[str] = field(default_factory=list)  # raw lines
    images: dict[int, Image] = field(default_factory=dict)
    points: list[list[str]] = field(default_factory=list)  # tokenised lines


def _lines(p: Path):
    for line in p.read_text().splitlines():
        if line.startswith("#"):
            continue
        yield line


def read_model(d: str | Path) -> Model:
    d = Path(d)
    m = Model()
    m.cameras = [l for l in _lines(d / "cameras.txt") if l.strip()]
    lines = list(_lines(d / "images.txt"))
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        tok = lines[i].split()
        img = Image(
            id=int(tok[0]),
            qvec=np.array([float(v) for v in tok[1:5]]),
            tvec=np.array([float(v) for v in tok[5:8]]),
            camera_id=int(tok[8]),
            name=" ".join(tok[9:]),
            points2d=lines[i + 1] if i + 1 < len(lines) else "",
        )
        m.images[img.id] = img
        i += 2
    p3 = d / "points3D.txt"
    if p3.exists():
        m.points = [l.split() for l in _lines(p3) if l.strip()]
    return m


def write_model(m: Model, d: str | Path) -> None:
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    (d / "cameras.txt").write_text("# CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n" + "\n".join(m.cameras) + "\n")
    with (d / "images.txt").open("w") as fh:
        fh.write("# IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME\n# POINTS2D[] as (X, Y, POINT3D_ID)\n")
        for img in sorted(m.images.values(), key=lambda x: x.id):
            q, t = img.qvec, img.tvec
            fh.write(f"{img.id} {q[0]:.10f} {q[1]:.10f} {q[2]:.10f} {q[3]:.10f} {t[0]:.10f} {t[1]:.10f} {t[2]:.10f} {img.camera_id} {img.name}\n")
            fh.write(img.points2d + "\n")
    with (d / "points3D.txt").open("w") as fh:
        fh.write("# POINT3D_ID, X, Y, Z, R, G, B, ERROR, TRACK[] as (IMAGE_ID, POINT2D_IDX)\n")
        for tok in m.points:
            fh.write(" ".join(tok) + "\n")


def points_xyz(m: Model) -> np.ndarray:
    if not m.points:
        return np.zeros((0, 3))
    return np.array([[float(t[1]), float(t[2]), float(t[3])] for t in m.points])
