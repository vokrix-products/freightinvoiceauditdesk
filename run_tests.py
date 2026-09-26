from processor import (
    _expand_rate_lanes,
    _normalize_row,
    apply_cross_upload_duplicates,
    process_file,
)
from rate_audit import (
    apply_rate_audit,
    expected_linehaul,
    extract_zip,
    fuel_table_maximum,
    lookup_fuel_band,
    match_rate_line,
    parse_rated_line,
)


def _csv(value: str) -> bytes:
    return value.encode("utf-8")


def test_invoice_valid():
    data = _csv(
        "invoice_number,carrier_name,ship_date,freight_charge,total_charges,payment_due_date\n"
        "INV-1,Carrier A,2025-04-01,100.00,100.00,2025-04-30\n"
    )
    records = process_file(data)
    assert len(records) == 1
    assert records[0]["status"] == "valid:good"
    assert records[0]["title"] == "Carrier A"
    assert records[0]["due_date"] == "2025-04-30"
    assert set(records[0].keys()) == {"title", "status", "details", "due_date"}


def test_invoice_missing():
    data = _csv(
        "invoice_number,carrier_name,ship_date\n"
        "INV-2,Carrier B,2025-05-01\n"
    )
    records = process_file(data)
    assert len(records) == 1
    assert records[0]["status"] == "missing:critical"


def test_invoice_duplicate():
    data = _csv(
        "invoice_number,carrier_name,ship_date,freight_charge,total_charges\n"
        "INV-3,Carrier C,2025-04-01,10,10\n"
        "INV-3,Carrier C,2025-04-02,20,20\n"
    )
    records = process_file(data)
    assert len(records) == 2
    assert all(record["status"] == "flagged:critical" for record in records)


def test_invoice_fuel_nested_inside_accessorials():
    # Regression: INV-2024-89341 lays the fuel surcharge out as the first line of
    # the accessorial subtotal, so freight + fuel + accessorials counts the fuel
    # twice and a correct invoice came back flagged:critical as overbilling.
    # Line items 781.25 + 180.00 + 49.20 = 1010.45 (Transportation Charges).
    # Accessorials 171.88 + 65.00 + 195.31 + 25.00 = 457.19.
    # Total 1010.45 + 457.19 = 1467.64.
    data = _csv(
        "invoice_number,carrier_name,ship_date,freight_charge,fuel_surcharge,accessorial_charges,total_charges\n"
        "INV-2024-89341,Pacific Crest Freight Lines,2024-03-12,1010.45,171.88,457.19,1467.64\n"
    )
    records = process_file(data)
    assert len(records) == 1
    assert records[0]["status"] == "valid:good"


def test_invoice_separate_fuel_component_still_validates():
    # The other layout: fuel is its own line beside the accessorial subtotal and
    # all three components do add up to the total.
    data = _csv(
        "invoice_number,carrier_name,ship_date,freight_charge,fuel_surcharge,accessorial_charges,total_charges\n"
        "INV-2024-88421,Summit Freight Logistics Inc.,2024-11-12,4162.50,1019.81,130.00,5312.31\n"
    )
    records = process_file(data)
    assert len(records) == 1
    assert records[0]["status"] == "valid:good"


def test_invoice_total_mismatch_still_flags():
    # The check must still catch a genuine variance under either decomposition.
    data = _csv(
        "invoice_number,carrier_name,ship_date,freight_charge,fuel_surcharge,accessorial_charges,total_charges\n"
        "INV-9,Carrier Z,2025-04-01,1000.00,220.00,130.00,9999.00\n"
    )
    records = process_file(data)
    assert len(records) == 1
    assert records[0]["status"] == "flagged:critical"


def test_rate_sheet_valid():
    # Expiration kept in the future on purpose: this fixture was pinned to
    # 2026-01-01, so the moment that date passed the test asserted valid:good
    # against a sheet the code correctly reports as expired.
    data = _csv(
        "carrier_name,effective_date,expiration_date,origin_zone,destination_zone,freight_class,rate_basis,minimum_charge,base_rate,fuel_surcharge_table\n"
        "Carrier D,2025-01-01,2030-01-01,100,200,50,per cwt,50,25,table\n"
    )
    records = process_file(data)
    assert len(records) == 1
    assert records[0]["status"] == "valid:good"


def test_rate_sheet_expired():
    data = _csv(
        "carrier_name,effective_date,expiration_date,rate_basis,base_rate\n"
        "Carrier E,2020-01-01,2020-12-31,per cwt,25\n"
    )
    records = process_file(data)
    assert len(records) == 1
    assert records[0]["status"] == "expired:warning"


def test_unknown_text():
    data = b"Hello this is just text"
    records = process_file(data)
    assert isinstance(records, list)
    assert len(records) == 1
    assert records[0]["status"] == "unmapped:warning"


def test_rate_sheet_ignores_blank_invoice_columns():
    # Regression: the extractor returns every schema key with explicit nulls, so
    # an empty invoice_number column used to force document_type=invoice and the
    # rate sheet was reported missing invoice fields.
    #
    # fuel_surcharge_table is required here: _assign_status returns
    # contract-review:warning for any rate basis outside flat / minimum charge /
    # per unit that has no fuel surcharge schedule. That rule is pre-existing on
    # main. Without the column this fixture stops at contract-review and never
    # reaches the valid:good this test is asserting.
    data = _csv(
        "invoice_number,carrier_name,effective_date,expiration_date,rate_basis,base_rate,fuel_surcharge_table\n"
        ",Carrier F,2025-01-01,2030-01-01,per cwt,25,12%\n"
    )
    records = process_file(data)
    assert len(records) == 1
    assert records[0]["details"]["document_type"] == "rate_sheet"
    assert records[0]["status"] == "valid:good"


def test_rate_sheet_lanes_expand_to_rate_lines():
    sheet = _normalize_row(
        {
            "carrier_name": "Carrier G",
            "effective_date": "2025-01-01",
            "expiration_date": "2030-01-01",
            "lanes": [
                {
                    "origin_zone": "100",
                    "destination_zone": "200",
                    "freight_class": "50",
                    "rate_basis": "per cwt",
                    "base_rate": 25,
                    "minimum_charge": 50,
                },
                {
                    "origin_zone": "101",
                    "destination_zone": "201",
                    "freight_class": "55",
                    "rate_basis": "per cwt",
                    "base_rate": 30,
                    "minimum_charge": 60,
                },
            ],
        }
    )
    lines = _expand_rate_lanes(sheet)
    assert len(lines) == 2
    assert all(line["_document_type_hint"] == "rate_sheet" for line in lines)
    assert lines[0]["base_rate"] == 25.0
    assert lines[1]["base_rate"] == 30.0
    assert all(line["carrier_name"] == "Carrier G" for line in lines)


def test_rate_sheet_with_minimum_charge_is_not_flagged():
    # Regression: a "minimum charge above 3x base rate" rule compared a flat
    # dollar minimum ($150.00) against a rate expressed per 100 lbs ($5.50), so
    # 150 > 5.50 * 3 fired on every rate sheet carrying a minimum charge and
    # pushed it to contract-review:warning. These are the real Redwood Freight
    # Systems values from records 20029 / 20030.
    data = _csv(
        "carrier_name,effective_date,expiration_date,rate_basis,minimum_charge,base_rate,fuel_surcharge_table\n"
        "Redwood Freight Systems,2026-01-01,2026-11-30,per 100 lbs,150.00,5.50,table\n"
    )
    records = process_file(data)
    assert len(records) == 1
    assert records[0]["status"] == "valid:good"


def test_cross_upload_duplicate_is_flagged():
    # Regression: records 20031 / 20032 are the same invoice number uploaded
    # twice. The per-file check cannot see the second upload, so both came back
    # valid:good. Numbers are the real Redwood Freight Systems invoice.
    data = _csv(
        "invoice_number,carrier_name,ship_date,freight_charge,fuel_surcharge,accessorial_charges,total_charges\n"
        "INV-2026-11487,Redwood Freight Systems,2026-09-15,1095.20,197.12,511.12,1606.32\n"
    )
    records = process_file(data)
    assert len(records) == 1
    assert records[0]["status"] == "valid:good"

    flagged = apply_cross_upload_duplicates(records, {"INV-2026-11487"})
    assert flagged[0]["status"] == "flagged:critical"
    assert "duplicate invoice number from a previous upload" in flagged[0]["details"]["_notes"]


def test_cross_upload_duplicate_leaves_new_invoices_alone():
    data = _csv(
        "invoice_number,carrier_name,ship_date,freight_charge,total_charges\n"
        "INV-2026-99001,Carrier H,2026-09-15,100.00,100.00\n"
    )
    records = process_file(data)
    unflagged = apply_cross_upload_duplicates(records, {"INV-2026-11487"})
    assert unflagged[0]["status"] == "valid:good"
    assert unflagged[0]["details"]["_notes"] == []

    empty_history = apply_cross_upload_duplicates(records, set())
    assert empty_history[0]["status"] == "valid:good"


def test_cross_upload_duplicate_keeps_existing_notes():
    # A missing-field reason must survive alongside the duplicate reason.
    records = [
        {
            "title": "Carrier I",
            "status": "missing:critical",
            "details": {
                "invoice_number": "INV-2026-11487",
                "_notes": ["missing required fields: ship_date"],
            },
            "due_date": None,
        }
    ]
    flagged = apply_cross_upload_duplicates(records, {"INV-2026-11487"})
    assert flagged[0]["status"] == "flagged:critical"
    assert flagged[0]["details"]["_notes"] == [
        "missing required fields: ship_date",
        "duplicate invoice number from a previous upload",
    ]


# --- rate audit -------------------------------------------------------------
#
# Values are the real Redwood Freight Systems pair: rate sheet RC-2026-0442
# (Reno 89502 -> Sacramento 95814, $5.50 per 100 lbs, fuel bands to $4.25) and
# invoice INV-2026-11487 (14,000 lbs rated at $6.40/100lbs, so 896.00 invoiced
# against 770.00 contracted).


RATE_LINE = {
    "title": "Redwood Freight Systems",
    "contract_rate_sheet_identifier": "RC-2026-0442",
    "origin_zone_zip_postal": "NV (Reno) 89502",
    "destination_zone_zip_postal": "CA (Sacramento) 95814",
    "freight_class_commodity": "100",
    "rate_basis": "per 100 lbs",
    "base_rate": 5.50,
    "minimum_charge": 150.00,
    "fuel_surcharge_table": [
        {"diesel_price_band": "$3.00 \u2013 $3.25 / gallon", "surcharge": "12.50%"},
        {"diesel_price_band": "$3.76 \u2013 $4.00 / gallon", "surcharge": "20.00%"},
        {"diesel_price_band": "$4.01 \u2013 $4.25 / gallon", "surcharge": "22.00%"},
    ],
}


def _redwood_invoice(line_description, line_amount, fuel):
    return {
        "document_type": "invoice",
        "invoice_number": "INV-2026-11487",
        "origin_location": "2200 Industrial Parkway, Reno, NV 89502",
        "destination_location": "800 Market Street, Sacramento, CA 95814",
        "ship_date": "2026-09-15",
        "freight_charge": 1095.20,
        "fuel_surcharge": fuel,
        "accessorial_charges": 511.12,
        "total_charges": 1606.32,
        "invoice_line_items": [
            {"description": line_description, "amount": line_amount},
            {"description": "Pallets (4-way entry)", "amount": 150.00},
            {"description": "Pallet Jacks (Flatbed Add-on)", "amount": 49.20},
        ],
    }


def _record(details, status="valid:good"):
    return {"title": "Redwood Freight Systems", "status": status, "details": details, "due_date": None}


def test_extract_zip_reads_both_spellings():
    assert extract_zip("NV (Reno) 89502") == "89502"
    assert extract_zip("2200 Industrial Parkway, Reno, NV 89502") == "89502"
    assert extract_zip("no zip here") is None
    assert extract_zip(None) is None


def test_expected_linehaul_only_handles_derivable_bases():
    assert expected_linehaul(14000, 5.50, "per 100 lbs") == 770.0
    assert expected_linehaul(14000, 5.50, "per cwt") == 770.0
    # A per-mile contract cannot be applied: no field on either document carries
    # the mileage, so the audit must decline rather than guess.
    assert expected_linehaul(14000, 1.85, "per mile") is None


def test_fuel_band_lookup():
    table = RATE_LINE["fuel_surcharge_table"]
    assert lookup_fuel_band(table, 4.10) == 0.22
    assert lookup_fuel_band(table, 3.90) == 0.20
    assert lookup_fuel_band(table, 6.285) is None
    assert fuel_table_maximum(table) == 4.25


def test_parse_rated_line_needs_class_weight_and_rate():
    rated = parse_rated_line(
        [{"description": "Corrugated Boxes (Class 100, 14,000 lbs @ $6.40/100lbs)", "amount": 896.00}]
    )
    assert rated["weight"] == 14000.0
    assert rated["rate"] == 6.40
    assert rated["amount"] == 896.00
    # The same PDF lost the class/weight/rate text on a later upload.
    assert parse_rated_line([{"description": "Corrugated Boxes, Retail Goods", "amount": 896.00}]) is None


def test_rate_audit_flags_line_haul_overcharge():
    invoice = _redwood_invoice(
        "Corrugated Boxes, Retail Goods (Class 100, 14,000 lbs @ $6.40/100lbs)", 896.00, 197.12
    )
    audited = apply_rate_audit([_record(invoice)], [RATE_LINE], lambda ship_date: 4.10)
    details = audited[0]["details"]

    assert audited[0]["status"] == "flagged:critical"
    assert details["overcharge_amount"] == 126.00
    assert details["matched_rate_line_reference"] == "RC-2026-0442"
    assert any("line-haul overcharge of 126.00" in note for note in details["_notes"])


def test_rate_audit_never_writes_expected_freight_charge():
    # expected_freight_charge is compared against freight_charge, which is the
    # transportation SUBTOTAL including pallets and pallet jacks. Writing it would
    # report 1095.20 - 770.00 = 325.20 of variance on a correct invoice.
    invoice = _redwood_invoice(
        "Corrugated Boxes, Retail Goods (Class 100, 14,000 lbs @ $6.40/100lbs)", 896.00, 197.12
    )
    audited = apply_rate_audit([_record(invoice)], [RATE_LINE], lambda ship_date: 4.10)
    details = audited[0]["details"]

    assert "expected_freight_charge" not in details
    assert "expected_fuel_surcharge" not in details
    assert details["overcharge_amount"] == 126.00


def test_rate_audit_guards_a_line_without_class_weight_and_rate():
    invoice = _redwood_invoice("Corrugated Boxes, Retail Goods", 896.00, 197.12)
    audited = apply_rate_audit([_record(invoice)], [RATE_LINE], lambda ship_date: 4.10)
    details = audited[0]["details"]

    assert audited[0]["status"] == "contract-review:warning"
    assert "overcharge_amount" not in details
    assert any("no invoice line carried class, weight and rate" in note for note in details["_notes"])


def test_rate_audit_ignores_a_lane_with_no_contracted_rate():
    invoice = _redwood_invoice(
        "Corrugated Boxes, Retail Goods (Class 100, 14,000 lbs @ $6.40/100lbs)", 896.00, 197.12
    )
    invoice["origin_location"] = "4500 Harbor Boulevard, Oakland, CA 94607"
    invoice["destination_location"] = "2200 Industrial Parkway, Reno, NV 89502"

    audited = apply_rate_audit([_record(invoice)], [RATE_LINE], lambda ship_date: 4.10)

    assert audited[0]["status"] == "valid:good"
    assert audited[0]["details"]["_notes"] == []


def test_rate_audit_reports_fuel_outside_the_contracted_table():
    # Line-haul priced at the contracted rate, so the only finding is the fuel
    # band: the contract's table tops out at $4.25 and the market is $6.285.
    invoice = _redwood_invoice(
        "Corrugated Boxes, Retail Goods (Class 100, 14,000 lbs @ $5.50/100lbs)", 770.00, 197.12
    )
    audited = apply_rate_audit([_record(invoice)], [RATE_LINE], lambda ship_date: 6.285)
    details = audited[0]["details"]

    assert audited[0]["status"] == "contract-review:warning"
    assert "overcharge_amount" not in details
    assert any("outside the contracted table" in note for note in details["_notes"])


def test_rate_audit_does_not_downgrade_a_flagged_record():
    # A cross-upload duplicate is a stronger finding than "cannot audit", so an
    # invoice the duplicate check already flagged keeps that status.
    invoice = _redwood_invoice("Corrugated Boxes, Retail Goods", 896.00, 197.12)
    audited = apply_rate_audit([_record(invoice, status="flagged:critical")], [RATE_LINE], lambda ship_date: 4.10)

    assert audited[0]["status"] == "flagged:critical"


def test_rate_audit_leaves_rate_sheets_alone():
    sheet = {
        "document_type": "rate_sheet",
        "origin_zone_zip_postal": "NV (Reno) 89502",
        "destination_zone_zip_postal": "CA (Sacramento) 95814",
        "rate_basis": "per 100 lbs",
        "base_rate": 5.50,
    }
    # A rate sheet is skipped outright, so the audit adds nothing at all - not a
    # status, not a note, not an empty _notes list.
    audited = apply_rate_audit([_record(sheet)], [RATE_LINE], lambda ship_date: 4.10)

    assert audited[0]["status"] == "valid:good"
    assert "_notes" not in audited[0]["details"]
    assert "overcharge_amount" not in audited[0]["details"]


def test_rate_audit_matches_on_lane_ignoring_carrier_name():
    # Rate sheets carry no details.carrier_name - the record title holds it - so
    # carrier cannot be part of the join.
    line_without_carrier = {key: value for key, value in RATE_LINE.items() if key != "title"}
    invoice = _redwood_invoice(
        "Corrugated Boxes, Retail Goods (Class 100, 14,000 lbs @ $6.40/100lbs)", 896.00, 197.12
    )
    assert match_rate_line(invoice, [line_without_carrier]) is not None


if __name__ == "__main__":
    test_invoice_valid()
    test_invoice_missing()
    test_invoice_duplicate()
    test_invoice_fuel_nested_inside_accessorials()
    test_invoice_separate_fuel_component_still_validates()
    test_invoice_total_mismatch_still_flags()
    test_rate_sheet_valid()
    test_rate_sheet_expired()
    test_unknown_text()
    test_rate_sheet_ignores_blank_invoice_columns()
    test_rate_sheet_lanes_expand_to_rate_lines()
    test_rate_sheet_with_minimum_charge_is_not_flagged()
    test_cross_upload_duplicate_is_flagged()
    test_cross_upload_duplicate_leaves_new_invoices_alone()
    test_cross_upload_duplicate_keeps_existing_notes()
    test_extract_zip_reads_both_spellings()
    test_expected_linehaul_only_handles_derivable_bases()
    test_fuel_band_lookup()
    test_parse_rated_line_needs_class_weight_and_rate()
    test_rate_audit_flags_line_haul_overcharge()
    test_rate_audit_never_writes_expected_freight_charge()
    test_rate_audit_guards_a_line_without_class_weight_and_rate()
    test_rate_audit_ignores_a_lane_with_no_contracted_rate()
    test_rate_audit_reports_fuel_outside_the_contracted_table()
    test_rate_audit_does_not_downgrade_a_flagged_record()
    test_rate_audit_leaves_rate_sheets_alone()
    test_rate_audit_matches_on_lane_ignoring_carrier_name()
    print("all tests passed")
