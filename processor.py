import csv
import io
import json
import os
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import openpyxl
import pdfplumber
from openai import OpenAI


STATUSES = [
    "New",
    "Parsing",
    "Parsed",
    "Missing rate card",
    "Missing document",
    "Matched",
    "Exception",
    "Flagged overbill",
    "Pending approval",
    "Approved to pay",
    "Disputed",
    "Awaiting carrier",
    "Recovered",
    "Written off",
    "Duplicate",
    "Needs review",
    "Low extraction confidence",
]

LINE_ITEM_STATUSES = [
    "Valid",
    "Missing rate",
    "Expired rate",
    "Rate mismatch",
    "Weight-break mismatch",
    "Min-charge violation",
    "Discount not applied",
    "Unauthorized accessorial",
    "Fuel-surcharge mismatch",
    "Fuel-index mismatch",
    "Duplicate line",
    "Tax mismatch",
    "Currency mismatch",
    "Flagged",
    "Dispute-ready",
    "Disputed",
    "Recovered",
    "Written off",
    "Needs review",
]

RATE_CARD_STATUSES = [
    "Missing",
    "Uploaded",
    "Parsing",
    "Needs normalization",
    "Active",
    "Expiring soon",
    "Expired",
    "Superseded",
    "Carrier mismatch",
    "Mode mismatch",
    "Needs review",
]

ALERT_STATUSES = [
    "No alert",
    "Alert pending",
    "Alert sent",
    "Alert acknowledged",
    "Escalated due to aging",
    "Dispute response overdue",
]

APPROVAL_STATUSES = [
    "Awaiting reviewer",
    "Awaiting AP",
    "Approved",
    "Rejected",
    "Escalated",
    "Disputed",
    "Closed",
]

INVOICE_HEADER_FIELDS = [
    "invoice_number",
    "invoice_date",
    "due_date",
    "carrier_name",
    "carrier_scac",
    "carrier_account_number",
    "bill_to_name",
    "remit_to_name",
    "purchase_order_number",
    "shipment_reference",
    "pro_number",
    "bol_number",
    "load_number",
    "shipment_date",
    "delivery_date",
    "origin_city",
    "origin_state",
    "origin_zip",
    "origin_country",
    "destination_city",
    "destination_state",
    "destination_zip",
    "destination_country",
    "mode",
    "service_level",
    "equipment_type",
    "payment_terms",
    "invoice_currency",
    "total_billed_amount",
    "total_expected_amount",
    "total_variance_amount",
    "total_variance_percent",
    "invoice_status",
    "source_channel",
    "received_at",
    "parsed_at",
    "extraction_confidence",
]

LINE_ITEM_FIELDS = [
    "line_number",
    "charge_code",
    "charge_description",
    "billed_amount",
    "expected_amount",
    "variance_amount",
    "variance_percent",
    "rate_basis",
    "billed_rate",
    "expected_rate",
    "quantity",
    "weight",
    "pieces",
    "pallets",
    "linear_feet",
    "miles",
    "fuel_surcharge_billed",
    "fuel_surcharge_expected",
    "accessorial_code",
    "accessorial_description",
    "accessorial_billed",
    "accessorial_expected",
    "accessorial_authorized_flag",
    "discount_billed",
    "discount_expected",
    "tax_billed",
    "tax_expected",
    "rate_card_reference",
    "tariff_reference",
    "contract_clause_reference",
    "fuel_index_provider",
    "fuel_index_date",
    "fuel_index_rate",
    "duplicate_flag",
    "line_status",
    "dispute_status",
    "aging_days",
    "recovery_status",
]

RATE_CARD_FIELDS = [
    "carrier_name",
    "carrier_scac",
    "mode",
    "service_level",
    "origin_zone",
    "destination_zone",
    "origin_zip_range",
    "destination_zip_range",
    "weight_break",
    "min_charge",
    "rate_per_mile",
    "rate_per_cwt",
    "flat_rate",
    "fuel_surcharge_table",
    "fuel_index_provider",
    "fuel_index_effective_date",
    "accessorial_code",
    "accessorial_rate",
    "accessorial_conditions",
    "discount_terms",
    "effective_date",
    "expiry_date",
    "contract_clause_reference",
    "rate_card_version",
    "currency",
    "upload_source",
    "normalization_status",
    "rate_card_status",
]

EVIDENCE_FIELDS = [
    "evidence_pack_id",
    "expected_vs_billed_summary",
    "supporting_document_links",
    "carrier_dispute_reference",
    "dispute_submission_date",
    "dispute_response_date",
    "recovered_amount",
    "write_off_amount",
    "reviewer",
    "reviewed_at",
    "approval_status",
    "audit_trail_notes",
    "notification_status",
]

PRIMARY_ENTITY_KEYS = [
    "carrier_name",
    "carrier",
    "vendor_name",
    "vendor",
    "supplier",
    "supplier_name",
    "bill_to_name",
    "remit_to_name",
    "customer_name",
    "client_name",
    "employee_name",
    "patient_name",
    "contract_party",
    "counterparty",
    "shipper_name",
]


def process_file(file_bytes: bytes) -> list[dict]:
    """Process PDF, Excel, CSV, or plain text bytes.

    Always tries PDF first, then Excel, then UTF-8 text/CSV fallback.
    Returns a list of records with keys: title, status, details, due_date.
    """
    text = _extract_text_from_pdf(file_bytes)
    rows = None

    if text and text.strip():
        rows = _parse_csv_dict_rows(text)

    if rows is None:
        rows = _extract_excel_dict_rows(file_bytes)

    if rows is None:
        decoded = file_bytes.decode("utf-8", errors="ignore").lstrip("\ufeff")
        text = decoded
        rows = _parse_csv_dict_rows(decoded)

    if rows:
        records = _rows_to_records(rows)
        if any(record["title"] != "Unknown counterparty" for record in records):
            return records

    if text and text.strip():
        return [_record_from_text(text)]

    return [
        {
            "title": "Unknown counterparty",
            "status": "Needs review",
            "details": {},
            "due_date": None,
        }
    ]


def _extract_text_from_pdf(file_bytes: bytes) -> Optional[str]:
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
            return "\n".join(pages)
    except Exception:
        return None


def _extract_excel_dict_rows(file_bytes: bytes) -> Optional[List[Dict[str, Any]]]:
    try:
        workbook = openpyxl.load_workbook(
            io.BytesIO(file_bytes), read_only=True, data_only=True
        )
        sheet = workbook.active
        raw_rows = []
        for row in sheet.iter_rows(values_only=True):
            raw_rows.append(
                ["" if cell is None else str(cell).strip() for cell in row]
            )

        if not raw_rows:
            return None

        header = [str(value).strip().lower() for value in raw_rows[0]]
        rows = []
        for raw_row in raw_rows[1:]:
            if any(value for value in raw_row):
                row = {
                    header[i] if i < len(header) else f"column_{i}": raw_row[i]
                    for i in range(len(raw_row))
                }
                rows.append(row)

        return rows or None
    except Exception:
        return None


def _parse_csv_dict_rows(text: str) -> Optional[List[Dict[str, Any]]]:
    cleaned = text.strip()
    if not cleaned:
        return None

    sample = cleaned[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        delimiter = dialect.delimiter
    except Exception:
        if "," in cleaned:
            delimiter = ","
        elif "\t" in cleaned:
            delimiter = "\t"
        elif "|" in cleaned:
            delimiter = "|"
        else:
            delimiter = ";"

    try:
        reader = csv.DictReader(io.StringIO(cleaned), delimiter=delimiter)
        rows = []
        for row in reader:
            if any(
                value is not None and str(value).strip()
                for value in row.values()
            ):
                rows.append({key: value for key, value in row.items()})
        return rows or None
    except Exception:
        return None


def _rows_to_records(rows: List[Dict[str, Any]]) -> list[dict]:
    records = []
    for row in rows:
        normalized = _normalize_row(row)
        if not any(str(value).strip() for value in normalized.values()):
            continue
        records.append(_row_to_record(normalized))
    return records


def _row_to_record(row: Dict[str, Any]) -> dict:
    title = _extract_title(row)
    status = _extract_status(row)
    due_date = _iso_date(
        _first_nonempty(
            row,
            ["due_date", "due date", "invoice_due_date", "payment_due_date"],
        )
    )

    details = dict(row)
    for key in [
        "due_date",
        "due date",
        "invoice_due_date",
        "payment_due_date",
        "status",
        "invoice_status",
    ]:
        details.pop(key, None)

    details.setdefault("source_channel", "upload")
    details.setdefault("extraction_confidence", "high")

    return {
        "title": title,
        "status": status,
        "details": details,
        "due_date": due_date,
    }


def _normalize_row(row: Dict[Any, Any]) -> Dict[str, Any]:
    return {
        str(key).strip().lower(): value
        for key, value in row.items()
        if key is not None
    }


def _first_nonempty(row: Dict[str, Any], keys: List[str]) -> Optional[Any]:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def _extract_title(row: Dict[str, Any]) -> str:
    """Return the primary entity the buyer tracks, never document type."""
    value = _first_nonempty(row, PRIMARY_ENTITY_KEYS)
    if value:
        return str(value).strip()

    entity_words = (
        "name",
        "vendor",
        "supplier",
        "carrier",
        "customer",
        "party",
        "account",
        "shipper",
    )
    for key, value in row.items():
        if value and any(word in str(key).lower() for word in entity_words):
            return str(value).strip()

    return "Unknown counterparty"


def _extract_status(row: Dict[str, Any]) -> str:
    raw = _first_nonempty(row, ["invoice_status", "status"])
    if raw is None:
        return "Parsed"

    raw_text = str(raw).strip()
    for allowed in STATUSES:
        if allowed.lower() == raw_text.lower():
            return allowed

    return "Needs review"


def _iso_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = str(value).strip()
    if not text:
        return None

    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except Exception:
            continue

    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

    return text[:10] if text else None


def _record_from_text(text: str) -> dict:
    clean_text = text.strip()
    if not clean_text:
        return {
            "title": "Unknown counterparty",
            "status": "Needs review",
            "details": {},
            "due_date": None,
        }

    if len(clean_text) > 200 and "DEEPSEEK_API_KEY" in os.environ:
        extracted = _extract_header_with_llm(clean_text)
        if extracted:
            return _record_from_extracted(extracted, clean_text)

    carrier = _regex_extract_carrier(clean_text)
    invoice_number = _regex_extract_invoice_number(clean_text)
    due_date = _iso_date(_regex_extract_due_date(clean_text))

    title = carrier or "Unknown counterparty"
    details = {"raw_text": clean_text[:5000], "extraction_confidence": "low"}
    if carrier:
        details["carrier_name"] = carrier
    if invoice_number:
        details["invoice_number"] = invoice_number

    return {
        "title": title,
        "status": "Low extraction confidence",
        "details": details,
        "due_date": due_date,
    }


def _record_from_extracted(header: Dict[str, Any], raw_text: Optional[str]) -> dict:
    normalized = _normalize_row(header)
    title = _extract_title(normalized)
    status = _extract_status(normalized)
    due_date = _iso_date(
        _first_nonempty(normalized, ["due_date", "due date", "invoice_due_date"])
    )

    details = dict(normalized)
    for key in ["due_date", "due date", "invoice_due_date", "status", "invoice_status"]:
        details.pop(key, None)

    if raw_text:
        details["raw_text"] = raw_text[:5000]

    return {
        "title": title,
        "status": status,
        "details": details,
        "due_date": due_date,
    }


def _extract_header_with_llm(text: str) -> Dict[str, Any]:
    try:
        client = OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com",
        )
        system_prompt = (
            "Extract freight invoice header fields from the provided invoice text. "
            "Return only valid JSON with keys matching invoice header field names. "
            "The title field must be the primary entity the buyer tracks "
            "(for freight invoices: carrier_name, vendor, supplier, or counterparty). "
            "Never use the document type or category as the title."
        )
        user_prompt = f"Invoice text:\n{text[:12000]}\n\nExtract invoice header fields."
        response = client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:]
        return json.loads(content)
    except Exception:
        return {}


def _regex_extract_carrier(text: str) -> Optional[str]:
    patterns = [
        r"carrier_name[:=\s]+([^\n,]+)",
        r"carrier[:=\s]+([^\n,]+)",
        r"vendor_name[:=\s]+([^\n,]+)",
        r"supplier[:=\s]+([^\n,]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _regex_extract_invoice_number(text: str) -> Optional[str]:
    patterns = [
        r"invoice_number[:=\s]+([^\n,]+)",
        r"invoice\s*#?[:=\s]+([^\n,]+)",
        r"\bINV[- ]?\d+\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip() if match.lastindex else match.group(0).strip()
    return None


def _regex_extract_due_date(text: str) -> Optional[str]:
    patterns = [
        r"due_date[:=\s]+([^\n,]+)",
        r"due date[:=\s]+([^\n,]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None
