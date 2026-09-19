"""Concurrent JSON-RPC client. Responses and server requests never block one another."""
import json
import os
import queue
import subprocess
import threading
from pathlib import Path


class RPCError(RuntimeError):
    pass


class RPC:
    def __init__(self, reader, sender, closer=lambda: None):
        self.sender, self.closer = sender, closer
        self.events = queue.Queue()
        self.pending = {}
        self.lock = threading.Lock()
        self.serial = 0
        self.closed = False
        self.request_handler = None
        self.thread = threading.Thread(target=self._read, args=(reader,), daemon=True)
        self.thread.start()

    def _read(self, reader):
        try:
            for raw in reader:
                message = json.loads(raw)
                if "id" in message and "method" not in message:
                    with self.lock:
                        target = self.pending.get(message["id"])
                    if target:
                        target.put(message)
                elif "id" in message and self.request_handler:
                    threading.Thread(target=self.request_handler, args=(message,), daemon=True).start()
                else:
                    self.events.put(message)
        except Exception:
            pass
        finally:
            self.closed = True
            with self.lock:
                for target in self.pending.values():
                    target.put({"error": {"message": "Codex connection closed"}})
            self.events.put({"method": "cxgear/closed"})

    def send(self, value):
        with self.lock:
            self.sender(json.dumps(value))

    def call(self, method, params=None, timeout=30):
        with self.lock:
            if self.closed:
                raise RPCError("Codex connection closed")
            self.serial += 1
            ident = self.serial
            result = self.pending[ident] = queue.Queue()
            self.sender(json.dumps({"id": ident, "method": method, **({"params": params} if params is not None else {})}))
        try:
            response = result.get(timeout=timeout)
            if "error" in response:
                # Callers may show method/code, never an unsanitized credential-bearing response.
                raise RPCError(f"{method} failed (code {response['error'].get('code', 'connection')})")
            return response["result"]
        except queue.Empty:
            raise RPCError(f"{method} timed out") from None
        finally:
            with self.lock:
                self.pending.pop(ident, None)

    def initialize(self, name="cxgear"):
        result = self.call("initialize", {"clientInfo": {"name": name, "version": "0.1.0"}, "capabilities": {"experimentalApi": True}})
        self.send({"method": "initialized"})
        return result

    def close(self):
        self.closed = True
        self.closer()


def stdio(binary, home, overrides=(), cwd=None):
    env = dict(os.environ, CODEX_HOME=str(home), CXGEAR_BYPASS="1")
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_IDENTITY_TOKEN_FILE"):
        env.pop(key, None)
    args = [str(binary), "app-server", "--stdio"]
    for value in overrides:
        args += ["-c", value]
    process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env, cwd=cwd)

    def send(raw):
        process.stdin.write(raw+"\n")
        process.stdin.flush()

    def close():
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdin.close()
        process.stdout.close()

    rpc = RPC(process.stdout, send, close)
    rpc.process = process
    return rpc


def websocket(url):
    from websockets.sync.client import connect, unix_connect
    ws = unix_connect(path=url[7:], proxy=None, compression=None) if url.startswith("unix://") else connect(url, proxy=None, compression=None)
    return RPC(iter(ws), ws.send, ws.close)
