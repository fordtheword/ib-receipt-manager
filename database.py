"""SQLite database operations for Receipt Manager."""

import sqlite3
from datetime import datetime, date
from dataclasses import dataclass
from pathlib import Path

import config


@dataclass
class Receipt:
    """Receipt record from database."""
    id: int | None
    original_filename: str
    stored_filename: str
    payment_date: date | None
    company_name: str
    payment_handler: str | None  # Klarna, Avarda, etc.
    category: str
    staging_path: str | None  # Path in local staging folder
    dropbox_path: str | None
    email_sent_to: str | None
    email_sent_at: datetime | None
    notes: str | None = None  # Custom message for email
    ocr_cost: float | None = None  # API cost in USD
    is_recurring: bool = False  # Monthly reminder enabled
    created_at: datetime | None = None


@dataclass
class Attachment:
    """Extra attachment for a receipt."""
    id: int | None
    receipt_id: int
    original_filename: str
    stored_filename: str
    file_path: str
    created_at: datetime | None = None


def get_connection() -> sqlite3.Connection:
    """Get database connection with row factory."""
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize database schema."""
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL,
            payment_date DATE,
            company_name TEXT NOT NULL,
            payment_handler TEXT,
            category TEXT NOT NULL,
            dropbox_path TEXT,
            email_sent_to TEXT,
            email_sent_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_id INTEGER NOT NULL,
            original_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (receipt_id) REFERENCES receipts(id) ON DELETE CASCADE
        )
    """)
    # Add payment_handler column if it doesn't exist (migration)
    try:
        conn.execute("ALTER TABLE receipts ADD COLUMN payment_handler TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Add notes column if it doesn't exist (migration)
    try:
        conn.execute("ALTER TABLE receipts ADD COLUMN notes TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Add staging_path column if it doesn't exist (migration)
    try:
        conn.execute("ALTER TABLE receipts ADD COLUMN staging_path TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Add ocr_cost column if it doesn't exist (migration)
    try:
        conn.execute("ALTER TABLE receipts ADD COLUMN ocr_cost REAL")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Add is_recurring column if it doesn't exist (migration)
    try:
        conn.execute("ALTER TABLE receipts ADD COLUMN is_recurring INTEGER DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Create reminder_dismissals table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reminder_dismissals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_id INTEGER NOT NULL,
            year_month TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'done',
            fulfilled_receipt_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (receipt_id) REFERENCES receipts(id) ON DELETE CASCADE,
            FOREIGN KEY (fulfilled_receipt_id) REFERENCES receipts(id) ON DELETE SET NULL,
            UNIQUE(receipt_id, year_month)
        )
    """)

    # Create settings table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Company aliases: collapse OCR variations onto a single canonical key so
    # 'Google' and 'Google Cloud EMEA Limited' (or 'Apple' and 'Apple Distribution
    # International Ltd.') can be treated as the same vendor for reminder matching.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS company_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alias TEXT NOT NULL UNIQUE,
            canonical TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def add_receipt(receipt: Receipt) -> int:
    """Add a new receipt to the database. Returns the new ID."""
    conn = get_connection()
    cursor = conn.execute("""
        INSERT INTO receipts (
            original_filename, stored_filename, payment_date, company_name,
            payment_handler, category, staging_path, dropbox_path, email_sent_to, email_sent_at, notes, ocr_cost
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        receipt.original_filename,
        receipt.stored_filename,
        receipt.payment_date.isoformat() if receipt.payment_date else None,
        receipt.company_name,
        receipt.payment_handler,
        receipt.category,
        receipt.staging_path,
        receipt.dropbox_path,
        receipt.email_sent_to,
        receipt.email_sent_at.isoformat() if receipt.email_sent_at else None,
        receipt.notes,
        receipt.ocr_cost,
    ))
    conn.commit()
    receipt_id = cursor.lastrowid
    conn.close()
    return receipt_id


def get_receipt(receipt_id: int) -> Receipt | None:
    """Get a receipt by ID."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM receipts WHERE id = ?", (receipt_id,)
    ).fetchone()
    conn.close()

    if row:
        return _row_to_receipt(row)
    return None


def get_all_receipts(
    limit: int = 100,
    offset: int = 0,
    category: str | None = None,
    company: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    email_status: str | None = None,
    year: int | None = None,
) -> list[Receipt]:
    """Get receipts with optional filters."""
    conn = get_connection()

    query = "SELECT * FROM receipts WHERE 1=1"
    params = []

    if category:
        query += " AND category = ?"
        params.append(category)

    if company:
        query += " AND company_name LIKE ?"
        params.append(f"%{company}%")

    if date_from:
        query += " AND payment_date >= ?"
        params.append(date_from.isoformat())

    if date_to:
        query += " AND payment_date <= ?"
        params.append(date_to.isoformat())

    if email_status == "not_sent":
        query += " AND email_sent_at IS NULL"
    elif email_status == "sent":
        query += " AND email_sent_at IS NOT NULL"

    if year:
        query += " AND strftime('%Y', payment_date) = ?"
        params.append(str(year))

    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = conn.execute(query, params).fetchall()
    conn.close()

    return [_row_to_receipt(row) for row in rows]


def update_receipt(receipt_id: int, **fields) -> bool:
    """Update specific fields of a receipt."""
    if not fields:
        return False

    # Handle date serialization
    if 'payment_date' in fields and fields['payment_date']:
        fields['payment_date'] = fields['payment_date'].isoformat()
    if 'email_sent_at' in fields and fields['email_sent_at']:
        fields['email_sent_at'] = fields['email_sent_at'].isoformat()

    set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
    values = list(fields.values()) + [receipt_id]

    conn = get_connection()
    cursor = conn.execute(
        f"UPDATE receipts SET {set_clause} WHERE id = ?",
        values
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated


def delete_receipt(receipt_id: int) -> bool:
    """Delete a receipt by ID."""
    conn = get_connection()
    cursor = conn.execute("DELETE FROM receipts WHERE id = ?", (receipt_id,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def get_receipt_count(
    category: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    email_status: str | None = None,
    year: int | None = None,
) -> int:
    """Get total count of receipts with optional filters."""
    conn = get_connection()

    query = "SELECT COUNT(*) FROM receipts WHERE 1=1"
    params = []

    if category:
        query += " AND category = ?"
        params.append(category)

    if date_from:
        query += " AND payment_date >= ?"
        params.append(date_from.isoformat())

    if date_to:
        query += " AND payment_date <= ?"
        params.append(date_to.isoformat())

    if email_status == "not_sent":
        query += " AND email_sent_at IS NULL"
    elif email_status == "sent":
        query += " AND email_sent_at IS NOT NULL"

    if year:
        query += " AND strftime('%Y', payment_date) = ?"
        params.append(str(year))

    count = conn.execute(query, params).fetchone()[0]
    conn.close()
    return count


def get_available_years() -> list[int]:
    """Get list of years that have receipts."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT DISTINCT strftime('%Y', payment_date) as year
        FROM receipts
        WHERE payment_date IS NOT NULL
        ORDER BY year DESC
    """).fetchall()
    conn.close()
    return [int(row[0]) for row in rows if row[0]]


def _row_to_receipt(row: sqlite3.Row) -> Receipt:
    """Convert database row to Receipt object."""
    return Receipt(
        id=row['id'],
        original_filename=row['original_filename'],
        stored_filename=row['stored_filename'],
        payment_date=date.fromisoformat(row['payment_date']) if row['payment_date'] else None,
        company_name=row['company_name'],
        payment_handler=row['payment_handler'] if 'payment_handler' in row.keys() else None,
        category=row['category'],
        staging_path=row['staging_path'] if 'staging_path' in row.keys() else None,
        dropbox_path=row['dropbox_path'],
        email_sent_to=row['email_sent_to'],
        email_sent_at=datetime.fromisoformat(row['email_sent_at']) if row['email_sent_at'] else None,
        notes=row['notes'] if 'notes' in row.keys() else None,
        ocr_cost=row['ocr_cost'] if 'ocr_cost' in row.keys() else None,
        is_recurring=bool(row['is_recurring']) if 'is_recurring' in row.keys() and row['is_recurring'] else False,
        created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
    )


def get_total_ocr_cost() -> float:
    """Get total OCR cost across all receipts."""
    conn = get_connection()
    result = conn.execute("SELECT COALESCE(SUM(ocr_cost), 0) FROM receipts").fetchone()
    conn.close()
    return result[0] or 0.0


# Attachment CRUD functions

def add_attachment(attachment: Attachment) -> int:
    """Add an attachment to a receipt. Returns the new ID."""
    conn = get_connection()
    cursor = conn.execute("""
        INSERT INTO attachments (receipt_id, original_filename, stored_filename, file_path)
        VALUES (?, ?, ?, ?)
    """, (
        attachment.receipt_id,
        attachment.original_filename,
        attachment.stored_filename,
        attachment.file_path,
    ))
    conn.commit()
    attachment_id = cursor.lastrowid
    conn.close()
    return attachment_id


def get_attachments(receipt_id: int) -> list[Attachment]:
    """Get all attachments for a receipt."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM attachments WHERE receipt_id = ? ORDER BY created_at",
        (receipt_id,)
    ).fetchall()
    conn.close()
    return [_row_to_attachment(row) for row in rows]


def delete_attachment(attachment_id: int) -> bool:
    """Delete an attachment by ID."""
    conn = get_connection()
    cursor = conn.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def update_attachment_path(attachment_id: int, new_path: str) -> bool:
    """Update the file path of an attachment."""
    conn = get_connection()
    cursor = conn.execute(
        "UPDATE attachments SET file_path = ? WHERE id = ?",
        (new_path, attachment_id)
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated


def _row_to_attachment(row: sqlite3.Row) -> Attachment:
    """Convert database row to Attachment object."""
    return Attachment(
        id=row['id'],
        receipt_id=row['receipt_id'],
        original_filename=row['original_filename'],
        stored_filename=row['stored_filename'],
        file_path=row['file_path'],
        created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
    )


# Settings functions
def get_setting(key: str, default: str | None = None) -> str | None:
    """Get a setting value by key."""
    conn = get_connection()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row['value'] if row else default


def set_setting(key: str, value: str) -> None:
    """Set a setting value."""
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, value)
    )
    conn.commit()
    conn.close()


def get_all_settings() -> dict[str, str]:
    """Get all settings as a dictionary."""
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {row['key']: row['value'] for row in rows}


# Reminder functions

@dataclass
class ReminderDismissal:
    """Dismissal record for a recurring reminder."""
    id: int | None
    receipt_id: int
    year_month: str
    status: str  # 'done' or 'fulfilled'
    fulfilled_receipt_id: int | None
    created_at: datetime | None = None


def _basic_normalize(name: str | None) -> str:
    if not name:
        return ""
    return name.split(",", 1)[0].strip().lower()


def _normalize_company(name: str | None, *, aliases: dict[str, str] | None = None) -> str:
    """Canonical key for matching company names across receipts.

    Step 1: take the text before the first comma, lowercase/strip it.
        'Anthropic, PBC, San Fransisco' -> 'anthropic'
        'Apple Distribution International Ltd.' -> 'apple distribution international ltd.'
    Step 2: consult the company_aliases table — if the result matches an alias,
    return that alias's canonical instead. Lets the user collapse OCR variants
    (e.g. 'Apple Distribution International Ltd.' -> 'apple') onto one key.
    """
    base = _basic_normalize(name)
    if not base:
        return ""
    if aliases is None:
        aliases = list_alias_map()
    return aliases.get(base, base)


def find_recurring_by_canonical(company_name: str | None, *, exclude_id: int | None = None) -> list[dict]:
    """Return any other recurring receipts whose canonical name (after aliases)
    matches the given company name. Used to warn before toggling a duplicate
    recurring source on the same vendor.
    """
    target = _normalize_company(company_name)
    if not target:
        return []
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, payment_date, company_name FROM receipts WHERE is_recurring=1"
    ).fetchall()
    conn.close()
    aliases = list_alias_map()
    matches = []
    for r in rows:
        if exclude_id is not None and r["id"] == exclude_id:
            continue
        if _normalize_company(r["company_name"], aliases=aliases) == target:
            matches.append(dict(r))
    return matches


def list_alias_map() -> dict[str, str]:
    """{normalized_alias: normalized_canonical} for all configured aliases."""
    conn = get_connection()
    rows = conn.execute("SELECT alias, canonical FROM company_aliases").fetchall()
    conn.close()
    return {r["alias"]: r["canonical"] for r in rows}


def list_aliases() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM company_aliases ORDER BY alias").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_alias(alias: str, canonical: str) -> bool:
    a = _basic_normalize(alias)
    c = _basic_normalize(canonical)
    if not a or not c:
        return False
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO company_aliases (alias, canonical) VALUES (?, ?)",
            (a, c),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def delete_alias(alias_id: int) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM company_aliases WHERE id=?", (alias_id,))
    conn.commit()
    conn.close()


@dataclass
class DueReminder:
    """A single pending recurring-reminder instance for a specific month."""
    receipt: Receipt
    year_month: str       # e.g. "2026-04"
    expected_date: date   # day-of-month from source, clamped to last day of year_month
    display_name: str = ""  # alias-resolved label; falls back to receipt.company_name


def get_due_reminders(target_date: date | None = None) -> list[DueReminder]:
    """Get every pending recurring-reminder instance up to and including target month.

    A recurring reminder fires every month from the month AFTER the source
    receipt's payment_date onward. For each such month we yield an instance if:
    - the reminder day (source day-of-month, clamped to that month's last day)
      has been reached or passed by target_date, AND
    - no dismissal exists for that (receipt, year_month).

    Missed/unfulfilled instances from prior months remain pending — they are
    not silently consumed when the calendar rolls over.
    """
    import calendar
    if target_date is None:
        target_date = date.today()

    target_ym = target_date.strftime("%Y-%m")

    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM receipts
        WHERE is_recurring = 1
        AND payment_date IS NOT NULL
        AND strftime('%Y-%m', payment_date) < ?
    """, (target_ym,)).fetchall()

    dismissed_rows = conn.execute("""
        SELECT receipt_id, year_month, fulfilled_receipt_id FROM reminder_dismissals
    """).fetchall()

    # Implicit fulfillment: if any receipt exists with the same (normalized)
    # company name in the reminder's target month, treat that month as covered
    # — even if it was uploaded via drag-drop instead of the reminder flow.
    fulfilling_rows = conn.execute("""
        SELECT id, company_name, strftime('%Y-%m', payment_date) AS ym
        FROM receipts
        WHERE payment_date IS NOT NULL AND company_name IS NOT NULL
    """).fetchall()
    conn.close()

    dismissed = {(d['receipt_id'], d['year_month']) for d in dismissed_rows}
    # Receipts already claimed as fulfillment by an explicit dismissal — they
    # can't double-count as an implicit slot for another duplicate source.
    already_claimed = {d['fulfilled_receipt_id'] for d in dismissed_rows if d['fulfilled_receipt_id']}

    aliases = list_alias_map()

    receipts_by_company_month: dict[tuple[str, str], set[int]] = {}
    for fr in fulfilling_rows:
        norm = _normalize_company(fr['company_name'], aliases=aliases)
        if not norm:
            continue
        receipts_by_company_month.setdefault((norm, fr['ym']), set()).add(fr['id'])

    # Available implicit-fulfillment slots per (canonical_company, year_month).
    # Each upload covers ONE pending reminder; duplicate twin sources need
    # multiple uploads to be cleared.
    available_slots: dict[tuple[str, str], int] = {
        key: len(ids - already_claimed)
        for key, ids in receipts_by_company_month.items()
    }

    results: list[DueReminder] = []
    for row in rows:
        receipt = _row_to_receipt(row)
        cname = _normalize_company(receipt.company_name, aliases=aliases)
        # Walk from the month AFTER the source month up to target month.
        year, month = receipt.payment_date.year, receipt.payment_date.month
        month += 1
        if month > 12:
            year += 1
            month = 1
        while (year, month) <= (target_date.year, target_date.month):
            last_day = calendar.monthrange(year, month)[1]
            reminder_day = min(receipt.payment_date.day, last_day)
            ym = f"{year:04d}-{month:02d}"
            is_due = (year, month) < (target_date.year, target_date.month) or \
                     ((year, month) == (target_date.year, target_date.month) and target_date.day >= reminder_day)
            explicitly_dismissed = (receipt.id, ym) in dismissed
            # Implicit fulfillment: claim a slot if one is still available.
            slot_key = (cname, ym)
            implicitly_fulfilled = False
            if not explicitly_dismissed and available_slots.get(slot_key, 0) > 0:
                available_slots[slot_key] -= 1
                implicitly_fulfilled = True
            if is_due and not explicitly_dismissed and not implicitly_fulfilled:
                # If an alias maps this company onto a different canonical, use
                # that as the display label so the reminder list reads in the
                # user's preferred terms (e.g. 'youtube premium' instead of
                # 'Google Commerce Limited').
                base = _basic_normalize(receipt.company_name)
                canonical = aliases.get(base, base)
                display_name = canonical.title() if canonical != base else (receipt.company_name or "")
                results.append(DueReminder(
                    receipt=receipt,
                    year_month=ym,
                    expected_date=date(year, month, reminder_day),
                    display_name=display_name,
                ))
            month += 1
            if month > 12:
                year += 1
                month = 1
    # Oldest pending first — those need attention more than recent ones.
    results.sort(key=lambda d: (d.year_month, d.receipt.id or 0))
    return results


def dismiss_reminder(receipt_id: int, year_month: str, status: str = "done",
                     fulfilled_receipt_id: int | None = None) -> bool:
    """Dismiss a reminder for a given month."""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO reminder_dismissals
            (receipt_id, year_month, status, fulfilled_receipt_id)
            VALUES (?, ?, ?, ?)
        """, (receipt_id, year_month, status, fulfilled_receipt_id))
        conn.commit()
        conn.close()
        return True
    except Exception:
        conn.close()
        return False


def get_dismissal(receipt_id: int, year_month: str) -> ReminderDismissal | None:
    """Get dismissal record for a specific receipt and month."""
    conn = get_connection()
    row = conn.execute("""
        SELECT * FROM reminder_dismissals
        WHERE receipt_id = ? AND year_month = ?
    """, (receipt_id, year_month)).fetchone()
    conn.close()
    if row:
        return ReminderDismissal(
            id=row['id'],
            receipt_id=row['receipt_id'],
            year_month=row['year_month'],
            status=row['status'],
            fulfilled_receipt_id=row['fulfilled_receipt_id'] if 'fulfilled_receipt_id' in row.keys() else None,
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
        )
    return None


def get_fulfilled_reminders(year_month: str) -> list[dict]:
    """Get fulfilled reminders for a given month with their linked receipts."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT rd.*, r.company_name, r.payment_date, r.category
        FROM reminder_dismissals rd
        JOIN receipts r ON r.id = rd.receipt_id
        WHERE rd.year_month = ? AND rd.status = 'fulfilled'
    """, (year_month,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


# Initialize database on module import
init_db()
