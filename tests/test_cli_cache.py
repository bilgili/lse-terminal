import io
import json
import os
import urllib.error
import urllib.request

import pandas as pd
import pytest

from lse_terminal import cli
from lse_terminal.contracts import CANDLE_COLUMNS
from lse_terminal.engine.cache_store import SqliteStore

K = ("lse", "AAPL", "1m")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("LSE_TERMINAL_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _prefill(cfg):
    st = SqliteStore(cfg / "cache.sqlite")
    df = pd.DataFrame([(60, 1.0, 2.0, 0.5, 1.5, 10.0)], columns=CANDLE_COLUMNS)
    st.write_page(K, df, 60, 120, None, now=1000)
    return st


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"{}"


def _record(monkeypatch, exc=None):
    seen = []

    def fake(req, timeout=None):
        seen.append((req.get_method(), req.full_url))
        if exc:
            raise exc
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return seen


def _engine(cfg, pid, port):
    d = cfg / "engines"
    d.mkdir(exist_ok=True)
    f = d / f"engine-{pid}.json"
    f.write_text(json.dumps({"port": port, "pid": pid}))
    return f


def test_engine_file_port_is_used(cfg, monkeypatch):
    _engine(cfg, 1, 7801)
    seen = _record(monkeypatch)
    assert cli.main(["cache", "clear", "--provider", "lse"]) == 0
    assert seen == [("DELETE", "http://127.0.0.1:7801/api/cache?provider=lse")]


def test_two_engines_stale_first_is_deleted_second_answers(cfg, monkeypatch):
    f1, f2 = _engine(cfg, 1, 7801), _engine(cfg, 2, 7802)
    seen = []

    def fake(req, timeout=None):
        seen.append(req.full_url)
        if ":7801" in req.full_url:
            raise urllib.error.URLError(ConnectionRefusedError())
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert cli.main(["cache", "stats"]) == 0
    assert seen == ["http://127.0.0.1:7801/api/cache/stats",
                    "http://127.0.0.1:7802/api/cache/stats"]
    assert not f1.exists() and f2.exists()


@pytest.mark.parametrize("exc", [TimeoutError("t"), urllib.error.URLError(TimeoutError("t"))])
def test_hung_engine_returns_1_and_leaves_file(cfg, monkeypatch, capsys, exc):
    st = _prefill(cfg)
    f = _engine(cfg, 1, 7801)
    seen = _record(monkeypatch, exc)
    assert cli.main(["cache", "clear"]) == 1
    assert "engine on port 7801 did not answer" in capsys.readouterr().out
    assert len(seen) == 1 and f.exists() and st.keys() == [K]


def test_no_engines_and_port_refuses_uses_file(cfg, monkeypatch):
    st = _prefill(cfg)
    f = _engine(cfg, 1, 7801)
    _record(monkeypatch, urllib.error.URLError("refused"))
    assert cli.main(["cache", "clear"]) == 0
    assert not f.exists() and st.keys() == []


def test_no_engine_file_uses_port_flag_then_default(cfg, monkeypatch):
    seen = _record(monkeypatch)
    cli.main(["cache", "clear", "--port", "7790"])
    cli.main(["cache", "stats"])
    assert seen == [("DELETE", "http://127.0.0.1:7790/api/cache"),
                    ("GET", "http://127.0.0.1:7787/api/cache/stats")]


def test_urlerror_falls_back_to_file(cfg, monkeypatch):
    st = _prefill(cfg)
    _record(monkeypatch, urllib.error.URLError("refused"))
    assert cli.main(["cache", "clear"]) == 0
    assert st.keys() == []


def test_httperror_prints_body_and_leaves_file(cfg, monkeypatch, capsys):
    st = _prefill(cfg)
    err = urllib.error.HTTPError("u", 500, "boom", {}, io.BytesIO(b"engine broke"))
    _record(monkeypatch, err)
    assert cli.main(["cache", "clear"]) == 1
    assert "engine broke" in capsys.readouterr().out
    assert st.keys() == [K]


def test_stats_direct_file_prints_json(cfg, monkeypatch, capsys):
    _prefill(cfg)
    _record(monkeypatch, urllib.error.URLError("refused"))
    assert cli.main(["cache", "stats"]) == 0
    assert json.loads(capsys.readouterr().out)["bars"] == 1


def test_server_writes_own_engine_file_and_removes_only_it(cfg, monkeypatch):
    import uvicorn
    seen = {}
    other = _engine(cfg, 999999, 7999)
    mine = cfg / "engines" / f"engine-{os.getpid()}.json"

    def fake_run(app, **kw):
        seen.update(json.loads(mine.read_text()))

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr("lse_terminal.engine.server.create_app", lambda: None)
    assert cli.main(["--no-browser", "--port", "7805"]) == 0
    assert seen == {"port": 7805, "pid": os.getpid()}
    assert not mine.exists() and other.exists()
