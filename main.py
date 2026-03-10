"""Convenience launcher for local development.

Run:
    python main.py
or:
    uvicorn taxspine_orchestrator.main:app --reload --port 8000
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "taxspine_orchestrator.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
