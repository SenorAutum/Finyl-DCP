"""
AI Financial Health Agent.

Plain Python + httpx against ANY OpenAI-compatible chat-completions endpoint
(configured via LLM_BASE_URL / LLM_API_KEY / LLM_MODEL). No vendor SDKs.
The live analytics snapshot (pandas over SQL) is injected as grounded context.
"""
import json

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.analytics import build_ai_snapshot

SYSTEM_PROMPT = """You are the Finyl-DCP Financial Health Agent — an expert analyst embedded in a
digital-credit-provider (DCP) platform operating in Kenya. You receive a live JSON analytics
snapshot of the tenant's portfolio and must give concrete, quantified, actionable recommendations.

Rules:
- Ground every claim in the snapshot numbers (cite figures like PAR 30, KES amounts, agent names).
- Be direct and specific: e.g. "Coast region has PAR 30 of 18.4% concentrated in the Liquipay
  8-weeks product — pause new disbursements there and deploy call-center follow-ups."
- Flag regulatory risks (complaint SLA breaches, unreviewed AML flags) proactively.
- Currency is KES. Keep answers concise, use short bullet points and bold key figures.
"""


def chat(db: Session, tenant_id: int, message: str, history: list[dict]) -> str:
    snapshot = build_ai_snapshot(db, tenant_id)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": "LIVE ANALYTICS SNAPSHOT:\n" + json.dumps(snapshot, default=str)},
    ]
    for h in history[-8:]:  # cap context
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": str(h["content"])[:4000]})
    messages.append({"role": "user", "content": message[:4000]})

    resp = httpx.post(
        f"{settings.LLM_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.LLM_API_KEY}",
                 "Content-Type": "application/json"},
        json={"model": settings.LLM_MODEL, "messages": messages, "max_tokens": 1200},
        timeout=90,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
