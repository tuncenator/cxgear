"""Reversible PATH shim; no edits to Codex or project configuration."""
import os
from pathlib import Path
import shutil
import sys

from . import accounts, store


def install(directory=None):
    directory = Path(directory or Path.home() / ".local/bin").expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "codex"
    wrapper = Path(sys.executable).parent / "cxgear-codex"
    if not wrapper.is_file():
        raise ValueError("Install cxgear into its virtual environment first (./install.sh)")
    meta_path = store.root() / "install.json"
    with store.locked():
        if target.is_symlink() and target.resolve() == wrapper.resolve():
            return target
        if meta_path.exists():
            raise ValueError("A previous installation is recorded; run cxgear uninstall first")
        source = Path(accounts.binary())
        backup = directory / ".codex-before-cxgear"
        if backup.exists() or backup.is_symlink():
            raise ValueError("Existing Codex backup found; refusing to overwrite it")
        existed = target.exists() or target.is_symlink()
        native = str(source)
        if existed:
            # Store a stable upstream link when possible so Codex updates still work.
            if source == target:
                native = str(target.resolve()) if target.is_symlink() else str(backup)
            target.rename(backup)
        try:
            target.symlink_to(wrapper)
            cfg = store.config()
            cfg["codex_binary"] = native
            store.write(store.root() / "config.json", cfg)
            store.write(meta_path, {"target": str(target), "backup": str(backup) if existed else None, "wrapper": str(wrapper)})
        except BaseException:
            target.unlink(missing_ok=True)
            if existed:
                backup.rename(target)
            raise
    return target


def uninstall():
    with store.locked():
        meta = store.read(store.root() / "install.json")
        if not meta:
            return
        target = Path(meta["target"])
        if not target.is_symlink() or target.resolve() != Path(meta["wrapper"]).resolve():
            raise ValueError("Codex command changed since installation; leaving it untouched")
        target.unlink()
        if meta["backup"]:
            Path(meta["backup"]).rename(target)
        cfg = store.config()
        if cfg.get("codex_binary") == meta["backup"]:
            cfg["codex_binary"] = str(target)
            store.write(store.root() / "config.json", cfg)
        (store.root() / "install.json").unlink()
