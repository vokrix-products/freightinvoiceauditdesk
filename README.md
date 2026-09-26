# FreightInvoiceAuditDesk

## Product

FreightInvoiceAuditDesk is a freight (transportation) invoice audit and overbill-recovery platform. It ingests carrier invoices, extracts header and line-item fields, compares billed charges against contracted rate cards, flags overbills, and drives carrier disputes through to recovery or write-off.

## Archetype

Freight invoice audit / overbill recovery. The primary tracked entity is the carrier or vendor (the counterparty), never the document type. Every record is keyed on the carrier/vendor name so audits, disputes, and recoveries can be grouped per counterparty.

## What the poller expects as input

The poller feeds raw file bytes to `process_file(file_bytes: bytes) -> list[dict]`. Supported inputs, tried in order:

1. PDF carrier invoices (rasterized or text-based).
2. Excel workbooks (.xlsx) with invoice or line-item rows.
3. CSV / delimited text exports (comma, tab, semicolon, or pipe delimited).
4. Plain-text invoice bodies.

Each returned record has:

- `title` — the carrier/vendor/counterparty name (never the document type).
- `status` — one of the approved invoice status strings, e.g. `New`, `Parsed`, `Matched`, `Exception`, `Flagged overbill`, `Pending approval`, `Approved to pay`, `Disputed`, `Recovered`, `Written off`, `Needs review`, `Low extraction confidence`.
- `details` — dict of extra extracted fields only (no duplicated status or due date).
- `due_date` — top-level ISO-8601 date string or `None`.

## Files

- `processor.py` — local file processing. Supports PDF, Excel, CSV, and plain text.
- `run_demo.py` — zero-argument demo with hardcoded CSV data.
- `run_tests.py` — unit tests using Python stdlib `unittest`.
- `requirements.txt` — required packages.

## Setup

pip install -r requirements.txt

## Run demo

python3 run_demo.py

## Run tests

python3 run_tests.py

Railway: freightinvoiceauditdesk
Cloudflare: freightinvoiceauditdesk.vokrix.co

Billing: price_1UJjVR2c9uGCcgMSmpe36Dlx

