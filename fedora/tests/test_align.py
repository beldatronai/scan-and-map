import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from scanmap import align
from scanmap.colmap_io import Image, Model, read_model, write_model
from scanmap.geometry import Sim3, ar_pose_to_colmap, quat_xyzw_to_R, ransac_sim3, umeyama
from scanmap.session import Session
import fake_session


def random_rotation(rng):
    q = rng.normal(size=4)
    return quat_xyzw_to_R(q / np.linalg.norm(q))


def test_umeyama_recovers_sim3():
    rng = np.random.default_rng(0)
    src = rng.normal(size=(50, 3))
    T = Sim3(2.7, random_rotation(rng), np.array([1.0, -2.0, 0.5]))
    est = umeyama(src, T.apply(src))
    assert abs(est.scale - 2.7) < 1e-9
    assert np.allclose(est.R, T.R) and np.allclose(est.t, T.t)


def test_ransac_ignores_outliers():
    rng = np.random.default_rng(1)
    src = rng.uniform(-3, 3, size=(200, 3))
    T = Sim3(0.4, random_rotation(rng), np.array([0.2, 0.1, -0.3]))
    dst = T.apply(src) + rng.normal(0, 0.005, size=src.shape)
    dst[:40] += rng.uniform(-2, 2, size=(40, 3))
    est, inl = ransac_sim3(src, dst, threshold=0.05)
    assert inl[40:].mean() > 0.95 and inl[:40].mean() < 0.2
    assert abs(est.scale - 0.4) < 0.01


def test_align_session_end_to_end(tmp_path):
    root = fake_session.make(tmp_path / "s")
    s = Session(root)
    fr = s.frames()
    # Fake a COLMAP model: AR cameras expressed in an arbitrary frame (scale 0.37, rotated, shifted)
    rng = np.random.default_rng(3)
    T_ar_from_colmap = Sim3(1 / 0.37, random_rotation(rng), np.array([3.0, -1.0, 2.0]))
    inv_R = T_ar_from_colmap.R.T
    m = Model(cameras=["1 PINHOLE 1920 1080 1450 1450 960 540"])
    for i, name in enumerate(fr.names):
        q, t = ar_pose_to_colmap(fr.position[i], fr.quat_xyzw[i])
        # move the camera into the "COLMAP" frame: X_c = R^T (X_ar - t) / s
        from scanmap.geometry import quat_wxyz_to_R, R_to_quat_wxyz
        Rc = quat_wxyz_to_R(q)
        C_ar = fr.position[i]
        C_c = inv_R @ (C_ar - T_ar_from_colmap.t) / T_ar_from_colmap.scale
        Rc_c = Rc @ T_ar_from_colmap.R
        m.images[i + 1] = Image(i + 1, R_to_quat_wxyz(Rc_c), -Rc_c @ C_c, 1, name, "")
    m.points = [["1", "0.1", "0.2", "0.3", "255", "255", "255", "0.5"]]
    write_model(m, tmp_path / "colmap_txt")
    rep = align.align(s, tmp_path / "colmap_txt", tmp_path / "aligned")
    assert rep["median_error_m"] < 1e-6, rep
    assert abs(rep["sim3"]["scale"] - 1 / 0.37) < 1e-6
    aligned = read_model(tmp_path / "aligned")
    for i, name in enumerate(fr.names[:20]):
        assert np.allclose(aligned.images[i + 1].center, fr.position[i], atol=1e-6)
        # rotation must match the AR camera too
        q, _ = ar_pose_to_colmap(fr.position[i], fr.quat_xyzw[i])
        assert np.allclose(np.abs(aligned.images[i + 1].qvec @ q), 1.0, atol=1e-6)


def test_ar_pose_to_colmap_looks_forward():
    # identity AR pose looks down -Z; in COLMAP the camera's +Z axis must point to world -Z
    q, t = ar_pose_to_colmap(np.zeros(3), np.array([0, 0, 0, 1.0]))
    from scanmap.geometry import quat_wxyz_to_R
    R_w2c = quat_wxyz_to_R(q)
    assert np.allclose(R_w2c @ np.array([0, 0, -1.0]), [0, 0, 1.0])
