"""Unattended Codex transport through the same native app-server as the TUI."""
import argparse
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import uuid

from . import rpc
from .output import Output
from .session import Session


class Unsupported(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Unsupported(message)


def parse(args):
    p = Parser(add_help=False, allow_abbrev=False)
    for names, kw in [(("-c", "--config"), {"action": "append", "default": []}), (("--enable",), {"action": "append", "default": []}), (("--disable",), {"action": "append", "default": []}), (("-m", "--model"), {}), (("-s", "--sandbox"), {"choices": ["read-only", "workspace-write", "danger-full-access"]}), (("-C", "--cd"), {}), (("-i", "--image"), {"action": "append", "default": []}), (("--add-dir",), {"action": "append", "default": []}), (("--output-schema",), {}), (("-o", "--output-last-message"), {}), (("--color",), {"choices": ["auto", "always", "never"], "default": "auto"})]:
        p.add_argument(*names, **kw)
    for name in ("json", "experimental-json", "skip-git-repo-check", "ephemeral", "last", "all", "approve-for-me", "dangerously-bypass-approvals-and-sandbox"):
        p.add_argument("--" + name, action="store_true")
    p.add_argument("words", nargs="*")
    values = p.parse_intermixed_args(args)
    words = values.words
    values.mode = words.pop(0) if words and words[0] in ("resume", "fork") else "start"
    values.thread = None
    if values.mode != "start" and not values.last:
        if not words:
            raise Unsupported("A session ID is required")
        values.thread = words.pop(0)
    if len(words) > 1:
        raise Unsupported("Unexpected positional arguments")
    values.prompt = words[0] if words else None
    values.json = values.json or values.experimental_json
    return values


def overrides(options):
    out = list(options.config)
    out += [f"features.{name}=true" for name in options.enable]
    out += [f"features.{name}=false" for name in options.disable]
    if options.add_dir:
        out += ["sandbox_workspace_write.writable_roots=" + json.dumps([str(Path(p).resolve()) for p in options.add_dir])]
    return out


def prompt_text(prompt):
    if prompt in (None, "-"):
        if sys.stdin.isatty():
            raise ValueError("Provide a prompt or pipe it on stdin")
        return sys.stdin.read()
    if not sys.stdin.isatty():
        extra = sys.stdin.read()
        if extra:
            return prompt + "\n\n<stdin>\n" + extra + "\n</stdin>"
    return prompt


def resolve_thread(client, options, cwd):
    if options.thread:
        try:
            uuid.UUID(options.thread)
            return options.thread
        except ValueError:
            pass
    params = {"limit": 100, "sortKey": "updated_at", "sortDirection": "desc"}
    if not options.all:
        params["cwd"] = cwd
    if options.thread:
        params["searchTerm"] = options.thread
    while True:
        result = client.call("thread/list", params)
        found = [t for t in result.get("data", []) if not options.thread or t.get("name") == options.thread]
        if found:
            return found[0]["id"]
        if not result.get("nextCursor"):
            raise ValueError("No matching Codex session")
        params["cursor"] = result["nextCursor"]


def run(options):
    cwd = str(Path(options.cd or os.getcwd()).resolve())
    if not options.skip_git_repo_check and subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        raise ValueError("Not inside a trusted Git directory; pass --skip-git-repo-check")
    text = prompt_text(options.prompt)
    if not text.strip() and not options.image:
        raise ValueError("The prompt is empty")
    config = overrides(options)
    output = Output(options.json)
    with Session(config, cwd=cwd) as session:
        client = session.connection()
        request_failed = []

        def server_request(message):
            method = message.get("method")
            if method == "account/chatgptAuthTokens/refresh":
                # The authentication controller owns these; it is also notified.
                return
            if method == "mcpServer/elicitation/request":
                client.send({"id": message["id"], "result": {"action": "cancel", "content": None}})
            else:
                request_failed.append(method)
                client.send({"id": message["id"], "error": {"code": -32000, "message": "Interactive approval/input is unavailable in exec mode"}})

        client.request_handler = server_request
        start = {"cwd": cwd, "approvalPolicy": "never"}
        if options.model:
            start["model"] = options.model
        if options.sandbox:
            start["sandbox"] = options.sandbox
        if options.approve_for_me:
            start.update(approvalPolicy="on-request", approvalsReviewer="auto_review", sandbox="workspace-write")
        if options.dangerously_bypass_approvals_and_sandbox:
            start.update(approvalPolicy="never", sandbox="danger-full-access")
        if options.mode == "start":
            start["ephemeral"] = options.ephemeral
        else:
            start["threadId"] = resolve_thread(client, options, cwd)
            # Exec consumes only the new turn. Keep history in the server,
            # matching native exec, instead of downloading old tool output.
            start["excludeTurns"] = True
        response = client.call("thread/" + options.mode, start)
        thread = response["thread"]["id"]
        output.emit("thread.started", thread_id=thread)
        inputs = [{"type": "text", "text": text, "text_elements": []}] if text else []
        for paths in options.image:
            inputs += [{"type": "localImage", "path": str(Path(path).resolve())} for path in paths.split(",")]
        turn = {"threadId": thread, "input": inputs}
        if options.output_schema:
            turn["outputSchema"] = json.loads(Path(options.output_schema).read_text())
        begun = client.call("turn/start", turn)
        turn_id = begun["turn"]["id"]
        output.emit("turn.started")
        old_handlers = {}
        interrupted = []

        def interrupt(sig, _):
            interrupted.append(sig)
            def send_interrupt():
                try:
                    client.call("turn/interrupt", {"threadId": thread, "turnId": turn_id}, timeout=5)
                except rpc.RPCError:
                    pass
            threading.Thread(target=send_interrupt, daemon=True).start()

        for sig in (signal.SIGINT, signal.SIGTERM):
            old_handlers[sig] = signal.signal(sig, interrupt)
        try:
            while True:
                try:
                    message = client.events.get(timeout=1)
                except queue.Empty:
                    if interrupted:
                        return 128+interrupted[0]
                    continue
                if message.get("method") == "cxgear/closed":
                    raise ValueError("Codex connection ended before turn completion")
                params = message.get("params", {})
                if params.get("threadId") not in (None, thread):
                    continue
                if params.get("turnId") not in (None, turn_id):
                    continue
                if message.get("method") == "turn/completed" and params["turn"]["id"] == turn_id:
                    status = params["turn"]["status"]
                    if status == "completed" and not request_failed:
                        output.completed()
                        code = 0
                    else:
                        output.emit("turn.failed", error={"message": (params["turn"].get("error") or {}).get("message", "Turn interrupted" if interrupted else "Codex turn failed")})
                        code = 128+interrupted[0] if interrupted else 1
                    if options.output_last_message:
                        Path(options.output_last_message).write_text(output.last_message)
                    return code
                output.observe(message)
        finally:
            for sig, old in old_handlers.items():
                signal.signal(sig, old)
            client.close()
