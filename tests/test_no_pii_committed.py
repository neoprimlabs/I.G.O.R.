"""Fails if a tracked file contains something that looks like real personal data.

Run: python tests/test_no_pii_committed.py   (repo root, stdlib only, no network)

2026-10-12: `search_files` went live, was pointed at IGOR's own source as a smoke
test, and returned the user's real name and phone number out of four test fixtures.
The tool was behaving correctly - the real memory/resume.md was skipped, as it should
be. The fixtures were the leak, and **this repository is public**, so they had been
readable by anyone for as long as they had existed.

The fixtures only ever needed *a* name and *a* phone number, not his. They now use
reserved fictional values.

This test checks PATTERNS, never values. A guard that knew the real phone number in
order to look for it would have to contain the real phone number, which is the thing
it exists to prevent.

What it cannot do is clean history. The values are still in earlier commits of a
public repo; removing them means rewriting published history, which is the user's
call and not something a test can make.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


# 555-0100 through 555-0199 is the range reserved for fiction. Anything else shaped
# like a North American number is assumed real until a human says otherwise.
_PHONE = re.compile(r"\(?\b(\d{3})\)?[ .\-](\d{3})[ .\-]\d{4}\b")

# 555 is the reserved EXCHANGE, not the area code. The first version of this checked
# group 1 and so read "(412) 555-1234" as a real number. Either position holding 555
# is treated as invented, which is looser than the NANP rule and the right way round
# for a guard: a false negative publishes a phone number, a false positive costs a
# one-line edit.
_FAKE = "555"

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b")
_FAKE_DOMAINS = {"example.com", "example.org", "example.net", "example.invalid",
                 "invalid", "test", "localhost", "noreply.anthropic.com"}

# A street address is too varied to pattern-match without drowning in false
# positives, so only the obvious shape is checked.
_STREET = re.compile(r"\b\d{1,5}\s+[A-Z][a-z]+\s+(Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr|Court|Ct|Boulevard|Blvd)\b")

_SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".onnx", ".wav"}

# This file is exempt from its own content scan, and has to be: a guard has to hold
# the shapes it searches for. test_the_guard_actually_catches_something below carries
# a deliberately real-looking number, email and street address as positive controls.
# Nothing else is exempt, and the exemption is one resolved path, not a pattern.
_SELF = Path(__file__).resolve()


def _tracked() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                         capture_output=True, text=True, check=True)
    return [ROOT / name for name in out.stdout.split("\0")
            if name and (ROOT / name).resolve() != _SELF]


def test_no_real_phone_numbers() -> None:
    hits = []
    for path in _tracked():
        if path.suffix.lower() in _SKIP_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in _PHONE.finditer(text):
            if _FAKE in (match.group(1), match.group(2)):
                continue
            line = text[:match.start()].count("\n") + 1
            hits.append(f"{path.relative_to(ROOT)}:{line}")
    _check("no tracked file holds a phone number outside the 555 fiction range",
           not hits, f"found at {hits[:6]}")


def test_no_real_email_addresses() -> None:
    hits = []
    for path in _tracked():
        if path.suffix.lower() in _SKIP_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in _EMAIL.finditer(text):
            domain = match.group(1).lower()
            if domain in _FAKE_DOMAINS or domain.endswith(".example.com"):
                continue
            line = text[:match.start()].count("\n") + 1
            hits.append(f"{path.relative_to(ROOT)}:{line} ({domain})")
    _check("no tracked file holds an email outside the example domains",
           not hits, f"found at {hits[:6]}")


def test_no_street_addresses() -> None:
    hits = []
    for path in _tracked():
        if path.suffix.lower() in _SKIP_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in _STREET.finditer(text):
            line = text[:match.start()].count("\n") + 1
            hits.append(f"{path.relative_to(ROOT)}:{line}")
    _check("no tracked file holds something shaped like a street address",
           not hits, f"found at {hits[:6]}")


def test_the_private_memory_files_are_not_tracked() -> None:
    """resume.md and memory/private/ live on the server and are edited with
    sudo -u igor, never through git. If one were ever committed, every guard above
    would be beside the point."""
    tracked = {str(p.relative_to(ROOT)).replace("\\", "/") for p in _tracked()}
    for never in ("memory/resume.md", "memory/corrections.md", "memory/drafts.md"):
        _check(f"{never} is not tracked", never not in tracked)
    private = [t for t in tracked if t.startswith("memory/private/")]
    _check("nothing under memory/private/ is tracked", not private, str(private))


def test_the_guard_actually_catches_something() -> None:
    """A guard that cannot fail is decoration. These are the shapes it must reject,
    checked against the patterns rather than against any real value."""
    real = _PHONE.search("call me on (412) 867-5309")
    _check("a real-looking phone is caught", real is not None)
    _check("and is not waved through as fiction",
           real is not None and _FAKE not in (real.group(1), real.group(2)), str(real))

    area = _PHONE.search("(555) 010-0001")
    _check("555 in the area position is recognised as invented",
           area is not None and _FAKE in (area.group(1), area.group(2)), str(area))

    # The bug this replaced: only group 1 was checked, so a real area code with the
    # reserved exchange read as real and would have been reported as a leak.
    exchange = _PHONE.search("(412) 555-0100")
    _check("and so is 555 in the exchange position",
           exchange is not None and _FAKE in (exchange.group(1), exchange.group(2)),
           str(exchange))
    _check("a real-looking email is caught",
           _EMAIL.search("someone@gmail.com").group(1) == "gmail.com")
    _check("an example domain is recognised as fake",
           _EMAIL.search("a@example.com").group(1) in _FAKE_DOMAINS)
    _check("a street address shape is caught",
           _STREET.search("lives at 123 Maple Street now") is not None)


if __name__ == "__main__":
    print("phone numbers")
    test_no_real_phone_numbers()
    print("\nemail addresses")
    test_no_real_email_addresses()
    print("\nstreet addresses")
    test_no_street_addresses()
    print("\nprivate files are not tracked")
    test_the_private_memory_files_are_not_tracked()
    print("\nthe guard can fail")
    test_the_guard_actually_catches_something()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
