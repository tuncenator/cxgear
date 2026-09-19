"""ccgear percentage/reset selection, adapted to native Codex quota observations."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Decision:
    account: str | None
    reason: str


def window(usage, minutes):
    for w in usage.get("windows", []):
        if w["minutes"] == minutes:
            return w
    return None


def fresh(usage, now):
    windows = usage.get("windows", [])
    return bool(windows) and not usage.get("error") and now-usage.get("at", 0) <= 180 and all(
        (w["reset"] is None and w["used"] == 0) or
        (w["reset"] is not None and w["reset"] > now) for w in windows)


def short_used(usage):
    return max((w["used"] for w in usage.get("windows", []) if w["minutes"] < 10080), default=0)


def weekly_used(usage):
    w = window(usage, 10080)
    return w["used"] if w else 0


def hours(usage, now):
    w = window(usage, 10080)
    return max((w["reset"]-now)/3600, 1/60) if w and w["reset"] is not None else None


def pressure(_cfg, usage, now):
    """ccgear's percentage per hour; plan capacity never changes routing."""
    w = window(usage, 10080)
    if not w:
        return 0  # No weekly observation: no inferred weekly urgency.
    remaining = hours(usage, now)
    return (100-w["used"])/remaining if remaining is not None else math.inf


def choose(accounts, readings, current, pinned, now, last_switch=0):
    # Keep last_switch for compatibility with stored state; ccgear has no
    # time-based cooldown. Its pressure deadband suppresses small changes.
    if pinned:
        return Decision(pinned, "Manually pinned")
    known = [n for n in accounts if fresh(readings.get(n, {}), now)]
    if not known:
        return Decision(current, "No fresh account observations; holding")
    score = lambda n: pressure(accounts[n], readings[n], now)
    short = lambda n: short_used(readings[n])
    week = lambda n: weekly_used(readings[n])
    if current not in known:
        if current is not None:
            return Decision(max(known, key=score), "Current account unavailable; use a healthy account")
        ready = [n for n in known if short(n) < 80 and week(n) < 96]
        extra = [n for n in known if accounts[n].get("extra_usage")]
        if not ready and not extra:
            return Decision(None, "No account with available allowance")
        return Decision(max(ready or extra, key=score), "Initial account selection")
    if len(known) == 1:
        return Decision(current, "Other accounts unavailable; holding")

    others = [n for n in known if n != current]
    # ccgear's first account is Max, which it drains before Team overage.
    # Registration order supplies that role without using capacity weights.
    primary = known[0]

    def drained(kind, threshold):
        used = short if kind == "short window" else week
        if used(primary) < threshold:
            return Decision(primary, f"All accounts capped; drain {primary} before extra usage")
        extra = [n for n in known if accounts[n].get("extra_usage")]
        if extra:
            return Decision(current if current in extra else extra[0], "Included allowances drained; explicitly enabled extra usage")
        return Decision(current, "Included allowances drained; no extra-usage account enabled")

    # Preserve ccgear's ordering and emergency exceptions to the 80% guard.
    if all(short(n) >= 90 for n in known):
        return drained("short window", 98)
    if short(current) >= 90:
        escape = [n for n in others if short(n) < 90 and week(n) < 96]
        if escape:
            return Decision(max(escape, key=score), "Current short window capped")
    if week(current) >= 96:
        escape = [n for n in others if week(n) < 96]
        if escape:
            return Decision(max(escape, key=score), "Current weekly allowance capped")
    if all(week(n) >= 96 for n in known):
        return drained("weekly allowance", 99)

    current_hours = hours(readings[current], now)
    expiring = [n for n in others if (h := hours(readings[n], now)) is not None
                and current_hours is not None and h <= 24 and current_hours >= 3*h
                and week(n) < 96 and short(n) < 80]
    if expiring:
        return Decision(max(expiring, key=score), "Use allowance before its earlier weekly reset")

    if all(math.isinf(score(n)) for n in known):
        return Decision(current, "All accounts dormant; holding")
    best = max(known, key=score)
    if best == current:
        return Decision(current, "Current account has the highest remaining percentage per hour")
    old, new = score(current), score(best)
    if math.isfinite(old) and math.isfinite(new):
        reset_hours = [hours(readings[n], now) for n in (current, best)]
        shorter = max(min(h for h in reset_hours if h is not None), 1) if any(h is not None for h in reset_hours) else 1
        if abs(new-old) < 2/shorter:
            return Decision(current, "Pressure difference within ccgear's deadband")
    if week(best) >= 96:
        return Decision(current, "Preferred account weekly allowance capped; holding")
    if short(best) >= 80:
        return Decision(current, "Preferred account short window at or above 80%; holding")
    return Decision(best, "Higher remaining percentage per hour before reset")


def parse_limits(result, now):
    limit = (result.get("rateLimitsByLimitId") or {}).get("codex") or result.get("rateLimits") or {}
    if limit.get("limitId") not in (None, "codex"):
        return {"at": now, "windows": []}
    windows = []
    for key in ("primary", "secondary"):
        w = limit.get(key) or {}
        used, minutes, reset = w.get("usedPercent"), w.get("windowDurationMins"), w.get("resetsAt")
        valid_reset = (reset is None and used == 0) or (isinstance(reset, (int, float)) and not isinstance(reset, bool) and math.isfinite(reset) and reset > 0)
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (used, minutes)) and 0 <= used <= 100 and minutes > 0 and valid_reset:
            windows.append({"used": used, "minutes": minutes, "reset": reset})
    return {"at": now, "windows": windows}
