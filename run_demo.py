from processor import process_file


test_bytes = b"""invoice_number,carrier_name,ship_date,bill_of_lading_pro_number,origin_location,destination_location,actual_weight,billable_weight,freight_charge,fuel_surcharge,accessorial_codes,accessorial_descriptions,accessorial_charges,total_charges,payment_due_date,currency
INV-1001,Acme Freight,2025-04-01,PRO123456,Chicago IL 60606,Dallas TX 75201,1040,1050,825.50,221.25,LIFTGATE,Residential delivery,75.00,1121.75,2025-04-30,USD
INV-1002,Acme Freight,2025-04-05,PRO123457,Cleveland OH 44113,Atlanta GA 30303,2200,2200,1510.00,399.80,LIFTGATE,Residential delivery,75.00,1984.80,2025-05-05,USD
"""


def main():
    results = process_file(test_bytes)

    assert isinstance(results, list)
    assert len(results) == 2

    required_keys = {"title", "status", "details", "due_date"}

    for record in results:
        assert set(record.keys()) == required_keys, record.keys()
        assert record["title"] == "Acme Freight"
        assert record["status"] == "valid:good"
        assert record["due_date"] in {"2025-04-30", "2025-05-05"}

    print("demo ok")


if __name__ == "__main__":
    main()
