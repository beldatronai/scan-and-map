"""Rotations, COLMAP <-> ARCore conventions, and robust similarity (Sim3) fitting."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# ARCore/OpenGL camera: +X right, +Y up, looks down -Z.  COLMAP/OpenCV: +X right, +Y down, looks down +Z.
GL_TO_CV = np.diag([1.0, -1.0, -1.0])


def quat_xyzw_to_R(q: np.ndarray) -> np.ndarray:
    x, y, z, w = q / np.linalg.norm(q)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def R_to_quat_wxyz(R: np.ndarray) -> np.ndarray:
    """Rotation matrix to unit quaternion (w, x, y, z), w >= 0 (COLMAP order)."""
    m = R
    t = np.trace(m)
    if t > 0:
        s = np.sqrt(t + 1.0) * 2
        q = [0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = [(m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s]
    elif m[1, 1] > m[2, 2]:
        s = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = [(m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s]
    else:
        s = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = [(m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s]
    q = np.array(q)
    q /= np.linalg.norm(q)
    return q if q[0] >= 0 else -q


def quat_wxyz_to_R(q: np.ndarray) -> np.ndarray:
    w, x, y, z = q
    return quat_xyzw_to_R(np.array([x, y, z, w]))


def ar_pose_to_colmap(position: np.ndarray, quat_xyzw: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """ARCore camera-to-world (GL camera) -> COLMAP world-to-camera (qvec wxyz, tvec)."""
    R_c2w = quat_xyzw_to_R(quat_xyzw) @ GL_TO_CV
    R_w2c = R_c2w.T
    t_w2c = -R_w2c @ position
    return R_to_quat_wxyz(R_w2c), t_w2c


@dataclass
class Sim3:
    scale: float
    R: np.ndarray
    t: np.ndarray

    def apply(self, X: np.ndarray) -> np.ndarray:
        return self.scale * (X @ self.R.T) + self.t

    def to_dict(self) -> dict:
        M = np.eye(4)
        M[:3, :3] = self.scale * self.R
        M[:3, 3] = self.t
        return {"scale": float(self.scale), "rotation": self.R.tolist(), "translation": self.t.tolist(), "matrix4x4": M.tolist()}


def umeyama(src: np.ndarray, dst: np.ndarray) -> Sim3:
    """Least-squares similarity with dst ~= s * R @ src + t (Umeyama 1991)."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    xs, xd = src - mu_s, dst - mu_d
    cov = xd.T @ xs / len(src)
    U, D, Vt = np.linalg.svd(cov)
    S = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[2, 2] = -1
    R = U @ S @ Vt
    var_s = (xs ** 2).sum() / len(src)
    s = float(np.trace(np.diag(D) @ S) / var_s)
    t = mu_d - s * R @ mu_s
    return Sim3(s, R, t)


def ransac_sim3(src: np.ndarray, dst: np.ndarray, threshold: float = 0.10, iters: int = 2000,
                seed: int = 0) -> tuple[Sim3, np.ndarray]:
    """Robust Sim3 fit. threshold is in dst units (metres). Returns (model, inlier mask)."""
    n = len(src)
    if n < 3:
        raise ValueError("need at least 3 correspondences")
    rng = np.random.default_rng(seed)
    best_inl = np.zeros(n, bool)
    for _ in range(iters):
        idx = rng.choice(n, 3, replace=False)
        try:
            m = umeyama(src[idx], dst[idx])
        except np.linalg.LinAlgError:
            continue
        if not np.isfinite(m.scale) or m.scale <= 0:
            continue
        err = np.linalg.norm(m.apply(src) - dst, axis=1)
        inl = err < threshold
        if inl.sum() > best_inl.sum():
            best_inl = inl
            if inl.mean() > 0.95:
                break
    if best_inl.sum() < 3:
        best_inl = np.ones(n, bool)
    model = umeyama(src[best_inl], dst[best_inl])
    # one refinement round with the refit model
    err = np.linalg.norm(model.apply(src) - dst, axis=1)
    inl = err < threshold
    if inl.sum() >= 3:
        model = umeyama(src[inl], dst[inl])
        best_inl = inl
    return model, best_inl
