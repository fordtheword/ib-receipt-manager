"""Pluggable OCR system for receipt data extraction.

Supports multiple backends:
- Tesseract (free, local)
- EasyOCR (free, local, better Swedish support)
- Claude Vision (paid, best accuracy)
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from PIL import Image

import config


@dataclass
class ReceiptData:
    """Extracted receipt data."""
    payment_date: date | None
    company_name: str | None
    payment_handler: str | None  # Klarna, Avarda, etc.
    raw_text: str
    confidence: float  # 0.0 to 1.0
    ocr_cost: float = 0.0  # API cost in USD


class OCRBackend(ABC):
    """Abstract base class for OCR backends."""

    @abstractmethod
    def extract_text(self, image_path: Path) -> str:
        """Extract raw text from image."""
        pass

    def extract_receipt_data(self, image_paths: list[Path]) -> ReceiptData:
        """Extract structured receipt data from one or more images."""
        # Default: OCR each page and concatenate
        raw_texts = [self.extract_text(p) for p in image_paths]
        raw_text = '\n\n'.join(raw_texts)
        payment_date = self._parse_date(raw_text)
        company_name, payment_handler = self._parse_company_and_handler(raw_text)

        return ReceiptData(
            payment_date=payment_date,
            company_name=company_name,
            payment_handler=payment_handler,
            raw_text=raw_text,
            confidence=0.5 if payment_date or company_name else 0.2
        )

    def _parse_date(self, text: str) -> date | None:
        """Try to find a date in the text, prioritizing invoice/print date."""
        # Swedish month names
        swedish_months = {
            'jan': 1, 'januari': 1,
            'feb': 2, 'februari': 2,
            'mar': 3, 'mars': 3,
            'apr': 4, 'april': 4,
            'maj': 5,
            'jun': 6, 'juni': 6,
            'jul': 7, 'juli': 7,
            'aug': 8, 'augusti': 8,
            'sep': 9, 'sept': 9, 'september': 9,
            'okt': 10, 'oktober': 10,
            'nov': 11, 'november': 11,
            'dec': 12, 'december': 12,
        }

        # Swedish keywords - prioritize payment/due date over invoice date
        date_keywords = [
            r'betala\s*senast[:\s]*',
            r'betalning\s*oss\s*tillhanda[:\s]*',  # Resurs Bank style
            r'förfallodatum[:\s]*',
            r'förfallodag[:\s]*',
            r'förfaller[:\s]*',
            r'due\s*date[:\s]*',
            r'betalningsdatum[:\s]*',
            r'att\s*betala\s*senast[:\s]*',
            r'sista\s*betalningsdag[:\s]*',
            # Invoice dates (lower priority)
            r'utskriftsdatum[:\s]*',
            r'fakturadatum[:\s]*',
            r'orderdatum[:\s]*',
        ]

        # Date patterns
        date_patterns = [
            (r'(\d{4})-(\d{2})-(\d{2})', 'ymd'),  # 2024-12-29
            (r'(\d{2})/(\d{2})/(\d{4})', 'dmy'),  # 29/12/2024
            (r'(\d{2})\.(\d{2})\.(\d{4})', 'dmy'),  # 29.12.2024
            (r'(\d{2})-(\d{2})-(\d{4})', 'dmy'),  # 29-12-2024
        ]

        # Swedish text date pattern: "26 dec 2025" or "26 december 2025" or "26 nov. 2025"
        swedish_date_pattern = r'(\d{1,2})\s+(jan(?:uari)?|feb(?:ruari)?|mar(?:s)?|apr(?:il)?|maj|jun(?:i)?|jul(?:i)?|aug(?:usti)?|sep(?:t(?:ember)?)?|okt(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+(\d{4})'

        def parse_date_match(match, order):
            groups = match.groups()
            try:
                if order == 'ymd':
                    return date(int(groups[0]), int(groups[1]), int(groups[2]))
                else:  # dmy
                    return date(int(groups[2]), int(groups[1]), int(groups[0]))
            except ValueError:
                return None

        def parse_swedish_date(match):
            day = int(match.group(1))
            month_str = match.group(2).lower()
            year = int(match.group(3))
            month = swedish_months.get(month_str)
            if month:
                try:
                    return date(year, month, day)
                except ValueError:
                    return None
            return None

        # First, look for dates near Swedish keywords (prioritized)
        text_lower = text.lower()
        for keyword in date_keywords:
            keyword_match = re.search(keyword, text_lower)
            if keyword_match:
                # Look for date after the keyword (check next 100 chars to handle newlines)
                after_keyword = text[keyword_match.end():]
                after_keyword_lower = after_keyword[:100].lower()

                # Try Swedish text date first (e.g., "26 dec 2025")
                swedish_match = re.search(swedish_date_pattern, after_keyword_lower)
                if swedish_match:
                    result = parse_swedish_date(swedish_match)
                    if result:
                        return result

                # Then try numeric patterns
                for pattern, order in date_patterns:
                    match = re.search(pattern, after_keyword[:100])
                    if match:
                        result = parse_date_match(match, order)
                        if result:
                            return result

        # Fallback: find any date (numeric first, then Swedish text)
        for pattern, order in date_patterns:
            for match in re.finditer(pattern, text):
                result = parse_date_match(match, order)
                if result:
                    return result

        # Last resort: Swedish text date anywhere
        swedish_match = re.search(swedish_date_pattern, text.lower())
        if swedish_match:
            return parse_swedish_date(swedish_match)

        return None

    def _parse_company_and_handler(self, text: str) -> tuple[str | None, str | None]:
        """Try to find company name and payment handler in the text.

        Returns: (company_name, payment_handler)
        """
        lines = text.strip().split('\n')

        # Words to skip as company names (Swedish invoice terms)
        skip_words = {
            'faktura', 'invoice', 'kvitto', 'receipt', 'orderbekräftelse',
            'order', 'betalning', 'totalt', 'summa', 'moms', 'att betala',
            'belopp', 'datum', 'ocr', 'bankgiro', 'plusgiro', 'swish',
            'utskriftsdatum', 'sida', 'kontonummer', 'kundnummer',
        }

        # Payment handlers - these are NOT the actual vendor
        payment_handlers = {
            'klarna': 'Klarna',
            'avarda': 'Avarda',
            'svea': 'Svea',
            'walley': 'Walley',
            'collector': 'Collector',
            'resurs': 'Resurs',
            'qliro': 'Qliro',
            'billmate': 'Billmate',
            'paypal': 'PayPal',
            'nets': 'Nets',
            'bambora': 'Bambora',
            'stripe': 'Stripe',
            'tf bank': 'TF Bank',
            'nordea finans': 'Nordea Finans',
            'santander': 'Santander',
            'ikano bank': 'Ikano Bank',
        }

        # Detect payment handler in the text
        text_lower = text.lower()
        detected_handler = None
        for handler_key, handler_name in payment_handlers.items():
            if handler_key in text_lower:
                detected_handler = handler_name
                break

        # Look for company indicators (Swedish company types)
        company_patterns = [
            r'(.+?\s(?:AB|HB|KB|Aktiebolag|Handelsbolag|Kommanditbolag))\b',
            r'(.+?\s(?:Inc|LLC|Ltd|GmbH|AS|A/S))\b',
        ]

        def is_valid_company(name: str) -> bool:
            """Check if the name looks like a valid company name."""
            name_lower = name.lower().strip()
            if len(name_lower) < 3:
                return False
            # Skip if contains any skip word
            if any(skip in name_lower for skip in skip_words):
                return False
            if name_lower.isdigit():
                return False
            # Skip payment handlers - we want the actual vendor
            if any(handler in name_lower for handler in payment_handlers.keys()):
                return False
            # Skip lines that look like page headers (e.g., "sida 1(4)")
            if re.search(r'sida\s*\d+\s*\(\d+\)', name_lower):
                return False
            # Skip lines that are mostly numbers/dates
            if re.search(r'^\d{4}-\d{2}-\d{2}', name_lower):
                return False
            return True

        # First pass: look for line right after "FAKTURA" or similar header
        # This is most reliable for Swedish invoices
        for i, line in enumerate(lines[:10]):
            line_lower = line.strip().lower()
            if line_lower in ['faktura', 'invoice', 'kvitto', 'receipt']:
                # The company name is usually the next non-empty line
                for next_line in lines[i+1:i+4]:
                    next_line = next_line.strip()
                    if next_line and is_valid_company(next_line):
                        if len(next_line) > 3 and not next_line[0].isdigit():
                            return next_line, detected_handler

        # Second pass: look for explicit company type indicators
        for line in lines[:15]:
            line = line.strip()
            if not line:
                continue
            for pattern in company_patterns:
                match = re.search(pattern, line, re.IGNORECASE)
                if match:
                    company = match.group(1).strip()
                    if is_valid_company(company):
                        return company, detected_handler

        # Third pass: find substantial line that's not a skip word
        for line in lines[:10]:
            line = line.strip()
            if is_valid_company(line) and len(line) > 5:
                # Prefer lines with mixed case or proper capitalization
                if not line.isupper() or ' ' in line:
                    return line, detected_handler

        # Option 2: If no vendor found but payment handler detected, use handler as company
        if detected_handler:
            return detected_handler, detected_handler

        return None, detected_handler


class TesseractOCR(OCRBackend):
    """Tesseract OCR backend (free, local)."""

    def __init__(self):
        import pytesseract
        self.pytesseract = pytesseract
        # Set Swedish as primary language with English fallback
        self.lang = 'swe+eng'

    def extract_text(self, image_path: Path) -> str:
        image = Image.open(image_path)
        try:
            text = self.pytesseract.image_to_string(image, lang=self.lang)
        except Exception:
            # Fallback to English only if Swedish not installed
            text = self.pytesseract.image_to_string(image, lang='eng')
        return text


class EasyOCROCR(OCRBackend):
    """EasyOCR backend (free, local, good Swedish support)."""

    def __init__(self):
        import easyocr
        # Initialize with Swedish and English
        self.reader = easyocr.Reader(['sv', 'en'], gpu=False)

    def extract_text(self, image_path: Path) -> str:
        results = self.reader.readtext(str(image_path))
        # Combine all detected text
        lines = [text for (_, text, _) in results]
        return '\n'.join(lines)


class GPT4VisionOCR(OCRBackend):
    """GPT-4 Vision OCR backend (paid, excellent accuracy)."""

    def __init__(self):
        if not config.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY not set in .env")

        from openai import OpenAI
        self.client = OpenAI(api_key=config.OPENAI_API_KEY)
        self.model = "gpt-4o"

    def extract_text(self, image_path: Path) -> str:
        import base64

        with open(image_path, 'rb') as f:
            image_data = base64.standard_b64encode(f.read()).decode('utf-8')

        suffix = image_path.suffix.lower()
        media_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
        }
        media_type = media_types.get(suffix, 'image/jpeg')

        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media_type};base64,{image_data}"
                        }
                    },
                    {
                        "type": "text",
                        "text": "Extract all text from this receipt image. Return only the raw text, preserving the layout as much as possible."
                    }
                ]
            }]
        )

        return response.choices[0].message.content

    def extract_receipt_data(self, image_paths: list[Path]) -> ReceiptData:
        """Use GPT-4's understanding for structured extraction."""
        import base64
        import json

        media_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
        }

        # Build image content blocks for all pages
        image_content = []
        for image_path in image_paths:
            with open(image_path, 'rb') as f:
                image_data = base64.standard_b64encode(f.read()).decode('utf-8')
            media_type = media_types.get(image_path.suffix.lower(), 'image/jpeg')
            image_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{media_type};base64,{image_data}"
                }
            })

        image_content.append({
            "type": "text",
            "text": """Analyze this document (all pages) and extract payment date, company name, and total amount.

The document could be:
- A Swedish invoice (faktura) - look for förfallodatum, betala senast, betalningsdatum
- An English invoice - look for due date, payment due, invoice date
- An e-commerce order - look for order date, purchase date
- A receipt/kvitto - look for transaction date

For the DATE:
- PRIORITIZE due date (förfallodatum, betala senast, due date, payment due)
- IGNORE document print date (Datum in header) - this is NOT the payment date
- IGNORE email header dates (sent date, received date)
- For Kickstarter/crowdfunding: use "card charged" date, not backing date
- Format as YYYY-MM-DD

For the COMPANY:
- Use the merchant/vendor who sold the goods (e.g., webhallen.com, not Klarna)
- Do NOT use payment processors (Klarna, PayPal, Avarda) as the company

For PAYMENT HANDLER:
- Identify if a payment processor is used (Klarna, Avarda, Swish, PayPal, Resurs, etc.)
- Return the handler name, or null if direct payment

For the TOTAL:
- Swedish: totalt, att betala, belopp att betala, summa
- English: total, amount due, grand total
- Include currency (SEK, kr, $, €)

LANGUAGE RULE FOR raw_text:
- Detect the language of the receipt itself.
- Write raw_text in THAT SAME LANGUAGE. If the receipt is Swedish, raw_text MUST be Swedish. If German, German. Never translate.
- raw_text should be a short summary (1-2 sentences) of what the receipt is for.

Return JSON only, no other text:
{"payment_date": "YYYY-MM-DD", "company_name": "Company Name", "payment_handler": null, "total": "123.45 SEK", "raw_text": "summary in receipt's language"}

If you can't find a field, use null."""
        })

        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": image_content
            }]
        )

        # Calculate cost from usage (GPT-4o pricing)
        # Input: $2.50/1M tokens, Output: $10/1M tokens
        # Images: ~765 tokens for low detail, more for high detail
        ocr_cost = 0.0
        if hasattr(response, 'usage') and response.usage:
            input_tokens = response.usage.prompt_tokens
            output_tokens = response.usage.completion_tokens
            ocr_cost = (input_tokens * 2.50 / 1_000_000) + (output_tokens * 10.00 / 1_000_000)

        try:
            # Handle potential markdown code blocks in response
            text = response.choices[0].message.content
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]

            data = json.loads(text.strip())
            payment_date = None
            if data.get('payment_date'):
                parts = data['payment_date'].split('-')
                payment_date = date(int(parts[0]), int(parts[1]), int(parts[2]))

            return ReceiptData(
                payment_date=payment_date,
                company_name=data.get('company_name'),
                payment_handler=data.get('payment_handler'),
                raw_text=data.get('raw_text', ''),
                confidence=0.9,
                ocr_cost=ocr_cost
            )
        except (json.JSONDecodeError, KeyError, ValueError, IndexError):
            result = super().extract_receipt_data(image_paths)
            result.ocr_cost = ocr_cost
            return result


class Gemma4VisionOCR(GPT4VisionOCR):
    """Gemma 4 via Docker Model Runner (OpenAI-compatible, free — runs on a LAN/Tailscale host)."""

    def __init__(self):
        if not config.GEMMA_API_BASE:
            raise ValueError("GEMMA_API_BASE not set in .env")

        from openai import OpenAI
        # DMR doesn't require a real key, but the OpenAI SDK insists on one being present.
        self.client = OpenAI(api_key="not-needed", base_url=config.GEMMA_API_BASE)
        self.model = config.GEMMA_MODEL

    def extract_receipt_data(self, image_paths: list[Path]) -> ReceiptData:
        result = super().extract_receipt_data(image_paths)
        # Local inference is free — never report a cost.
        result.ocr_cost = 0.0
        return result


class LocalVisionOCR(OCRBackend):
    """Local vision LLM backend using llama-cpp-python with Qwen2.5-VL (free, no server needed)."""

    def __init__(self):
        if not config.LOCAL_VISION_MODEL or not config.LOCAL_VISION_MMPROJ:
            raise ValueError("LOCAL_VISION_MODEL and LOCAL_VISION_MMPROJ must be set in .env")

        model_path = Path(config.LOCAL_VISION_MODEL)
        mmproj_path = Path(config.LOCAL_VISION_MMPROJ)

        if not model_path.exists():
            raise FileNotFoundError(f"Vision model not found: {model_path}")
        if not mmproj_path.exists():
            raise FileNotFoundError(f"Vision mmproj not found: {mmproj_path}")

        self.model_path = str(model_path)
        self.mmproj_path = str(mmproj_path)

    def _load_model(self):
        """Load model into VRAM on demand."""
        import time
        import logging
        logger = logging.getLogger("uvicorn")

        t0 = time.perf_counter()
        from llama_cpp import Llama
        from llama_cpp.llama_chat_format import Qwen25VLChatHandler

        chat_handler = Qwen25VLChatHandler(
            clip_model_path=self.mmproj_path,
            verbose=False
        )
        llm = Llama(
            model_path=self.model_path,
            chat_handler=chat_handler,
            n_gpu_layers=-1,
            n_ctx=4096,
            verbose=False
        )
        t1 = time.perf_counter()
        logger.info(f"Local vision model loaded in {t1 - t0:.1f}s")
        return llm, chat_handler

    def _unload_model(self, llm, chat_handler):
        """Unload model and free VRAM."""
        import gc
        del llm
        del chat_handler
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def extract_text(self, image_path: Path) -> str:
        """Extract raw text from image using local vision model."""
        import base64

        with open(image_path, 'rb') as f:
            image_data = base64.standard_b64encode(f.read()).decode('utf-8')

        suffix = image_path.suffix.lower()
        media_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
        }
        media_type = media_types.get(suffix, 'image/jpeg')

        llm, chat_handler = self._load_model()
        try:
            response = llm.create_chat_completion(
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{image_data}"
                            }
                        },
                        {
                            "type": "text",
                            "text": "Extract all text from this receipt image. Return only the raw text, preserving the layout as much as possible."
                        }
                    ]
                }],
                max_tokens=1024
            )
            return response['choices'][0]['message']['content']
        finally:
            self._unload_model(llm, chat_handler)

    def extract_receipt_data(self, image_paths: list[Path]) -> ReceiptData:
        """Extract structured receipt data using local vision model."""
        import base64

        media_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
        }

        # Build image content blocks for all pages
        image_content = []
        for image_path in image_paths:
            with open(image_path, 'rb') as f:
                image_data = base64.standard_b64encode(f.read()).decode('utf-8')
            media_type = media_types.get(image_path.suffix.lower(), 'image/jpeg')
            image_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{media_type};base64,{image_data}"
                }
            })

        prompt = """Analyze this document (all pages) and extract payment date, company name, and total amount.

The document could be:
- A Swedish invoice (faktura) - look for förfallodatum, betala senast, betalningsdatum
- An English invoice - look for due date, payment due, invoice date
- An e-commerce order - look for order date, purchase date
- A receipt/kvitto - look for transaction date

For the DATE:
- PRIORITIZE due date (förfallodatum, betala senast, due date, payment due)
- If multiple dates exist, use the LATEST payment-related date (förfallodatum > fakturadatum)
- IGNORE document print date (Datum in header) - this is NOT the payment date
- IGNORE email header dates (sent date, received date)
- IGNORE fakturadatum if förfallodatum exists
- For Kickstarter/crowdfunding: use "card charged" date, not backing date
- Format as YYYY-MM-DD

For the COMPANY:
- Use the merchant/vendor who sold the goods or services
- For e-commerce (Amazon, eBay, etc.), use the platform name (e.g., "Amazon")
- Do NOT use payment processors (Klarna, PayPal, Avarda) as the company
- Do NOT use individual marketplace sellers, use the platform

For the TOTAL:
- Swedish: look for "totalt", "att betala", "summa", "belopp"
- English: look for "total", "amount due", "grand total", "total due"
- Include the currency (SEK, kr, $, €, etc.)

Return ONLY in this exact format, nothing else:
Date: YYYY-MM-DD
Company: [name]
Total: [amount with currency]"""

        image_content.append({
            "type": "text",
            "text": prompt
        })

        import time
        import logging
        logger = logging.getLogger("uvicorn")

        llm, chat_handler = self._load_model()
        try:
            t0 = time.perf_counter()
            response = llm.create_chat_completion(
                messages=[{
                    "role": "user",
                    "content": image_content
                }],
                max_tokens=256
            )
            result_text = response['choices'][0]['message']['content']
            t1 = time.perf_counter()
            logger.info(f"Local vision inference completed in {t1 - t0:.1f}s ({len(image_paths)} page(s))")
        finally:
            self._unload_model(llm, chat_handler)

        # Parse the response
        payment_date = None
        company_name = None

        for line in result_text.strip().split('\n'):
            line = line.strip()
            if line.lower().startswith('date:'):
                date_str = line.split(':', 1)[1].strip()
                try:
                    parts = date_str.split('-')
                    if len(parts) == 3:
                        payment_date = date(int(parts[0]), int(parts[1]), int(parts[2]))
                except (ValueError, IndexError):
                    pass
            elif line.lower().startswith('company:'):
                company_name = line.split(':', 1)[1].strip()

        # Detect payment handler from company name or raw text
        payment_handler = None
        payment_handlers = ['klarna', 'avarda', 'svea', 'walley', 'collector', 'resurs', 'qliro', 'paypal']
        text_lower = result_text.lower()
        for handler in payment_handlers:
            if handler in text_lower:
                payment_handler = handler.capitalize()
                break

        return ReceiptData(
            payment_date=payment_date,
            company_name=company_name,
            payment_handler=payment_handler,
            raw_text=result_text,
            confidence=0.85 if payment_date and company_name else 0.5,
            ocr_cost=0.0  # Local = free
        )


class ClaudeVisionOCR(OCRBackend):
    """Claude Vision OCR backend (paid, best accuracy)."""

    def __init__(self):
        if not config.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY not set in .env")

        import anthropic
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    def extract_text(self, image_path: Path) -> str:
        import base64

        # Read and encode image
        with open(image_path, 'rb') as f:
            image_data = base64.standard_b64encode(f.read()).decode('utf-8')

        # Determine media type
        suffix = image_path.suffix.lower()
        media_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
        }
        media_type = media_types.get(suffix, 'image/jpeg')

        response = self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data,
                        }
                    },
                    {
                        "type": "text",
                        "text": "Extract all text from this receipt image. Return only the raw text, preserving the layout as much as possible."
                    }
                ]
            }]
        )

        return response.content[0].text

    def extract_receipt_data(self, image_paths: list[Path]) -> ReceiptData:
        """Override to use Claude's understanding for structured extraction."""
        import base64
        import json

        media_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
        }

        # Build image content blocks for all pages
        content = []
        for image_path in image_paths:
            with open(image_path, 'rb') as f:
                image_data = base64.standard_b64encode(f.read()).decode('utf-8')
            media_type = media_types.get(image_path.suffix.lower(), 'image/jpeg')
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": image_data,
                }
            })

        content.append({
            "type": "text",
            "text": """Analyze this document (all pages) and extract payment date, company name, and total amount.

The document could be:
- A Swedish invoice (faktura) - look for förfallodatum, betala senast, betalningsdatum
- An English invoice - look for due date, payment due, invoice date
- An e-commerce order - look for order date, purchase date
- A receipt/kvitto - look for transaction date

For the DATE:
- PRIORITIZE due date (förfallodatum, betala senast, due date, payment due)
- IGNORE document print date (Datum in header) - this is NOT the payment date
- IGNORE email header dates (sent date, received date)
- For Kickstarter/crowdfunding: use "card charged" date, not backing date
- Format as YYYY-MM-DD

For the COMPANY:
- Use the merchant/vendor who sold the goods (e.g., webhallen.com, not Klarna)
- Do NOT use payment processors (Klarna, PayPal, Avarda) as the company

For PAYMENT HANDLER:
- Identify if a payment processor is used (Klarna, Avarda, Swish, PayPal, Resurs, etc.)
- Return the handler name, or null if direct payment

For the TOTAL:
- Swedish: totalt, att betala, belopp att betala, summa
- English: total, amount due, grand total
- Include currency (SEK, kr, $, €)

LANGUAGE RULE FOR raw_text:
- Detect the language of the receipt itself.
- Write raw_text in THAT SAME LANGUAGE. If the receipt is Swedish, raw_text MUST be Swedish. If German, German. Never translate.
- raw_text should be a short summary (1-2 sentences) of what the receipt is for.

Return JSON only, no other text:
{"payment_date": "YYYY-MM-DD", "company_name": "Company Name", "payment_handler": null, "total": "123.45 SEK", "raw_text": "summary in receipt's language"}

If you can't find a field, use null."""
        })

        response = self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": content
            }]
        )

        try:
            text = response.content[0].text
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]

            data = json.loads(text.strip())
            if isinstance(data, list) and data:
                data = data[0]
            payment_date = None
            if data.get('payment_date'):
                parts = data['payment_date'].split('-')
                payment_date = date(int(parts[0]), int(parts[1]), int(parts[2]))

            return ReceiptData(
                payment_date=payment_date,
                company_name=data.get('company_name'),
                payment_handler=data.get('payment_handler'),
                raw_text=data.get('raw_text', ''),
                confidence=0.9
            )
        except (json.JSONDecodeError, KeyError, ValueError, IndexError, AttributeError):
            # Fallback to basic extraction
            return super().extract_receipt_data(image_paths)


def get_ocr_backend(backend: str | None = None) -> OCRBackend:
    """Get OCR backend by name, or auto-detect based on available API keys."""
    backend = backend or config.OCR_BACKEND

    # Auto-detect: if set to 'auto' or AI backend without key, pick best available
    if backend == 'auto' or (backend == 'claude' and not config.ANTHROPIC_API_KEY) or (backend == 'gpt4' and not config.OPENAI_API_KEY):
        if config.ANTHROPIC_API_KEY:
            backend = 'claude'
        elif config.OPENAI_API_KEY:
            backend = 'gpt4'
        else:
            backend = 'easyocr'  # Fallback to free option

    backends = {
        'tesseract': TesseractOCR,
        'easyocr': EasyOCROCR,
        'claude': ClaudeVisionOCR,
        'gpt4': GPT4VisionOCR,
        'local': LocalVisionOCR,
        'gemma': Gemma4VisionOCR,
    }

    if backend not in backends:
        raise ValueError(f"Unknown OCR backend: {backend}. Options: {list(backends.keys())}")

    return backends[backend]()


def pdf_to_images(pdf_path: Path) -> list[Path]:
    """Convert all pages of PDF to images using PyMuPDF."""
    import fitz  # PyMuPDF
    import tempfile

    doc = fitz.open(pdf_path)
    if len(doc) == 0:
        doc.close()
        raise ValueError("PDF has no pages")

    temp_dir = Path(tempfile.gettempdir())
    mat = fitz.Matrix(2, 2)  # 2x resolution for better OCR
    temp_paths = []

    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=mat)
        if pix.width == 0 or pix.height == 0:
            continue
        temp_path = temp_dir / f"receipt_ocr_temp_{i}.png"
        pix.save(temp_path)
        temp_paths.append(temp_path)

    doc.close()

    if not temp_paths:
        raise ValueError("PDF rendered no valid pages")

    return temp_paths


def extract_receipt_data(image_path: str | Path, backend: str | None = None) -> ReceiptData:
    """Extract receipt data from an image or PDF.

    Args:
        image_path: Path to the receipt image (JPG, PNG) or PDF
        backend: OCR backend to use (tesseract, easyocr, claude, gpt4, local)

    Returns:
        ReceiptData with extracted information
    """
    path = Path(image_path)

    # Handle PDFs by converting all pages to images
    if path.suffix.lower() == '.pdf':
        temp_paths = pdf_to_images(path)
        try:
            ocr = get_ocr_backend(backend)
            return ocr.extract_receipt_data(temp_paths)
        finally:
            for p in temp_paths:
                p.unlink(missing_ok=True)

    ocr = get_ocr_backend(backend)
    return ocr.extract_receipt_data([path])
