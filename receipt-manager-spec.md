# Receipt Manager — Project Spec

## Overview

Web-based receipt management system for tracking, storing, and forwarding business receipts to a bookkeeper. Automates the manual workflow of creating folders, renaming files, composing emails, and attaching documents — from upload to bookkeeper delivery in one action.

**Status:** Fully operational (Phase 1 + Phase 2 complete). Runs locally on Windows with auto-start.
**Access:** http://127.0.0.1:8000

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.11+, FastAPI |
| Templates | Jinja2 |
| Database | SQLite |
| OCR / AI | OpenAI GPT-4 Vision (primary), Claude Vision (fallback), llama-cpp-python / EasyOCR (local) |
| File Storage | Local staging folder + Dropbox sync |
| Email | SMTP via Gmail (Google Workspace app password) |
| PDF handling | PyMuPDF |
| Auto-start | Windows Startup folder via start-server.bat |

---

## Features

### Core Workflow
- Drag-and-drop upload of PDF or image receipts
- AI extracts payment date, company name, and total amount
- User confirms or edits extracted data on a review screen
- One-click processing: create local staging folder, rename file, send email to bookkeeper, log to database

### OCR Backends (auto-detected by priority)
- **GPT-4 Vision** (OpenAI) — highest accuracy for Swedish receipts, cost-tracked
- **Claude Vision** (Anthropic) — fallback
- **llama-cpp-python** (Qwen2.5-VL-7B, local) — free, loads on demand, no API needed
- **EasyOCR** — final fallback

### Receipt List / Management
- Collapsible month sections (current month open by default)
- Filters: year, category, email status (mailed / not mailed)
- Edit and delete receipts (with staging folder cleanup)
- Resend email on previously processed receipts
- View/download attachments from list view
- Bulk Dropbox upload for all pending receipts
- Open staging folder directly from UI

### Recurring Reminders
- Checkbox on each receipt to mark as monthly recurring
- Collapsible reminder section at top of receipts list (amber, with pulsing indicator)
- Reminders appear when today >= the receipt's day-of-month for the current month
- Inline reminder rows in current month section with Upload and Done buttons
- Upload directly to a reminder: pre-fills company, category, date — auto-dismisses on processing
- Done button: dismisses for current month only, returns next month
- Handles day-of-month edge cases (31st in 30-day months, February)
- Unchecking the recurring checkbox stops reminders permanently

### UX
- Dark mode toggle (persistent via localStorage)
- Mobile-responsive layout
- Loading spinner during OCR processing
- Category memory (last selection persisted)
- "Add Manually" mode (no OCR, for rent invoices etc.)
- OCR cost tracking — per receipt and running total displayed in UI

---

## Architecture / Key Components

```
receipt-manager/
├── app.py              # FastAPI routes and main entry point
├── config.py           # Loads .env, exposes settings
├── database.py         # SQLite CRUD (receipts table)
├── ocr_handler.py      # Pluggable OCR: GPT-4, Claude, local LLM, EasyOCR
├── dropbox_handler.py  # Local staging folder sync (no Dropbox API)
├── email_handler.py    # Gmail SMTP with PDF attachment
├── templates/          # Jinja2 HTML templates
│   ├── base.html       # Layout + dark mode
│   ├── index.html      # Upload page with drag-drop
│   ├── confirm.html    # OCR result review + extra attachments + notes
│   ├── receipt.html    # Single receipt detail + resend
│   ├── receipts.html   # List view with filters and cost total
│   ├── edit.html       # Edit form
│   └── error.html
├── static/             # CSS and JS
├── receipt drops/      # Staging folder (mirrors Dropbox structure)
│   └── YYYY/
│       └── YYYY-MM-DD CompanyName/
│           └── yyyy-mm-dd-companyname.pdf
├── receipts.db         # SQLite database (auto-created on first run)
├── start-server.bat    # Windows auto-start script
└── requirements.txt
```

### Database Schema

```sql
CREATE TABLE receipts (
    id INTEGER PRIMARY KEY,
    original_filename TEXT,
    stored_filename TEXT,
    payment_date DATE,
    company_name TEXT,
    category TEXT,
    dropbox_path TEXT,
    email_sent_to TEXT,
    email_sent_at DATETIME,
    is_recurring INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

```sql
CREATE TABLE reminder_dismissals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id INTEGER NOT NULL,
    year_month TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'done',
    fulfilled_receipt_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (receipt_id) REFERENCES receipts(id) ON DELETE CASCADE,
    UNIQUE(receipt_id, year_month)
);
```

### Category Email Mapping

Categories route receipts to different bookkeeper email addresses, configured via `.env`:

| Category | .env Variable |
|----------|---------------|
| Leverantörsfaktura | `EMAIL_LEVERANTORSFAKTURA` |
| Annat | `EMAIL_ANNAT` |

### Naming Conventions

| Item | Format |
|------|--------|
| Staging folder | `YYYY-MM-DD CompanyName/` |
| File on disk | `yyyy-mm-dd-companyname.pdf` (lowercase, spaces to hyphens, Swedish characters preserved) |
| Email subject | `YYYY-MM-DD CompanyName` |
| Dropbox path | `/Receipts/YYYY/YYYY-MM-DD CompanyName/` |

---

## Configuration (.env)

See `.env.example` for all available variables with descriptions.

---

## Running the App

```bash
# Create and activate virtual environment
python -m venv venv
venv\Scripts\activate          # Windows

# Install dependencies
pip install -r requirements.txt

# Start development server
uvicorn app:app --reload --port 8000

# Lint check before committing
ruff check .
```

The `start-server.bat` script can be placed in the Windows Startup folder for automatic launch on boot.

---

## Future / Planned

- Email reminders via cron job (requires always-on server)
- Watch folder auto-trigger: drop files into `incoming/` and OCR runs automatically
- Editable categories from UI
- Duplicate detection
- Statistics dashboard
- Batch processing
