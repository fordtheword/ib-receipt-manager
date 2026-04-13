"""Configuration management for Receipt Manager."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Base paths
BASE_DIR = Path(__file__).parent
RECEIPT_DROPS_DIR = BASE_DIR / "receipt drops"
DATABASE_PATH = BASE_DIR / "receipts.db"

# OCR Configuration
# Options: "tesseract", "easyocr", "claude", "gpt4", "local", "auto"
OCR_BACKEND = os.getenv("OCR_BACKEND", "auto")

# Anthropic (for Claude Vision OCR)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# OpenAI (for GPT-4 Vision OCR)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Local vision LLM (llama-cpp-python with Qwen2.5-VL)
LOCAL_VISION_MODEL = os.getenv("LOCAL_VISION_MODEL", "")
LOCAL_VISION_MMPROJ = os.getenv("LOCAL_VISION_MMPROJ", "")

# Dropbox (local folder - synced by Dropbox desktop client)
DROPBOX_LOCAL_PATH = Path(os.getenv("DROPBOX_LOCAL_PATH", ""))

# Source folder for unprocessed receipts (for cleanup after processing)
OHANTERADE_FOLDER = Path(os.getenv("OHANTERADE_FOLDER", "")) if os.getenv("OHANTERADE_FOLDER") else None

# Email (SMTP)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "")

# Bookkeeper email addresses
EMAIL_LEVERANTORSFAKTURA = os.getenv("EMAIL_LEVERANTORSFAKTURA", "")
EMAIL_ANNAT = os.getenv("EMAIL_ANNAT", "")

# Category to email mapping
CATEGORY_EMAILS = {
    "leverantörsfaktura": EMAIL_LEVERANTORSFAKTURA,
    "annat": EMAIL_ANNAT,
}


def get_email_for_category(category: str) -> str:
    """Get bookkeeper email address for a category."""
    return CATEGORY_EMAILS.get(category.lower(), EMAIL_ANNAT)


def validate_config() -> dict[str, bool]:
    """Check which integrations are configured."""
    return {
        "ocr_tesseract": True,  # Always available if installed
        "ocr_easyocr": True,    # Always available if installed
        "ocr_claude": bool(ANTHROPIC_API_KEY),
        "ocr_gpt4": bool(OPENAI_API_KEY),
        "ocr_local": bool(LOCAL_VISION_MODEL and LOCAL_VISION_MMPROJ),
        "dropbox": DROPBOX_LOCAL_PATH.exists(),
        "email": bool(SMTP_USERNAME and SMTP_PASSWORD),
    }
