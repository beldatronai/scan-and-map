# Fedora side: receiver, reconstruction, signal fields

## One-time GPU setup (Fedora + NVIDIA RTX)

GPU tools run in Podman containers, so you never install CUDA on the host (Fedora's GCC is usually
newer than CUDA supports).

```bash
# NVIDIA driver from RPM Fusion
sudo dnf install https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm \
                 https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm
sudo dnf install akmod-nvidia xorg-x11-drv-nvidia-cuda   # reboot after the kmod builds

# Container toolkit + CDI spec so Podman can pass the GPU in
curl -s -L https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo \
  | sudo tee /etc/yum.repos.d/nvidia-container-toolkit.repo
sudo dnf install nvidia-container-toolkit podman
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml

# check
podman run --rm --device nvidia.com/gpu=all --security-opt label=disable \
  docker.io/nvidia/cuda:12.6.3-base-ubi9 nvidia-smi

# pull the tool images (several GB)
podman pull docker.io/colmap/colmap:latest
podman pull ghcr.io/nerfstudio-project/nerfstudio:latest

# let the phone reach the receiver
sudo firewall-cmd --add-port=8765/tcp --permanent && sudo firewall-cmd --reload
```

## Install and run

```bash
cd fedora
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
(cd ../viewer && npm ci && npm run build)
.venv/bin/scanmap serve                       # http://0.0.0.0:8765, sessions in ~/scanmap/sessions
```

To keep it running: `cp systemd/scanmap.service ~/.config/systemd/user/` and
`systemctl --user enable --now scanmap` (adjust the paths in the unit if the repo is elsewhere).

## Pipeline

```bash
scanmap process ~/scanmap/sessions/<id>                       # colmap, align, splat, signal
scanmap process <session> --mode ar_poses                     # keep AR poses, only triangulate
scanmap process <session> --steps colmap,align,mesh,signal    # dense mesh instead of splat
scanmap process <session> --matcher exhaustive                # small sessions, more robust
scanmap signal <session>                                      # signal grids only, no GPU
```

| Step | What it does |
|---|---|
| `colmap` | Features with the phone's intrinsics (PINHOLE, median of `frames.csv`), sequential matching, mapper with intrinsics fixed. `ar_poses` mode writes the AR poses as a fixed model and runs `point_triangulator` instead. |
| `align` | Robust Sim3 (RANSAC + Umeyama) from COLMAP camera centres to AR camera centres; writes `colmap/aligned` and `alignment.json`. Under 5 cm median error is good, over 15 cm means drift or mismatched rooms. |
| `splat` | `ns-train splatfacto` on the aligned model with re-centring, re-orienting and auto-scaling **off**, so the splat stays in the AR frame; `ns-export gaussian-splat` to `splat/splat.ply`. |
| `mesh` | Optional COLMAP dense stereo, fusion and Poisson mesh (slow). |
| `signal` | Per transmitter: 15 cm voxel averaging, Gaussian process on a 20 cm grid, cells more than 1 m from any sample left unknown, path-loss fit to estimate where the transmitter is. A "best of" layer per SSID with several APs (mesh Wi-Fi, dual band). BLE is median-smoothed over 1 s first. |

If a GPU step fails, the signal step still runs, so you always get the heatmap over the AR point cloud.

Environment overrides: `SCANMAP_DATA`, `SCANMAP_RUNTIME=docker`, `SCANMAP_COLMAP=native` (use a host
`colmap` binary), `SCANMAP_COLMAP_IMAGE`, `SCANMAP_NERFSTUDIO_IMAGE`.

## API

```
POST /api/sessions?name=<id>&process=1      body: session zip
GET  /api/sessions                          list with status and outputs
GET  /api/sessions/{id}                     one session
POST /api/sessions/{id}/process?steps=&mode=photogrammetry|ar_poses
GET  /api/sessions/{id}/log                 tail of process.log
GET  /data/{id}/...                         session files
```

The receiver has no authentication: run it on your home LAN only.

## Tests

```bash
.venv/bin/pytest -q     # alignment math, signal field on a synthetic flat, upload + zip-slip guard
```
