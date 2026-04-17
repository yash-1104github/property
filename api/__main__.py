"""
Run the API locally for Postman or curl:

  cd /path/to/Property
  python -m api

Optional env: API_HOST (default 0.0.0.0), API_PORT (default 8000),
API_RELOAD (default 0; set 1 for dev reload).
"""
from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8000"))
    reload = os.getenv("API_RELOAD", "0").lower() in ("1", "true", "yes")
    if reload:
        uvicorn.run("api.main:app", host=host, port=port, reload=True)
    else:
        from api.main import app

        uvicorn.run(app, host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
