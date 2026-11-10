"""Phase 3: move a COLMAP reconstruction into the phone's AR frame with one Sim3.

Radio samples never move: they are already in the AR frame. Every keyframe has two camera
centres (AR tracking and COLMAP); a robust Sim3 between the two sets puts the photogrammetry
model, and anything trained from it (the Gaussian splat), at true metric scale, gravity aligned.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .colmap_io import Model, read_model, write_model
from .geometry import Sim3, quat_wxyz_to_R, R_to_quat_wxyz, ransac_sim3
from .session import Session


def transform_model(m: Model, T: Sim3) -> Model:
    """Apply X' = s R X + t to every point and camera of a COLMAP model."""
    out = Model(cameras=list(m.cameras))
    for img in m.images.values():
        Rc = quat_wxyz_to_R(img.qvec)
        Rn = Rc @ T.R.T
        tn = T.scale * img.tvec - Rn @ T.t
        out.images[img.id] = type(img)(img.id, R_to_quat_wxyz(Rn), tn, img.camera_id, img.name, img.points2d)
    for tok in m.points:
        X = np.array([float(v) for v in tok[1:4]])
        Xn = T.apply(X[None])[0]
        tok = list(tok)
        tok[1:4] = [f"{v:.6f}" for v in Xn]
        out.points.append(tok)
    return out


def align(session: Session, model_dir: Path, out_dir: Path, threshold_m: float = 0.10) -> dict:
    model = read_model(model_dir)
    fr = session.frames()
    ar_by_name = {n: fr.position[i] for i, n in enumerate(fr.names)}
    names, src, dst = [], [], []
    for img in model.images.values():
        if img.name in ar_by_name:
            names.append(img.name)
            src.append(img.center)
            dst.append(ar_by_name[img.name])
    if len(src) < 3:
        raise RuntimeError(f"only {len(src)} registered images match keyframes; cannot align")
    src, dst = np.array(src), np.array(dst)
    T, inliers = ransac_sim3(src, dst, threshold=threshold_m)
    err = np.linalg.norm(T.apply(src) - dst, axis=1)
    aligned = transform_model(model, T)
    write_model(aligned, out_dir)

    report = {
        "sim3": T.to_dict(),
        "registered_images": len(model.images),
        "keyframes": len(fr.names),
        "matched": len(src),
        "inliers": int(inliers.sum()),
        "median_error_m": float(np.median(err)),
        "p90_error_m": float(np.percentile(err, 90)),
        "quality": quality(float(np.median(err))),
        "up_axis_check": up_axis_check(aligned),
    }
    (session.root / "alignment.json").write_text(json.dumps(report, indent=2))
    return report


def quality(median_err: float) -> str:
    if median_err < 0.05:
        return "good"
    if median_err < 0.15:
        return "ok"
    return "poor: the walk drifted or COLMAP mismatched rooms; try mode=ar_poses"


def up_axis_check(m: Model) -> dict:
    """After alignment +Y should be up: the floor is the low end of the point cloud in Y."""
    from .colmap_io import points_xyz
    P = points_xyz(m)
    if len(P) < 50:
        return {"points": len(P)}
    lo, hi = np.percentile(P[:, 1], [2, 98])
    return {"points": len(P), "floor_y_m": float(lo), "ceiling_y_m": float(hi), "room_height_m": float(hi - lo)}
