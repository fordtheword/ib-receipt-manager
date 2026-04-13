"""Email handler for Receipt Manager.

Sends receipts to bookkeeper via Gmail SMTP.
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from datetime import date

import config


def send_receipt_email(
    to_email: str,
    company_name: str,
    payment_date: date,
    payment_handler: str | None,
    category: str,
    attachment_path: Path,
    stored_filename: str,
    extra_attachments: list[Path] | None = None,
    notes: str | None = None,
) -> bool:
    """Send receipt email with PDF attachment.

    Args:
        to_email: Recipient email address
        company_name: Company name for subject
        payment_date: Payment date for subject
        payment_handler: Optional payment handler (Klarna, etc.)
        category: Receipt category
        attachment_path: Path to the PDF file
        stored_filename: Filename to use for attachment
        extra_attachments: Optional list of additional files to attach
        notes: Optional message to include in email body

    Returns:
        True if sent successfully, False otherwise
    """
    if not config.SMTP_USERNAME or not config.SMTP_PASSWORD:
        raise ValueError("SMTP credentials not configured in .env")

    # Build subject line
    date_str = payment_date.isoformat() if payment_date else "Unknown date"
    if payment_handler and payment_handler != company_name:
        subject = f"Kvitto: {company_name} ({payment_handler}) - {date_str}"
    else:
        subject = f"Kvitto: {company_name} - {date_str}"

    # Create message
    msg = MIMEMultipart()
    msg['From'] = config.SENDER_EMAIL
    msg['To'] = to_email
    msg['Subject'] = subject

    # Email body
    notes_section = f"\nMeddelande: {notes}\n" if notes else ""
    body = f"""Kvitto bifogat.

Företag: {company_name}
{"Betalningshanterare: " + payment_handler if payment_handler else ""}
Betalningsdatum: {date_str}
Kategori: {category}
{notes_section}
---
Skickat från Receipt Manager
"""
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    # Attach main PDF
    if attachment_path.exists():
        with open(attachment_path, 'rb') as f:
            part = MIMEBase('application', 'pdf')
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                'Content-Disposition',
                f'attachment; filename="{stored_filename}"'
            )
            msg.attach(part)

    # Attach extra files
    if extra_attachments:
        for extra_path in extra_attachments:
            if extra_path.exists():
                with open(extra_path, 'rb') as f:
                    # Determine MIME type based on extension
                    ext = extra_path.suffix.lower()
                    if ext == '.pdf':
                        part = MIMEBase('application', 'pdf')
                    elif ext in ['.jpg', '.jpeg']:
                        part = MIMEBase('image', 'jpeg')
                    elif ext == '.png':
                        part = MIMEBase('image', 'png')
                    else:
                        part = MIMEBase('application', 'octet-stream')
                    part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header(
                        'Content-Disposition',
                        f'attachment; filename="{extra_path.name}"'
                    )
                    msg.attach(part)

    # Send via Gmail SMTP
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
            server.starttls()
            server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Email send failed: {e}")
        raise


def get_recipient_for_category(category: str) -> str:
    """Get recipient email based on category."""
    return config.get_email_for_category(category)
