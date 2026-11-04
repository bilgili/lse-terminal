from types import SimpleNamespace

import pandas as pd

from lse_terminal.contracts import Provider
from lse_terminal.engine import registry as regmod


def _prov(n):
    class P(Provider):
        name = n

        def search(self, query="", limit=50):
            return []

        def candles(self, symbol, timeframe, limit=500, start=None, end=None):
            return pd.DataFrame()
    return P


def _eps(monkeypatch, *pairs):
    """pairs: (name, loader) tuples, or bare names loading a fresh provider."""
    eps = [SimpleNamespace(name=p, load=(lambda p=p: _prov(p))) if isinstance(p, str)
           else SimpleNamespace(name=p[0], load=p[1]) for p in pairs]
    monkeypatch.setattr(regmod, "entry_points",
                        lambda group: eps if group == "lse_terminal.providers" else [])


def test_plugins_load_sorted_and_custom(monkeypatch):
    _eps(monkeypatch, "zeta", "alpha")
    reg = regmod.Registry()
    regmod.load_plugins(reg)
    assert [p.name for p in reg.all()] == ["alpha", "zeta"]
    assert all(p.is_custom for p in reg.all())


def test_plugin_cannot_opt_out_of_custom(monkeypatch):
    # Entry-point providers are user-installed by definition, so the fleet
    # directory gate must never hide one, even if the class opts out.
    class Opt(_prov("x")):
        is_custom = False

    _eps(monkeypatch, ("x", lambda: Opt))
    reg = regmod.Registry()
    regmod.load_plugins(reg)
    assert reg.get("x").is_custom is True


def test_plugin_instance_is_marked_custom(monkeypatch):
    inst = _prov("inst")()
    _eps(monkeypatch, ("inst", lambda: inst))
    reg = regmod.Registry()
    regmod.load_plugins(reg)
    assert reg.get("inst").is_custom is True


def test_builtins_are_not_custom():
    reg = regmod.Registry()
    regmod.load_builtins(reg)
    assert not any(p.is_custom for p in reg.all())
