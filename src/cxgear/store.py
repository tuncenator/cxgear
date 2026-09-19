"""Private local state; credentials never enter event logs or status output."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import time


def root():
    return Path(os.environ.get("CXGEAR_HOME", Path.home() / ".local/share/cxgear")).expanduser()


def private_dir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def account_name(name):
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", name):
        raise ValueError("Account names must start with a letter and contain only lowercase letters, digits, - or _.")
    return name


def account_home(name):
    return root() / "accounts" / account_name(name)


def read(path, default=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {} if default is None else default


def write(path, value):
    private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(prefix=".cxgear-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextlib.contextmanager
def locked(name="state"):
    private_dir(root())
    fd = os.open(root() / (name + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def config():
    return read(root() / "config.json", {"accounts": {}})


def event(kind, **fields):
    # Explicit call-site fields only; no raw exceptions, protocol frames, or tokens.
    private_dir(root())
    data = (json.dumps({"at": time.time(), "event": kind, **fields}) + "\n").encode()
    fd = os.open(root() / "events.jsonl", os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
