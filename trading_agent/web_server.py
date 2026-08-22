from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

import uvicorn


def ensure_frontend_build(rebuild: bool = False) -> None:
    root = Path(__file__).resolve().parents[1]
    frontend = root / "frontend"
    output = frontend / "dist" / "index.html"
    if output.exists() and not rebuild:
        return
    npm = shutil.which("npm")
    if not npm or not (frontend / "package.json").exists():
        raise RuntimeError("FRONTEND_BUILD_UNAVAILABLE: npm or frontend/package.json missing")
    if not (frontend / "node_modules").exists():
        install = "ci" if (frontend / "package-lock.json").exists() else "install"
        subprocess.run([npm, install], cwd=frontend, check=True)
    subprocess.run([npm, "run", "build"], cwd=frontend, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start the local OKX Trading Dashboard")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--rebuild-frontend", action="store_true")
    args = parser.parse_args(argv)
    ensure_frontend_build(args.rebuild_frontend)
    uvicorn.run(
        "trading_agent.web_api.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=args.port,
        workers=1,
        reload=False,
        access_log=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
