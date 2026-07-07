"""Local Cloud Storage folder handler for Receipt Manager.

Copies receipts to local Cloud Storage folder which syncs via desktop client (Dropbox or Google Drive).
"""

import shutil
from pathlib import Path
from datetime import date

import config


def build_folder_name(payment_date: date, company_name: str, payment_handler: str | None) -> str:
    """Build the Storage folder name: YYYY-MM-DD CompanyName (Handler).

    Removes characters invalid in Windows folder names and trailing
    spaces/dots (Windows strips them on mkdir but not in intermediate
    path components, causing WinError 3 on file writes).
    """
    date_str = payment_date.isoformat()
    company_name = company_name.strip()
    payment_handler = payment_handler.strip() if payment_handler else None

    if payment_handler and payment_handler != company_name:
        folder_name = f"{date_str} {company_name} ({payment_handler})"
    else:
        folder_name = f"{date_str} {company_name}"

    for char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
        folder_name = folder_name.replace(char, '')
    return folder_name.strip(' .')


def upload_receipt(
    source_path: Path,
    payment_date: date,
    company_name: str,
    payment_handler: str | None,
    stored_filename: str,
) -> str:
    """Copy receipt to Cloud Storage folder.

    Creates folder structure: Year / YYYY-MM-DD CompanyName (Handler) / file.pdf

    Args:
        source_path: Path to the source PDF file
        payment_date: Payment date for folder naming
        company_name: Company name for folder naming
        payment_handler: Optional payment handler (Klarna, etc.)
        stored_filename: Final filename for the PDF

    Returns:
        Relative path to the uploaded file (for database storage)
    """
    year = str(payment_date.year)
    folder_name = build_folder_name(payment_date, company_name, payment_handler)

    # Create full path: Storage / Year / Folder / File
    target_dir = config.get_storage_local_path() / year / folder_name
    target_dir.mkdir(parents=True, exist_ok=True)

    # Find unique filename if file already exists in Storage
    stored_filename = stored_filename.strip()
    target_path = target_dir / stored_filename
    if target_path.exists():
        base = stored_filename.rsplit('.', 1)[0]
        ext = stored_filename.rsplit('.', 1)[1] if '.' in stored_filename else 'pdf'
        counter = 2
        while target_path.exists():
            stored_filename = f"{base}-{counter}.{ext}"
            target_path = target_dir / stored_filename
            counter += 1

    # Copy file
    shutil.copy2(source_path, target_path)

    # Return relative path for database (from Storage root)
    relative_path = f"{year}/{folder_name}/{stored_filename}"
    return relative_path


def get_full_path(relative_path: str) -> Path:
    """Get full local path from relative Storage path."""
    return config.get_storage_local_path() / relative_path


def copy_attachment_to_folder(source_path: Path, storage_folder: Path) -> Path:
    """Copy an attachment to the same Cloud Storage folder as the receipt.

    Args:
        source_path: Path to the source file
        storage_folder: Target Cloud Storage folder path

    Returns:
        Full path to the copied file
    """
    target_path = storage_folder / source_path.name
    storage_folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)
    return target_path


def get_storage_folder_path(payment_date: date, company_name: str, payment_handler: str | None) -> Path:
    """Get the Cloud Storage folder path for a receipt (without creating it).

    Args:
        payment_date: Payment date for folder naming
        company_name: Company name for folder naming
        payment_handler: Optional payment handler

    Returns:
        Full path to the Cloud Storage folder
    """
    year = str(payment_date.year)
    folder_name = build_folder_name(payment_date, company_name, payment_handler)
    return config.get_storage_local_path() / year / folder_name
