"""url_safety.py: fetch_url and the heartbeat refuse internal addresses, every hop.

Nothing here connects to an internal address: refusals happen before any request,
and the redirect cases use httpx's mock transport.

Run: python tests/test_url_safety.py   (repo root, stdlib only, no API calls)
"""

import asyncio
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import url_safety

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


def test_addresses() -> None:
    refused = ["http://169.254.169.254/opc/v2/instance/", "http://[::ffff:169.254.169.254]/",
               "http://metadata.google.internal/", "http://127.0.0.1:8080/", "http://localhost/",
               "http://[::1]/", "http://10.0.0.5/", "http://192.168.1.1/", "http://172.16.0.1/",
               "http://100.64.1.1/", "http://[fd00::1]/", "http://0.0.0.0/",
               "file:///etc/passwd", "ftp://example.com/", "http:///nohost"]
    for url in refused:
        _check(f"refuses {url}", url_safety.problem(url) is not None, str(url_safety.problem(url)))
    for url in ("https://www.python.org/downloads/", "http://93.184.216.34/", "https://8.8.8.8/"):
        _check(f"allows {url}", url_safety.problem(url) is None, str(url_safety.problem(url)))


def test_fetch_url_refuses_before_connecting() -> None:
    import httpx
    from agents import react
    real = httpx.Client
    sent = []

    class _Spy(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(lambda r: sent.append(str(r.url)) or httpx.Response(200))
            super().__init__(*a, **kw)

    try:
        httpx.Client = _Spy
        reply = asyncio.run(react._fetch_url("http://169.254.169.254/opc/v2/instance/"))
    finally:
        httpx.Client = real
    _check("fetch_url refuses the metadata address", reply.startswith("[refused"), reply)
    _check("and nothing was sent", sent == [], str(sent))


def test_fetch_url_checks_redirects() -> None:
    import httpx
    from agents import react
    real = httpx.Client
    sent = []

    def handler(request):
        sent.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})

    class _Spy(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    try:
        httpx.Client = _Spy
        reply = asyncio.run(react._fetch_url("http://93.184.216.34/start"))
    finally:
        httpx.Client = real
    _check("a public page redirecting to metadata is refused at the redirect",
           reply.startswith("[refused") and "169.254.169.254" in reply, reply)
    _check("only the public hop was sent", sent == ["http://93.184.216.34/start"], str(sent))


def test_heartbeat_refuses() -> None:
    import heartbeats
    lines, why = asyncio.run(heartbeats._fetch("http://127.0.0.1:9/"))
    _check("the heartbeat refuses an internal page", lines is None and why.startswith("refused"), why)
    handler = heartbeats._CheckedRedirects()
    req = urllib.request.Request("http://93.184.216.34/")
    try:
        handler.redirect_request(req, None, 302, "Found", {}, "http://169.254.169.254/")
        raised = False
    except urllib.error.URLError:
        raised = True
    _check("and a redirect to an internal address", raised)
    ok = handler.redirect_request(req, None, 302, "Found", {}, "http://93.184.216.34/next")
    _check("but follows a redirect to a public one", ok is not None and ok.full_url.endswith("/next"))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
