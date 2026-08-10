"""_attach_sources: the digest's links come from code, not from the model.

Regression cover for 2026-08-10, when llama-3.1-8b stopped copying URLs out of its
input and wrote publication names instead. Prompt, code and input were unchanged.
"""
import sys

sys.path.insert(0, r"c:\Dev\IGOR")

from agents.monitor import _attach_sources

RESULTS = [
    {"url": "https://apnews.com/article/meta-ai-hacking-0e8061437da"},
    {"url": "https://www.bbc.com/news/articles/cx2kgdnyk2po"},
    {"url": "https://www.scientificamerican.com/article/agents-deception/"},
]

_passed = 0
_failed = 0


def check(name, condition, detail=""):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


print("=== _attach_sources ===")

out = _attach_sources(
    "- Meta model went rogue in testing. Source: [1]\n"
    "- UK institute found deceptive behaviour. Source: [3]",
    RESULTS,
)
check("index becomes the real URL", "https://apnews.com/article/meta-ai-hacking-0e8061437da" in out, out)
check("each bullet resolves independently", "scientificamerican.com/article/agents-deception/" in out, out)
check("no bracketed index survives", "[1]" not in out and "[3]" not in out, out)

out = _attach_sources("- A story. Source: 2", RESULTS)
check("bare number without brackets also resolves", "bbc.com/news/articles/cx2kgdnyk2po" in out, out)

out = _attach_sources("- A story. Source: [9]", RESULTS)
check("out-of-range index is dropped, not rendered", "[9]" not in out and "Source" not in out, out)

# The exact 2026-08-10 output. Code cannot invent a link the model never pointed at;
# what it must do is not crash and leave the line readable.
out = _attach_sources("- Meta model went rogue. Source: AP News", RESULTS)
check("publication name is left alone rather than mangled", out.strip().endswith("Source: AP News"), out)

out = _attach_sources("- One. Source: [1]", [{"url": ""}])
check("empty URL in results does not emit a broken link", "http" not in out, out)

out = _attach_sources("", RESULTS)
check("empty synthesis does not raise", out == "", repr(out))

multi = _attach_sources(
    "- First. Source: [1]\n- Second. Source: [2]\n- Third. Source: [3]",
    RESULTS,
)
check("all three bullets carry distinct URLs", multi.count("http") == 3, multi)

# The live model puts "Source:" on its own line. A per-line check called every
# healthy bullet a failure, which is how a real warning gets ignored.
import io
import logging
from contextlib import redirect_stderr

buf = io.StringIO()
handler = logging.StreamHandler(buf)
logging.getLogger("agents.monitor").addHandler(handler)

healthy = _attach_sources(
    "- OpenAI pauses its Astra update over safety fears. \nSource: [1]\n"
    "- Meta discloses a model incident. \nSource: [2]",
    RESULTS,
)
check("source on its own line still resolves", healthy.count("http") == 2, healthy)
check("healthy multi-line bullets log nothing", "no usable source" not in buf.getvalue(),
      repr(buf.getvalue()))

buf.truncate(0), buf.seek(0)
_attach_sources("- A bullet the model gave no source for at all.", RESULTS)
check("a genuinely sourceless bullet still warns", "no usable source" in buf.getvalue(),
      repr(buf.getvalue()))

print(f"\n  {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
