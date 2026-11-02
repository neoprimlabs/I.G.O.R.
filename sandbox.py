"""Runs IGOR's shell and python_run commands inside bubblewrap.

Hermes runs agent commands in a Docker container. This host has 956 MB with about
500 MB free, and Docker would mean putting igor in the docker group, which is root
by another name. bubblewrap is what Flatpak and Anthropic's sandbox-runtime use on
Linux: unprivileged namespaces, no daemon, nothing resident.

It enforces, for the two tools that could walk around them, the rules IGOR's file
tools already follow:
- No secrets. systemd loads .env into the service's environment and every
  subprocess inherited it, so `env` in the shell printed the Discord token and both
  API keys. The sandbox starts from a clean environment, hides .env, and has its own
  process table, so /proc/<igor>/environ is out of reach as well.
- No network. A shell `curl` brought web text into a turn without the quarantine
  that fetch_url triggers.
- The sealed and guarded memory files and the deploy machinery are not there.
- Read-only, except a private /tmp, the repo's .git, which the self-modification
  workflow commits to before an edit, and memory/workspace for shell and python_run: Hermes'
  "filesystem persists between calls", so a command can leave a file for the next one, or for
  send_file. Nothing that reads outside text can write there, so it needs no quarantine of its own.

If bubblewrap cannot start, the command is refused rather than run bare.
"""

import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)

_HIDDEN_FILES = (".env", "memory/corrections.md", "memory/drafts.md", "memory/resume.md")
_HIDDEN_DIRS = ("memory/private", "/usr/local/lib/igor-deploy")
_WRITABLE = (".git",)
# Per command: 1 GiB of address space unless the caller says otherwise, 64 MiB per file written, no core dumps.
_LIMITS = ("--fsize=67108864", "--core=0")

_WORKSPACE_DAYS = 7
_WORKSPACE_BYTES = 200 * 1024 * 1024
_TIDY_EVERY_S = 3600

_ready = False
_tidied = 0.0


class Unavailable(Exception):
    """The sandbox cannot start. str() is the message for the model."""


def _path(p: str) -> Path:
    return Path(p) if p.startswith("/") else config.BASE_DIR / p


def workspace_folder() -> Path:
    return config.MEMORY_DIR / "workspace"


def _tidy_workspace(now: Optional[float] = None) -> None:
    """Oldest out first, past a week or past the cap. At most hourly. Never raises."""
    global _tidied
    now = time.time() if now is None else now
    if now - _tidied < _TIDY_EVERY_S:
        return
    _tidied = now
    try:
        found = []
        for path in workspace_folder().rglob("*"):
            if path.is_file() and not path.is_symlink():
                info = path.stat()
                found.append((info.st_mtime, info.st_size, path))
        total = sum(size for _, size, _ in found)
        for mtime, size, path in sorted(found, key=lambda item: item[0]):
            if now - mtime > _WORKSPACE_DAYS * 86400 or total > _WORKSPACE_BYTES:
                path.unlink()
                total -= size
    except OSError as e:
        logger.error("Could not tidy the workspace - %s: %s", type(e).__name__, e)


def command(argv: list[str], hide_memory: bool = False, network: bool = False,
            mounts: tuple = (), env: Optional[dict] = None, workspace: bool = False,
            address_space_mb: int = 1024) -> list[str]:
    """argv wrapped in bubblewrap with the mounts and limits above. Paths that do not
    exist are skipped: bubblewrap cannot mount over a file that is not there.
    hide_memory empties the whole memory folder, for a script that reaches tools only
    through the rules of script_tool's caller. network and mounts are for prerun.py's
    scripts alone, which only the user or Claude Code can write, and which never get the
    workspace: a script with the network could leave a page's words where React trusts them.
    address_space_mb is for the browser alone: V8 reserves far more address space than it uses, and
    browser.py watches what it really holds instead."""
    base = str(config.BASE_DIR)
    args = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
            "--tmpfs", "/tmp"]
    # A copy of IGOR under /tmp, as the live tests make, vanished under the empty /tmp, and bwrap could not
    # even change into it: every sandboxed command failed in those copies (found 2026-11-02).
    if Path(base).is_relative_to("/tmp"):
        args += ["--ro-bind", base, base]
    for rel in _WRITABLE:
        p = _path(rel)
        if p.is_dir():
            args += ["--bind", str(p), str(p)]
    if workspace and not hide_memory and not network and workspace_folder().is_dir():
        args += ["--bind", str(workspace_folder()), str(workspace_folder())]
    for rel in _HIDDEN_FILES:
        p = _path(rel)
        if p.is_file():
            args += ["--ro-bind", "/dev/null", str(p)]
    for rel in _HIDDEN_DIRS:
        p = _path(rel)
        if p.is_dir():
            args += ["--tmpfs", str(p)]
    if hide_memory and config.MEMORY_DIR.is_dir():
        args += ["--tmpfs", str(config.MEMORY_DIR)]
    for flag, host, inside in mounts:
        args += [flag, str(host), inside]
    if not network:
        args += ["--unshare-net"]
    args += ["--unshare-pid", "--unshare-ipc", "--unshare-uts",
             "--die-with-parent", "--new-session", "--clearenv",
             "--setenv", "PATH", f"{base}/venv/bin:/usr/local/bin:/usr/bin:/bin",
             "--setenv", "HOME", base,
             "--setenv", "LANG", "C.UTF-8",
             "--setenv", "XDG_CACHE_HOME", "/tmp/.cache",
             "--setenv", "PYTHONDONTWRITEBYTECODE", "1"]
    for name, value in (env or {}).items():
        args += ["--setenv", name, value]
    args += ["--chdir", base,
             "--", "prlimit", f"--as={address_space_mb * 1048576}", *_LIMITS, "--", *argv]
    return args


def _probe() -> str:
    """"" when a trivial command runs in the sandbox, otherwise why it did not."""
    if not shutil.which("bwrap"):
        return "bubblewrap is not installed"
    try:
        r = subprocess.run(command(["true"]), capture_output=True, text=True, timeout=10)
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    if r.returncode != 0:
        return (r.stderr or f"exit code {r.returncode}").strip()[:200]
    return ""


def prepare(argv: list[str], hide_memory: bool = False, network: bool = False,
            mounts: tuple = (), env: Optional[dict] = None, workspace: bool = False,
            address_space_mb: int = 1024) -> list[str]:
    """The sandboxed argv, or Unavailable. A success is remembered; a failure is
    probed again next time, so one bad moment does not disable the tools until a
    restart."""
    global _ready
    if not _ready:
        why = _probe()
        if why:
            logger.error("Sandbox unavailable, command refused - %s", why)
            raise Unavailable(
                f"NOT RUN: the sandbox that contains shell and python_run could not start "
                f"({why}). Nothing ran, and commands are never run outside it. Tell the user; "
                f"it needs fixing on the server.")
        _ready = True
    if workspace:
        workspace_folder().mkdir(parents=True, exist_ok=True)
        _tidy_workspace()
    return command(argv, hide_memory, network, mounts, env, workspace, address_space_mb)


def git_read_only() -> tuple:
    """Mounts for what parses or runs other people's files: .git is the one writable path left, and
    a hook written there would run outside the sandbox at the next commit."""
    git = _path(".git")
    return (("--ro-bind", git, str(git)),) if git.is_dir() else ()


def startup_check() -> str:
    """For main.py: "" when the sandbox works, otherwise why not."""
    why = _probe()
    if why:
        logger.error("Sandbox check failed at startup - shell and python_run will refuse: %s", why)
    return why
