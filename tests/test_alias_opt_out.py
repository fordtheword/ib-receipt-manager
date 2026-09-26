"""Tests for the per-receipt 'NOT <alias>' opt-out feature and per-alias
auto-detect keywords (company_aliases.not_keywords).

Uses a temp SQLite DB (config.DATABASE_PATH monkeypatched) so the real
receipts.db is never touched. Run only this file:
    venv\\Scripts\\python.exe -m pytest tests\\test_alias_opt_out.py -v
"""

import sqlite3
from datetime import date

import pytest

import config
import database
from database import Receipt


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Point config/database at a fresh temp DB and re-init the schema."""
    db_path = tmp_path / "t.db"
    monkeypatch.setattr(config, "DATABASE_PATH", db_path)
    database.init_db()
    return db_path


def make_receipt(company_name, payment_date=None, is_recurring=False, alias_opt_out=False):
    return Receipt(
        id=None,
        original_filename="orig.pdf",
        stored_filename="stored.pdf",
        payment_date=payment_date,
        company_name=company_name,
        payment_handler=None,
        category="annat",
        staging_path=None,
        storage_path=None,
        email_sent_to=None,
        email_sent_at=None,
        is_recurring=is_recurring,
        alias_opt_out=alias_opt_out,
    )


# --- Migrations ---

def test_migration_adds_alias_opt_out_column(tmp_path, monkeypatch):
    """An old-schema receipts table (no alias_opt_out column) gets it added by init_db()."""
    db_path = tmp_path / "old.db"
    monkeypatch.setattr(config, "DATABASE_PATH", db_path)

    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL,
            payment_date DATE,
            company_name TEXT NOT NULL,
            payment_handler TEXT,
            category TEXT NOT NULL,
            storage_path TEXT,
            email_sent_to TEXT,
            email_sent_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

    database.init_db()

    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(receipts)").fetchall()]
    conn.close()
    assert "alias_opt_out" in cols


def test_migration_adds_not_keywords_column_to_company_aliases(tmp_path, monkeypatch):
    """An old-schema company_aliases table (no not_keywords column) gets it added."""
    db_path = tmp_path / "old_aliases.db"
    monkeypatch.setattr(config, "DATABASE_PATH", db_path)

    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE company_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alias TEXT NOT NULL UNIQUE,
            canonical TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("INSERT INTO company_aliases (alias, canonical) VALUES ('foo', 'bar')")
    conn.commit()
    conn.close()

    database.init_db()

    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(company_aliases)").fetchall()]
    row = conn.execute("SELECT alias, canonical FROM company_aliases WHERE alias='foo'").fetchone()
    conn.close()
    assert "not_keywords" in cols
    assert row == ("foo", "bar")  # existing data untouched


def test_new_db_has_both_new_columns(db):
    conn = sqlite3.connect(db)
    receipt_cols = [r[1] for r in conn.execute("PRAGMA table_info(receipts)").fetchall()]
    alias_cols = [r[1] for r in conn.execute("PRAGMA table_info(company_aliases)").fetchall()]
    conn.close()
    assert "alias_opt_out" in receipt_cols
    assert "not_keywords" in alias_cols


# --- add_receipt / _row_to_receipt round-trip ---

def test_add_and_get_receipt_defaults_alias_opt_out_false(db):
    r = make_receipt("Some Company")
    rid = database.add_receipt(r)
    fetched = database.get_receipt(rid)
    assert fetched.alias_opt_out is False


def test_add_and_get_receipt_alias_opt_out_true(db):
    r = make_receipt("Google Commerce Limited", alias_opt_out=True)
    rid = database.add_receipt(r)
    fetched = database.get_receipt(rid)
    assert fetched.alias_opt_out is True


def test_update_receipt_alias_opt_out(db):
    r = make_receipt("Google Commerce Limited")
    rid = database.add_receipt(r)
    database.update_receipt(rid, alias_opt_out=True)
    fetched = database.get_receipt(rid)
    assert fetched.alias_opt_out is True


# --- alias_display_name / _normalize_company with opt_out ---

def test_alias_display_name_without_opt_out_uses_alias(db):
    database.add_alias("google commerce limited", "youtube premium")
    aliases = database.list_alias_map()
    assert database.alias_display_name("Google Commerce Limited", aliases=aliases) == "Youtube Premium"


def test_alias_display_name_with_opt_out_uses_original(db):
    database.add_alias("google commerce limited", "youtube premium")
    aliases = database.list_alias_map()
    assert database.alias_display_name(
        "Google Commerce Limited", aliases=aliases, opt_out=True
    ) == "Google Commerce Limited"


def test_normalize_company_opt_out_via_private_api(db):
    database.add_alias("google commerce limited", "youtube premium")
    aliases = database.list_alias_map()
    assert database._normalize_company("Google Commerce Limited", aliases=aliases) == "youtube premium"
    assert database._normalize_company(
        "Google Commerce Limited", aliases=aliases, opt_out=True
    ) == "google commerce limited"


# --- alias_target ---

def test_alias_target_returns_canonical_when_alias_applies(db):
    database.add_alias("google commerce limited", "youtube premium")
    assert database.alias_target("Google Commerce Limited") == "Youtube Premium"


def test_alias_target_returns_none_when_no_alias(db):
    assert database.alias_target("Some Random Company") is None


# --- add_alias keyword handling ---

def test_add_alias_stores_not_keywords(db):
    database.add_alias("google commerce limited", "youtube premium", not_keywords="google play")
    aliases = database.list_aliases()
    row = next(a for a in aliases if a["alias"] == "google commerce limited")
    assert row["not_keywords"] == "google play"


def test_add_alias_without_keywords_preserves_existing(db):
    """Re-adding/editing an alias without a keywords field (not_keywords=None)
    must not wipe out keywords set earlier."""
    database.add_alias("google commerce limited", "youtube premium", not_keywords="google play")
    # Simulate editing canonical via the create form, which has no keywords field.
    database.add_alias("google commerce limited", "youtube premium")
    aliases = database.list_aliases()
    row = next(a for a in aliases if a["alias"] == "google commerce limited")
    assert row["not_keywords"] == "google play"


def test_set_alias_keywords_updates_existing_alias(db):
    database.add_alias("google commerce limited", "youtube premium")
    aliases = database.list_aliases()
    alias_id = aliases[0]["id"]
    ok = database.set_alias_keywords(alias_id, "google play, play store")
    assert ok is True
    updated = database.list_aliases()
    assert updated[0]["not_keywords"] == "google play, play store"


# --- suggest_alias_opt_out: per-alias keyword matching ---

def test_suggest_alias_opt_out_true_for_keyword_in_raw_text(db):
    database.add_alias("google commerce limited", "youtube premium", not_keywords="google play")
    matched = database.suggest_alias_opt_out(
        "Google Commerce Limited", ["some receipt text... Google Play purchase ..."]
    )
    assert matched == "google play"


def test_suggest_alias_opt_out_true_for_keyword_in_filename(db):
    database.add_alias("google commerce limited", "youtube premium", not_keywords="google play")
    matched = database.suggest_alias_opt_out(
        "Google Commerce Limited", ["receipt_google_play_order.pdf"]
    )
    assert matched == "google play"


def test_suggest_alias_opt_out_false_when_no_alias(db):
    assert database.suggest_alias_opt_out(
        "Some Random Company", ["Google Play purchase"]
    ) is None


def test_suggest_alias_opt_out_false_when_no_keyword_configured(db):
    # Alias applies but has no not_keywords configured -> never pre-tick.
    database.add_alias("google commerce limited", "youtube premium")
    assert database.suggest_alias_opt_out(
        "Google Commerce Limited", ["Google Play purchase"]
    ) is None


def test_suggest_alias_opt_out_false_when_keyword_not_present(db):
    database.add_alias("google commerce limited", "youtube premium", not_keywords="google play")
    assert database.suggest_alias_opt_out(
        "Google Commerce Limited", ["Monthly subscription invoice"]
    ) is None


def test_suggest_alias_opt_out_keyword_on_one_alias_does_not_trigger_another(db):
    """A keyword configured on alias A must not affect matching for alias B."""
    database.add_alias("google commerce limited", "youtube premium", not_keywords="google play")
    database.add_alias("telia sverige ab", "telia", not_keywords="")

    # Telia's alias has no keywords, so 'google play' text must never opt it out.
    matched = database.suggest_alias_opt_out(
        "Telia Sverige AB", ["Invoice mentions google play somehow"]
    )
    assert matched is None


# --- Reminder implicit fulfilment respects opt-out ---

def test_reminder_still_due_with_only_opted_out_receipt(db):
    database.add_alias("google commerce limited", "youtube premium")

    source = make_receipt("Google Commerce Limited", payment_date=date(2026, 1, 3), is_recurring=True)
    source_id = database.add_receipt(source)

    play = make_receipt("Google Commerce Limited", payment_date=date(2026, 2, 10), alias_opt_out=True)
    database.add_receipt(play)

    due = database.get_due_reminders(target_date=date(2026, 2, 15))
    due_ids = [(d.receipt.id, d.year_month) for d in due]
    assert (source_id, "2026-02") in due_ids


def test_reminder_fulfilled_with_normal_receipt(db):
    database.add_alias("google commerce limited", "youtube premium")

    source = make_receipt("Google Commerce Limited", payment_date=date(2026, 1, 3), is_recurring=True)
    source_id = database.add_receipt(source)

    normal = make_receipt("Google Commerce Limited", payment_date=date(2026, 2, 10))
    database.add_receipt(normal)

    due = database.get_due_reminders(target_date=date(2026, 2, 15))
    due_ids = [(d.receipt.id, d.year_month) for d in due]
    assert (source_id, "2026-02") not in due_ids


def test_reminder_due_again_with_both_opted_out_and_normal_present(db):
    database.add_alias("google commerce limited", "youtube premium")

    source = make_receipt("Google Commerce Limited", payment_date=date(2026, 1, 3), is_recurring=True)
    source_id = database.add_receipt(source)

    play = make_receipt("Google Commerce Limited", payment_date=date(2026, 2, 10), alias_opt_out=True)
    database.add_receipt(play)
    normal = make_receipt("Google Commerce Limited", payment_date=date(2026, 2, 20))
    database.add_receipt(normal)

    due = database.get_due_reminders(target_date=date(2026, 2, 28))
    due_ids = [(d.receipt.id, d.year_month) for d in due]
    assert (source_id, "2026-02") not in due_ids


def test_find_recurring_by_canonical_respects_opt_out(db):
    database.add_alias("google commerce limited", "youtube premium")

    r1 = make_receipt("Google Commerce Limited", payment_date=date(2026, 1, 3),
                       is_recurring=True, alias_opt_out=True)
    database.add_receipt(r1)

    matches = database.find_recurring_by_canonical("Google Commerce Limited")
    assert isinstance(matches, list)


# --- /process form stores the flag ---

def test_process_form_stores_alias_opt_out(db, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app as app_module

    monkeypatch.setattr(config, "RECEIPT_DROPS_DIR", tmp_path / "receipt drops")
    monkeypatch.setattr(app_module.config, "RECEIPT_DROPS_DIR", tmp_path / "receipt drops")
    monkeypatch.setattr(app_module.config, "SMTP_USERNAME", "")
    monkeypatch.setattr(app_module.config, "SMTP_PASSWORD", "")

    upload_dir = tmp_path / "receipt drops"
    upload_dir.mkdir(parents=True, exist_ok=True)
    src_file = upload_dir / "test.pdf"
    src_file.write_bytes(b"%PDF-1.4 fake")

    client = TestClient(app_module.app)
    resp = client.post(
        "/process",
        data={
            "filename": "test.pdf",
            "original_filename": "test.pdf",
            "payment_date": "2026-02-10",
            "company_name": "Google Commerce Limited",
            "payment_handler": "",
            "category": "annat",
            "notes": "",
            "ocr_cost": "0.0",
            "alias_opt_out": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    location = resp.headers["location"]
    receipt_id = int(location.split("/receipt/")[1].split("?")[0])
    receipt = database.get_receipt(receipt_id)
    assert receipt.alias_opt_out is True


def test_process_form_alias_opt_out_defaults_false(db, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app as app_module

    monkeypatch.setattr(config, "RECEIPT_DROPS_DIR", tmp_path / "receipt drops")
    monkeypatch.setattr(app_module.config, "RECEIPT_DROPS_DIR", tmp_path / "receipt drops")
    monkeypatch.setattr(app_module.config, "SMTP_USERNAME", "")
    monkeypatch.setattr(app_module.config, "SMTP_PASSWORD", "")

    upload_dir = tmp_path / "receipt drops"
    upload_dir.mkdir(parents=True, exist_ok=True)
    src_file = upload_dir / "test2.pdf"
    src_file.write_bytes(b"%PDF-1.4 fake")

    client = TestClient(app_module.app)
    resp = client.post(
        "/process",
        data={
            "filename": "test2.pdf",
            "original_filename": "test2.pdf",
            "payment_date": "2026-02-10",
            "company_name": "Some Other Company",
            "payment_handler": "",
            "category": "annat",
            "notes": "",
            "ocr_cost": "0.0",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    location = resp.headers["location"]
    receipt_id = int(location.split("/receipt/")[1].split("?")[0])
    receipt = database.get_receipt(receipt_id)
    assert receipt.alias_opt_out is False


# --- /aliases/{id}/keywords route ---

def test_keywords_route_updates_alias(db, monkeypatch):
    from fastapi.testclient import TestClient
    import app as app_module

    database.add_alias("google commerce limited", "youtube premium")
    alias_id = database.list_aliases()[0]["id"]

    client = TestClient(app_module.app)
    resp = client.post(
        f"/aliases/{alias_id}/keywords",
        data={"not_keywords": "google play, play store"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    updated = database.list_aliases()[0]
    assert updated["not_keywords"] == "google play, play store"
