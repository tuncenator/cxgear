"""Official Codex owns browser login and OAuth refresh for each account."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from . import rpc, store
from .policy import parse_limits


def binary():
    configured = os.environ.get("CXGEAR_CODEX_BINARY") or store.config().get("codex_binary")
    if configured:
        path = Path(configured).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise ValueError("Configured Codex binary is unavailable; run cxgear install again.")
    # Follow the stable updater-managed link, not a specific installed release.
    standalone = Path.home() / ".codex/packages/standalone/current/bin/codex"
    if standalone.is_file():
        return str(standalone)
    path = shutil.which("codex")
    if path and "cxgear" not in str(Path(path).resolve()):
        return path
    raise ValueError("Install Codex first, or set CXGEAR_CODEX_BINARY to its executable.")


def claims(token):
    try:
        return json.loads(base64.urlsafe_b64decode(token.split(".")[1]+"==="))
    except (IndexError, ValueError, TypeError):
        return {}


def credentials(name):
    data = store.read(store.account_home(name) / "auth.json")
    tokens = data.get("tokens") or {}
    access = tokens.get("access_token")
    info = claims(tokens.get("id_token", "")).get("https://api.openai.com/auth", {})
    access_info = claims(access or "")
    account_id = tokens.get("account_id") or info.get("chatgpt_account_id")
    if not access or not account_id or data.get("OPENAI_API_KEY"):
        raise ValueError("Account needs a ChatGPT login")
    return {"accessToken": access, "chatgptAccountId": account_id, "chatgptPlanType": info.get("chatgpt_plan_type"), "expires": access_info.get("exp", 0)}


def account_config(home):
    store.private_dir(home)
    (home / "config.toml").write_text('cli_auth_credentials_store = "file"\n[analytics]\nenabled = false\n')
    (home / "config.toml").chmod(0o600)


def login(name, device=False):
    home = store.account_home(name)
    if (home / "auth.json").is_symlink():
        raise ValueError("This alias links an existing login. Sign in at its original Codex home, or choose a new alias.")
    account_config(home)
    env = dict(os.environ, CODEX_HOME=str(home), CXGEAR_BYPASS="1")
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"):
        env.pop(key, None)
    result = subprocess.run([binary(), "login", *(["--device-auth"] if device else [])], env=env, cwd=home)
    if result.returncode:
        raise ValueError("Codex login did not complete")
    credentials(name)
    with store.locked():
        cfg = store.config()
        cfg.setdefault("accounts", {}).setdefault(name, {"capacity": 1})
        store.write(store.root() / "config.json", cfg)


def import_account(name, source):
    home = store.account_home(name)
    path = Path(source).expanduser().resolve(strict=True)
    if path.is_dir():
        path = path / "auth.json"
    if (home / "auth.json").exists():
        raise ValueError("Account already exists; choose another name or use login")
    account_config(home)
    # Keep one token authority: importing links to the original credential file.
    # Codex file storage writes through this link when refreshing tokens.
    (home / "auth.json").symlink_to(path)
    try:
        credentials(name)
    except Exception:
        (home / "auth.json").unlink()
        raise
    with store.locked():
        cfg = store.config()
        cfg.setdefault("accounts", {})[name] = {"capacity": 1}
        store.write(store.root() / "config.json", cfg)


class Account:
    def __init__(self, name):
        self.name = name
        self.client = None
        self.usage = {"windows": []}

    def connect(self):
        if self.client and not self.client.closed:
            return self.client
        self.close()
        self.client = rpc.stdio(binary(), store.account_home(self.name), cwd=store.account_home(self.name))
        self.client.initialize()
        return self.client

    def poll(self):
        try:
            client = self.connect()
            auth = credentials(self.name)
            if auth["expires"] and auth["expires"] < time.time()+300:
                client.call("account/read", {"refreshToken": True}, timeout=15)
            self.usage = parse_limits(client.call("account/rateLimits/read", timeout=15), time.time())
        except (rpc.RPCError, OSError, ValueError):
            # A failed poll is unknown, never fabricated zero allowance.
            self.usage = {**self.usage, "error": "Login or usage refresh unavailable"}
            self.close()
        return self.usage

    def auth(self):
        auth = credentials(self.name)
        if auth["expires"] and auth["expires"] <= time.time()+60:
            self.connect().call("account/read", {"refreshToken": True}, timeout=15)
            auth = credentials(self.name)
        if auth["expires"] and auth["expires"] <= time.time():
            raise ValueError("Account login has expired")
        return {k: v for k, v in auth.items() if k != "expires"}

    def close(self):
        if self.client:
            self.client.close()
            self.client = None
