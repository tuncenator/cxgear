"""Readable account status; only explicit, non-secret metadata reaches output."""
from datetime import datetime
from pathlib import Path
import time

from . import accounts, store


def enrich(snapshot):
    snapshot = {**snapshot, "accounts": {n: dict(u) for n, u in snapshot.get("accounts", {}).items()}}
    configured = store.config().get("accounts", {})
    for name, usage in snapshot["accounts"].items():
        cfg = configured.get(name, {})
        usage["label"] = cfg.get("label") or name
        usage["capacity"] = cfg.get("capacity", 1)
        usage["login_source"] = "linked login" if (store.account_home(name)/"auth.json").is_symlink() else "separate login"
        try:
            # Do not serialize the credential object: it contains bearer tokens.
            usage["plan"] = accounts.credentials(name).get("chatgptPlanType")
        except (ValueError, OSError):
            usage["plan"] = None
    meta = store.read(store.root()/"install.json")
    target, wrapper = meta.get("target"), meta.get("wrapper")
    snapshot["launcher_installed"] = bool(target and wrapper and Path(target).is_symlink() and Path(target).resolve() == Path(wrapper).resolve())
    return snapshot


def duration(seconds):
    minutes = max(0, int(seconds)//60)
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m" if minutes else "less than 1m"


def bar(used, color=False, width=24):
    filled = max(0, min(width, round(used*width/100)))
    text = "█"*filled+"░"*(width-filled)
    if color:
        code = 31 if used >= 90 else 33 if used >= 70 else 36
        text = f"\033[{code}m{text}\033[0m"
    return text


def describe(snapshot, color=False, now=None):
    now = time.time() if now is None else now
    pinned = snapshot.get("pinned")
    mode = "pinned to " + pinned if pinned else "automatic"
    lines = [f"cxgear · {mode}", "Codex launcher: " + ("enabled" if snapshot.get("launcher_installed") else "not enabled — run cxgear install")]
    for name, usage in snapshot.get("accounts", {}).items():
        active = snapshot.get("active") == name
        label = usage.get("label") or name
        if label != name:
            label += f" ({name})"
        plan = {"pro": "Pro", "prolite": "Pro (prolite)", "business": "Business", "team": "Team"}.get(usage.get("plan"), usage.get("plan") or "plan unavailable")
        title = f"{'●' if active else '○'} {label} · {plan}" + (" · ACTIVE" if active else "")
        if color:
            title = f"\033[1m{title}\033[0m"
        lines += ["", title]
        if usage.get("login_source"):
            lines.append(f"  {usage['login_source']} · capacity weight {usage.get('capacity', 1):g}")
        windows = sorted(usage.get("windows", []), key=lambda w: w["minutes"], reverse=True)
        stale = now-usage.get("at", 0) > 180 or bool(usage.get("error"))
        for w in windows:
            title = "Weekly" if w["minutes"] == 10080 else f"{w['minutes']/60:g}h window"
            expired = w["reset"] <= now
            if expired or stale:
                why = "reset passed" if expired else "reading stale"
                lines.append(f"  {title}: last recorded {w['used']:g}% used ({why}; current allowance unknown)")
            else:
                lines.append(f"  {title:<9} {bar(w['used'], color)}  {w['used']:g}% used · {100-w['used']:g}% left")
            reset = datetime.fromtimestamp(w["reset"]).astimezone().strftime("%a %d %b %H:%M %Z")
            lines.append(f"  Reset: {reset}" + (f" · in {duration(w['reset']-now)}" if not expired else " · awaiting fresh quota"))
            if w["minutes"] == 10080 and not expired and not stale:
                elapsed = max(0, min(100, 100*(1-(w["reset"]-now)/(w["minutes"]*60))))
                needed = (100-w['used'])/max((w['reset']-now)/3600, 1/60)
                lines.append(f"  Pace guide: {elapsed:.0f}% of weekly window elapsed · {needed:.2f}%/h to use the remainder")
        if not windows:
            lines.append("  Usage unavailable — no allowance inferred")
        if not any(w["minutes"] < 10080 for w in windows):
            lines.append("  Short window: not reported by Codex")
        if usage.get("at"):
            lines.append("  Quota checked " + duration(now-usage['at']) + " ago")
        if usage.get("error"):
            lines.append("  " + usage["error"])
    lines += ["", "Selection: " + snapshot.get("reason", "Add an account to get started")]
    return "\n".join(lines)
