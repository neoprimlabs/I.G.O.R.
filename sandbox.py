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
- Read-only, except a private /tmp and the repo's .git, which the self-modification
  workflow commits to before an edit.

If bubblewrap cannot start, the command is refused rather than run bare.
"""

import logging
import shutil
import subprocess
from pathlib import Path

import config

logger = logging.getLogger(__name__)

_HIDDEN_FILES = (".env", "memory/corrections.md", "memory/drafts.md", "memory/resume.md")
_HIDDEN_DIRS = ("memory/private", "/usr/local/lib/igor-deploy")
_WRITABLE = (".git",)
# Per command: 1 GiB of address space, 64 MiB per file written, no core dumps.
_LIMITS = ("--as=1073741824", "--fsize=67108864", "--core=0")

_ready = False


class Unavailable(Exception):
    """The sandbox cannot start. str() is the message for the model."""


def _path(p: str) -> Path:
    return Path(p) if p.startswith("/") else config.BASE_DIR / p


def command(argv: list[str], hide_memory: bool = False) -> list[str]:
    """argv wrapped in bubblewrap with the mounts and limits above. Paths that do not
    exist are skipped: bubblewrap cannot mount over a file that is not there.
    hide_memory empties the whole memory folder, for a script that reaches tools only
    through the rules of script_tool's caller."""
    base = str(config.BASE_DIR)
    args = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
            "--tmpfs", "/tmp"]
    for rel in _WRITABLE:
        p = _path(rel)
        if p.is_dir():
            args += ["--bind", str(p), str(p)]
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
    args += ["--unshare-net", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
             "--die-with-parent", "--new-session", "--clearenv",
             "--setenv", "PATH", f"{base}/venv/bin:/usr/local/bin:/usr/bin:/bin",
             "--setenv", "HOME", base,
             "--setenv", "LANG", "C.UTF-8",
             "--setenv", "XDG_CACHE_HOME", "/tmp/.cache",
             "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
             "--chdir", base,
             "--", "prlimit", *_LIMITS, "--", *argv]
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


def prepare(argv: list[str], hide_memory: bool = False) -> list[str]:
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
    return command(argv, hide_memory)


def startup_check() -> str:
    """For main.py: "" when the sandbox works, otherwise why not."""
    why = _probe()
    if why:
        logger.error("Sandbox check failed at startup - shell and python_run will refuse: %s", why)
    return why
