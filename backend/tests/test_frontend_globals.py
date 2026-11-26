"""Guards against a bug that silently broke 'Add to cart', the maps, the badges and PWA setup for the whole app:
a top-level `const Cart = ...` in a classic <script> is NOT a property of `window`, so `window.Cart && Cart.add()` is always false."""
import re
from pathlib import Path

JS = Path(__file__).resolve().parents[2] / "frontend" / "js"


def top_level_globals() -> set[str]:
    out = set()
    for f in JS.glob("*.js"):
        out |= set(re.findall(r"^(?:const|let|class)\s+([A-Z][A-Za-z0-9]+)\b", f.read_text(encoding="utf-8"), flags=re.M))
    return out


def test_no_script_checks_for_a_const_global_through_window():
    names = top_level_globals()
    assert {"Cart", "App", "MapKit", "Share"} <= names                       # the scan really sees the shared modules
    bad = []
    for f in JS.glob("*.js"):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for n in re.findall(r"window\.([A-Z][A-Za-z0-9]+)\b", line):
                if n in names:
                    bad.append(f"{f.name}:{i} uses window.{n}, but {n} is a const (so window.{n} is always undefined). Use typeof {n} !== 'undefined'")
    assert not bad, "\n".join(bad)


def test_card_clicks_are_handled_once():
    app = (JS / "app.js").read_text(encoding="utf-8")
    assert "__cardClickHandled" in app                                       # body and page both listen; the click must act only once
