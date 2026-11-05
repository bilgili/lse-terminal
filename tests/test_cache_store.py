import json
import sqlite3
import threading

import pandas as pd
import pytest

from lse_terminal.contracts import CANDLE_COLUMNS
from lse_terminal.engine.cache_store import MemorySnapshots, SqliteStore

K = ("p", "SYM", "1m")


def _df(ts):
    return pd.DataFrame([(t, 1.0, 2.0, 0.5, 1.5, 10.0) for t in ts], columns=CANDLE_COLUMNS)


def test_write_page_then_read_is_ascending_and_half_open(tmp_path):
    st = SqliteStore(tmp_path / "c.sqlite")
    st.write_page(K, _df([180, 60, 120]), 60, 240, None, now=1000)
    got = st.read(K, 60, 180)
    assert list(got["ts"]) == [60, 120]
    assert list(got.columns) == CANDLE_COLUMNS


def test_adjacent_ranges_merge(tmp_path):
    st = SqliteStore(tmp_path / "c.sqlite")
    st.write_page(K, _df([0]), 0, 100, None, now=1000)
    st.write_page(K, _df([100]), 100, 200, None, now=1000)
    assert st.coverage(K, now=1000) == [(0, 200, None)]


def test_different_empty_until_do_not_merge_and_expired_drop_out(tmp_path):
    st = SqliteStore(tmp_path / "c.sqlite")
    st.write_page(K, _df([0]), 0, 100, None, now=1000)
    st.write_page(K, _df([]), 100, 200, 4600, now=1000)
    assert st.coverage(K, now=1000) == [(0, 100, None), (100, 200, 4600)]
    assert st.coverage(K, now=4600) == [(0, 100, None)]


def test_upsert_replaces_a_bar(tmp_path):
    st = SqliteStore(tmp_path / "c.sqlite")
    st.write_page(K, _df([60]), 60, 120, None, now=1000)
    d = _df([60]); d.loc[0, "close"] = 9.0
    st.write_page(K, d, 60, 120, None, now=1000)
    assert st.read(K, 0, 999)["close"].tolist() == [9.0]


def test_clear_and_keys(tmp_path):
    st = SqliteStore(tmp_path / "c.sqlite")
    st.write_page(K, _df([60]), 60, 120, None, now=1000)
    st.write_page(("q", "X", "1d"), _df([0]), 0, 86400, None, now=1000)
    assert sorted(st.keys()) == [K, ("q", "X", "1d")]
    st.clear(K)
    assert st.read(K, 0, 999).empty and st.coverage(K, now=1000) == []
    assert st.keys() == [("q", "X", "1d")]


def test_snapshot_round_trip_ttl_and_prune(tmp_path):
    st = SqliteStore(tmp_path / "c.sqlite")
    st.snapshot_put('["p","m"]', json.dumps([{"a": 1, "x": float("nan")}]), now=100, ttl=15)
    hit, body = st.snapshot_get('["p","m"]', now=114)
    assert hit and body[0]["a"] == 1 and body[0]["x"] != body[0]["x"]  # NaN survives
    assert st.snapshot_get('["p","m"]', now=115) == (False, None)
    st.snapshot_put('["p","n"]', '[]', now=200, ttl=15)  # prunes the expired row
    assert st.stats()["snapshots"] == 1


def test_snapshot_survives_reopen(tmp_path):
    SqliteStore(tmp_path / "c.sqlite").snapshot_put('["p","m"]', '[1]', now=100, ttl=60)
    assert SqliteStore(tmp_path / "c.sqlite").snapshot_get('["p","m"]', now=130) == (True, [1])


def test_snapshot_clear_by_provider(tmp_path):
    st = SqliteStore(tmp_path / "c.sqlite")
    st.snapshot_put('["p","m"]', '[1]', now=100, ttl=60)
    st.snapshot_put('["q","m"]', '[2]', now=100, ttl=60)
    st.snapshot_clear("p")
    assert st.snapshot_get('["p","m"]', now=101) == (False, None)
    assert st.snapshot_get('["q","m"]', now=101) == (True, [2])


def test_connections_are_per_thread(tmp_path):
    st = SqliteStore(tmp_path / "c.sqlite")
    errs = []

    def work(i):
        try:
            for j in range(20):
                st.write_page(("p", f"S{i}", "1m"), _df([j * 60]), j * 60, j * 60 + 60, None, now=1000)
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert errs == []
    assert len(st.keys()) == 4


def test_memory_snapshots_ttl_bound_and_clear():
    m = MemorySnapshots(bound=2)
    m.snapshot_put('["p","a"]', '1', now=0, ttl=10)
    m.snapshot_put('["p","b"]', '2', now=1, ttl=10)
    m.snapshot_put('["q","c"]', '3', now=2, ttl=10)   # evicts the oldest
    assert m.snapshot_get('["p","a"]', now=3) == (False, None)
    assert m.snapshot_get('["p","b"]', now=10) == (True, 2)
    assert m.snapshot_get('["p","b"]', now=11) == (False, None)
    m.snapshot_clear("q")
    assert m.snapshot_get('["q","c"]', now=3) == (False, None)


def test_same_start_different_kind_both_kept(tmp_path):
    st = SqliteStore(tmp_path / "c.sqlite")
    st.write_page(K, _df([]), 100, 200, 4600, now=1000)
    st.write_page(K, _df([100]), 100, 150, None, now=1000)
    assert st.coverage(K, now=1000) == [(100, 150, None), (100, 200, 4600)]


def test_nan_bar_values_round_trip(tmp_path):
    st = SqliteStore(tmp_path / "c.sqlite")
    d = _df([60]); d.loc[0, "volume"] = float("nan")
    st.write_page(K, d, 60, 120, None, now=1000)
    got = st.read(K, 0, 999)
    assert got["volume"].dtype == "float64" and got["volume"].isna().all()


def test_snapshot_clear_provider_name_with_like_wildcards(tmp_path):
    st = SqliteStore(tmp_path / "c.sqlite")
    st.snapshot_put('["a_b","m"]', '[1]', now=100, ttl=60)
    st.snapshot_put('["aXb","m"]', '[2]', now=100, ttl=60)
    st.snapshot_clear("a_b")
    assert st.snapshot_get('["a_b","m"]', now=101) == (False, None)
    assert st.snapshot_get('["aXb","m"]', now=101) == (True, [2])


def test_count_is_half_open_and_per_key(tmp_path):
    st = SqliteStore(tmp_path / "c.sqlite")
    st.write_page(K, _df([60, 120, 180]), 60, 240, None, now=1000)
    st.write_page(("p", "OTHER", "1m"), _df([60, 120]), 60, 180, None, now=1000)
    assert st.count(K, 60, 180) == 2
    assert st.count(K, 0, 2**62) == 3
    assert st.count(K, 240, 300) == 0


def test_memory_snapshot_get_is_a_fresh_copy():
    m = MemorySnapshots()
    m.snapshot_put('["p","a"]', json.dumps([{"a": 1}]), now=0, ttl=10)
    _, body = m.snapshot_get('["p","a"]', now=1)
    body[0]["a"] = 2
    assert m.snapshot_get('["p","a"]', now=1) == (True, [{"a": 1}])


def test_write_page_coerces_values_and_rejects_bad_ts(tmp_path):
    st = SqliteStore(tmp_path / "c.sqlite")
    d = _df([60, 120]).astype({"volume": object, "open": object})
    d["volume"] = None
    d.loc[0, "open"] = "x"
    st.write_page(K, d, 60, 180, None, now=1000)
    got = st.read(K, 0, 999)
    assert got["volume"].isna().all() and pd.isna(got["open"].iloc[0])
    assert got["open"].iloc[1] == 1.0
    for bad in (60.5, None, "x"):
        d = _df([60]).astype({"ts": object})
        d.loc[0, "ts"] = bad
        with pytest.raises(sqlite3.InterfaceError):
            st.write_page(("p", "B", "1m"), d, 0, 999, None, now=1000)
    assert st.coverage(("p", "B", "1m"), now=1000) == []
