"""Checkpoints: what a file looked like before IGOR's tools changed it, and rollback.

Hermes snapshots a working directory into a shadow git repo before its file tools
and destructive shell commands run, at most once per directory per turn, and
/rollback lists, diffs and restores those snapshots. IGOR keeps per-file
before-images instead. Since sandbox.py made shell and python_run read-only, the
only things that can change a file are write_file, patch_file and memory_write, one
file each, so the files they touch are exactly what a checkpoint needs to hold.

Hermes' rules otherwise: one checkpoint per turn, and the first before-image of a
file in a turn wins; a rollback is itself checkpointed, so it can be undone; a file
changed since IGOR last wrote it is kept, not overwritten, unless "all" is asked
for; files over 10 MB are skipped; only the newest 20 checkpoints are kept.

Hermes' rollback also rewinds the conversation. IGOR's does not: the files are the
damage worth undoing, and context.db is a rolling window anyway.

Checkpointing fails open. It is an undo convenience on top of the nightly backup, so
a fault here is logged and never blocks the write it was protecting.
"""

import contextvars
import difflib
import hashlib
import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)

_MAX_KEPT = 20
_MAX_FILE_BYTES = 10 * 1024 * 1024
_DIFF_CHARS = 1800
_MANIFEST = "manifest.json"

_turn: contextvars.ContextVar = contextvars.ContextVar("checkpoint_turn", default=None)


def _root() -> Path:
    return config.MEMORY_DIR / ".checkpoints"


def _key(path: Path) -> Optional[str]:
    """Where a file sits, as the user would name it. None outside IGOR's folders."""
    resolved = path.resolve()
    for base, prefix in ((config.MEMORY_DIR.resolve(), "memory/"), (config.BASE_DIR.resolve(), "")):
        try:
            return prefix + resolved.relative_to(base).as_posix()
        except ValueError:
            continue
    return None


def _path_of(key: str) -> Optional[Path]:
    """The file a key names, or None if it would land outside IGOR's folders."""
    if key.startswith("memory/"):
        base, rest = config.MEMORY_DIR.resolve(), key[len("memory/"):]
    else:
        base, rest = config.BASE_DIR.resolve(), key
    path = (base / rest).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return None
    return path


def _sha(data: Optional[bytes]) -> str:
    """A file's state: its hash, or "absent". Never None, so "IGOR deleted it" cannot
    be mistaken for "nothing was recorded"."""
    return "absent" if data is None else hashlib.sha256(data).hexdigest()


def _read(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def start_turn(label: str) -> None:
    """Once per top-level React turn. The folder is made on the first write, so a
    turn that changes nothing leaves nothing behind."""
    _turn.set({"label": " ".join((label or "").split())[:80], "id": None})


def _current(label: str) -> Path:
    """This turn's checkpoint folder, made on first use. Outside a turn (ConfigEdit,
    a test) each write gets its own."""
    state = _turn.get()
    if state is None:
        state = {"label": label, "id": None}
    if state["id"] is None:
        root = _root()
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        # After the highest in this second, not the first free number: a pruned name
        # would be reused, sort oldest and be pruned straight away.
        taken = [int(p.name.rsplit("-", 1)[1]) for p in root.glob(f"{stamp}-*")
                 if p.name.rsplit("-", 1)[1].isdigit()]
        folder = root / f"{stamp}-{max(taken, default=-1) + 1:03d}"
        folder.mkdir()
        _save(folder, {"label": state["label"], "at": datetime.now(timezone.utc).isoformat(),
                       "files": []})
        state["id"] = folder.name
        _prune()
    return _root() / state["id"]


def _load(folder: Path) -> dict:
    return json.loads((folder / _MANIFEST).read_text(encoding="utf-8"))


def _save(folder: Path, manifest: dict) -> None:
    (folder / _MANIFEST).write_text(json.dumps(manifest, indent=1), encoding="utf-8")


def _prune() -> None:
    folders = sorted(p for p in _root().iterdir() if p.is_dir())
    for old in folders[:-_MAX_KEPT]:
        shutil.rmtree(old, ignore_errors=True)


def before_write(path: Path, label: str = "") -> None:
    """Save what path holds now, unless this turn already saved it."""
    try:
        key = _key(path)
        if key is None:
            return
        folder = _current(label or f"write to {path.name}")
        manifest = _load(folder)
        if any(f["path"] == key for f in manifest["files"]):
            return
        data = _read(path)
        entry = {"path": key, "existed": data is not None, "before": _sha(data), "after": None}
        if data is not None and len(data) > _MAX_FILE_BYTES:
            entry["skipped"] = "over 10 MB"
        elif data is not None:
            entry["blob"] = f"{len(manifest['files'])}.bin"
            (folder / entry["blob"]).write_bytes(data)
        manifest["files"].append(entry)
        _save(folder, manifest)
    except Exception as e:
        logger.error("Checkpoint before writing %s failed - %s: %s", path, type(e).__name__, e)


def after_write(path: Path) -> None:
    """Record what IGOR wrote, so a later rollback can tell IGOR's write from a hand edit."""
    try:
        key = _key(path)
        state = _turn.get()
        if key is None:
            return
        folder = _root() / state["id"] if state and state.get("id") else _latest_with(key)
        if folder is None:
            return
        manifest = _load(folder)
        for f in manifest["files"]:
            if f["path"] == key:
                f["after"] = _sha(_read(path))
        _save(folder, manifest)
    except Exception as e:
        logger.error("Checkpoint after writing %s failed - %s: %s", path, type(e).__name__, e)


def _latest_with(key: str) -> Optional[Path]:
    for folder in reversed(_folders()):
        try:
            if any(f["path"] == key for f in _load(folder)["files"]):
                return folder
        except Exception:
            continue
    return None


def _folders() -> list[Path]:
    """Oldest first."""
    root = _root()
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / _MANIFEST).exists())


def _newest_first() -> list[tuple[Path, dict]]:
    out = []
    for folder in reversed(_folders()):
        try:
            manifest = _load(folder)
        except Exception as e:
            logger.error("Unreadable checkpoint %s - %s: %s", folder.name, type(e).__name__, e)
            continue
        if manifest["files"]:
            out.append((folder, manifest))
    return out


def _when(manifest: dict) -> str:
    import clock
    try:
        at = datetime.fromisoformat(manifest["at"]).astimezone(clock.USER_TZ)
    except Exception:
        return "unknown time"
    return f"{at:%b} {at.day} {at:%H:%M}"


def list_text() -> str:
    checkpoints = _newest_first()
    if not checkpoints:
        return "No checkpoints. One is saved each time IGOR changes a file, before the change."
    lines = ["Checkpoints, newest first:"]
    for i, (_, m) in enumerate(checkpoints, 1):
        files = ", ".join(f["path"] for f in m["files"])
        label = f' - "{m["label"]}"' if m.get("label") else ""
        lines.append(f"{i}. {_when(m)}{label} - {files}")
    lines.append("Say 'rollback diff N' to see what would change, 'rollback N' to restore, "
                 "or 'rollback N <file>' for one file.")
    return "\n".join(lines)


def _plan(n: int, only: Optional[str]) -> tuple[list[dict], str]:
    """For checkpoint n (1 is newest): each file to restore with its target bytes and
    what IGOR last wrote there. ([], why) when n is out of range."""
    checkpoints = _newest_first()
    if not 1 <= n <= len(checkpoints):
        return [], f"There is no checkpoint {n}. " + (
            f"Checkpoints run from 1 to {len(checkpoints)}." if checkpoints else "There are none yet.")
    targets: dict[str, dict] = {}
    for i, (folder, m) in enumerate(checkpoints[:n]):
        for f in m["files"]:
            if only and f["path"] != only:
                continue
            item = targets.setdefault(f["path"], {"path": f["path"], "last": f})
            # Newest to oldest, so the oldest before-image within 1..n ends up the target.
            item["target"] = (folder, f)
    if only and not targets:
        return [], f"Checkpoints 1 to {n} did not change {only}."
    return list(targets.values()), ""


def _target_bytes(item: dict) -> tuple[Optional[bytes], str]:
    """(bytes to restore, None meaning delete), or a reason it cannot be restored."""
    folder, f = item["target"]
    if f.get("skipped"):
        return None, f"it was {f['skipped']} and not saved"
    if not f["existed"]:
        return None, ""
    return (folder / f["blob"]).read_bytes(), ""


def diff_text(n: int) -> str:
    plan, why = _plan(n, None)
    if why:
        return why
    out = []
    for item in plan:
        path = _path_of(item["path"])
        if path is None:
            continue
        target, problem = _target_bytes(item)
        if problem:
            out.append(f"{item['path']}: cannot restore, {problem}")
            continue
        now = (_read(path) or b"").decode("utf-8", "replace").splitlines()
        then = (target or b"").decode("utf-8", "replace").splitlines()
        if target is None:
            out.append(f"{item['path']}: IGOR created it; a rollback deletes it")
            continue
        lines = list(difflib.unified_diff(now, then, f"{item['path']} now",
                                          f"{item['path']} after rollback", n=1, lineterm=""))
        out.append("\n".join(lines) if lines else f"{item['path']}: already as it was")
    text = "\n\n".join(out) or "Nothing to show."
    if len(text) > _DIFF_CHARS:
        text = text[:_DIFF_CHARS] + "\n... (cut short)"
    return f"Rollback {n} would change:\n{text}"


def restore(n: int, only: Optional[str] = None, overwrite: bool = False) -> str:
    plan, why = _plan(n, only)
    if why:
        return why
    _, chosen = _newest_first()[n - 1]
    restored, removed, kept, failed = [], [], [], []
    # Everything is read before anything is written: the rollback's own checkpoint
    # can prune the oldest one, which may be the one being restored from.
    ready = []
    for item in plan:
        path = _path_of(item["path"])
        if path is None:
            failed.append(f"{item['path']} (outside IGOR's folders)")
            continue
        try:
            target, problem = _target_bytes(item)
        except Exception as e:
            failed.append(f"{item['path']} ({type(e).__name__})")
            continue
        if problem:
            failed.append(f"{item['path']} ({problem})")
            continue
        last = item["last"]
        expected = last["after"] if last.get("after") is not None else last.get("before")
        if not overwrite and _sha(_read(path)) != expected:
            kept.append(item["path"])
            continue
        ready.append((item, path, target))
    start_turn(f"before rollback {n}")
    for item, path, target in ready:
        try:
            before_write(path, f"before rollback {n}")
            if target is None:
                path.unlink(missing_ok=True)
                removed.append(item["path"])
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(target)
                restored.append(item["path"])
            after_write(path)
        except Exception as e:
            logger.error("Rollback of %s failed - %s: %s", item["path"], type(e).__name__, e)
            failed.append(f"{item['path']} ({type(e).__name__})")
    label = f' ("{chosen["label"]}")' if chosen.get("label") else ""
    lines = [f"Rolled back to before checkpoint {n}, {_when(chosen)}{label}."]
    if restored:
        lines.append("Restored: " + ", ".join(restored) + ".")
    if removed:
        lines.append("Removed, because IGOR created them: " + ", ".join(removed) + ".")
    if kept:
        # This rollback's own checkpoint, if it made one, is now 1 and moved the rest up.
        again = n + 1 if restored or removed else n
        lines.append("Kept, because they changed after IGOR last wrote them: " + ", ".join(kept)
                     + f". Say 'rollback {again} all' to overwrite them too.")
    if failed:
        lines.append("Not restored: " + ", ".join(failed) + ".")
    if not restored and not removed:
        lines.append("Nothing was changed.")
    else:
        if any(p.endswith(".py") for p in restored + removed):
            lines.append("Code files change on disk now; the running IGOR uses them after its next restart.")
        lines.append("Say 'rollback 1' to undo this rollback.")
    return "\n".join(lines)


_COMMAND = re.compile(r"^/?rollback(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)


def parse(text: str) -> Optional[tuple]:
    """A rollback command, or None so the message goes to the router as usual."""
    m = _COMMAND.match((text or "").strip())
    if not m:
        return None
    rest = (m.group(1) or "").strip()
    words = rest.split()
    if not words or words == ["list"]:
        return ("list",)
    if len(words) == 2 and words[0].lower() == "diff" and words[1].isdigit():
        return ("diff", int(words[1]))
    if words[0].isdigit():
        n = int(words[0])
        if len(words) == 1:
            return ("restore", n, None, False)
        if len(words) == 2 and words[1].lower() == "all":
            return ("restore", n, None, True)
        if len(words) == 2:
            return ("restore", n, words[1].removeprefix("./"), False)
    return None


def run(command: tuple) -> str:
    if command[0] == "list":
        return list_text()
    if command[0] == "diff":
        return diff_text(command[1])
    _, n, only, overwrite = command
    return restore(n, only, overwrite)
