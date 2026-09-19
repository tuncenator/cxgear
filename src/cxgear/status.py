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


# ccgear's GPT palette and textures, kept local so cxgear stays independent.
PALETTE = (("ad7fa8", "█"), ("75507b", "▒"), ("ff4fa3", "▓"), ("555555", "░"))


def span(style, count, color):
    if count <= 0:
        return ""
    rgb, glyph = style
    text = glyph*count
    if color:
        r, g, b = (int(rgb[i:i+2], 16) for i in (0, 2, 4))
        return f"\033[38;2;{r};{g};{b}m{text}\033[0m"
    return text


def bar(used, color=False, width=24, pace=None):
    used = max(0, min(100, used))
    pace = used if pace is None else max(0, min(100, pace))
    u, p = round(used*width/100), round(pace*width/100)
    solid, under, over = min(u, p), max(0, p-u), max(0, u-p)
    # Keep a real gap visible even when both values round to the same cell.
    if not under and not over and abs(used-pace) >= 1 and solid < width:
        under, over = (1, 0) if pace > used else (0, 1)
    counts = (solid, under, over, width-solid-under-over)
    return "".join(span(style, count, color) for style, count in zip(PALETTE, counts))


def describe(snapshot, color=False, now=None):
    now = time.time() if now is None else now
    pinned = snapshot.get("pinned")
    mode = "pinned to " + pinned if pinned else "automatic"
    lines = [f"cxgear · {mode}", "Codex launcher: " + ("enabled" if snapshot.get("launcher_installed") else "not enabled — run cxgear install")]
    has_pace_bar = False
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
            elapsed = max(0, min(100, 100*(1-(w["reset"]-now)/(w["minutes"]*60)))) if w["minutes"] == 10080 else None
            if expired or stale:
                why = "reset passed" if expired else "reading stale"
                lines.append(f"  {title}: last recorded {w['used']:g}% used ({why}; current allowance unknown)")
            else:
                lines.append(f"  {title:<9} {bar(w['used'], color, pace=elapsed)}  {w['used']:g}% used · {100-w['used']:g}% left")
                has_pace_bar = has_pace_bar or elapsed is not None
            reset = datetime.fromtimestamp(w["reset"]).astimezone().strftime("%a %d %b %H:%M %Z")
            lines.append(f"  Reset: {reset}" + (f" · in {duration(w['reset']-now)}" if not expired else " · awaiting fresh quota"))
            if w["minutes"] == 10080 and not expired and not stale:
                needed = (100-w['used'])/max((w['reset']-now)/3600, 1/60)
                delta = w['used']-elapsed
                comparison = "on pace" if abs(delta) < .5 else f"{abs(delta):.0f} points {'ahead' if delta > 0 else 'behind'}"
                lines.append(f"  Pace guide: {elapsed:.0f}% of week elapsed · {comparison}")
                lines.append(f"  To use the remainder before reset: {needed:.2f}%/h")
        if not windows:
            lines.append("  Usage unavailable — no allowance inferred")
        if not any(w["minutes"] < 10080 for w in windows):
            lines.append("  Short window: not reported by Codex")
        if usage.get("at"):
            lines.append("  Quota checked " + duration(now-usage['at']) + " ago")
        if usage.get("error"):
            lines.append("  " + usage["error"])
    if has_pace_bar:
        solid, under, over, empty = (span(style, 1, color) for style in PALETTE)
        lines += ["", f"Weekly bars: {solid} within pace  {under} below-pace gap", f"             {over} above-pace usage  {empty} remaining"]
    lines += ["", "Selection: " + snapshot.get("reason", "Add an account to get started")]
    return "\n".join(lines)
