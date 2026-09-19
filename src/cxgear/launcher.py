"""The codex-compatible entry point. Non-model commands use the native executable."""
import os
import signal
import subprocess
import sys

from . import accounts, store
from .exec import Unsupported, parse, run
from .session import Session


COMMANDS = {"agents", "exec", "e", "review", "login", "logout", "mcp", "plugin", "app-server", "remote-control", "completion", "update", "doctor", "sandbox", "debug", "apply", "resume", "queue", "archive", "delete", "migrate-rollouts", "unarchive", "fork", "cloud", "exec-server", "features", "help"}
VALUE_OPTIONS = {"-c", "--config", "-m", "--model", "-s", "--sandbox", "-C", "--cd", "-p", "--profile", "-i", "--image", "--add-dir", "--enable", "--disable", "--remote", "--remote-auth-token-env", "--local-provider", "--code-mode-host"}


def command(args):
    skip = False
    for i, arg in enumerate(args):
        if skip:
            skip = False
            continue
        if arg in VALUE_OPTIONS:
            skip = True
            continue
        if arg == "--":
            return None, None
        if arg.startswith("-"):
            continue
        return (arg, i) if arg in COMMANDS else (None, None)
    return None, None


def native(args):
    env = dict(os.environ, CXGEAR_BYPASS="1")
    os.execve(accounts.binary(), [accounts.binary(), *args], env)


def launch(args):
    cmd, index = command(args)
    cfg = store.config()
    if os.environ.get("CXGEAR_BYPASS") == "1" or not cfg.get("accounts") or any(x.split("=", 1)[0] in ("--help", "-h", "--version", "-V", "--oss", "--remote") for x in args) or any(os.environ.get(k) for k in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_IDENTITY_TOKEN_FILE")):
        native(args)
    if cmd in ("exec", "e"):
        try:
            options = parse(args[:index]+args[index+1:])
        except Unsupported:
            print("cxgear: this invocation uses native Codex without rotation (unsupported exec options).", file=sys.stderr)
            native(args)
        return run(options)
    if cmd and cmd not in ("resume", "fork", "agents"):
        native(args)
    # Keep native handling for settings that cannot be faithfully forwarded.
    if any(a.split("=", 1)[0] in ("--profile", "-p", "--worktree", "--dangerously-bypass-hook-trust", "--code-mode-host", "--ignore-user-config", "--ignore-rules", "--strict-config") or (a.startswith("-p") and not a.startswith("--")) for a in args):
        print("cxgear: this invocation uses native Codex without rotation (special launch settings).", file=sys.stderr)
        native(args)
    configs = []
    cwd = None
    for arg in args:
        if arg.startswith("--config="):
            configs.append(arg.split("=", 1)[1])
        elif arg.startswith("-c") and not arg.startswith("--") and arg != "-c":
            configs.append(arg[2:])
        elif arg.startswith("--cd="):
            cwd = arg.split("=", 1)[1]
        elif arg.startswith(("--enable=", "--disable=")):
            flag, value = arg.split("=", 1)
            configs.append("features."+value+"="+("true" if flag == "--enable" else "false"))
    for i, arg in enumerate(args[:-1]):
        if arg in ("-c", "--config"):
            configs.append(args[i+1])
        elif arg in ("-C", "--cd"):
            cwd = args[i+1]
        elif arg in ("--enable", "--disable"):
            configs.append("features." + args[i+1] + "=" + ("true" if arg == "--enable" else "false"))
    with Session(configs, cwd=cwd) as session:
        env = dict(os.environ, CXGEAR_BYPASS="1")
        # Child model-tool commands must also resolve the cxgear codex launcher.
        env.pop("CXGEAR_BYPASS", None)
        process = subprocess.Popen([accounts.binary(), "--remote", session.endpoint, *args], env=env)
        def terminate(sig, _):
            process.send_signal(sig)
            raise SystemExit(128+sig)
        previous = signal.signal(signal.SIGTERM, terminate)
        try:
            code = process.wait()
            return code if code >= 0 else 128-code
        except KeyboardInterrupt:
            try:
                return process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                return 130
        finally:
            signal.signal(signal.SIGTERM, previous)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def main():
    try:
        raise SystemExit(launch(sys.argv[1:]))
    except (ValueError, OSError, RuntimeError) as error:
        print("cxgear: " + str(error), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
