"""Pure selection policy inspired by ccgear's pressure-balanced weekly drain."""
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


def percent(usage, minutes, now):
    w = window(usage, minutes)
    # An expired observation is unknown, not an invented reset to zero.
    return w["used"] if w and w["reset"] > now else None


def available(usage, now, target=False):
    if usage.get("error") or now - usage.get("at", 0) > 180:
        return False
    valid = [w for w in usage.get("windows", []) if w["reset"] > now]
    if not valid:
        return False
    return all(w["used"] < (96 if w["minutes"] >= 10080 else (80 if target else 90)) for w in valid)


def choose(accounts, readings, current, pinned, now, last_switch=0):
    if pinned:
        return Decision(pinned, "Manually pinned")
    candidates = [n for n in accounts if available(readings.get(n, {}), now, target=True)]
    if current in accounts and available(readings.get(current, {}), now) and current not in candidates:
        candidates.append(current)
    if not candidates:
        # Paid extra usage is strictly opt-in, never inferred from account type.
        extra = [n for n, cfg in accounts.items() if cfg.get("extra_usage") and not readings.get(n, {}).get("error") and now-readings.get(n, {}).get("at", 0) <= 180]
        if extra:
            return Decision(current if current in extra else extra[0], "Included allowances exhausted; explicitly enabled extra usage")
        return Decision(current, "No fresh account with available allowance; holding")
    if current not in candidates:
        return Decision(max(candidates, key=lambda n: pressure(accounts[n], readings[n], now)), "Current account unavailable or near its limit")
    if now-last_switch < 60:
        return Decision(current, "Switch cooldown")
    current_week = window(readings[current], 10080)
    if current_week:
        expiring = [n for n in candidates if n != current and (w := window(readings[n], 10080)) and 0 < w["reset"]-now <= 86400 and current_week["reset"]-now >= 3*(w["reset"]-now)]
        if expiring:
            name = max(expiring, key=lambda n: pressure(accounts[n], readings[n], now))
            return Decision(name, "Use allowance before its earlier weekly reset")
    best = max(candidates, key=lambda n: pressure(accounts[n], readings[n], now))
    if best == current:
        return Decision(current, "Current account has the most allowance to use before reset")
    old, new = pressure(accounts[current], readings[current], now), pressure(accounts[best], readings[best], now)
    hours = [max((w["reset"]-now)/3600, 1) for n in (current, best) if (w := window(readings[n], 10080))]
    deadband = 2/min(hours) if hours else 2
    if new-old <= deadband:
        return Decision(current, "Difference too small to justify switching")
    return Decision(best, "More remaining allowance to use before reset")


def pressure(cfg, usage, now):
    w = window(usage, 10080)
    if not w or w["reset"] <= now:
        # Short-window-only accounts can be selected, but lack weekly urgency.
        return 0
    return (100-w["used"])*cfg.get("capacity", 1)/max((w["reset"]-now)/3600, 1/60)


def parse_limits(result, now):
    limit = (result.get("rateLimitsByLimitId") or {}).get("codex") or result.get("rateLimits") or {}
    if limit.get("limitId") not in (None, "codex"):
        return {"at": now, "windows": []}
    windows = []
    for key in ("primary", "secondary"):
        w = limit.get(key) or {}
        used, minutes, reset = w.get("usedPercent"), w.get("windowDurationMins"), w.get("resetsAt")
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (used, minutes, reset)) and 0 <= used <= 100 and minutes > 0 and reset > 0:
            windows.append({"used": used, "minutes": minutes, "reset": reset})
    return {"at": now, "windows": windows}
