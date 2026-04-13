"""Local Dropbox folder handler for Receipt Manager.

Copies receipts to local Dropbox folder which syncs via desktop client.
"""

import shutil
from pathlib import Path
from datetime import date

import config


def upload_receipt(
    source_path: Path,
    payment_date: date,
    company_name: str,
    payment_handler: str | None,
    stored_filename: str,
) -> str:
    """Copy receipt to Dropbox folder.

    Creates folder structure: Year / YYYY-MM-DD CompanyName (Handler) / file.pdf

    Args:
        source_path: Path to the source PDF file
        payment_date: Payment date for folder naming
        company_name: Company name for folder naming
        payment_handler: Optional payment handler (Klarna, etc.)
        stored_filename: Final filename for the PDF

    Returns:
        Full path to the uploaded file (for database storage)
    """
    # Build folder name: YYYY-MM-DD CompanyName (Handler)
    date_str = payment_date.isoformat()
    year = str(payment_date.year)

    if payment_handler and payment_handler != company_name:
        folder_name = f"{date_str} {company_name} ({payment_handler})"
    else:
        folder_name = f"{date_str} {company_name}"

    # Clean folder name of invalid characters
    for char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
        folder_name = folder_name.replace(char, '')

    # Create full path: Dropbox / Year / Folder / File
    target_dir = config.DROPBOX_LOCAL_PATH / year / folder_name
    target_dir.mkdir(parents=True, exist_ok=True)

    # Find unique filename if file already exists in Dropbox
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

    # Return relative path for database (from Dropbox root)
    relative_path = f"{year}/{folder_name}/{stored_filename}"
    return relative_path


def get_full_path(relative_path: str) -> Path:
    """Get full local path from relative Dropbox path."""
    return config.DROPBOX_LOCAL_PATH / relative_path


def copy_attachment_to_folder(source_path: Path, dropbox_folder: Path) -> Path:
    """Copy an attachment to the same Dropbox folder as the receipt.

    Args:
        source_path: Path to the source file
        dropbox_folder: Target Dropbox folder path

    Returns:
        Full path to the copied file
    """
    target_path = dropbox_folder / source_path.name
    dropbox_folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)
    return target_path


def get_dropbox_folder_path(payment_date: date, company_name: str, payment_handler: str | None) -> Path:
    """Get the Dropbox folder path for a receipt (without creating it).

    Args:
        payment_date: Payment date for folder naming
        company_name: Company name for folder naming
        payment_handler: Optional payment handler

    Returns:
        Full path to the Dropbox folder
    """
    date_str = payment_date.isoformat()
    year = str(payment_date.year)

    if payment_handler and payment_handler != company_name:
        folder_name = f"{date_str} {company_name} ({payment_handler})"
    else:
        folder_name = f"{date_str} {company_name}"

    # Clean folder name of invalid characters
    for char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
        folder_name = folder_name.replace(char, '')

    return config.DROPBOX_LOCAL_PATH / year / folder_name
