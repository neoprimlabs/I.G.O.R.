"""The Discord heartbeat (monitor._heartbeat) the root-owned check reads.

Run: python tests/test_heartbeat.py   (repo root, no network)

systemd sees a process, not a connection, so a dropped gateway looked healthy. The
heartbeat file is touched only while Discord reports a live connection; a stale file
is what /usr/local/lib/igor-deploy/heartbeat.sh acts on.
"""

import asyncio
import math
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.MEMORY_DIR = Path(tempfile.mkdtemp())

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


def test_heartbeat_only_while_connected() -> None:
    from agents import monitor
    path = config.MEMORY_DIR / "heartbeat.txt"
    state = {"up": True}
    monitor._connected_fn = lambda: state["up"]
    asyncio.run(monitor._heartbeat())
    _check("a connected bot writes the heartbeat", path.exists() and path.read_text().startswith("20"))
    path.unlink()
    state["up"] = False
    asyncio.run(monitor._heartbeat())
    _check("a disconnected bot does not", not path.exists())

    def broken():
        raise RuntimeError("client gone")

    monitor._connected_fn = broken
    asyncio.run(monitor._heartbeat())
    _check("a failing check writes nothing and does not raise", not path.exists())
    monitor._connected_fn = None
    asyncio.run(monitor._heartbeat())
    _check("before setup there is nothing to report", not path.exists())


def test_connection_test() -> None:
    from agents import monitor
    for ready, closed, latency, expected in ((True, False, 0.08, True), (True, False, float("inf"), False),
                                             (True, False, float("nan"), False), (False, False, 0.08, False),
                                             (True, True, 0.08, False)):
        fake = SimpleNamespace(is_ready=lambda r=ready: r, is_closed=lambda c=closed: c, latency=latency)
        got = monitor.gateway_connected(fake)
        _check(f"ready={ready} closed={closed} latency={latency} -> {expected}", got is expected, str(got))
    _check("a client that raises counts as disconnected", monitor.gateway_connected(None) is False)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
