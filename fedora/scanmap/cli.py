"""scanmap CLI: `scanmap serve`, `scanmap process <session>`, `scanmap signal <session>`."""
from __future__ import annotations

import argparse
import json
import os


def main() -> None:
    ap = argparse.ArgumentParser(prog="scanmap")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the receiver + viewer")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--data", help="sessions folder (default ~/scanmap/sessions)")

    p = sub.add_parser("process", help="run the pipeline on a session folder")
    p.add_argument("session")
    p.add_argument("--steps", default="colmap,align,splat,signal")
    p.add_argument("--mode", choices=["photogrammetry", "ar_poses"], default="photogrammetry")
    p.add_argument("--matcher", choices=["sequential", "exhaustive"], default="sequential")
    p.add_argument("--iterations", type=int, default=30000)

    g = sub.add_parser("signal", help="only build the signal grids (no GPU needed)")
    g.add_argument("session")
    g.add_argument("--voxel", type=float, default=0.2)

    a = ap.parse_args()
    if a.cmd == "serve":
        if a.data:
            os.environ["SCANMAP_DATA"] = a.data
        import uvicorn
        uvicorn.run("scanmap.server:app", host=a.host, port=a.port)
    elif a.cmd == "process":
        from .pipeline import process
        print(json.dumps(process(a.session, a.steps.split(","), a.mode, a.matcher, a.iterations), indent=2))
    elif a.cmd == "signal":
        from .pipeline import step_signal
        from .session import Session
        print(json.dumps(step_signal(Session(a.session), a.voxel), indent=2))


if __name__ == "__main__":
    main()
