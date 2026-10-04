import contextlib
import csv
import io
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from prompt_workflow import history
from prompt_workflow.config import Settings, user_data_dir
from prompt_workflow.history import HistoryError, HistoryStore, load_price_table

SRC = Path(history.__file__).resolve().parents[1]

# The allowlist, written out here on purpose: a column added to the schema without being
# added to this list (after a privacy review) fails test_schema_matches_allowlist.
ALLOWED = {
    "operations": [
        "id",
        "occurred_at_utc",
        "origin",
        "trigger_id",
        "kind",
        "profile_id",
        "outcome",
        "latency_ms",
        "app_version",
    ],
    "attempts": [
        "operation_id",
        "seq",
        "provider",
        "requested_model",
        "endpoint",
        "attempt",
        "status",
        "error_kind",
        "latency_ms",
        "cost_state",
        "returned_model",
        "returned_provider",
        "generation_id",
        "input_uncached",
        "cache_read",
        "cache_write",
        "output",
        "reasoning",
        "charged_amount",
        "charged_unit",
        "upstream_cost",
        "upstream_unit",
        "server_total_ms",
        "server_load_ms",
        "server_input_ms",
        "server_output_ms",
        "estimated_amount",
        "estimated_unit",
        "price_table_version",
    ],
}


@pytest.fixture
def store(tmp_path):
    return HistoryStore(tmp_path / "data" / "prompt-workflow" / history.DB_NAME)


def _op(**changes):
    return {
        "id": history.new_operation_id(),
        "origin": "espanso_managed",
        "trigger_id": "-i-",
        "kind": "improve",
        "profile_id": "default",
        "outcome": "ok",
        "latency_ms": 1234.5,
        **changes,
    }


def _attempt(**changes):
    return {
        "provider": "openrouter",
        "requested_model": "google/gemini-3.5-flash-lite",
        "endpoint": "remote",
        "attempt": 1,
        "status": 200,
        "error_kind": None,
        "latency_ms": 800.0,
        "cost_state": "reported",
        "input_uncached": 900,
        "cache_read": 100,
        "output": 300,
        "charged_amount": Decimal("0.000123456789012345678901"),
        "charged_unit": "credits",
        **changes,
    }


def _rows(store, table):
    with contextlib.closing(sqlite3.connect(store.path)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]  # noqa: S608


def _lost(store):
    return json.loads(store.lost_path.read_text("utf-8"))["lost_writes"]


# -- settings and paths --------------------------------------------------------------------


def test_history_is_on_by_default_with_a_year_of_retention():
    cfg = Settings.load()
    assert cfg.history is True
    assert cfg.history_retention_days == 365


def test_history_settings_parse_strictly(monkeypatch):
    monkeypatch.setenv("PROMPT_HISTORY", "off")
    with pytest.raises(ValueError, match="PROMPT_HISTORY must be true or false"):
        Settings.load()
    monkeypatch.setenv("PROMPT_HISTORY", "false")
    monkeypatch.setenv("PROMPT_HISTORY_RETENTION_DAYS", "0")
    with pytest.raises(ValueError, match="PROMPT_HISTORY_RETENTION_DAYS"):
        Settings.load()


def test_data_dir_is_per_device(tmp_path):
    if os.name == "nt":
        assert user_data_dir({"LOCALAPPDATA": str(tmp_path)}) == tmp_path / "prompt-workflow"
        assert user_data_dir({}).parent.name == "Local"
    else:
        assert user_data_dir({"XDG_DATA_HOME": str(tmp_path)}) == tmp_path / "prompt-workflow"
        assert user_data_dir({}) == Path.home() / ".local" / "share" / "prompt-workflow"


def test_from_settings_uses_the_data_and_config_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMPT_HISTORY", "false")
    monkeypatch.setenv("PROMPT_HISTORY_RETENTION_DAYS", "30")
    store = HistoryStore.from_settings(Settings.load())
    assert store.path == tmp_path / "data" / "prompt-workflow" / history.DB_NAME
    assert store.prices_path == tmp_path / "config" / "prompt-workflow" / history.PRICES_NAME
    assert (store.enabled, store.retention_days) == (False, 30)


def test_disabled_history_records_nothing(store):
    store.enabled = False
    assert store.record(_op(), [_attempt()]) is True
    assert not store.path.parent.exists()


def test_importing_the_cli_or_history_loads_no_sqlite():
    code = (
        "import sys, prompt_workflow.cli; bad = {'sqlite3', 'prompt_workflow.history'}; "
        "print(sorted(bad & sys.modules.keys()))\n"
        "import prompt_workflow.history; bad = {'sqlite3', 'tomllib', 'decimal'}; "
        "print(sorted(bad & sys.modules.keys()))"
    )
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60
    )
    assert result.stdout.split() == ["[]", "[]"], result.stderr


# -- schema and round trip -----------------------------------------------------------------


def test_schema_matches_allowlist(store):
    assert store.record(_op(), [_attempt()])
    with contextlib.closing(sqlite3.connect(store.path)) as conn:
        tables = {
            name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert tables == ALLOWED.keys()
        for table, columns in ALLOWED.items():
            found = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
            assert found == columns, table
        assert conn.execute("PRAGMA user_version").fetchone()[0] == history.SCHEMA_VERSION
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert list(history.OPERATION_COLUMNS) == ALLOWED["operations"]
    assert list(history.ATTEMPT_COLUMNS) == ALLOWED["attempts"]


def test_money_round_trips_exactly_with_its_unit(store):
    amount = Decimal("0.000123456789012345678901")
    upstream = Decimal("1E-9")
    assert store.record(
        _op(), [_attempt(upstream_cost=upstream, upstream_unit="USD"), _attempt(attempt=2)]
    )
    rows = _rows(store, "attempts")
    assert rows[0]["charged_amount"] == "0.000123456789012345678901"
    assert Decimal(rows[0]["charged_amount"]) == amount
    assert rows[0]["charged_unit"] == "credits"
    assert rows[0]["upstream_cost"] == "0.000000001"
    assert rows[0]["upstream_unit"] == "USD"
    with contextlib.closing(sqlite3.connect(store.path)) as conn:
        types = set(conn.execute("SELECT typeof(charged_amount) FROM attempts"))
    assert types == {("text",)}
    exported = io.StringIO()
    store.export(exported, "json")
    attempt = json.loads(exported.getvalue())["attempts"][0]
    assert (attempt["charged_amount"], attempt["charged_unit"]) == (str(amount), "credits")


def test_a_reported_zero_stays_zero_and_a_missing_cost_stays_unknown(store):
    free = _attempt(charged_amount=Decimal(0))
    missing = _attempt(charged_amount=None, charged_unit=None, cost_state="unknown")
    assert store.record(_op(), [free, missing])
    rows = _rows(store, "attempts")
    assert (rows[0]["charged_amount"], rows[0]["cost_state"]) == ("0", "reported")
    assert (rows[1]["charged_amount"], rows[1]["cost_state"]) == (None, "unknown")


def test_a_retried_write_never_duplicates(store):
    op = _op()
    attempts = [_attempt(), _attempt(attempt=2)]
    assert store.record(op, attempts)
    assert store.record(op, attempts)
    assert len(_rows(store, "operations")) == 1
    assert [(r["operation_id"], r["seq"]) for r in _rows(store, "attempts")] == [
        (op["id"], 1),
        (op["id"], 2),
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"id": "not-a-random-id"},
        {"origin": "somewhere"},
        {"kind": "rewrite"},
        {"outcome": "Failed with a long message"},
        {"occurred_at_utc": "2026-10-04"},
        {"occurred_at_utc": datetime(2026, 10, 4)},  # naive on purpose
    ],
)
def test_an_invalid_record_is_dropped_and_counted(store, change):
    assert store.record(_op(**change)) is False
    assert _lost(store) == 1


@pytest.mark.parametrize("change", [{"endpoint": "cloud"}, {"provider": "open router"}])
def test_an_invalid_attempt_drops_the_whole_record(store, change):
    assert store.record(_op(), [_attempt(), _attempt(**change)]) is False
    assert not store.path.exists() or _rows(store, "operations") == []


@pytest.mark.parametrize(
    "change",
    [
        {"charged_unit": None},
        {"charged_unit": "EUR"},
        {"charged_amount": Decimal(-1)},
        {"charged_amount": Decimal("NaN")},
        {"charged_amount": "lots"},
        {"charged_amount": [1]},
    ],
)
def test_a_bad_cost_is_unknown_and_the_record_is_kept(store, change):
    assert store.record(_op(), [_attempt(**change)])
    row = _rows(store, "attempts")[0]
    assert (row["charged_amount"], row["charged_unit"], row["cost_state"]) == (
        None,
        None,
        "unknown",
    )
    assert row["output"] == 300


def test_a_negative_zero_is_stored_as_zero(store):
    assert store.record(_op(), [_attempt(charged_amount=Decimal("-0.000"))])
    row = _rows(store, "attempts")[0]
    assert (row["charged_amount"], row["cost_state"]) == ("0.000", "reported")


def test_error_kinds_match_what_post_json_records():
    base = (SRC / "prompt_workflow" / "providers" / "base.py").read_text("utf-8")
    recorded = set(re.findall(r'error_kind(?: or)? = .*?"([a-z_0-9]+)"', base))
    assert recorded == set(history.ERROR_KINDS)


# -- privacy -------------------------------------------------------------------------------

KEY = "sk-or-v1-" + "0123456789abcdef" * 4
# Values that must never be stored in any identifier column.
HOSTILE = [
    "SENTINEL draft about the merger",
    "I am SENTINEL Head of Data",
    "/Users/SENTINEL/secret.txt",
    "C:\\Users\\SENTINEL\\secret.txt",
    "SENTINEL:p4ss@host",
    "SENTINEL@example.com",
    "SENTINEL" + "aB3xY9" * 6,  # a bare token
    "x/SENTINEL" + "Q7z!K2" * 4,  # one inside a model-like path
    '{"choices": "SENTINEL body"}',
    KEY,
]
OPERATION_IDENTS = ("trigger_id", "profile_id", "app_version")
ATTEMPT_IDENTS = (
    "provider",
    "requested_model",
    "returned_model",
    "returned_provider",
    "generation_id",
    "error_kind",
)
REQUIRED = {"provider", "requested_model"}


def _assert_nothing_leaked(store, *needles):
    stored = b"".join(p.read_bytes() for p in store.path.parent.iterdir() if p.is_file())
    as_json, as_csv = io.StringIO(), io.StringIO()
    store.export(as_json, "json")
    store.export(as_csv, "csv")
    texts = [as_json.getvalue(), as_csv.getvalue(), repr(store.health())]
    for needle in needles:
        assert needle.encode() not in stored
        assert all(needle not in text for text in texts)


def test_sentinels_never_reach_any_column_exports_or_health(store):
    unlisted = {
        "prompt": "SENTINEL draft",
        "output": "SENTINEL rewrite",
        "persona": "SENTINEL persona",
        "form": "SENTINEL-form-pick",
        "raw_body": "SENTINEL body",
        "api_key": KEY,
    }
    assert store.record(_op(**unlisted), [_attempt(**unlisted)])
    for value in HOSTILE:
        for column in OPERATION_IDENTS:
            assert store.record(_op(**{column: value}), [_attempt()]), (column, value)
        for column in ATTEMPT_IDENTS:
            stored = store.record(_op(), [_attempt(**{column: value})])
            assert stored is (column not in REQUIRED), (column, value)
    assert store.record(_op(latency_ms="SENTINEL"), [_attempt(latency_ms="SENTINEL")])
    _assert_nothing_leaked(store, "SENTINEL", KEY)
    for row in _rows(store, "operations")[1:]:
        assert {row["trigger_id"], row["profile_id"]} <= {"-i-", "default", None}


def test_extra_patterns_keep_a_slug_shaped_sentinel_out(store):
    # A slug is indistinguishable from a model or provider name by shape; the user's own
    # PROMPT_EXTRA_PATTERNS catch it, and an invalid pattern drops the write instead.
    store.extra_patterns = "sentinel"
    for column in (*OPERATION_IDENTS, *ATTEMPT_IDENTS):
        op = _op(**{column: "SENTINEL-form-pick"} if column in OPERATION_IDENTS else {})
        attempt = _attempt(**{column: "SENTINEL-form-pick"} if column in ATTEMPT_IDENTS else {})
        assert store.record(op, [attempt]) is (column not in REQUIRED)
    _assert_nothing_leaked(store, "SENTINEL")
    store.extra_patterns = "("
    assert store.record(_op(), [_attempt()]) is False


def test_real_identifiers_are_kept(store):
    models = [
        "google/gemini-3.5-flash-lite",
        "meta-llama/Llama-3.3-70B-Instruct:free",
        "hf.co/bartowski/Qwen2.5-7B-Instruct-GGUF:Q4_K_M",
        "qwen3:8b",
        "claude-sonnet-5",
    ]
    for model in models:
        assert store.record(
            _op(trigger_id="-iok-", profile_id="my-profile_2"),
            [
                _attempt(
                    requested_model=model,
                    returned_model=model,
                    returned_provider="Google",
                    generation_id="gen-1759-AbC123xyz",
                    error_kind="timeout",
                ),
                _attempt(provider="anthropic", generation_id="msg_01ABCdefGHI"),
            ],
        )
    rows = _rows(store, "attempts")
    assert [r["requested_model"] for r in rows[::2]] == models
    assert [r["returned_model"] for r in rows[::2]] == models
    assert {r["generation_id"] for r in rows} == {"gen-1759-AbC123xyz", "msg_01ABCdefGHI"}
    assert {r["error_kind"] for r in rows} == {"timeout", None}
    assert {r["returned_provider"] for r in rows} == {"Google", None}
    ops = _rows(store, "operations")
    assert {(r["trigger_id"], r["profile_id"]) for r in ops} == {("-iok-", "my-profile_2")}


# -- fail-open writer ----------------------------------------------------------------------


def test_a_locked_database_drops_the_write_within_the_budget(store):
    assert store.record(_op())
    with contextlib.closing(sqlite3.connect(store.path, isolation_level=None)) as other:
        other.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        assert store.record(_op()) is False
        # Generous bound: the budget is 0.25 s; this only catches a writer that blocks.
        assert time.monotonic() - started < 5
        other.execute("ROLLBACK")
    assert _lost(store) == 1
    health = store.health()
    assert (health.lost_writes, health.operations) == (1, 1)
    assert health.last_lost_utc is not None


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores modes")
def test_a_read_only_database_never_raises(store):
    assert store.record(_op())
    store.path.chmod(stat.S_IREAD)
    try:
        assert store.record(_op()) is False
    finally:
        store.path.chmod(stat.S_IREAD | stat.S_IWRITE)
    assert _lost(store) == 1


def test_a_corrupt_database_never_raises_and_reset_recovers(store):
    store.path.parent.mkdir(parents=True)
    store.path.write_bytes(b"this is not an sqlite database, just junk " * 200)
    assert store.record(_op()) is False
    health = store.health()
    assert health.error == "corrupt"
    assert health.lost_writes == 1
    with pytest.raises(HistoryError, match="corrupt"):
        store.stats()
    store.reset()
    assert not store.lost_path.exists()
    assert store.record(_op())
    assert store.health().operations == 1


def test_when_database_and_sidecar_both_fail_nothing_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    store = HistoryStore(blocker / "prompt-workflow" / history.DB_NAME)
    assert store.record(_op(), [_attempt()]) is False
    assert store._mark_lost() is False
    health = store.health()
    assert (health.exists, health.tracking_incomplete) == (False, True)


def test_a_database_from_a_newer_version_is_never_written(store):
    assert store.record(_op())
    with contextlib.closing(sqlite3.connect(store.path)) as conn:
        conn.execute("PRAGMA user_version = 99")
        conn.commit()
    assert store.record(_op()) is False
    with pytest.raises(HistoryError, match="newer"):
        store.stats()
    assert store.health().schema_version == 99


def test_migrations_run_forward(store, monkeypatch):
    assert store.record(_op())
    step = ("CREATE INDEX attempts_model ON attempts (requested_model)",)
    monkeypatch.setattr(history, "_MIGRATIONS", (*history._MIGRATIONS, step))
    monkeypatch.setattr(history, "SCHEMA_VERSION", history.SCHEMA_VERSION + 1)
    assert store.record(_op())
    with contextlib.closing(sqlite3.connect(store.path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == history.SCHEMA_VERSION
        indexes = {r[1] for r in conn.execute("PRAGMA index_list(attempts)")}
    assert "attempts_model" in indexes
    assert len(_rows(store, "operations")) == 2


def test_health_reports_without_creating_the_database(store):
    health = store.health()
    assert health.sqlite_version == sqlite3.sqlite_version
    assert (health.exists, health.schema_version, health.operations) == (False, None, None)
    assert not store.path.parent.exists()
    assert store.record(_op(), [_attempt(), _attempt(attempt=2)])
    health = store.health()
    assert (health.exists, health.schema_version) == (True, history.SCHEMA_VERSION)
    assert (health.operations, health.attempts, health.lost_writes) == (1, 2, 0)
    assert health.path == str(store.path)
    assert health.tracking_incomplete is False


def test_an_unreadable_sidecar_marks_tracking_incomplete(store):
    store.lost_path.parent.mkdir(parents=True)
    store.lost_path.write_text("{half a file", "utf-8")
    assert store.health().tracking_incomplete is True


# -- concurrency (separate processes, as Espanso runs one CLI per trigger) -----------------

_WRITER = """
import json, sys
from pathlib import Path
from prompt_workflow.history import HistoryStore
store = HistoryStore(Path(sys.argv[1]))
worker = int(sys.argv[2])
stored = []
for n in range({count}):
    op = {{"id": f"{{worker:016x}}{{n:016x}}", "origin": "direct", "kind": "improve",
          "outcome": "ok", "latency_ms": 1.0}}
    attempts = [{{"provider": "ollama", "requested_model": "qwen3:8b", "endpoint": "loopback",
                 "latency_ms": 1.0, "cost_state": "not_applicable"}}] * 2
    # The second write is a retry of the first: it must never add a row.
    if any([store.record(op, attempts), store.record(op, attempts)]):
        stored.append(op["id"])
print(json.dumps(stored))
"""


def _run_workers(tmp_path, script, args_for, workers=4):
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", script, *args_for(worker)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        for worker in range(workers)
    ]
    outputs = []
    for proc in procs:
        out, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err
        outputs.append(json.loads(out))
    return outputs


def test_concurrent_writers_complete_or_drop_without_duplicates(store, tmp_path):
    count = 15
    outputs = _run_workers(
        tmp_path,
        _WRITER.format(count=count),
        lambda worker: [str(store.path), str(worker)],
    )
    stored = {op_id for out in outputs for op_id in out}
    ops = _rows(store, "operations")
    assert {row["id"] for row in ops} == stored
    assert len(ops) == len(stored)
    keys = [(row["operation_id"], row["seq"]) for row in _rows(store, "attempts")]
    assert len(keys) == len(set(keys)) == 2 * len(stored)
    # Each write is one transaction: no operation without its attempts.
    assert {op_id for op_id, _ in keys} == stored
    # Most writes get through; the rest were dropped within the budget and counted.
    assert len(stored) > count
    if len(stored) < 4 * count:
        assert store.health().lost_writes > 0


_MARKER = """
import sys
from pathlib import Path
from prompt_workflow.history import HistoryStore
store = HistoryStore(Path(sys.argv[1]))
print(sum(store._mark_lost() for _ in range(25)))
"""


def test_the_sidecar_marker_is_updated_atomically(store, tmp_path):
    store.path.parent.mkdir(parents=True)
    outputs = _run_workers(tmp_path, _MARKER, lambda worker: [str(store.path)])
    # Every successful update is counted (no lost update), and the file is whole JSON.
    assert _lost(store) == sum(outputs) > 0
    assert sorted(p.name for p in store.path.parent.iterdir()) == [history.LOST_NAME]


# -- services ------------------------------------------------------------------------------


def _seed(store):
    day1 = datetime(2026, 10, 1, 9, tzinfo=UTC)
    day2 = datetime(2026, 10, 2, 9, tzinfo=UTC)
    store.prices_path = None
    assert store.record(
        _op(occurred_at_utc=day1, latency_ms=100.0),
        [
            _attempt(status=503, error_kind="non_2xx", charged_amount=None, charged_unit=None),
            _attempt(attempt=2, charged_amount=Decimal("0.001")),
        ],
    )
    assert store.record(
        _op(occurred_at_utc=day2, latency_ms=300.0),
        [_attempt(charged_amount=Decimal("0.002"), cache_read=None)],
    )
    assert store.record(
        _op(occurred_at_utc=day2, trigger_id="-ic-", latency_ms=200.0),
        [
            _attempt(
                provider="anthropic",
                requested_model="claude-sonnet-5",
                charged_amount=None,
                charged_unit=None,
                cost_state="unknown",
                cache_read=None,
            )
        ],
    )
    assert store.record(_op(occurred_at_utc=day2, trigger_id="-p-", kind="persona"))


def test_stats_by_trigger_keep_costs_apart(store):
    _seed(store)
    rows = {row.key: row for row in store.stats("trigger")}
    i = rows["-i-"]
    assert (i.operations, i.attempts) == (2, 3)
    assert i.reported == {"credits": Decimal("0.003")}
    assert i.estimated == {}
    assert i.unknown_cost_attempts == 1  # the 503 reported no cost: unknown, never 0
    assert (i.latency_p50_ms, i.latency_p95_ms) == (100.0, 300.0)
    assert i.tokens["output"] == 900
    assert i.tokens["cache_read"] == 200
    assert i.tokens["cache_write"] is None  # never reported: unknown, not 0
    assert i.last_used_utc.startswith("2026-10-02T09:00:00")
    ic = rows["-ic-"]
    assert (ic.reported, ic.unknown_cost_attempts) == ({}, 1)
    assert ic.tokens["cache_read"] is None
    p = rows["-p-"]
    assert (p.operations, p.attempts, p.tokens["output"]) == (1, 0, None)


def test_stats_by_provider_model_and_day(store):
    _seed(store)
    providers = {row.key: row for row in store.stats("provider")}
    assert set(providers) == {"openrouter", "anthropic"}  # persona made no request
    assert (providers["openrouter"].operations, providers["openrouter"].attempts) == (2, 3)
    assert providers["openrouter"].latency_p50_ms == 800.0
    models = {row.key for row in store.stats("model")}
    assert models == {"google/gemini-3.5-flash-lite", "claude-sonnet-5"}
    days = store.stats("day")
    assert [row.key for row in days] == ["2026-10-02", "2026-10-01"]
    assert days[0].operations == 3
    with pytest.raises(ValueError, match="group_by"):
        store.stats("profile")


def test_stats_and_export_of_no_history_are_empty(store):
    assert store.stats() == []
    out = io.StringIO()
    assert store.export(out, "json") == 0
    assert json.loads(out.getvalue())["operations"] == []
    assert store.prune() == 0
    assert not store.path.exists()


def test_export_csv_is_one_row_per_attempt(store):
    _seed(store)
    out = io.StringIO()
    assert store.export(out, "csv") == 4
    rows = list(csv.DictReader(io.StringIO(out.getvalue())))
    assert len(rows) == 5  # 2 + 1 + 1 attempts, and the persona with none
    assert rows[0]["operation_trigger_id"] == "-i-"
    assert rows[1]["charged_amount"] == "0.001"
    assert rows[1]["charged_unit"] == "credits"
    persona = [row for row in rows if row["operation_kind"] == "persona"]
    assert [row["provider"] for row in persona] == [""]
    assert set(rows[0]) == {
        *(f"operation_{c}" for c in ALLOWED["operations"]),
        *ALLOWED["attempts"][1:],
    }
    with pytest.raises(ValueError, match="csv or json"):
        store.export(io.StringIO(), "xml")  # type: ignore[arg-type]


def test_prune_deletes_old_operations_and_their_attempts(store):
    now = datetime.now(UTC)
    old = _op(occurred_at_utc=now - timedelta(days=400))
    assert store.record(old, [_attempt()])
    assert store.record(_op(occurred_at_utc=now - timedelta(days=10)), [_attempt()])
    assert store.prune() == 1  # retention_days defaults to 365
    assert [r["operation_id"] for r in _rows(store, "attempts")] != [old["id"]]
    assert len(_rows(store, "attempts")) == 1
    assert store.prune(older_than=timedelta(days=5)) == 1
    assert store.health().operations == 0
    with pytest.raises(ValueError, match="negative"):
        store.prune(older_than=timedelta(days=-1))


def test_reset_deletes_every_record_and_the_marker(store):
    _seed(store)
    store._mark_lost()
    store.reset()
    health = store.health()
    assert (health.operations, health.attempts, health.lost_writes) == (0, 0, 0)
    store.reset()  # nothing left to reset
    # Every connection is closed: the files can be deleted (Windows refuses an open one).
    for path in store.path.parent.iterdir():
        path.unlink()


# -- estimated costs (D-HIST-1) ------------------------------------------------------------

PRICES = """
version = "2026-10-01"
unit = "USD"
[models."claude-sonnet-5"]
input_uncached = 3.00
cache_read = 0.30
cache_write = 3.75
output = 15
"""


def test_estimates_come_only_from_a_price_table(store, tmp_path):
    unknown = {"charged_amount": None, "charged_unit": None, "cost_state": "unknown"}
    sonnet = _attempt(
        provider="anthropic",
        requested_model="claude-sonnet-5",
        input_uncached=1000,
        cache_read=2000,
        cache_write=None,
        output=500,
        reasoning=400,
        **unknown,
    )
    assert store.record(_op(), [sonnet])  # no table: unknown
    store.prices_path = tmp_path / "prices.toml"
    store.prices_path.write_text(PRICES, "utf-8")
    store._prices_loaded = False
    assert store.record(
        _op(),
        [
            sonnet,
            _attempt(),  # reported: never estimated
            _attempt(requested_model="unlisted", **unknown),
            _attempt(provider="ollama", cost_state="not_applicable", charged_amount=None),
        ],
    )
    rows = _rows(store, "attempts")
    assert (rows[0]["cost_state"], rows[0]["estimated_amount"]) == ("unknown", None)
    estimate = rows[1]
    # 1000 * 3 + 2000 * 0.30 + 500 * 15 per million tokens; reasoning is part of output.
    assert Decimal(estimate["estimated_amount"]) == Decimal("0.0111")
    assert (estimate["cost_state"], estimate["estimated_unit"]) == ("estimated", "USD")
    assert estimate["price_table_version"] == "2026-10-01"
    assert estimate["charged_amount"] is None
    assert [r["estimated_amount"] for r in rows[2:]] == [None, None, None]
    assert [r["cost_state"] for r in rows[2:]] == ["reported", "unknown", "not_applicable"]
    row = next(r for r in store.stats("trigger"))
    assert row.estimated == {"USD": Decimal("0.0111")}
    assert row.reported == {"credits": _attempt()["charged_amount"]}


def test_a_count_without_a_price_gets_no_estimate(tmp_path):
    path = tmp_path / "prices.toml"
    path.write_text(PRICES.replace("cache_write = 3.75\n", ""), "utf-8")
    table = load_price_table(path)
    assert table.estimate("claude-sonnet-5", {"input_uncached": 1, "cache_write": 1}) is None
    assert table.estimate("claude-sonnet-5", {"output": None}) is None
    assert table.estimate("claude-sonnet-5", {"output": 1_000_000}) == ("15", "USD", "2026-10-01")


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("version = ", "not valid TOML"),
        ('unit = "USD"\n[models.m]\noutput = 1', "needs a version"),
        ('version = "1"\nunit = "EUR"\n[models.m]\noutput = 1', "unit"),
        ('version = "1"\nunit = "USD"', "models"),
        ('version = "1"\nunit = "USD"\n[models.m]\ntokens = 1', "unknown price"),
        ('version = "1"\nunit = "USD"\n[models.m]\noutput = -1', "0 or more"),
        ('version = "1"\nunit = "USD"\n[models.m]\noutput = "cheap"', "0 or more"),
        ('version = "1"\nunit = "USD"\nmodels = {m = 1}', "must be a table"),
    ],
)
def test_a_malformed_price_table_is_reported(tmp_path, text, error):
    path = tmp_path / "prices.toml"
    path.write_text(text, "utf-8")
    with pytest.raises(ValueError, match=error):
        load_price_table(path)


def test_a_broken_price_table_never_stops_a_write(store, tmp_path):
    assert load_price_table(tmp_path / "missing.toml") is None
    store.prices_path = tmp_path / "prices.toml"
    store.prices_path.write_text("version = ", "utf-8")
    unknown = _attempt(charged_amount=None, charged_unit=None, cost_state="unknown")
    assert store.record(_op(), [unknown])
    assert _rows(store, "attempts")[0]["cost_state"] == "unknown"


def test_a_stale_sidecar_lock_is_broken_and_a_held_one_times_out(store):
    store.path.parent.mkdir(parents=True)
    lock = store.lost_path.with_name(f"{history.LOST_NAME}.lock")
    lock.touch()
    old = time.time() - 60
    os.utime(lock, (old, old))
    assert store._mark_lost() is True  # left by a killed process
    lock.touch()
    assert store._mark_lost() is False  # held: give up within the lock budget
    assert _lost(store) == 1
    lock.unlink()


def test_a_service_migrates_an_empty_database(store):
    store.path.parent.mkdir(parents=True)
    with contextlib.closing(sqlite3.connect(store.path)) as conn:
        conn.execute("PRAGMA user_version = 0")
    assert store.stats() == []
    assert store.health().schema_version == history.SCHEMA_VERSION


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ("file is not a database", "corrupt"),
        ("database disk image is malformed", "corrupt"),
        ("database is locked", "locked"),
        ("attempt to write a readonly database", "read-only"),
        ("disk I/O error", "unreadable"),
    ],
)
def test_database_errors_become_fixed_words(message, kind):
    assert history._error_kind(sqlite3.DatabaseError(message)) == kind


def test_reset_never_deletes_a_database_that_is_only_locked(store, monkeypatch):
    monkeypatch.setattr(history, "_SERVICE_TIMEOUT", 0.05)
    assert store.record(_op())
    with contextlib.closing(sqlite3.connect(store.path, isolation_level=None)) as other:
        other.execute("BEGIN IMMEDIATE")
        with pytest.raises(HistoryError, match="locked") as caught:
            store.reset()
        assert not isinstance(caught.value, history.UnusableHistory)
        other.execute("ROLLBACK")
    assert store.path.exists()
    assert store.health().operations == 1


@pytest.mark.parametrize("text", ["[]", "5", '"x"', '{"lost_writes": true}', "\xff"])
def test_a_malformed_sidecar_never_breaks_health_and_is_rewritten(store, text):
    store.lost_path.parent.mkdir(parents=True)
    store.lost_path.write_text(text, "latin-1")
    assert store.health().tracking_incomplete is True
    assert store._mark_lost() is True
    assert _lost(store) == 1


@pytest.mark.skipif(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="needs POSIX directory modes, which root ignores",
)
def test_health_shows_a_store_that_cannot_be_written(store):
    assert store.record(_op())
    assert store.health().writable is True
    store.path.chmod(stat.S_IREAD)
    store.path.parent.chmod(stat.S_IREAD | stat.S_IEXEC)
    try:
        assert store.record(_op()) is False  # and the sidecar cannot be written either
        health = store.health()
    finally:
        store.path.parent.chmod(stat.S_IRWXU)
        store.path.chmod(stat.S_IREAD | stat.S_IWRITE)
    assert not store.lost_path.exists()
    assert (health.writable, health.tracking_incomplete, health.lost_writes) == (False, True, 0)


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores modes")
def test_health_shows_a_read_only_database(store):
    assert store.record(_op())
    store.path.chmod(stat.S_IREAD)
    try:
        health = store.health()
    finally:
        store.path.chmod(stat.S_IREAD | stat.S_IWRITE)
    assert (health.writable, health.tracking_incomplete) == (False, True)


def test_retention_is_capped_and_prune_never_overflows(store, monkeypatch):
    monkeypatch.setenv("PROMPT_HISTORY_RETENTION_DAYS", "36501")
    with pytest.raises(ValueError, match="PROMPT_HISTORY_RETENTION_DAYS must be a whole number"):
        Settings.load()
    monkeypatch.setenv("PROMPT_HISTORY_RETENTION_DAYS", "36500")
    assert Settings.load().history_retention_days == 36500
    assert store.record(_op())
    assert store.prune(older_than=timedelta(days=10**8)) == 0
    store.retention_days = 10**8
    assert store.prune() == 0


def test_a_fresh_lock_is_never_broken_and_only_the_holder_removes_it(store):
    store.path.parent.mkdir(parents=True)
    lock = store.lost_path.with_name(f"{history.LOST_NAME}.lock")
    lock.write_text("someone else", "ascii")
    assert history._break_stale(lock) is False
    assert lock.read_text("ascii") == "someone else"
    lock.unlink()
    with store._sidecar_lock(time.monotonic() + 1):
        # Another process broke our lock as stale and took its own.
        lock.write_text("someone else", "ascii")
    assert lock.read_text("ascii") == "someone else"
    lock.unlink()


def test_a_write_blocked_everywhere_gives_up(store):
    assert store.record(_op())
    lock = store.lost_path.with_name(f"{history.LOST_NAME}.lock")
    lock.write_text("held", "ascii")
    with contextlib.closing(sqlite3.connect(store.path, isolation_level=None)) as other:
        other.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        assert store.record(_op()) is False
        # Generous bound; _BUDGET (0.25 s) is checked by reading the code, not the clock.
        assert time.monotonic() - started < 5
        other.execute("ROLLBACK")
    lock.unlink()
    assert not store.lost_path.exists()
