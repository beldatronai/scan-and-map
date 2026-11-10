"""Run the Fedora-side pipeline for one session.

Steps (default order): colmap -> align -> splat -> signal.   Optional: mesh.
GPU tools run in Podman containers with the NVIDIA CDI device, so Fedora's newer GCC never has
to fight CUDA. Override with SCANMAP_RUNTIME=docker or SCANMAP_COLMAP=native if you prefer.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import time
import traceback
from pathlib import Path

import numpy as np

from . import align as align_mod
from . import signal_field
from .colmap_io import read_model
from .geometry import ar_pose_to_colmap
from .session import Session

COLMAP_IMAGE = os.environ.get("SCANMAP_COLMAP_IMAGE", "docker.io/colmap/colmap:latest")
NERFSTUDIO_IMAGE = os.environ.get("SCANMAP_NERFSTUDIO_IMAGE", "ghcr.io/nerfstudio-project/nerfstudio:latest")
RUNTIME = os.environ.get("SCANMAP_RUNTIME", "podman")
DEFAULT_STEPS = ["colmap", "align", "splat", "signal"]


class StepError(RuntimeError):
    pass


def _log(session: Session, msg: str) -> None:
    with (session.root / "process.log").open("a") as fh:
        fh.write(time.strftime("%H:%M:%S ") + msg + "\n")


def run_tool(session: Session, image: str, args: list[str], native: str | None = None, shm: str | None = None) -> None:
    """Run a containerised tool with the session folder mounted at /data."""
    if native and shutil.which(native.split()[0]):
        cmd = native.split() + [a.replace("/data", str(session.root)) for a in args]
    else:
        if not shutil.which(RUNTIME):
            raise StepError(f"{RUNTIME} not found; install podman and nvidia-container-toolkit (see fedora/README.md)")
        gpu = ["--device", "nvidia.com/gpu=all"] if RUNTIME == "podman" else ["--gpus", "all"]
        cmd = [RUNTIME, "run", "--rm", *gpu, "--security-opt", "label=disable",
               "-v", f"{session.root}:/data", "-w", "/data"]
        if shm:
            cmd += ["--shm-size", shm]
        cmd += [image, *args]
    _log(session, "$ " + " ".join(cmd))
    with (session.root / "process.log").open("a") as fh:
        r = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        raise StepError(f"{args[0]} failed with exit code {r.returncode}; see process.log")


def colmap(session: Session, *args: str) -> None:
    native = "colmap" if os.environ.get("SCANMAP_COLMAP") == "native" else None
    run_tool(session, COLMAP_IMAGE, ["colmap", *args] if native is None else list(args), native=native)


def _camera_params(session: Session) -> tuple[str, int, int]:
    fr = session.frames()
    if len(fr.names) < 10:
        raise StepError(f"only {len(fr.names)} keyframes; walk longer or more slowly")
    fx, fy, cx, cy = np.median(fr.intrinsics, axis=0)
    w, h = (int(v) for v in np.median(fr.size, axis=0))
    return f"{fx:.4f},{fy:.4f},{cx:.4f},{cy:.4f}", w, h


def step_colmap(session: Session, mode: str = "photogrammetry", matcher: str = "sequential") -> dict:
    """Sparse reconstruction. mode=photogrammetry lets COLMAP solve poses (then align);
    mode=ar_poses keeps the AR poses fixed and only triangulates points (always lines up)."""
    out = session.root / "colmap"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    params, w, h = _camera_params(session)
    colmap(session, "feature_extractor", "--database_path", "/data/colmap/database.db", "--image_path", "/data/frames",
           "--ImageReader.single_camera", "1", "--ImageReader.camera_model", "PINHOLE",
           "--ImageReader.camera_params", params)
    if matcher == "exhaustive":
        colmap(session, "exhaustive_matcher", "--database_path", "/data/colmap/database.db")
    else:
        colmap(session, "sequential_matcher", "--database_path", "/data/colmap/database.db",
               "--SequentialMatching.overlap", "20", "--SequentialMatching.quadratic_overlap", "1")

    if mode == "ar_poses":
        model_in = out / "ar_model"
        write_ar_model(session, out / "database.db", model_in, w, h, params)
        (out / "ar_tri").mkdir()
        colmap(session, "point_triangulator", "--database_path", "/data/colmap/database.db", "--image_path", "/data/frames",
               "--input_path", "/data/colmap/ar_model", "--output_path", "/data/colmap/ar_tri")
        (out / "aligned").mkdir()
        colmap(session, "model_converter", "--input_path", "/data/colmap/ar_tri", "--output_path", "/data/colmap/aligned",
               "--output_type", "TXT")
        m = read_model(out / "aligned")
        return {"mode": mode, "registered_images": len(m.images), "points": len(m.points)}

    (out / "sparse").mkdir()
    colmap(session, "mapper", "--database_path", "/data/colmap/database.db", "--image_path", "/data/frames",
           "--output_path", "/data/colmap/sparse",
           "--Mapper.ba_refine_focal_length", "0", "--Mapper.ba_refine_principal_point", "0",
           "--Mapper.ba_refine_extra_params", "0")
    best, best_n = None, -1
    for sub in sorted((out / "sparse").iterdir()):
        txt = out / f"sparse_txt_{sub.name}"
        txt.mkdir()
        colmap(session, "model_converter", "--input_path", f"/data/colmap/sparse/{sub.name}",
               "--output_path", f"/data/colmap/{txt.name}", "--output_type", "TXT")
        n = len(read_model(txt).images)
        if n > best_n:
            best, best_n = txt, n
    if best is None:
        raise StepError("COLMAP produced no model (white walls? try mode=ar_poses)")
    best.rename(out / "sparse_txt")
    return {"mode": mode, "models": len(list((out / "sparse").iterdir())), "registered_images": best_n}


def write_ar_model(session: Session, db: Path, out: Path, w: int, h: int, params: str) -> None:
    """COLMAP text model whose cameras are the AR poses, with image ids taken from the database."""
    con = sqlite3.connect(db)
    ids = {name: (iid, cid) for iid, name, cid in con.execute("SELECT image_id, name, camera_id FROM images")}
    con.close()
    fr = session.frames()
    out.mkdir(parents=True)
    cam_ids = sorted({cid for _, cid in ids.values()})
    (out / "cameras.txt").write_text("".join(f"{c} PINHOLE {w} {h} {params.replace(',', ' ')}\n" for c in cam_ids))
    with (out / "images.txt").open("w") as fh:
        for i, name in enumerate(fr.names):
            if name not in ids:
                continue
            iid, cid = ids[name]
            q, t = ar_pose_to_colmap(fr.position[i], fr.quat_xyzw[i])
            fh.write(f"{iid} {q[0]} {q[1]} {q[2]} {q[3]} {t[0]} {t[1]} {t[2]} {cid} {name}\n\n")
    (out / "points3D.txt").write_text("")


def step_align(session: Session) -> dict:
    out = session.root / "colmap"
    if (out / "aligned").exists() and not (out / "sparse_txt").exists():
        # ar_poses mode: already in the AR frame
        rep = {"mode": "ar_poses", "sim3": {"scale": 1.0}, "median_error_m": 0.0, "quality": "exact (AR poses)"}
        (session.root / "alignment.json").write_text(json.dumps(rep, indent=2))
        return rep
    if (out / "aligned").exists():
        shutil.rmtree(out / "aligned")
    return align_mod.align(session, out / "sparse_txt", out / "aligned")


def step_splat(session: Session, iterations: int = 30000) -> dict:
    """Gaussian splat with nerfstudio splatfacto, trained directly in the AR frame."""
    runs = session.root / "splat_runs"
    if runs.exists():
        shutil.rmtree(runs)
    args = ["ns-train", "splatfacto",
            "--output-dir", "/data/splat_runs", "--experiment-name", "scan", "--timestamp", "run",
            "--max-num-iterations", str(iterations),
            "--viewer.quit-on-train-completion", "True", "--vis", "tensorboard",
            "--data", "/data",
            "colmap",
            "--images-path", "frames", "--colmap-path", "colmap/aligned",
            # keep the AR frame: no re-centring, re-orienting or re-scaling
            "--orientation-method", "none", "--center-method", "none", "--auto-scale-poses", "False",
            "--assume-colmap-world-coordinate-convention", "False",
            "--downscale-factor", "1", "--load-3D-points", "True"]
    run_tool(session, NERFSTUDIO_IMAGE, args, shm="12g")
    configs = list(runs.rglob("config.yml"))
    if not configs:
        raise StepError("splatfacto finished without a config.yml")
    cfg = "/data/" + str(configs[0].relative_to(session.root))
    run_tool(session, NERFSTUDIO_IMAGE, ["ns-export", "gaussian-splat", "--load-config", cfg, "--output-dir", "/data/splat"])
    ply = session.root / "splat" / "splat.ply"
    if not ply.exists():
        cands = list((session.root / "splat").glob("*.ply"))
        if not cands:
            raise StepError("ns-export produced no .ply")
        cands[0].rename(ply)
    return {"file": "splat/splat.ply", "bytes": ply.stat().st_size}


def step_mesh(session: Session) -> dict:
    """Optional dense mesh (slow): undistort, PatchMatch stereo, fusion, Poisson."""
    d = "/data/colmap/dense"
    (session.root / "colmap" / "dense").mkdir(exist_ok=True)
    colmap(session, "image_undistorter", "--image_path", "/data/frames", "--input_path", "/data/colmap/aligned",
           "--output_path", d, "--output_type", "COLMAP")
    colmap(session, "patch_match_stereo", "--workspace_path", d)
    colmap(session, "stereo_fusion", "--workspace_path", d, "--output_path", f"{d}/fused.ply")
    (session.root / "mesh").mkdir(exist_ok=True)
    colmap(session, "poisson_mesher", "--input_path", f"{d}/fused.ply", "--output_path", "/data/mesh/mesh.ply")
    return {"file": "mesh/mesh.ply"}


def _extra_points(session: Session) -> np.ndarray | None:
    p = session.root / "depth_points.ply"
    if not p.exists():
        return None
    return read_ascii_ply_xyz(p)


def read_ascii_ply_xyz(p: Path) -> np.ndarray:
    with p.open() as fh:
        n = 0
        for line in fh:
            if line.startswith("element vertex"):
                n = int(line.split()[2])
            if line.strip() == "end_header":
                break
        rows = [fh.readline().split()[:3] for _ in range(n)]
    return np.array(rows, dtype=np.float64).reshape(-1, 3)


def step_signal(session: Session, voxel: float = 0.2) -> dict:
    idx = signal_field.build(session, voxel=voxel, extra_points=_extra_points(session))
    return {"layers": len(idx["layers"]), "grid": idx["grid"]}


STEPS = {"colmap": step_colmap, "align": step_align, "splat": step_splat, "mesh": step_mesh, "signal": step_signal}


def process(root: str | Path, steps: list[str] | None = None, mode: str = "photogrammetry",
            matcher: str = "sequential", iterations: int = 30000) -> dict:
    session = Session(root)
    steps = steps or DEFAULT_STEPS
    unknown = [s for s in steps if s not in STEPS]
    if unknown:
        raise ValueError(f"unknown steps {unknown}; choose from {list(STEPS)}")
    session.write_status(state="running", steps=steps, done=[], current=None, error=None, results={},
                         started=time.time())
    results: dict = {}
    for name in steps:
        session.write_status(current=name)
        _log(session, f"== {name}")
        t0 = time.time()
        try:
            if name == "colmap":
                res = step_colmap(session, mode=mode, matcher=matcher)
            elif name == "splat":
                res = step_splat(session, iterations=iterations)
            else:
                res = STEPS[name](session)
        except Exception as e:  # noqa: BLE001 - report every failure into status.json
            _log(session, traceback.format_exc())
            session.write_status(state="failed", current=name, error=f"{name}: {e}", results=results)
            # signal fields need nothing from the photogrammetry steps: still build them
            if name != "signal" and "signal" in steps and name in ("colmap", "align", "splat", "mesh"):
                try:
                    results["signal"] = step_signal(session)
                    session.write_status(results=results)
                except Exception:  # noqa: BLE001
                    _log(session, traceback.format_exc())
            return session.read_status()
        res["seconds"] = round(time.time() - t0, 1)
        results[name] = res
        st = session.read_status()
        session.write_status(done=st.get("done", []) + [name], results=results)
    session.write_status(state="done", current=None, finished=time.time())
    return session.read_status()
