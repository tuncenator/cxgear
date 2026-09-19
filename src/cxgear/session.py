"""A stock Codex app-server with a private, independent authentication controller."""
import os
from pathlib import Path
import queue
import signal
import subprocess
import tempfile
import threading
import time

from . import accounts, broker, rpc, store


class Session:
    def __init__(self, overrides=(), cwd=None, account=None):
        self.home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        self.overrides = overrides
        self.cwd = cwd or os.getcwd()
        self.pin = account or os.environ.get("CXGEAR_ACCOUNT")
        self.account = None
        self.auth = None
        self.client = None
        self.process = None
        self.stopped = threading.Event()
        self.temp = None
        self.thread = None

    def __enter__(self):
        broker.ensure()
        selected = broker.request("resolve", account=self.pin)
        self.temp = tempfile.TemporaryDirectory(prefix="cxgear-")
        self.endpoint = "unix://" + self.temp.name + "/codex.sock"
        args = [accounts.binary(), "app-server", "--listen", self.endpoint]
        for value in self.overrides:
            args += ["-c", value]
        env = dict(os.environ)
        env.pop("CXGEAR_BYPASS", None)
        try:
            self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, cwd=self.cwd, start_new_session=True)
            for _ in range(150):
                if self.process.poll() is not None:
                    raise ValueError("Codex app-server could not start with these settings")
                try:
                    self.client = rpc.websocket(self.endpoint)
                    break
                except OSError:
                    time.sleep(.1)
            if not self.client:
                raise ValueError("Codex control socket did not become available")
            self.client.request_handler = self._request
            self.client.initialize("cxgear-controller")
            self._apply(selected)
            self.thread = threading.Thread(target=self._watch, daemon=True)
            self.thread.start()
            return self
        except BaseException:
            self.close()
            raise

    def _apply(self, selected):
        auth = selected["auth"]
        if auth == self.auth and selected["account"] == self.account:
            return
        self.client.call("account/login/start", {"type": "chatgptAuthTokens", **auth})
        self.auth, self.account = auth, selected["account"]

    def _request(self, message):
        if message.get("method") != "account/chatgptAuthTokens/refresh":
            # Requests owned by the terminal/exec connection are answered there.
            return
        try:
            selected = broker.request("refresh_auth", account=self.account)
            self.client.send({"id": message["id"], "result": selected["auth"]})
            self.auth = selected["auth"]
        except (OSError, ValueError, rpc.RPCError):
            self.client.send({"id": message["id"], "error": {"code": -32000, "message": "Account login needs attention; run cxgear status"}})

    def _watch(self):
        while not self.stopped.wait(2):
            try:
                # Account-less notifications can arrive after an owner switch.
                # The broker polls each account independently for attribution.
                while True:
                    try:
                        event = self.client.events.get_nowait()
                    except queue.Empty:
                        break
                self._apply(broker.request("resolve", account=self.pin))
            except (OSError, ValueError, rpc.RPCError):
                # Keep an already-working session alive during a broker outage.
                continue

    def connection(self, name="codex_exec"):
        client = rpc.websocket(self.endpoint)
        client.initialize(name)
        return client

    def close(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=3)
        if self.client:
            self.client.close()
        if self.process and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()
        if self.temp:
            self.temp.cleanup()

    def __exit__(self, *_):
        self.close()
