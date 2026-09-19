"""One per-user account broker; native sessions and projects have no dependency on it."""
import json
import os
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time

from . import store
from .accounts import Account
from .policy import choose, parse_limits


def socket_path():
    return store.root() / "control.sock"


def request(action, **params):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(45)
        sock.connect(str(socket_path()))
        sock.sendall((json.dumps({"action": action, **params})+"\n").encode())
        response = json.loads(sock.makefile("rb").readline(1 << 20))
    if "error" in response:
        raise ValueError(response["error"])
    return response["result"]


def ensure():
    try:
        request("ping")
        return
    except (OSError, ValueError):
        pass
    with store.locked("start"):
        try:
            request("ping")
            return
        except (OSError, ValueError):
            pass
        process = subprocess.Popen([sys.executable, "-m", "cxgear", "_daemon"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        threading.Thread(target=process.wait, daemon=True).start()
        for _ in range(150):
            try:
                request("ping")
                return
            except (OSError, ValueError):
                time.sleep(.1)
        raise ValueError("cxgear account service did not start")


class Broker:
    def __init__(self):
        self.lock = threading.RLock()
        self.poll_lock = threading.Lock()
        self.accounts = {}
        self.state = store.read(store.root() / "state.json")
        self.last_poll = 0
        self.stop = threading.Event()

    def refresh(self, force=False):
        # Network polling must not delay credential delivery to live turns.
        if not self.poll_lock.acquire(blocking=force or not self.accounts):
            return
        try:
            with self.lock:
                cfg = store.config().get("accounts", {})
                for name in list(self.accounts):
                    if name not in cfg:
                        self.accounts.pop(name).close()
                for name in cfg:
                    self.accounts.setdefault(name, Account(name))
                accounts = list(self.accounts.items())
            if not force and time.time()-self.last_poll < 60:
                return
            for name, account in accounts:
                usage = account.poll()
                store.event("usage", account=name, **usage)
            with self.lock:
                self.last_poll = time.time()
                self.select()
        finally:
            self.poll_lock.release()

    def select(self):
        readings = {n: a.usage for n, a in self.accounts.items()}
        decision = choose(store.config().get("accounts", {}), readings, self.state.get("active"), self.state.get("pinned"), time.time(), self.state.get("last_switch", 0))
        previous = self.state.get("active")
        if decision.account != previous:
            self.state["last_switch"] = time.time()
            store.event("switch", previous=previous, account=decision.account, reason=decision.reason)
        self.state.update(active=decision.account, reason=decision.reason)
        store.write(store.root() / "state.json", self.state)

    def handle(self, data):
        action = data.get("action")
        if action == "ping":
            return {"pid": os.getpid()}
        if action == "stop":
            self.stop.set()
            return True
        self.refresh(force=action == "refresh")
        with self.lock:
            if action in ("pin", "auto"):
                name = data.get("account") if action == "pin" else None
                if name and name not in store.config().get("accounts", {}):
                    raise ValueError("Unknown account")
                self.state["pinned"] = name
                self.select()
                return dict(self.state)
            if action == "report":
                name = data.get("account")
                if name in self.accounts:
                    reading = parse_limits(data.get("limits", {}), time.time())
                    if reading["windows"]:
                        self.accounts[name].usage = reading
                        store.event("usage", account=name, **reading)
                        self.select()
                return True
            if action in ("status", "refresh"):
                return {**self.state, "accounts": {name: account.usage for name, account in self.accounts.items()}}
            if action in ("resolve", "refresh_auth"):
                self.select()
                name = data.get("account") or self.state.get("active")
                if name not in self.accounts:
                    raise ValueError("No usable ChatGPT account; run cxgear login or cxgear import")
                if action == "refresh_auth":
                    self.accounts[name].connect().call("account/read", {"refreshToken": True}, timeout=15)
                return {"account": name, "reason": self.state.get("reason"), "auth": self.accounts[name].auth()}
            raise ValueError("Unknown cxgear request")

    def run(self):
        while not self.stop.wait(5):
            try:
                self.refresh()
            except (OSError, ValueError):
                pass


def serve():
    store.private_dir(store.root())
    with store.locked("daemon"):
        path = socket_path()
        path.unlink(missing_ok=True)
        manager = Broker()

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.request.settimeout(50)
                try:
                    raw = self.rfile.readline(1 << 20)
                    result = manager.handle(json.loads(raw))
                    reply = {"result": result}
                except (ValueError, OSError, RuntimeError):
                    reply = {"error": "Account operation unavailable; check cxgear status or sign in again"}
                try:
                    self.wfile.write((json.dumps(reply)+"\n").encode())
                except OSError:
                    pass

        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads = True

        with Server(str(path), Handler) as server:
            path.chmod(0o600)
            server.timeout = .5
            threading.Thread(target=manager.run, daemon=True).start()
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, lambda *_: manager.stop.set())
            while not manager.stop.is_set():
                server.handle_request()
        for account in manager.accounts.values():
            account.close()
        path.unlink(missing_ok=True)
