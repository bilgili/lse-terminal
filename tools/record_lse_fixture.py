"""Record one row per LSE data surface into tests/fixtures/lse_rows.json.

Run once by hand with a real key. The contract's required row keys are
derived from this file, so re-record when the LSE feed adds or drops a key.
"""
import json
import sys
from pathlib import Path

from lse_terminal.providers import LseProvider


def first(rows):
    return rows[0] if rows else None


def main() -> int:
    p = LseProvider()
    if not p.configured():
        print("no LSE key set", file=sys.stderr)
        return 1
    out, errors = {}, {}

    def rec(name, fn):
        # One surface failing (an SDK/feed mismatch) must not lose the rest;
        # the failure is recorded so the gap is visible in the fixture.
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = None
            errors[name] = f"{type(e).__name__}: {e}"[:300]

    rec("prices", lambda: first(p.prices(["AAPL"])))
    rec("screener", lambda: {k: (v[:1] if isinstance(v, list) else v)
                             for k, v in p.screener().items()})
    rec("logos", lambda: dict(list(p.logos().items())[:1]))
    rec("options_underlyings", lambda: first(p.options_underlyings()))
    # SPY, not the first underlying: the listing's head can be a name with
    # no contracts currently listed (404), which records nothing.
    rec("option_chain", lambda: first(p.option_chain("SPY", limit=5)))
    rec("options_flow", lambda: first(p.options_flow(limit=5)))
    rec("economic_calendar", lambda: first(p.economic_calendar(limit=5)))
    rec("macro_catalog", lambda: first(p.macro_catalog()))
    cat = out.get("macro_catalog")
    rec("macro_series",
        lambda: first(p.macro_series(cat["symbol"], limit=5)) if cat else None)
    if errors:
        out["_errors"] = errors
    path = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "lse_rows.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
