# Receipt Manager

> Web-based receipt management system for tracking, storing, and forwarding business receipts to a bookkeeper.

## Purpose

Automate the manual workflow of: creating Dropbox folders, renaming files, composing emails, and attaching files for each receipt. One-click processing from upload to bookkeeper.

## Tech Stack

- **Backend**: Python 3.11+ with FastAPI
- **Database**: SQLite
- **Frontend**: HTML/CSS/JS (Jinja2 templates)
- **File Storage**: Local Dropbox folder (synced by Dropbox desktop client)
- **Email**: SMTP (Gmail)
- **OCR/AI**: GPT-4 Vision, Claude Vision, local LLM (Qwen2.5-VL), Gemma 4 via self-hosted OpenAI-compatible endpoint, EasyOCR, Tesseract
- **PDF**: PyMuPDF, pdf2image

## Project Structure

```
receipt-manager/
├── app.py              # FastAPI routes and main entry point
├── config.py           # Loads .env, exposes settings
├── database.py         # SQLite CRUD operations
├── dropbox_handler.py  # Local staging folder sync
├── email_handler.py    # Gmail SMTP with PDF attachment
├── ocr_handler.py      # Pluggable OCR: GPT-4, Claude, local LLM, EasyOCR
├── templates/          # Jinja2 HTML templates
├── static/             # CSS, JS
├── receipts.db         # SQLite database (auto-created on first run)
├── .env                # Environment variables (not in git)
└── requirements.txt
```

## User Workflow

1. Upload/drop PDF or image
2. AI extracts: payment date, company name, payment handler
3. User confirms or edits on review screen
4. User selects category: "Leverantörsfaktura" or "Annat"
5. One click triggers all automated actions
6. Done

## Automated Actions (on submit)

1. Create local staging folder: `YYYY-MM-DD CompanyName/`
2. Rename file: `yyyy-mm-dd-companyname.pdf` (lowercase, hyphens, preserve Swedish characters)
3. Sync to Dropbox folder (via desktop client)
4. Send email with attachment to bookkeeper
5. Log to SQLite database

## Category Email Mapping

Categories map to bookkeeper email addresses configured in `.env`:

| Category | .env Variable |
|----------|---------------|
| Leverantörsfaktura | `EMAIL_LEVERANTORSFAKTURA` |
| Annat | `EMAIL_ANNAT` |

## Naming Conventions

- **Staging folder**: `YYYY-MM-DD CompanyName/` (original case)
- **Filename**: `yyyy-mm-dd-companyname.pdf` (lowercase, spaces to hyphens, preserves Swedish characters)
- **Email subject**: `YYYY-MM-DD CompanyName`

## Commands

```bash
# Setup
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt

# Development
uvicorn app:app --reload --port 8000

# Database
# Auto-created on first run

# Lint
ruff check .
```

## Environment Variables (.env)

See `.env.example` for all available variables with descriptions.

## File Boundaries

- **Safe to edit**: All `.py` files, `templates/`, `static/`
- **Config**: `.env` (secrets), `config.py`
- **Never touch**: `venv/`, `__pycache__/`, `.git/`

## Verification

Before committing:
1. `ruff check .` - no lint errors
2. Test upload -> OCR -> Dropbox -> email flow manually
3. Check database entry created

## Security

- API keys in `.env` only (never in code)
- Validate file types (PDF, JPG, PNG only)
- Sanitize company names before file paths

