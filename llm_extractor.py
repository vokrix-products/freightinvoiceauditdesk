import json
import os
import re
from typing import Any, Dict, List

from openai import OpenAI

# The exact key names the processor's canonical schema expects. The prompt must
# name them: asking only for "the RateMatch schema fields" produced invoice_date /
# total_amount / rate_sheet_effective_date, none of which mapped to a canonical
# field, so every invoice reported required fields as missing.
INVOICE_FIELDS = [
    "invoice_number",
    "carrier_name",
    "ship_date",
    "payment_due_date",
    "bill_of_lading_pro_number",
    "origin_location",
    "destination_location",
    "actual_weight",
    "billable_weight",
    "freight_charge",
    "fuel_surcharge",
    "accessorial_charges",
    "total_charges",
    "invoice_line_items",
    "currency",
]

RATE_SHEET_FIELDS = [
    "carrier_name",
    "contract_rate_sheet_identifier",
    "effective_date",
    "expiration_date",
    "rate_basis",
    "base_rate",
    "minimum_charge",
    "fuel_surcharge_table",
    "origin_zone_zip_postal",
    "destination_zone_zip_postal",
    "freight_class_commodity",
    "lanes",
]

SYSTEM_PROMPT = (
    "You are a freight invoice and carrier rate sheet extractor.\n"
    "First decide whether the document is an invoice or a rate sheet.\n"
    "Then extract only that document's fields, using these exact key names.\n\n"
    "invoice fields: " + ", ".join(INVOICE_FIELDS) + "\n"
    "rate_sheet fields: " + ", ".join(RATE_SHEET_FIELDS) + "\n\n"
    "Rules:\n"
    "- Omit keys that are not present in the document. Never emit null values.\n"
    "- ship_date is the date the shipment was tendered, as YYYY-MM-DD.\n"
    "- payment_due_date is the invoice due date, as YYYY-MM-DD.\n"
    "- freight_charge is the line-haul charge as a number, no currency symbol or commas.\n"
    "- total_charges is the invoice grand total as a number.\n"
    "- invoice_line_items is an array of objects with description and amount.\n"
    "- A rate sheet with several lanes returns ONE object per lane inside the lanes\n"
    "  array, each with base_rate, minimum_charge, rate_basis,\n"
    "  origin_zone_zip_postal, destination_zone_zip_postal and freight_class_commodity.\n"
    "  Repeated carrier_name, effective_date and expiration_date go on each object.\n"
    "- carrier_name is the carrier or vendor name, never a document type.\n"
    "- Return ONLY valid JSON: an array of objects. No markdown, no prose.\n"
)


def extract_from_unstructured_text(text: str) -> List[Dict[str, Any]]:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        return []
    if not text or not text.strip():
        return []

    try:
        client = OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com",
        )
        response = client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text[:12000]},
            ],
            temperature=0,
        )

        content = response.choices[0].message.content or "[]"
        content = re.sub(r"```(?:json)?", "", content).strip()
        data = json.loads(content)

        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return []

        return [
            {key: value for key, value in item.items() if value is not None}
            for item in data
            if isinstance(item, dict)
        ]
    except Exception:
        return []
