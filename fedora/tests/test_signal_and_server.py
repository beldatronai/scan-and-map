import io
import json
import sys
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import fake_session
from scanmap import signal_field
from scanmap.pipeline import process
from scanmap.session import Session


def test_signal_field_router_is_hottest(tmp_path):
    root = fake_session.make(tmp_path / "s")
    idx = signal_field.build(Session(root), voxel=0.25)
    layers = {l["id"]: l for l in idx["layers"]}
    conn = layers["wifi_02:11:22:33:44:55"]
    assert "connected" in conn["name"]
    hdr = idx["grid"]
    vals = np.fromfile(root / "signal" / conn["file"], dtype=np.float32)
    assert vals.size == np.prod(hdr["dims"])
    nx, ny, nz = hdr["dims"]
    g = vals.reshape(nz, ny, nx)
    k = np.nanargmax(g)
    iz, iy, ix = np.unravel_index(k, g.shape)
    hot = np.array(hdr["origin"]) + (np.array([ix, iy, iz]) + 0.5) * hdr["voxel"]
    router = np.array(json.loads((root / "meta.json").read_text())["truth"]["router"])
    assert np.linalg.norm(hot[[0, 2]] - router[[0, 2]]) < 1.5, (hot, router)
    est = conn["estimate"]["position"]
    assert np.linalg.norm(np.array(est)[[0, 2]] - router[[0, 2]]) < 1.5, est
    assert any(l["kind"] == "ble" for l in idx["layers"])
    assert any(l["kind"] == "wifi_ssid" for l in idx["layers"])


def test_process_without_gpu_still_builds_signal(tmp_path, monkeypatch):
    import scanmap.pipeline as p
    monkeypatch.setattr(p, "RUNTIME", "definitely-not-installed")
    root = fake_session.make(tmp_path / "s")
    st = process(root)
    assert st["state"] == "failed" and "colmap" in st["error"]
    assert (root / "signal" / "index.json").exists()


def test_upload_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANMAP_DATA", str(tmp_path / "data"))
    import importlib
    import scanmap.server as server
    importlib.reload(server)
    from fastapi.testclient import TestClient
    src = fake_session.make(tmp_path / "20261031-120000")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in src.rglob("*"):
            if f.is_file():
                zf.write(f, f"{src.name}/{f.relative_to(src)}")
    c = TestClient(server.app)
    r = c.post("/api/sessions?name=20261031-120000&process=0", content=buf.getvalue(),
               headers={"Content-Type": "application/zip"})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert (tmp_path / "data" / sid / "samples.csv").exists()
    lst = c.get("/api/sessions").json()
    assert lst[0]["id"] == sid and lst[0]["outputs"]["depth_points"]
    assert c.get(f"/data/{sid}/meta.json").status_code == 200
    # zip-slip is refused
    evil = io.BytesIO()
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../escape.txt", "x")
        zf.writestr("samples.csv", "t_ns\n")
    r = c.post("/api/sessions?name=evil&process=0", content=evil.getvalue())
    assert r.status_code == 400
    assert not (tmp_path / "escape.txt").exists()
