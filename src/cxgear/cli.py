import argparse
import json
import math
import shlex
import os
from pathlib import Path
import subprocess
import sys
import time

from . import __version__, accounts, broker, install, store
from .policy import choose


from .status import describe, enrich


def main():
    if sys.argv[1:2] == ["codex"]:
        from .launcher import main as launch_main
        sys.argv.pop(1)
        return launch_main()
    p = argparse.ArgumentParser(description="Rotate ChatGPT accounts underneath Codex, independently of your projects.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login", help="Sign in with ChatGPT using native Codex")
    login.add_argument("account")
    login.add_argument("--device-auth", action="store_true")
    imp = sub.add_parser("import", help="Link an existing Codex file login without copying its tokens")
    imp.add_argument("account")
    imp.add_argument("--from", dest="source", default=str(Path.home() / ".codex/auth.json"))
    for cmd in ("status", "refresh", "dryrun", "indicator"):
        x = sub.add_parser(cmd)
        x.add_argument("--json", action="store_true")
    watch = sub.add_parser("watch", help="Live account dashboard")
    watch.add_argument("--interval", type=float, default=5)
    pin = sub.add_parser("pin", help="Stay on one account until auto is restored")
    pin.add_argument("account")
    sub.add_parser("auto", help="Restore automatic selection")
    cap = sub.add_parser("configure", help="Set reported plan capacity and explicit extra-usage preference")
    cap.add_argument("account")
    cap.add_argument("--capacity", type=float, help="Plan multiplier for reporting only; never weights account selection")
    cap.add_argument("--label", help="Display name; the account alias stays valid")
    cap.add_argument("--extra-usage", choices=["on", "off"])
    sub.add_parser("stop", help="Stop quota polling; already-running sessions keep their current login")
    sub.add_parser("doctor", help="Check the installed Codex and launcher paths")
    sub.add_parser("uninstall", help="Restore the original codex command")
    ins = sub.add_parser("install", help="Make ordinary codex invocations use cxgear; reversible")
    ins.add_argument("--bin-dir")
    log = sub.add_parser("log", help="Recent credential-free decisions and usage")
    log.add_argument("--lines", type=int, default=20)
    sub.add_parser("_daemon", help=argparse.SUPPRESS)
    codex = sub.add_parser("codex", help="Run Codex through cxgear without installing the shim", add_help=False)
    codex.add_argument("args", nargs=argparse.REMAINDER)
    args = p.parse_args()
    try:
        if args.command == "_daemon":
            broker.serve()
        elif args.command == "codex":
            from .launcher import launch
            raise SystemExit(launch(args.args))
        elif args.command == "login":
            accounts.login(args.account, args.device_auth)
            print("Signed in as " + args.account)
        elif args.command == "import":
            accounts.import_account(args.account, args.source)
            print("Linked " + args.account)
        elif args.command == "configure":
            with store.locked():
                cfg = store.config()
                account = cfg.get("accounts", {}).get(args.account)
                if account is None:
                    raise ValueError("Unknown account")
                if args.label is not None:
                    label = args.label.strip()
                    if not label or len(label) > 60 or not label.isprintable():
                        raise ValueError("Display name must be 1–60 printable characters")
                    account["label"] = label
                if args.capacity is not None:
                    if not math.isfinite(args.capacity) or args.capacity <= 0:
                        raise ValueError("Capacity must be a positive number")
                    account["capacity"] = args.capacity
                if args.extra_usage:
                    account["extra_usage"] = args.extra_usage == "on"
                store.write(store.root() / "config.json", cfg)
        elif args.command in ("pin", "auto"):
            broker.ensure()
            result = broker.request(args.command, account=getattr(args, "account", None))
            print(result.get("reason"))
        elif args.command in ("status", "refresh", "dryrun", "indicator", "watch"):
            broker.ensure()
            while True:
                result = broker.request("refresh" if args.command == "refresh" else "status")
                result = enrich(result)
                if args.command == "dryrun":
                    d = choose(store.config().get("accounts", {}), result["accounts"], result.get("active"), result.get("pinned"), time.time(), result.get("last_switch", 0))
                    result = {"account": d.account, "reason": d.reason}
                if args.command == "indicator":
                    result = {"text": "CX " + (result.get("active") or "—"), "tooltip": describe(result), "class": "pinned" if result.get("pinned") else "auto"}
                if getattr(args, "json", False) or args.command == "indicator":
                    print(json.dumps(result))
                elif args.command == "dryrun":
                    print(f"{result['account']}: {result['reason']}")
                else:
                    if args.command == "watch" and sys.stdout.isatty():
                        print("\033[2J\033[H", end="")
                    print(describe(result, color=sys.stdout.isatty() and "NO_COLOR" not in os.environ), flush=True)
                if args.command != "watch":
                    break
                time.sleep(max(1, args.interval))
        elif args.command == "stop":
            try:
                broker.request("stop")
            except OSError:
                pass
        elif args.command == "install":
            print("Installed " + str(install.install(args.bin_dir)))
            meta = store.read(store.root()/"install.json")
            if meta.get("mode") == "path":
                print("New shells use cxgear automatically. Activate this shell with:")
                print("  source " + shlex.quote(meta["env"]))
        elif args.command == "uninstall":
            install.uninstall()
            print("cxgear launcher removed; native Codex remains available")
        elif args.command == "doctor":
            path = accounts.binary()
            print("Native executable: " + path)
            subprocess.run([path, "--version"], check=True)
            print("Accounts: " + ", ".join(store.config().get("accounts", {})))
            print("State: " + str(store.root()))
            print("Launcher: " + str(store.read(store.root() / "install.json").get("target", "not installed; use cxgear codex")))
        elif args.command == "log":
            from collections import deque
            path = store.root() / "events.jsonl"
            if path.exists():
                with path.open() as f:
                    for line in deque(f, maxlen=max(1, args.lines)):
                        print(line, end="")
    except KeyboardInterrupt:
        raise SystemExit(130)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print("cxgear: " + str(error), file=sys.stderr)
        raise SystemExit(1)
