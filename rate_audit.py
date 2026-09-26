"""Invoice-versus-contract rate audit.

Separate from processor.py on purpose. process_file() only ever sees one
document, and auditing an invoice means comparing it against the customer's
contracted rate lines and a market diesel price - both of which only the poller
can supply. Every function here is pure; the caller owns the database and the
network calls.

Three rules govern the code below. Each comes from a defect this product has
already shipped, not from taste.

1. Never write expected_freight_charge. processor._assign_status compares
   expected_freight_charge against freight_charge, and freight_charge is the
   transportation SUBTOTAL - it includes pallet and pallet-jack lines that no
   contract rate governs. For invoice INV-2026-11487 that is 1095.20 against a
   contracted line-haul of 770.00, so populating the field would report a 325.20
   variance on a correct invoice. This module records its finding in
   overcharge_amount plus a note instead; overcharge_amount is not one of the
   fields _assign_status compares.

2. Never report a figure the inputs do not support. Extraction is an LLM call and
   is not deterministic: the same PDF returned "Corrugated Boxes, Retail Goods
   (Class 100, 14,000 lbs @ $6.40/100lbs)" on one upload and "Corrugated Boxes,
   Retail Goods" on the next. When the rated line cannot be read the audit says
   so and produces no number, because a wrong overcharge figure is worse than no
   figure in an audit product.

3. An invoice with no matching contracted lane is left untouched. Every invoice
   on hand from a carrier with no rate sheet would otherwise be marked
   contract-review:warning, which would bury the invoices that do carry a
   finding.
"""

import re

# Mirrored from processor.py rather than imported: importing processor pulls in
# pdfplumber and llm_extractor, and this module is pure logic.
STATUS_VALID = "valid:good"
STATUS_FLAGGED = "flagged:critical"
STATUS_CONTRACT_REVIEW = "contract-review:warning"

# One cent. Below this the difference is rounding, not a finding.
MONEY_TOLERANCE = 0.01

# Five digits not adjacent to another digit, so a street number like 45000 in an
# address does not read as a ZIP.
ZIP_PATTERN = re.compile(r"(?<!\d)(\d{5})(?!\d)")

# Contract fuel tables write the band with a hyphen, an en dash or an em dash
# depending on the carrier's template.
BAND_PATTERN = re.compile(r"\$?\s*(\d+(?:\.\d+)?)\s*[-\u2013\u2014]\s*\$?\s*(\d+(?:\.\d+)?)")
PERCENT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*%")

# A rated line item as the extractor returns it, e.g.
# "Corrugated Boxes, Retail Goods (Class 100, 14,000 lbs @ $6.40/100lbs)".
RATED_LINE_PATTERN = re.compile(
    r"class\s*(?P<freight_class>\d+(?:\.\d+)?)"
    r".*?(?P<weight>\d[\d,]*(?:\.\d+)?)\s*(?:lbs|lb|pounds)"
    r".*?\$?\s*(?P<rate>\d[\d,]*(?:\.\d+)?)\s*/\s*100",
    re.IGNORECASE | re.DOTALL,
)


def _to_number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("$", "").replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def extract_zip(value):
    """The 5-digit ZIP in a location string, or None.

    Rate sheets write the lane as "NV (Reno) 89502" and invoices write the
    shipment address as "2200 Industrial Parkway, Reno, NV 89502". ZIP is the
    only part the two spellings share, so it is the join key.
    """
    if value is None:
        return None
    match = ZIP_PATTERN.search(str(value))
    return match.group(1) if match else None


def match_rate_line(invoice_details, rate_lines):
    """The contracted rate line covering this shipment's lane, or None."""
    origin = extract_zip(invoice_details.get("origin_location"))
    destination = extract_zip(invoice_details.get("destination_location"))
    if not origin or not destination:
        return None

    for line in rate_lines or []:
        if not isinstance(line, dict):
            continue
        if (
            extract_zip(line.get("origin_zone_zip_postal")) == origin
            and extract_zip(line.get("destination_zone_zip_postal")) == destination
        ):
            return line
    return None


def parse_rated_line(line_items):
    """The one invoice line that carries class, weight and rate.

    Returns None when no line carries all three. That is the guard: without them
    the contracted charge for the shipment cannot be computed at all, and
    guessing from the transportation subtotal would count pallet and
    pallet-jack charges that no contract rate governs.
    """
    for item in line_items or []:
        if not isinstance(item, dict):
            continue
        description = str(item.get("description") or "")
        match = RATED_LINE_PATTERN.search(description)
        if not match:
            continue
        weight = _to_number(match.group("weight"))
        rate = _to_number(match.group("rate"))
        amount = _to_number(item.get("amount"))
        if weight is None or rate is None or amount is None:
            continue
        return {
            "freight_class": match.group("freight_class"),
            "weight": weight,
            "rate": rate,
            "amount": amount,
        }
    return None


def expected_linehaul(weight, base_rate, rate_basis):
    """The contracted line-haul charge, or None when it is not derivable.

    Only bases computable from invoice fields are supported. A per-mile contract
    cannot be applied because no field on either document carries the mileage,
    so it returns None and the caller reports contract-review rather than a
    figure.
    """
    if weight is None or base_rate is None:
        return None
    basis = str(rate_basis or "").lower()
    if "100" in basis or "cwt" in basis or "hundred" in basis:
        return weight / 100.0 * base_rate
    if "flat" in basis:
        return float(base_rate)
    return None


def _band_texts(entry):
    if isinstance(entry, dict):
        return [str(value) for value in entry.values() if value is not None]
    return [str(entry)]


def _band_bounds(entry):
    for text in _band_texts(entry):
        match = BAND_PATTERN.search(text)
        if match:
            return float(match.group(1)), float(match.group(2))
    return None, None


def _band_percent(entry):
    for text in _band_texts(entry):
        match = PERCENT_PATTERN.search(text)
        if match:
            return float(match.group(1)) / 100.0
    return None


def fuel_table_maximum(fuel_surcharge_table):
    """Highest diesel price the contracted fuel table covers, or None.

    None also means the table could not be read at all, which the caller treats
    as "no fuel audit" rather than as a finding.
    """
    maximum = None
    for entry in fuel_surcharge_table or []:
        _, high = _band_bounds(entry)
        if high is None:
            continue
        maximum = high if maximum is None else max(maximum, high)
    return maximum


def lookup_fuel_band(fuel_surcharge_table, diesel_price):
    """Contracted fuel surcharge for a diesel price, or None when out of range.

    The price bands are the contract's own; a market price above the highest band
    means the contract does not cover today's market, which is a finding in
    itself rather than a number to invent.
    """
    if diesel_price is None:
        return None
    for entry in fuel_surcharge_table or []:
        low, high = _band_bounds(entry)
        if low is None or high is None:
            continue
        if low - 0.005 <= diesel_price <= high + 0.005:
            return _band_percent(entry)
    return None


def audit_invoice(invoice_details, rate_lines, diesel_price=None):
    """Audit one invoice against the customer's contracted rate lines.

    Returns {"status": str|None, "notes": [str], "updates": dict}. A None status
    means the record keeps the status processor.py already gave it.
    """
    notes = []
    updates = {}
    status = None

    rate_line = match_rate_line(invoice_details, rate_lines)
    if rate_line is None:
        return {"status": None, "notes": notes, "updates": updates}

    reference = str(
        rate_line.get("contract_rate_sheet_identifier") or rate_line.get("title") or ""
    ).strip()
    updates["matched_rate_line_reference"] = reference or None

    rated_line = parse_rated_line(invoice_details.get("invoice_line_items"))
    if rated_line is None:
        notes.append(
            "cannot audit against the contracted rate: no invoice line carried "
            "class, weight and rate"
        )
        return {"status": STATUS_CONTRACT_REVIEW, "notes": notes, "updates": updates}

    base_rate = _to_number(rate_line.get("base_rate"))
    expected = expected_linehaul(rated_line["weight"], base_rate, rate_line.get("rate_basis"))
    if expected is None:
        notes.append(
            "cannot audit against the contracted rate: rate basis "
            f"'{rate_line.get('rate_basis')}' is not derivable from the invoice fields on hand"
        )
        return {"status": STATUS_CONTRACT_REVIEW, "notes": notes, "updates": updates}

    origin = extract_zip(invoice_details.get("origin_location"))
    destination = extract_zip(invoice_details.get("destination_location"))

    detected = 0.0

    difference = rated_line["amount"] - expected
    if abs(difference) > MONEY_TOLERANCE:
        detected += difference
        notes.append(
            f"line-haul overcharge of {difference:.2f}: invoiced "
            f"{rated_line['amount']:.2f} at {rated_line['rate']:.2f}/100lbs against "
            f"contracted {expected:.2f} at {base_rate:.2f}/100lbs for "
            f"{origin} to {destination}"
        )

    table = rate_line.get("fuel_surcharge_table")
    maximum_band = fuel_table_maximum(table)
    if maximum_band is not None:
        percent = lookup_fuel_band(table, diesel_price)
        if percent is None:
            if diesel_price is None:
                notes.append("fuel cannot be audited: market diesel price unavailable")
            else:
                notes.append(
                    f"fuel cannot be audited: market diesel {diesel_price:.3f} is outside "
                    f"the contracted table (highest band {maximum_band:.2f})"
                )
            status = STATUS_CONTRACT_REVIEW
        else:
            actual_fuel = _to_number(invoice_details.get("fuel_surcharge"))
            if actual_fuel is not None:
                expected_fuel = percent * rated_line["amount"]
                fuel_difference = actual_fuel - expected_fuel
                if abs(fuel_difference) > MONEY_TOLERANCE:
                    detected += fuel_difference
                    notes.append(
                        f"fuel surcharge variance of {fuel_difference:.2f}: invoiced "
                        f"{actual_fuel:.2f} against contracted {percent * 100:.2f}% of "
                        f"line-haul = {expected_fuel:.2f}"
                    )

    if abs(detected) > MONEY_TOLERANCE:
        updates["overcharge_amount"] = round(detected, 2)
        status = STATUS_FLAGGED

    return {"status": status, "notes": notes, "updates": updates}


def apply_rate_audit(result_records, rate_lines, diesel_price_for=None):
    """Annotate invoice records with the contracted-rate audit.

    diesel_price_for is a callable taking an ISO ship date and returning the
    market diesel price, or None when it cannot be resolved. Passing the lookup
    in keeps this function free of network access and makes it testable.

    A flagged duplicate from cross-upload detection outranks a contract-review
    finding, so an existing flagged:critical is never downgraded.
    """
    if not rate_lines:
        return result_records

    updated_records = []
    for item in result_records:
        if not isinstance(item, dict):
            updated_records.append(item)
            continue

        details = item.get("details")
        if not isinstance(details, dict) or details.get("document_type") != "invoice":
            updated_records.append(item)
            continue

        try:
            diesel_price = diesel_price_for(details.get("ship_date")) if diesel_price_for else None
        except Exception:
            diesel_price = None

        outcome = audit_invoice(details, rate_lines, diesel_price)

        notes = list(details.get("_notes") or [])
        for note in outcome["notes"]:
            if note not in notes:
                notes.append(note)

        updated = dict(item)
        updated["details"] = {**details, **outcome["updates"], "_notes": notes}

        if outcome["status"] == STATUS_FLAGGED:
            updated["status"] = STATUS_FLAGGED
        elif outcome["status"] and item.get("status") == STATUS_VALID:
            updated["status"] = outcome["status"]

        updated_records.append(updated)

    return updated_records
