# Receipt Manager

Web-based receipt management system that automates the workflow of organizing, storing, and forwarding business receipts to a bookkeeper. Upload a receipt, let AI extract the details, and send it off with one click.

## What It Does

1. **Upload** a PDF or image (drag-and-drop or click)
2. **AI extracts** payment date, company name, and payment handler
3. **Review and confirm** the extracted data
4. **One click** creates an organized folder, emails the receipt to your bookkeeper, and logs everything

No more manually creating folders, renaming files, writing emails, and attaching documents for each receipt.

## Features

- **Multiple OCR/AI backends** — GPT-4 Vision, Claude Vision, EasyOCR, Tesseract (+ optional local LLM, + optional Gemma 4 over a self-hosted endpoint)
- **Smart date parsing** — prioritizes payment due dates, handles Swedish date formats
- **Payment handler detection** — identifies Klarna, Swish, PayPal, and others
- **Dropbox sync** — organized folder structure via Dropbox desktop client
- **Email forwarding** — category-based routing to different bookkeeper addresses
- **Recurring reminders** — mark receipts as monthly recurring, get reminded when they're due
- **Attachments** — add extra files to any receipt
- **Receipt management** — filter, search, edit, resend, bulk upload
- **Dark mode** — toggle with persistent preference
- **OCR cost tracking** — monitor API costs per receipt

## Requirements

- **Python 3.11+**
- **Windows** (batch scripts are Windows-only, but the Python code should work on other platforms)
- **Poppler** — required for converting PDFs to images for OCR

### Installing Poppler (Windows)

1. Download the latest release from [poppler-windows](https://github.com/oschwartz10612/poppler-windows/releases)
2. Extract to a folder, e.g. `C:\Program Files\poppler\`
3. Add the `bin` folder to your system PATH: `C:\Program Files\poppler\Library\bin`
4. Verify: open a new terminal and run `pdfinfo --version`

### Optional dependencies

- **[Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki)** — for free local OCR. Install, add to PATH, verify with `tesseract --version`
- **[Dropbox desktop client](https://www.dropbox.com/install)** — for automatic cloud sync of receipt folders
- **Gmail account** with [App Password](https://support.google.com/accounts/answer/185833) — for emailing receipts to your bookkeeper

## Setup

### 1. Clone and install

```bash
git clone https://github.com/fordtheword/ib-receipt-manager.git
cd ib-receipt-manager

python -m venv venv
venv\Scripts\activate          # Windows

pip install -r requirements.txt
```

> **Note:** `easyocr` pulls in PyTorch (~2 GB download). This is normal and may take a while on the first install.

### 2. Configure environment

Copy the example file and fill in your values:

```bash
copy .env.example .env
```

Open `.env` in a text editor. Here's what each setting does:

#### OCR Backend

```env
OCR_BACKEND=auto
```

| Backend | Cost | Accuracy | Requirements |
|---------|------|----------|-------------|
| `gpt4` | ~$0.01/receipt | Best | OpenAI API key |
| `claude` | ~$0.01/receipt | Great | Anthropic API key |
| `easyocr` | Free | Good | None (installed with pip) |
| `tesseract` | Free | Decent | Tesseract installed on system |
| `local` | Free | Good | Local GGUF model files (advanced, see below) |
| `gemma` | Free | Great | Gemma 4 served via an OpenAI-compatible HTTP endpoint (e.g. Docker Model Runner on a LAN/Tailscale host) |
| `auto` | Varies | Best available | Uses first available: GPT-4 > Claude > local > EasyOCR |

**Recommended:** Start with `gpt4` or `claude` for the best experience. The API cost is minimal (roughly 1 cent per receipt). Set your API key:

```env
ANTHROPIC_API_KEY=your-key-here
# and/or
OPENAI_API_KEY=your-key-here
```

#### Email (SMTP)

Required for sending receipts to your bookkeeper.

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your.email@gmail.com
SMTP_PASSWORD=your-app-password
SENDER_EMAIL=your.email@gmail.com
```

For Gmail:
1. Enable 2-Factor Authentication on your Google account
2. Go to [App Passwords](https://myaccount.google.com/apppasswords)
3. Create a password for "Mail"
4. Use that 16-character password as `SMTP_PASSWORD`

#### Bookkeeper Emails

Receipts are routed by category to different email addresses:

```env
EMAIL_LEVERANTORSFAKTURA=bookkeeper-invoices@example.com
EMAIL_ANNAT=bookkeeper-other@example.com
```

#### Dropbox (optional)

If you use Dropbox, point to a local folder synced by the Dropbox desktop client:

```env
DROPBOX_LOCAL_PATH=C:\Users\YourName\Dropbox\Receipts
```

The app creates an organized folder structure:
```
Receipts/
└── 2026/
    └── 2026-04-12 Company Name/
        └── 2026-04-12-company-name.pdf
```

#### Local Vision Model (optional, advanced)

For running OCR entirely locally without API costs. Requires downloading a ~5 GB model file. Only recommended if you want to avoid API costs entirely.

```env
LOCAL_VISION_MODEL=C:\path\to\qwen2.5-vl-7b.gguf
LOCAL_VISION_MMPROJ=C:\path\to\mmproj-qwen2.5-vl-7b.gguf
```

Building llama-cpp-python with GPU support requires CUDA. See `build_llama_cuda.bat` for a helper script.

#### Gemma 4 via a self-hosted endpoint (optional)

If you have Gemma 4 running behind an OpenAI-compatible HTTP endpoint — for example via [Docker Model Runner](https://github.com/docker/model-runner) on a separate machine reachable over LAN or Tailscale — point the app at it:

```env
GEMMA_API_BASE=http://<host>:12434/engines/llama.cpp/v1
GEMMA_MODEL=ai/gemma4:E4B
```

When `GEMMA_API_BASE` is set, a "Process with Gemma 4" button appears alongside the other backends. Inference is free (you're hosting the model yourself) but slower than the cloud APIs — expect 30–60 s per receipt on Apple Silicon CPU/Metal.

### 3. Run

```bash
uvicorn app:app --reload --port 8000
```

Open http://127.0.0.1:8000 in your browser. The database is created automatically on first run.

### 4. Auto-start (optional)

To start the server automatically when Windows boots:
1. Edit `start-server.bat` if needed (it uses the script's own directory by default)
2. Press `Win+R`, type `shell:startup`, press Enter
3. Create a shortcut to `start-server.bat` in that folder

## Usage

### Processing a receipt

1. Go to the upload page and drop a PDF or image
2. The AI extracts payment date, company name, and handler
3. Review the extracted data — edit if needed
4. Select a category (Leverantörsfaktura or Annat)
5. Add optional notes for your bookkeeper
6. Click process — the app creates the folder, emails the receipt, and logs it

### Recurring reminders

For monthly bills (rent, subscriptions, etc.):
1. Open a processed receipt
2. Toggle "Recurring" on
3. Each month, a reminder appears at the top of your receipt list
4. Click "Upload" to process that month's receipt, or "Done" to skip

### Settings

Visit `/settings` to configure:
- Source folder for unprocessed receipts (optional cleanup feature)
- View current Dropbox path, OCR backend, and email status

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `pdfinfo is not recognized` | Poppler not installed or not on PATH. See install steps above |
| `tesseract is not installed or it's not in your PATH` | Install Tesseract and add to PATH, or switch to a different OCR backend |
| `pip install` fails on `llama-cpp-python` | This package is optional. If you don't need local LLM, ignore the error — all other backends will work |
| OCR returns empty/wrong data | Try a different backend. API-based options (GPT-4, Claude) are significantly more accurate |
| Email not sending | Check SMTP settings. For Gmail, make sure you're using an App Password, not your regular password |
| Dropbox folder not found | Verify the path in `.env` exists and the Dropbox desktop client is running |

## Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | Python 3.11+, FastAPI |
| Frontend | Jinja2 templates, vanilla JS |
| Database | SQLite |
| OCR | GPT-4 Vision, Claude Vision, EasyOCR, Tesseract, Qwen2.5-VL (optional), Gemma 4 (optional, via self-hosted endpoint) |
| Email | SMTP (Gmail) |
| Storage | Local filesystem + Dropbox sync |

## License

MIT License with Commons Clause — free to use, modify, and share. Cannot be sold as a commercial product. See [LICENSE](LICENSE) for details.
