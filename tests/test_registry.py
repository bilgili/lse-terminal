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


def test_plugin_shadowing_a_provider_records_a_plugin_error(monkeypatch):
    reg = regmod.Registry()
    first = _prov("dup")()
    reg.register(first)
    second = _prov("dup")()
    _eps(monkeypatch, ("shadow", lambda: second))
    regmod.load_plugins(reg)
    assert reg.get("dup") is second
    assert len(reg.plugin_errors) == 1
    assert "'shadow'" in reg.plugin_errors[0] and "'dup'" in reg.plugin_errors[0]


def test_plain_reregister_is_not_an_error():
    # set_key / clear_key / broker reconnect replace a provider on purpose.
    reg = regmod.Registry()
    reg.register(_prov("lse")())
    reg.register(_prov("lse")())
    assert reg.plugin_errors == []


def test_plugin_with_unsettable_is_custom_is_reported(monkeypatch):
    class Slotted(_prov("slot")):
        __slots__ = ()

        @property
        def is_custom(self):
            return False

    _eps(monkeypatch, ("slot", lambda: Slotted))
    reg = regmod.Registry()
    regmod.load_plugins(reg)
    assert reg.all() == []
    assert len(reg.plugin_errors) == 1
    assert "'slot'" in reg.plugin_errors[0]
    assert "cannot set is_custom" in reg.plugin_errors[0]


def test_cache_wraps_cacheable_provider_only(tmp_path):
    from lse_terminal.engine.cache import CachedProvider, CandleCache
    from lse_terminal.engine.cache_store import MemorySnapshots
    cache = CandleCache(store=None, snapshots=MemorySnapshots())
    reg = regmod.Registry(cache=cache)
    a, b = _prov("a")(), _prov("b")()
    b.cacheable = False
    reg.register(a)
    reg.register(b)
    assert isinstance(reg.get("a"), CachedProvider) and reg.get("a").name == "a"
    assert reg.get("b") is b


def test_no_cache_never_wraps():
    reg = regmod.Registry()
    p = _prov("a")()
    reg.register(p)
    assert reg.get("a") is p


def test_shadowing_error_with_wrapped_existing(monkeypatch):
    from lse_terminal.engine.cache import CandleCache
    from lse_terminal.engine.cache_store import MemorySnapshots
    reg = regmod.Registry(cache=CandleCache(store=None, snapshots=MemorySnapshots()))
    reg.register(_prov("x")())
    _eps(monkeypatch, "x")
    regmod.load_plugins(reg)
    assert any("replaced the existing provider 'x'" in e for e in reg.plugin_errors)
