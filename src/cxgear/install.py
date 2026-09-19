"""Reversible PATH shim; no edits to Codex or project configuration."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import re
import shlex

from . import accounts, store


def _install_custom(directory):
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
            meta = store.read(meta_path)
            # A native installer can replace the PATH symlink. Repair only that
            # known target; preserve the original backup and unrelated commands.
            if (meta.get("target") == str(target)
                    and Path(meta.get("wrapper", "")).resolve() == wrapper.resolve()
                    and target.is_symlink()
                    and target.resolve() == Path(accounts.binary()).resolve()):
                with tempfile.TemporaryDirectory(prefix=".cxgear-repair-", dir=directory) as tmp:
                    replacement = Path(tmp)/"codex"
                    replacement.symlink_to(wrapper)
                    replacement.replace(target)
                return target
            raise ValueError("Recorded Codex launcher was replaced by an unrecognized command; leaving it untouched")
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


def _uninstall_custom():
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


_START = "# >>> cxgear >>>"
_END = "# <<< cxgear <<<"


def _without_block(text):
    return re.sub(r"(?m)^" + re.escape(_START) + r"\n[\s\S]*?^" + re.escape(_END) + r"\n?", "", text)


def _shell_paths():
    home = Path.home()
    zhome = Path(os.environ.get("ZDOTDIR") or home).expanduser()
    paths = [zhome/".zshrc", zhome/".zprofile", home/".bashrc", home/".profile"]
    paths += [home/name for name in (".bash_profile", ".bash_login") if (home/name).exists()]
    return list(dict.fromkeys(paths))


def _restore_old_launcher(meta):
    target = Path(meta["target"])
    wrapper = Path(meta["wrapper"])
    native = Path(accounts.binary())
    if not target.is_symlink() or target.resolve() not in (wrapper.resolve(), native.resolve()):
        raise ValueError("Previous Codex launcher is unrecognized; leaving it untouched")
    backup = Path(meta["backup"]) if meta.get("backup") else None
    if backup and not (backup.exists() or backup.is_symlink()):
        raise ValueError("Original Codex launcher backup is missing")
    target.unlink()
    if backup:
        backup.rename(target)
        cfg = store.config()
        if cfg.get("codex_binary") == str(backup):
            cfg["codex_binary"] = str(target)
            store.write(store.root()/"config.json", cfg)


def install(directory=None):
    if directory is not None:
        return _install_custom(directory)
    # Own a separate PATH entry. Native Codex installers own ~/.local/bin/codex
    # and may replace it at any update, even when the version stays the same.
    target = store.root()/"bin/codex"
    wrapper = Path(sys.executable).parent/"cxgear-codex"
    if not wrapper.is_file():
        raise ValueError("Install cxgear into its virtual environment first (./install.sh)")
    with store.locked():
        meta = store.read(store.root()/"install.json")
        if target.exists() or target.is_symlink():
            if not target.is_symlink() or target.resolve() != wrapper.resolve():
                raise ValueError("Private Codex launcher is unrecognized; leaving it untouched")
        source = Path(accounts.binary())
        if source.resolve() == wrapper.resolve():
            raise ValueError("Native Codex path resolves to cxgear; configure the upstream binary")
        if meta and meta.get("mode") != "path":
            _restore_old_launcher(meta)
            source = Path(accounts.binary())
        store.private_dir(target.parent)
        if not target.is_symlink():
            target.symlink_to(wrapper)
        env_path = store.root()/"env.sh"
        quoted_bin = shlex.quote(str(target.parent))
        env_path.write_text("# cxgear's private launcher takes precedence over native installers.\n"
                            + 'case "$PATH" in\n  ' + quoted_bin + ':*) ;;\n  *) export PATH='
                            + quoted_bin + ':"$PATH" ;;\nesac\n')
        env_path.chmod(0o600)
        block = _START + "\n. " + shlex.quote(str(env_path)) + "\n" + _END + "\n"
        previous = {entry["path"]: entry for entry in meta.get("shell_files", [])}
        shell_files = []
        for path in _shell_paths():
            exists = path.exists()
            old = path.read_text() if exists else ""
            clean = _without_block(old)
            separator = "" if not clean or clean.endswith("\n") else "\n"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(clean + separator + block)
            shell_files.append(previous.get(str(path), {"path": str(path), "created": not exists}))
        cfg = store.config()
        cfg["codex_binary"] = str(source)
        store.write(store.root()/"config.json", cfg)
        store.write(store.root()/"install.json", {"mode": "path", "target": str(target),
                    "wrapper": str(wrapper), "env": str(env_path), "shell_files": shell_files})
    return target


def uninstall():
    meta = store.read(store.root()/"install.json")
    if meta.get("mode") != "path":
        return _uninstall_custom()
    with store.locked():
        target = Path(meta["target"])
        if not target.is_symlink() or target.resolve() != Path(meta["wrapper"]).resolve():
            raise ValueError("Private Codex launcher changed; leaving it untouched")
        for entry in meta.get("shell_files", []):
            path = Path(entry["path"])
            if not path.exists():
                continue
            text = _without_block(path.read_text())
            if entry.get("created") and not text:
                path.unlink()
            else:
                path.write_text(text)
        target.unlink()
        Path(meta["env"]).unlink(missing_ok=True)
        (store.root()/"install.json").unlink()
