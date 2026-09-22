"""Is the LLM route usable right now? (T17, G6: LLM/OpenClaw down → manual mode, never a broken screen.)

Two cheap signals, no extra network calls on the hot path:
- every live client records the outcome of its last request per route ("openclaw" / "gateway") in a
  RouteTracker — a timestamp, ok/failed and the error text — as a side effect of the calls it makes anyway;
- /health adds the ≤ 2 s probes (OpenClaw's GET /health, the gateway's GET /api/tags) and combines both.

A route is "bad" when its probe says unreachable or its last real call failed; the status clears on the next
successful call, or on the next fresh probe that finds the route reachable again (a failure older than that probe
is forgotten). Mock mode has no LLM and is always "ok".
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from procureai.config.settings import Settings

Route = Literal["openclaw", "gateway"]
LlmStatus = Literal["ok", "degraded", "down"]
ProbeResult = Literal["reachable", "unreachable"] | None  # None = not probed (route unconfigured / mock)


@dataclass(frozen=True)
class Attempt:
    ok: bool
    at: datetime
    error: str | None = None

    def as_json(self) -> dict[str, object]:
        return {"ok": self.ok, "at": self.at.isoformat(), "error": self.error}


class RouteTracker:
    """Last outcome per route. One per process (TRACKER); tests build their own or reset it."""

    def __init__(self) -> None:
        self.last: dict[str, Attempt] = {}

    def record(self, route: str, ok: bool, error: str | None = None) -> None:
        self.last[route] = Attempt(ok=ok, at=datetime.now(timezone.utc), error=None if ok else error)

    def failed(self, route: str) -> bool:
        attempt = self.last.get(route)
        return attempt is not None and not attempt.ok

    def reset(self) -> None:
        self.last.clear()

    def forget_failure_before(self, route: str, at: datetime) -> bool:
        """Drop a failed attempt older than `at` — a fresh probe just saw the route up again (T18 drill a: after
        OpenClaw is restarted, a replay demo may never make another real call, so the failure would stick)."""
        attempt = self.last.get(route)
        if attempt is None or attempt.ok or attempt.at >= at:
            return False
        del self.last[route]
        return True

    def as_json(self) -> dict[str, dict[str, object]]:
        return {route: attempt.as_json() for route, attempt in self.last.items()}


TRACKER = RouteTracker()


def llm_status(settings: Settings, tracker: RouteTracker, *, openclaw: ProbeResult, gateway: ProbeResult) -> LlmStatus:
    """"ok" | "degraded" (OpenClaw route failed, direct gateway works) | "down" (nothing answers).

    `openclaw` / `gateway` are the probe results for this /health call (None when the route was not probed).
    """
    if settings.MODE != "live":
        return "ok"
    gateway_bad = tracker.failed("gateway") or gateway == "unreachable"
    if settings.LLM_BACKEND != "openclaw":
        return "down" if gateway_bad else "ok"
    openclaw_bad = tracker.failed("openclaw") or openclaw == "unreachable"
    if openclaw_bad and gateway_bad:
        return "down"
    return "degraded" if openclaw_bad else "ok"
