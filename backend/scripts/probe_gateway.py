"""One-shot probe of the organiser LLM gateway (PLAN.md OQ2, T4). Findings go to docs/INFRA.md.

Spends live tokens: ONE /api/chat call (plus one auth retry at most) and one GET /api/tags.
Usage: cd backend && uv run python scripts/probe_gateway.py [--no-num-predict]
"""

import json
import sys
import time

import httpx

from procureai.config.settings import get_settings

PROMPT = "Reply with exactly: pong"
# Long-output prompt: used with --no-num-predict to measure the gateway's default output cap.
LONG_PROMPT = "Count from 1 to 400, separated by spaces. Output only the numbers."
AUTH_HEADERS = ("Authorization: Bearer", "X-API-Key")


def _headers(style: str, key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"} if style.startswith("Authorization") else {"X-API-Key": key}


def _redact(headers: dict[str, str]) -> dict[str, str]:
    return {k: "<redacted>" for k in headers}


def probe_tags(client: httpx.Client, headers: dict[str, str]) -> None:
    started = time.perf_counter()
    try:
        r = client.get("/api/tags", headers=headers)
    except httpx.HTTPError as exc:
        print(f"GET /api/tags failed: {type(exc).__name__}: {exc}")
        return
    ms = (time.perf_counter() - started) * 1000
    print(f"GET /api/tags -> {r.status_code} in {ms:.0f} ms")
    if r.status_code != 200:
        print(f"  body: {r.text[:500]}")
        return
    body = r.json()
    print(f"  top-level keys: {sorted(body)}")
    for m in body.get("models", []):
        print(f"  model: {m.get('name')!r} size={m.get('size')} details={m.get('details')}")


def probe_chat(client: httpx.Client, model: str, key: str, num_predict: int | None) -> dict | None:
    """Try Bearer first, fall back to X-API-Key only on 401/403 (PLAN.md OQ2)."""
    options = {"temperature": 0}
    if num_predict is not None:
        options["num_predict"] = num_predict
    prompt = PROMPT if num_predict is not None else LONG_PROMPT
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False, "options": options}
    print(f"\nPOST /api/chat body ({len(json.dumps(body))} bytes): {json.dumps(body)}")

    for style in AUTH_HEADERS:
        headers = _headers(style, key)
        started = time.perf_counter()
        try:
            r = client.post("/api/chat", json=body, headers=headers)
        except httpx.HTTPError as exc:
            print(f"  [{style}] transport error: {type(exc).__name__}: {exc}")
            return None
        ms = (time.perf_counter() - started) * 1000
        print(f"  [{style}] -> {r.status_code} in {ms:.0f} ms (sent headers {_redact(headers)})")
        if r.status_code in (401, 403):
            print(f"    body: {r.text[:300]}")
            continue  # only auth failures justify a second call
        if r.status_code != 200:
            print(f"    body: {r.text[:500]}")
            return None
        payload = r.json()
        print(f"    working auth header: {style}")
        print(f"    response keys: {sorted(payload)}")
        print(f"    message keys: {sorted(payload.get('message', {}))}")
        text = payload.get("message", {}).get("content", "")
        print(f"    assistant text ({len(text)} chars): {text[:400]!r}")
        for field in ("done", "done_reason", "prompt_eval_count", "eval_count", "total_duration", "model"):
            if field in payload:
                print(f"    {field}: {payload[field]}")
        return payload
    print("  both auth headers rejected")
    return None


def main(argv: list[str]) -> int:
    settings = get_settings()
    if not settings.LLM_GATEWAY_URL or not settings.LLM_GATEWAY_API_KEY:
        print("LLM_GATEWAY_URL / LLM_GATEWAY_API_KEY missing from backend/.env", file=sys.stderr)
        return 2
    num_predict = None if "--no-num-predict" in argv else 64
    print(f"gateway: {settings.LLM_GATEWAY_URL}  model: {settings.LLM_MODEL}  num_predict: {num_predict}")

    with httpx.Client(base_url=settings.LLM_GATEWAY_URL.rstrip("/"), timeout=60, follow_redirects=True) as client:
        payload = probe_chat(client, settings.LLM_MODEL, settings.LLM_GATEWAY_API_KEY, num_predict)
        probe_tags(client, _headers(AUTH_HEADERS[0], settings.LLM_GATEWAY_API_KEY))
    return 0 if payload else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
