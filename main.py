"""Convenience launcher — exposes ``app`` so both launch styles work.

Run (either works):
    python main.py
    uvicorn main:app --reload --port 8000
    uvicorn taxspine_orchestrator.main:app --reload --port 8000
"""

from taxspine_orchestrator.main import app  # noqa: F401 — required for ``uvicorn main:app``

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "taxspine_orchestrator.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
