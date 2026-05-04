"""Receipt Manager - FastAPI Application."""

import shutil
import subprocess
import sys
from collections import OrderedDict
from datetime import datetime, date
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, UploadFile, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import config
import database
import dropbox_handler
import email_handler
from database import Receipt, Attachment
from ocr_handler import extract_receipt_data

app = FastAPI(title="Receipt Manager")

# Setup static files and templates
static_dir = Path(__file__).parent / "static"
templates_dir = Path(__file__).parent / "templates"
static_dir.mkdir(exist_ok=True)
templates_dir.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=templates_dir)


def get_ohanterade_folder() -> Path | None:
    """Get ohanterade folder path from DB setting or config fallback."""
    # Check database first
    db_path = database.get_setting('ohanterade_folder')
    if db_path:
        folder = Path(db_path)
        if folder.exists():
            return folder
    # Fallback to .env config
    if config.OHANTERADE_FOLDER and config.OHANTERADE_FOLDER.exists():
        return config.OHANTERADE_FOLDER
    return None


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Home page with upload form."""
    receipts = database.get_all_receipts(limit=10)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "receipts": receipts,
        "config_status": config.validate_config(),
    })


@app.post("/upload")
async def upload_receipt(request: Request, file: UploadFile = File(...), backend: str = Form(None)):
    """Handle receipt upload and OCR extraction."""
    # Save uploaded file temporarily
    upload_dir = config.RECEIPT_DROPS_DIR
    upload_dir.mkdir(exist_ok=True)

    # Generate unique filename if file already exists
    base_name = Path(file.filename).stem
    ext = Path(file.filename).suffix
    file_path = upload_dir / file.filename
    counter = 2
    while file_path.exists():
        file_path = upload_dir / f"{base_name}-{counter}{ext}"
        counter += 1

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Normalize empty backend to None
    backend = backend.strip() if backend else None

    # Skip OCR for manual entry
    if backend == "manual":
        return templates.TemplateResponse("confirm.html", {
            "request": request,
            "filename": file_path.name,
            "original_filename": file.filename,
            "payment_date": "",
            "company_name": "",
            "payment_handler": "",
            "raw_text": "",
            "confidence": 0,
            "ocr_cost": 0,
            "current_backend": "",
            "manual": True,
            "gemma_available": bool(config.GEMMA_API_BASE),
        })

    # Run OCR extraction
    try:
        result = extract_receipt_data(file_path, backend=backend)
    except Exception as e:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": f"OCR failed: {e}",
            "filename": file_path.name,  # Allow retry with GPT-4
        })

    # Show confirmation form
    return templates.TemplateResponse("confirm.html", {
        "request": request,
        "filename": file_path.name,
        "original_filename": file.filename,
        "payment_date": result.payment_date.isoformat() if result.payment_date else "",
        "company_name": result.company_name or "",
        "payment_handler": result.payment_handler or "",
        "raw_text": result.raw_text,
        "confidence": result.confidence,
        "ocr_cost": result.ocr_cost,
        "current_backend": config.OCR_BACKEND,
        "manual": False,
        "gemma_available": bool(config.GEMMA_API_BASE),
    })


@app.post("/retry-ocr")
async def retry_ocr(request: Request, filename: str = Form(...), backend: str = Form(...), original_filename: str = Form("")):
    """Retry OCR with a different backend."""
    file_path = config.RECEIPT_DROPS_DIR / filename

    if not file_path.exists():
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": f"File not found: {filename}",
        })

    # Run OCR with specified backend
    try:
        result = extract_receipt_data(file_path, backend=backend)
    except Exception as e:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": f"OCR failed with {backend}: {e}",
        })

    # Show confirmation form
    return templates.TemplateResponse("confirm.html", {
        "request": request,
        "filename": filename,
        "original_filename": original_filename or filename,
        "payment_date": result.payment_date.isoformat() if result.payment_date else "",
        "company_name": result.company_name or "",
        "payment_handler": result.payment_handler or "",
        "raw_text": result.raw_text,
        "confidence": result.confidence,
        "ocr_cost": result.ocr_cost,
        "current_backend": backend,
        "gemma_available": bool(config.GEMMA_API_BASE),
    })


def sanitize_for_path(name: str) -> str:
    """Remove characters that are invalid in Windows folder/file names."""
    for char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
        name = name.replace(char, '')
    return name.strip()


def get_staging_folder_path(payment_date: date | None, company_name: str, payment_handler: str | None) -> Path:
    """Generate staging folder path mirroring Dropbox structure."""
    date_str = payment_date.isoformat() if payment_date else datetime.now().strftime("%Y-%m-%d")
    year = date_str[:4]

    safe_company = sanitize_for_path(company_name)
    if payment_handler:
        safe_handler = sanitize_for_path(payment_handler)
        folder_name = f"{date_str} {safe_company} ({safe_handler})"
    else:
        folder_name = f"{date_str} {safe_company}"

    return config.RECEIPT_DROPS_DIR / year / folder_name


@app.post("/process")
async def process_receipt(
    request: Request,
    filename: str = Form(...),
    original_filename: str = Form(""),
    payment_date: str = Form(...),
    company_name: str = Form(...),
    payment_handler: str = Form(""),
    category: str = Form(...),
    notes: str = Form(""),
    ocr_cost: float = Form(0.0),
    extra_files: list[UploadFile] = File(default=[]),
    reminder_source_id: int | None = Form(default=None),
    reminder_year_month: str | None = Form(default=None),
):
    """Process confirmed receipt: save to DB, create staging folder structure, send email."""
    # Parse date
    try:
        parsed_date = date.fromisoformat(payment_date) if payment_date else None
    except ValueError:
        parsed_date = None

    # Generate stored filename: YYYY-MM-DD-companyname.pdf (with counter if exists)
    safe_company = company_name.lower().replace(" ", "-")
    for char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|', ',']:
        safe_company = safe_company.replace(char, '')

    date_str = parsed_date.isoformat() if parsed_date else datetime.now().strftime("%Y-%m-%d")
    base_filename = f"{date_str}-{safe_company}"

    # Create staging folder structure: receipt drops/YYYY/YYYY-MM-DD CompanyName/
    staging_folder = get_staging_folder_path(parsed_date, company_name, payment_handler or None)
    staging_folder.mkdir(parents=True, exist_ok=True)

    # Find unique filename (add counter if file exists)
    stored_filename = f"{base_filename}.pdf"
    staged_file_path = staging_folder / stored_filename
    counter = 2
    while staged_file_path.exists():
        stored_filename = f"{base_filename}-{counter}.pdf"
        staged_file_path = staging_folder / stored_filename
        counter += 1

    # Move file from upload location to staging folder with proper name
    source_path = config.RECEIPT_DROPS_DIR / filename
    shutil.move(str(source_path), str(staged_file_path))

    # Create receipt record
    receipt = Receipt(
        id=None,
        original_filename=filename,
        stored_filename=stored_filename,
        payment_date=parsed_date,
        company_name=company_name,
        payment_handler=payment_handler or None,
        category=category,
        staging_path=str(staged_file_path),
        dropbox_path=None,  # Will be set after Dropbox upload
        email_sent_to=None,
        email_sent_at=None,
        notes=notes or None,
        ocr_cost=ocr_cost if ocr_cost > 0 else None,
    )

    # Save to database first (so we have receipt_id for attachments)
    receipt_id = database.add_receipt(receipt)

    # NOTE: do NOT auto-mark this new receipt as recurring. The original source
    # already carries the recurring flag; mirroring it here multiplies sources
    # every month and creates duplicate reminders.

    # Handle extra attachments (save to staging folder)
    extra_paths = []
    for extra_file in extra_files:
        if extra_file.filename:  # Skip empty file inputs
            # Find unique filename if file exists
            base_name = Path(extra_file.filename).stem
            ext = Path(extra_file.filename).suffix
            stored_name = extra_file.filename
            file_path = staging_folder / stored_name
            counter = 2
            while file_path.exists():
                stored_name = f"{base_name}-{counter}{ext}"
                file_path = staging_folder / stored_name
                counter += 1

            # Save the file
            with open(file_path, "wb") as f:
                shutil.copyfileobj(extra_file.file, f)

            # Create attachment record
            attachment = Attachment(
                id=None,
                receipt_id=receipt_id,
                original_filename=extra_file.filename,
                stored_filename=stored_name,
                file_path=str(file_path),
            )
            database.add_attachment(attachment)
            extra_paths.append(file_path)

    # Send email to bookkeeper (after attachments are saved)
    if config.SMTP_USERNAME and config.SMTP_PASSWORD:
        try:
            email_to = config.get_email_for_category(category)
            email_handler.send_receipt_email(
                to_email=email_to,
                company_name=company_name,
                payment_date=parsed_date,
                payment_handler=payment_handler or None,
                category=category,
                attachment_path=staged_file_path,
                stored_filename=stored_filename,
                notes=notes or None,
                extra_attachments=extra_paths if extra_paths else None,
            )
            # Update receipt with email info
            database.update_receipt(
                receipt_id,
                email_sent_to=email_to,
                email_sent_at=datetime.now(),
            )
        except Exception as e:
            print(f"Email send failed: {e}")

    # If this was a reminder upload, dismiss that specific month's reminder as fulfilled
    if reminder_source_id and reminder_year_month:
        database.dismiss_reminder(
            reminder_source_id, reminder_year_month,
            status="fulfilled", fulfilled_receipt_id=receipt_id,
        )

    # Build redirect with cleanup filenames if ohanterade folder is configured
    redirect_url = f"/receipt/{receipt_id}"
    ohanterade = get_ohanterade_folder()
    if ohanterade:
        # Collect original filenames for potential cleanup
        cleanup_files = [original_filename or filename]  # Main receipt
        for extra_file in extra_files:
            if extra_file.filename:
                cleanup_files.append(extra_file.filename)
        redirect_url += f"?cleanup={','.join(quote(f, safe='') for f in cleanup_files)}"

    return RedirectResponse(url=redirect_url, status_code=303)


@app.get("/receipt/{receipt_id}", response_class=HTMLResponse)
async def view_receipt(request: Request, receipt_id: int, cleanup: str | None = None):
    """View a single receipt."""
    receipt = database.get_receipt(receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")

    attachments = database.get_attachments(receipt_id)

    # Parse cleanup filenames if provided
    cleanup_files = cleanup.split(',') if cleanup else []
    ohanterade = get_ohanterade_folder()

    return templates.TemplateResponse("receipt.html", {
        "request": request,
        "receipt": receipt,
        "attachments": attachments,
        "cleanup_files": cleanup_files,
        "ohanterade_configured": ohanterade is not None,
        "ohanterade_path": str(ohanterade) if ohanterade else "",
    })


@app.get("/receipt/{receipt_id}/edit", response_class=HTMLResponse)
async def edit_receipt_form(request: Request, receipt_id: int):
    """Show edit form for a receipt."""
    receipt = database.get_receipt(receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")

    attachments = database.get_attachments(receipt_id)

    return templates.TemplateResponse("edit.html", {
        "request": request,
        "receipt": receipt,
        "attachments": attachments,
    })


@app.post("/receipt/{receipt_id}/edit")
async def save_receipt_edit(
    request: Request,
    receipt_id: int,
    payment_date: str = Form(...),
    company_name: str = Form(...),
    payment_handler: str = Form(""),
    category: str = Form(...),
    notes: str = Form(""),
):
    """Save edited receipt."""
    receipt = database.get_receipt(receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")

    # Parse date
    try:
        parsed_date = date.fromisoformat(payment_date) if payment_date else None
    except ValueError:
        parsed_date = None

    # Update stored filename based on new values
    safe_company = company_name.lower().replace(" ", "-")
    for char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|', ',']:
        safe_company = safe_company.replace(char, '')
    date_str = parsed_date.isoformat() if parsed_date else datetime.now().strftime("%Y-%m-%d")
    base_filename = f"{date_str}-{safe_company}"

    # Check if folder-affecting data changed and receipt is still in staging
    new_staging_path = None
    if receipt.staging_path and not receipt.dropbox_path:
        old_folder = Path(receipt.staging_path).parent
        old_file = Path(receipt.staging_path)
        new_folder = get_staging_folder_path(parsed_date, company_name, payment_handler or None)

        if old_folder != new_folder:
            # Ensure target folder exists
            new_folder.mkdir(parents=True, exist_ok=True)

            # Find unique filename in new folder
            stored_filename = f"{base_filename}.pdf"
            new_file = new_folder / stored_filename
            counter = 2
            while new_file.exists():
                stored_filename = f"{base_filename}-{counter}.pdf"
                new_file = new_folder / stored_filename
                counter += 1

            # Move the file (not the folder) to target folder
            if old_file.exists():
                shutil.move(str(old_file), str(new_file))
            new_staging_path = str(new_file)

            # Move attachments to new folder
            for att in database.get_attachments(receipt_id):
                old_att_path = Path(att.file_path)
                if old_att_path.exists():
                    new_att_path = new_folder / old_att_path.name
                    # Handle attachment filename collision
                    if new_att_path.exists():
                        att_base = old_att_path.stem
                        att_ext = old_att_path.suffix
                        att_counter = 2
                        while new_att_path.exists():
                            new_att_path = new_folder / f"{att_base}-{att_counter}{att_ext}"
                            att_counter += 1
                    shutil.move(str(old_att_path), str(new_att_path))
                    database.update_attachment_path(att.id, str(new_att_path))

            # Delete old folder if empty
            if old_folder.exists() and not any(old_folder.iterdir()):
                old_folder.rmdir()
        else:
            # Folder same, but maybe filename changed - find unique name
            stored_filename = f"{base_filename}.pdf"
            new_file = old_folder / stored_filename
            counter = 2
            while new_file.exists() and new_file != old_file:
                stored_filename = f"{base_filename}-{counter}.pdf"
                new_file = old_folder / stored_filename
                counter += 1

            if old_file.exists() and old_file != new_file:
                shutil.move(str(old_file), str(new_file))
            new_staging_path = str(new_file)
    else:
        stored_filename = f"{base_filename}.pdf"

    # Update database
    update_fields = {
        "payment_date": parsed_date,
        "company_name": company_name,
        "payment_handler": payment_handler or None,
        "category": category,
        "stored_filename": stored_filename,
        "notes": notes or None,
    }
    if new_staging_path:
        update_fields["staging_path"] = new_staging_path

    database.update_receipt(receipt_id, **update_fields)

    return RedirectResponse(url=f"/receipt/{receipt_id}", status_code=303)


@app.get("/receipts", response_class=HTMLResponse)
async def list_receipts(
    request: Request,
    category: str | None = None,
    company: str | None = None,
    email_status: str | None = None,
    year: str | None = None,
    page: int = 1,
):
    """List all receipts with optional filters."""
    per_page = 500
    offset = (page - 1) * per_page

    # Convert year string to int (empty string = None)
    year_int = int(year) if year else None

    receipts = database.get_all_receipts(
        limit=per_page,
        offset=offset,
        category=category,
        company=company,
        email_status=email_status,
        year=year_int,
    )
    total = database.get_receipt_count(category=category, email_status=email_status, year=year_int)
    available_years = database.get_available_years()
    total_ocr_cost = database.get_total_ocr_cost()

    # Get attachments for all displayed receipts
    attachments_by_receipt = {}
    for r in receipts:
        attachments_by_receipt[r.id] = database.get_attachments(r.id)

    # Group receipts by month (YYYY-MM) sorted descending
    grouped_receipts = OrderedDict()
    month_names = {
        1: "January", 2: "February", 3: "March", 4: "April",
        5: "May", 6: "June", 7: "July", 8: "August",
        9: "September", 10: "October", 11: "November", 12: "December",
    }
    # Sort receipts by payment_date descending for consistent grouping
    sorted_receipts = sorted(
        receipts,
        key=lambda r: r.payment_date or date.min,
        reverse=True,
    )
    for r in sorted_receipts:
        if r.payment_date:
            month_key = r.payment_date.strftime("%Y-%m")
            month_label = f"{month_names[r.payment_date.month]} {r.payment_date.year}"
        else:
            month_key = "Unknown"
            month_label = "Unknown"
        if month_key not in grouped_receipts:
            grouped_receipts[month_key] = {"label": month_label, "receipts": []}
        grouped_receipts[month_key]["receipts"].append(r)

    # Determine current month key for default-open behavior
    now = datetime.now()
    current_month_key = now.strftime("%Y-%m")

    # Get due reminders for current month
    due_reminders = database.get_due_reminders()

    # Get fulfilled reminders for current month to mark in the list
    year_month = now.strftime("%Y-%m")
    fulfilled = database.get_fulfilled_reminders(year_month)
    fulfilled_source_ids = {f["receipt_id"] for f in fulfilled}
    fulfilled_receipt_ids = {f["fulfilled_receipt_id"] for f in fulfilled if f["fulfilled_receipt_id"]}

    return templates.TemplateResponse("receipts.html", {
        "request": request,
        "receipts": receipts,
        "grouped_receipts": grouped_receipts,
        "current_month_key": current_month_key,
        "attachments_by_receipt": attachments_by_receipt,
        "page": page,
        "total": total,
        "per_page": per_page,
        "category": category,
        "company": company,
        "email_status": email_status,
        "year": year_int,
        "available_years": available_years,
        "total_ocr_cost": total_ocr_cost,
        "due_reminders": due_reminders,
        "fulfilled_source_ids": fulfilled_source_ids,
        "fulfilled_receipt_ids": fulfilled_receipt_ids,
        "config_status": config.validate_config(),
    })


@app.post("/receipt/{receipt_id}/resend")
async def resend_email(receipt_id: int):
    """Resend email for a receipt."""
    receipt = database.get_receipt(receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")

    # Find the main file - try Dropbox, then staging
    attachment_path = None
    if receipt.dropbox_path:
        dropbox_file = Path(receipt.dropbox_path)
        if dropbox_file.is_absolute():
            attachment_path = dropbox_file
        else:
            attachment_path = config.DROPBOX_LOCAL_PATH / dropbox_file
    if not attachment_path or not attachment_path.exists():
        if receipt.staging_path:
            attachment_path = Path(receipt.staging_path)

    if not attachment_path or not attachment_path.exists():
        return {"success": False, "error": "File not found"}

    # Get extra attachments
    attachments = database.get_attachments(receipt_id)
    extra_paths = [Path(a.file_path) for a in attachments if Path(a.file_path).exists()]

    # Send email
    if not config.SMTP_USERNAME or not config.SMTP_PASSWORD:
        return {"success": False, "error": "Email not configured"}

    try:
        email_to = config.get_email_for_category(receipt.category)
        email_handler.send_receipt_email(
            to_email=email_to,
            company_name=receipt.company_name,
            payment_date=receipt.payment_date,
            payment_handler=receipt.payment_handler,
            category=receipt.category,
            attachment_path=attachment_path,
            stored_filename=receipt.stored_filename,
            extra_attachments=extra_paths,
            notes=receipt.notes,
        )
        # Update database
        database.update_receipt(
            receipt_id,
            email_sent_to=email_to,
            email_sent_at=datetime.now(),
        )
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/receipt/{receipt_id}/dropbox")
async def send_to_dropbox(receipt_id: int, force: bool = False):
    """Send receipt and attachments to Dropbox folder."""
    receipt = database.get_receipt(receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")

    # Skip if already uploaded (unless force)
    if receipt.dropbox_path and not force:
        return {"success": False, "error": "Already uploaded. Use force=true to re-upload."}

    if not receipt.payment_date:
        return {"success": False, "error": "Payment date required"}

    if not receipt.staging_path:
        return {"success": False, "error": "No staging file found"}

    source_path = Path(receipt.staging_path)
    if not source_path.exists():
        return {"success": False, "error": "Source file not found"}

    try:
        # Copy main receipt to Dropbox
        dropbox_path = dropbox_handler.upload_receipt(
            source_path=source_path,
            payment_date=receipt.payment_date,
            company_name=receipt.company_name,
            payment_handler=receipt.payment_handler,
            stored_filename=receipt.stored_filename,
        )

        # Get the Dropbox folder path for attachments
        dropbox_folder = dropbox_handler.get_dropbox_folder_path(
            receipt.payment_date,
            receipt.company_name,
            receipt.payment_handler,
        )

        # Copy attachments to Dropbox folder
        attachments = database.get_attachments(receipt_id)
        for att in attachments:
            att_source = Path(att.file_path)
            if att_source.exists():
                new_path = dropbox_handler.copy_attachment_to_folder(att_source, dropbox_folder)
                # Update attachment path in database
                database.update_attachment_path(att.id, str(new_path))

        # Update receipt with Dropbox path
        database.update_receipt(
            receipt_id,
            dropbox_path=str(dropbox_handler.get_full_path(dropbox_path)),
        )

        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/receipts/upload-all-dropbox")
async def upload_all_to_dropbox(include_uploaded: bool = False):
    """Upload all receipts that haven't been sent to Dropbox yet."""
    # Get receipts to upload
    conn = database.get_connection()
    if include_uploaded:
        rows = conn.execute("SELECT id FROM receipts").fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM receipts WHERE dropbox_path IS NULL OR dropbox_path = ''"
        ).fetchall()
    conn.close()

    if not rows:
        return {"success": True, "uploaded": 0, "failed": 0, "message": "No pending receipts"}

    uploaded = 0
    failed = 0
    errors = []

    for row in rows:
        receipt_id = row['id']
        receipt = database.get_receipt(receipt_id)

        if not receipt or not receipt.payment_date or not receipt.staging_path:
            failed += 1
            errors.append(f"Receipt #{receipt_id}: Missing required data")
            continue

        source_path = Path(receipt.staging_path)
        if not source_path.exists():
            failed += 1
            errors.append(f"Receipt #{receipt_id}: Source file not found")
            continue

        try:
            # Copy main receipt to Dropbox
            dropbox_path = dropbox_handler.upload_receipt(
                source_path=source_path,
                payment_date=receipt.payment_date,
                company_name=receipt.company_name,
                payment_handler=receipt.payment_handler,
                stored_filename=receipt.stored_filename,
            )

            # Get the Dropbox folder path for attachments
            dropbox_folder = dropbox_handler.get_dropbox_folder_path(
                receipt.payment_date,
                receipt.company_name,
                receipt.payment_handler,
            )

            # Copy attachments to Dropbox folder
            attachments = database.get_attachments(receipt_id)
            for att in attachments:
                att_source = Path(att.file_path)
                if att_source.exists():
                    new_path = dropbox_handler.copy_attachment_to_folder(att_source, dropbox_folder)
                    database.update_attachment_path(att.id, str(new_path))

            # Update receipt with Dropbox path
            database.update_receipt(
                receipt_id,
                dropbox_path=str(dropbox_handler.get_full_path(dropbox_path)),
            )
            uploaded += 1
        except Exception as e:
            failed += 1
            errors.append(f"Receipt #{receipt_id}: {str(e)}")

    return {
        "success": True,
        "uploaded": uploaded,
        "failed": failed,
        "errors": errors[:10] if errors else [],  # Limit error messages
    }


@app.post("/receipt/{receipt_id}/attachments")
async def upload_attachment(receipt_id: int, file: UploadFile = File(...)):
    """Upload an extra attachment to a receipt."""
    receipt = database.get_receipt(receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")

    # Determine where to save the attachment
    if receipt.dropbox_path:
        # Save in same Dropbox folder as receipt
        dropbox_file = Path(receipt.dropbox_path)
        if dropbox_file.is_absolute():
            target_folder = dropbox_file.parent
        else:
            target_folder = config.DROPBOX_LOCAL_PATH / dropbox_file.parent
    elif receipt.staging_path:
        # Save in same staging folder as receipt
        target_folder = Path(receipt.staging_path).parent
    else:
        # Fallback: generate staging folder
        target_folder = get_staging_folder_path(
            receipt.payment_date, receipt.company_name, receipt.payment_handler
        )
        target_folder.mkdir(parents=True, exist_ok=True)

    # Find unique filename if file exists
    base_name = Path(file.filename).stem
    ext = Path(file.filename).suffix
    stored_name = file.filename
    file_path = target_folder / stored_name
    counter = 2
    while file_path.exists():
        stored_name = f"{base_name}-{counter}{ext}"
        file_path = target_folder / stored_name
        counter += 1

    # Save the file
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Create attachment record
    attachment = Attachment(
        id=None,
        receipt_id=receipt_id,
        original_filename=file.filename,
        stored_filename=stored_name,
        file_path=str(file_path),
    )
    attachment_id = database.add_attachment(attachment)

    return {"success": True, "id": attachment_id, "filename": file.filename}


@app.delete("/attachment/{attachment_id}")
async def delete_attachment(attachment_id: int):
    """Delete an attachment and its file."""
    # Get attachment to find file path before deleting from DB
    conn = database.get_connection()
    row = conn.execute(
        "SELECT file_path FROM attachments WHERE id = ?", (attachment_id,)
    ).fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Attachment not found")

    # Delete the file if it exists
    file_path = Path(row['file_path'])
    if file_path.exists():
        file_path.unlink()

    # Delete from database
    if database.delete_attachment(attachment_id):
        return {"success": True}
    return {"success": False}


@app.post("/attachment/{attachment_id}/open")
async def open_attachment(attachment_id: int):
    """Open an attachment file directly."""
    conn = database.get_connection()
    row = conn.execute(
        "SELECT file_path FROM attachments WHERE id = ?", (attachment_id,)
    ).fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Attachment not found")

    file_path = Path(row['file_path'])
    if file_path.exists():
        if sys.platform == 'win32':
            import os
            os.startfile(str(file_path))
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', str(file_path)])
        else:
            subprocess.Popen(['xdg-open', str(file_path)])
        return {"success": True}
    else:
        return {"success": False, "error": "File not found"}


@app.post("/receipt/{receipt_id}/open-folder")
async def open_receipt_folder(receipt_id: int):
    """Open the folder containing this receipt in file explorer."""
    receipt = database.get_receipt(receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")

    # Determine the folder path - prefer Dropbox if archived, else staging
    if receipt.dropbox_path:
        dropbox_file = Path(receipt.dropbox_path)
        # Handle both absolute and relative paths
        if dropbox_file.is_absolute():
            folder_path = dropbox_file.parent
        else:
            folder_path = config.DROPBOX_LOCAL_PATH / dropbox_file.parent
    elif receipt.staging_path:
        folder_path = Path(receipt.staging_path).parent
    else:
        # Fallback: generate expected staging path
        folder_path = get_staging_folder_path(
            receipt.payment_date, receipt.company_name, receipt.payment_handler
        )

    if folder_path.exists():
        if sys.platform == 'win32':
            # Use os.startfile for reliable folder opening on Windows
            import os
            os.startfile(str(folder_path))
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', str(folder_path)])
        else:
            subprocess.Popen(['xdg-open', str(folder_path)])
        return {"success": True}
    else:
        return {"success": False, "error": "Folder not found"}


@app.post("/receipt/{receipt_id}/open-file")
async def open_receipt_file(receipt_id: int):
    """Open the receipt PDF file directly."""
    receipt = database.get_receipt(receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")

    # Determine the file path - prefer staging, else Dropbox
    file_path = None
    if receipt.staging_path:
        file_path = Path(receipt.staging_path)
    elif receipt.dropbox_path:
        dropbox_file = Path(receipt.dropbox_path)
        if dropbox_file.is_absolute():
            file_path = dropbox_file
        else:
            file_path = config.DROPBOX_LOCAL_PATH / dropbox_file

    if file_path and file_path.exists():
        if sys.platform == 'win32':
            import os
            os.startfile(str(file_path))
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', str(file_path)])
        else:
            subprocess.Popen(['xdg-open', str(file_path)])
        return {"success": True}
    else:
        return {"success": False, "error": "File not found"}


@app.post("/open-staging-folder")
async def open_staging_folder():
    """Open the local staging folder in file explorer."""
    folder_path = config.RECEIPT_DROPS_DIR
    folder_path.mkdir(exist_ok=True)

    if sys.platform == 'win32':
        import os
        os.startfile(str(folder_path))
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', str(folder_path)])
    else:
        subprocess.Popen(['xdg-open', str(folder_path)])
    return {"success": True}


@app.delete("/receipt/{receipt_id}")
async def delete_receipt(receipt_id: int):
    """Delete a receipt. If in staging (not archived), also delete files."""
    receipt = database.get_receipt(receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")

    # Delete staging folder if not archived to Dropbox
    if receipt.staging_path and not receipt.dropbox_path:
        staging_folder = Path(receipt.staging_path).parent
        if staging_folder.exists() and str(config.RECEIPT_DROPS_DIR) in str(staging_folder):
            shutil.rmtree(staging_folder, ignore_errors=True)

    if database.delete_receipt(receipt_id):
        return {"success": True}
    return {"success": False}


@app.post("/delete-originals")
async def delete_original_files(filenames: list[str] = Form(...)):
    """Delete original files from the ohanterade folder after processing."""
    ohanterade = get_ohanterade_folder()
    if not ohanterade:
        return {"success": False, "error": "Ohanterade folder not configured"}

    deleted = []
    not_found = []
    errors = []

    for filename in filenames:
        # Security: only allow simple filenames, no path traversal
        if '/' in filename or '\\' in filename or '..' in filename:
            errors.append(f"{filename}: Invalid filename")
            continue

        file_path = ohanterade / filename

        # Extra security: ensure the resolved path is still within ohanterade folder
        try:
            resolved = file_path.resolve()
            if not str(resolved).startswith(str(ohanterade.resolve())):
                errors.append(f"{filename}: Path traversal blocked")
                continue
        except Exception:
            errors.append(f"{filename}: Invalid path")
            continue

        if file_path.exists():
            try:
                file_path.unlink()
                deleted.append(filename)
            except Exception as e:
                errors.append(f"{filename}: {str(e)}")
        else:
            not_found.append(filename)

    return {
        "success": len(deleted) > 0 or (len(not_found) > 0 and len(errors) == 0),
        "deleted": deleted,
        "not_found": not_found,
        "errors": errors,
    }


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Settings page."""
    settings = database.get_all_settings()

    # Check folder status
    folder_path = settings.get('ohanterade_folder', '')
    if folder_path:
        folder_status = 'valid' if Path(folder_path).exists() else 'invalid'
    else:
        folder_status = 'empty'

    return templates.TemplateResponse("settings.html", {
        "request": request,
        "settings": settings,
        "folder_status": folder_status,
        "dropbox_path": str(config.DROPBOX_LOCAL_PATH) if config.DROPBOX_LOCAL_PATH else None,
        "ocr_backend": config.OCR_BACKEND,
        "email_configured": bool(config.SMTP_USERNAME and config.SMTP_PASSWORD),
    })


@app.post("/settings")
async def save_settings(
    request: Request,
    ohanterade_folder: str = Form(""),
):
    """Save settings."""
    # Save ohanterade folder (strip whitespace)
    ohanterade_folder = ohanterade_folder.strip()
    if ohanterade_folder:
        database.set_setting('ohanterade_folder', ohanterade_folder)
    else:
        # Clear the setting if empty
        database.set_setting('ohanterade_folder', '')

    return RedirectResponse(url="/settings", status_code=303)


@app.get("/api/ohanterade-configured")
async def check_ohanterade_configured():
    """Check if ohanterade folder is configured and exists."""
    if config.OHANTERADE_FOLDER and config.OHANTERADE_FOLDER.exists():
        return {"configured": True, "path": str(config.OHANTERADE_FOLDER)}
    return {"configured": False}


# API endpoints for future use
@app.get("/api/receipts")
async def api_list_receipts(
    limit: int = 100,
    offset: int = 0,
    category: str | None = None,
):
    """API: List receipts as JSON."""
    receipts = database.get_all_receipts(limit=limit, offset=offset, category=category)
    return [
        {
            "id": r.id,
            "original_filename": r.original_filename,
            "stored_filename": r.stored_filename,
            "payment_date": r.payment_date.isoformat() if r.payment_date else None,
            "company_name": r.company_name,
            "category": r.category,
            "dropbox_path": r.dropbox_path,
            "email_sent_to": r.email_sent_to,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in receipts
    ]


# Aliases endpoints

@app.get("/aliases", response_class=HTMLResponse)
async def aliases_page(request: Request):
    return templates.TemplateResponse("aliases.html", {
        "request": request,
        "aliases": database.list_aliases(),
    })


@app.post("/aliases")
async def create_alias(alias: str = Form(...), canonical: str = Form(...)):
    database.add_alias(alias, canonical)
    return RedirectResponse(url="/aliases", status_code=303)


@app.post("/aliases/{alias_id}/delete")
async def remove_alias(alias_id: int):
    database.delete_alias(alias_id)
    return RedirectResponse(url="/aliases", status_code=303)


# Reminder endpoints

@app.post("/receipt/{receipt_id}/toggle-recurring")
async def toggle_recurring(receipt_id: int, confirm: bool = Form(default=False)):
    """Toggle the recurring reminder flag on a receipt.

    When turning ON, refuse if another receipt with the same canonical
    company name (after aliases) is already recurring — unless the client
    explicitly passes confirm=true. This catches accidental double-checking
    of the same monthly bill.
    """
    receipt = database.get_receipt(receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")

    new_value = not receipt.is_recurring

    if new_value and not confirm:
        existing = database.find_recurring_by_canonical(receipt.company_name, exclude_id=receipt_id)
        if existing:
            other = existing[0]
            return {
                "success": False,
                "duplicate": True,
                "message": (
                    f"Another receipt is already set as recurring for the same vendor: "
                    f"'{other['company_name']}' on {other['payment_date']}. "
                    f"Marking this one too will create duplicate monthly reminders."
                ),
            }

    database.update_receipt(receipt_id, is_recurring=int(new_value))
    return {"success": True, "is_recurring": new_value}


@app.post("/receipt/{receipt_id}/stop-recurring")
async def stop_recurring(receipt_id: int):
    """Permanently stop a recurring reminder. Sets is_recurring=0 on the
    source receipt so all currently-pending and future months for it disappear.
    """
    receipt = database.get_receipt(receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")
    database.update_receipt(receipt_id, is_recurring=0)
    return {"success": True}


@app.post("/receipt/{receipt_id}/dismiss-reminder")
async def dismiss_reminder(receipt_id: int, year_month: str = Form(...)):
    """Dismiss a recurring reminder for a specific month (e.g. '2026-04')."""
    receipt = database.get_receipt(receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")

    success = database.dismiss_reminder(receipt_id, year_month, status="done")
    return {"success": success}


@app.post("/receipt/{receipt_id}/upload-reminder")
async def upload_reminder(request: Request, receipt_id: int, file: UploadFile = File(...), backend: str = Form(""), year_month: str = Form(...)):
    """Upload a file to fulfill a recurring reminder for a specific month (e.g. '2026-04')."""
    receipt = database.get_receipt(receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")

    # Save uploaded file temporarily
    upload_dir = config.RECEIPT_DROPS_DIR
    upload_dir.mkdir(exist_ok=True)

    base_name = Path(file.filename).stem
    ext = Path(file.filename).suffix
    file_path = upload_dir / file.filename
    counter = 2
    while file_path.exists():
        file_path = upload_dir / f"{base_name}-{counter}{ext}"
        counter += 1

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Calculate expected date for the target reminder month.
    import calendar
    try:
        target_year, target_month = (int(p) for p in year_month.split("-"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid year_month")
    last_day = calendar.monthrange(target_year, target_month)[1]
    reminder_day = min(receipt.payment_date.day, last_day)
    prefilled_date = date(target_year, target_month, reminder_day)

    backend = backend.strip() if backend else None

    if not backend or backend == "manual":
        # Skip OCR — pre-fill from source receipt
        return templates.TemplateResponse("confirm.html", {
            "request": request,
            "filename": file_path.name,
            "original_filename": file.filename,
            "payment_date": prefilled_date.isoformat(),
            "company_name": receipt.company_name,
            "payment_handler": receipt.payment_handler or "",
            "confidence": 1.0,
            "raw_text": "",
            "manual": True,
            "current_backend": "reminder",
            "ocr_cost": 0,
            "reminder_source_id": receipt.id,
            "category_prefill": receipt.category,
            "gemma_available": bool(config.GEMMA_API_BASE),
        })

    # Run OCR extraction
    try:
        result = extract_receipt_data(file_path, backend=backend if backend else None)
    except Exception as e:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": f"OCR failed: {e}",
            "filename": file_path.name,
        })

    return templates.TemplateResponse("confirm.html", {
        "request": request,
        "filename": file_path.name,
        "original_filename": file.filename,
        "payment_date": result.payment_date.isoformat() if result.payment_date else prefilled_date.isoformat(),
        "company_name": result.company_name or receipt.company_name,
        "payment_handler": result.payment_handler or receipt.payment_handler or "",
        "raw_text": result.raw_text,
        "confidence": result.confidence,
        "ocr_cost": result.ocr_cost,
        "current_backend": backend or config.OCR_BACKEND,
        "manual": False,
        "reminder_source_id": receipt.id,
        "reminder_year_month": year_month,
        "category_prefill": receipt.category,
        "gemma_available": bool(config.GEMMA_API_BASE),
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=True)
