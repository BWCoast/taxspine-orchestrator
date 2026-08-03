"""AI analysis endpoint — calls Anthropic Claude to reason about tax gaps.

Claude's role is strictly advisory:
  - Read review warnings, RF-1159 output, and error messages
  - Reason about what is missing and why
  - Suggest concrete actions the user should take (transfer linking, manual basis entry, etc.)
  - NEVER fabricate cost basis numbers, prices, or tax amounts
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .config import settings

_log = logging.getLogger(__name__)
router = APIRouter(prefix="/ai", tags=["ai"])

_SYSTEM_PROMPT = """You are a tax analysis assistant for ALTANA, a self-hosted Norwegian crypto tax system.

Your role:
1. Read the provided tax review data (warnings, errors, RF-1159 draft output, job details).
2. Reason clearly about what is missing, why errors occurred, and what the user should do.
3. Suggest specific, actionable steps the user can take to resolve gaps.

Hard constraints — never violate:
- NEVER fabricate, estimate, or suggest specific cost basis amounts, prices, or NOK values.
- NEVER claim an asset has a particular price or value unless it appears in the provided data.
- NEVER modify or suggest modifying the deterministic tax calculation itself.
- DO suggest: which events need manual review, what data sources are missing,
  how to resolve transfer linking issues, how to interpret error messages.
- DO explain: why an event has no basis, what "unlinked transfer" means in FIFO context,
  how carry-forward lots from previous years work, and what the user needs to supply.

Be concise and direct. Use bullet points for actionable steps. If you cannot determine
the root cause from the provided data, say so clearly rather than guessing."""


class AnalyzeRequest(BaseModel):
    context: str   # Structured context (review JSON snippet, warnings list, error text)
    question: str  # User question, or "analyze all" to get a full summary


class AnalyzeResponse(BaseModel):
    analysis: str
    model: str


@router.get("/status", tags=["ai"])
def ai_status() -> dict:
    """Return whether the AI analysis feature is available (ANTHROPIC_API_KEY configured)."""
    return {"available": bool(settings.ANTHROPIC_API_KEY)}


@router.post("/analyze", response_model=AnalyzeResponse, tags=["ai"])
def analyze(body: AnalyzeRequest) -> AnalyzeResponse:
    """Call Claude to reason about tax gaps and suggest remediation steps.

    Requires ANTHROPIC_API_KEY in the server environment (.env or shell).
    Claude is advisory only — it cannot modify tax calculations or invent numbers.

    Parameters
    ----------
    context:
        Structured context to send to Claude. Include relevant warnings,
        error messages, and any RF-1159 or review JSON snippets.
    question:
        The user's specific question, or ``"analyze all"`` for a full summary.
    """
    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "ANTHROPIC_API_KEY is not configured. "
                "Add it to your .env file and restart the server."
            ),
        )

    payload = {
        "model": "claude-opus-4-5",
        "max_tokens": 1500,
        "system": _SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"## Tax review data\n\n{body.context}"
                    f"\n\n## Question\n\n{body.question}"
                ),
            }
        ],
    }

    req_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=req_bytes,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw).get("error", {}).get("message", raw)
        except Exception:
            detail = raw
        _log.error("Anthropic API %s: %s", exc.code, detail)
        raise HTTPException(status_code=502, detail=f"Anthropic API error: {detail}") from exc
    except Exception as exc:
        _log.exception("Unexpected error calling Anthropic API")
        raise HTTPException(
            status_code=502, detail=f"Failed to reach Anthropic API: {exc}"
        ) from exc

    text = (data.get("content") or [{}])[0].get("text", "")
    if not text:
        raise HTTPException(status_code=502, detail="Empty response from Claude")

    return AnalyzeResponse(analysis=text, model=data.get("model", "claude-opus-4-5"))
