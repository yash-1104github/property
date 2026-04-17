"""
Run the HTTP API (from repository root):

    python -m backend

Install deps first:

    python -m pip install -r backend/requirements.txt

Env (optional): API_HOST, API_PORT, API_RELOAD; `.env` is read from the repo root.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _backend_root() -> Path:
    return Path(__file__).resolve().parent


def _ensure_import_path() -> Path:
    root = _backend_root()
    p = str(root)
    if p not in sys.path:
        sys.path.insert(0, p)
    return root


def main() -> None:
    backend_root = _ensure_import_path()
    from dotenv import load_dotenv

    load_dotenv(backend_root.parent / ".env")

    import uvicorn

    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8000"))
    reload = os.getenv("API_RELOAD", "0").lower() in ("1", "true", "yes")
    if reload:
        uvicorn.run(
            "api.main:app",
            host=host,
            port=port,
            reload=True,
            reload_dirs=[str(backend_root)],
        )
    else:
        from api.main import app

        uvicorn.run(app, host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
