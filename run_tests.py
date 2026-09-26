import unittest

from processor import STATUSES, process_file


class TestProcessFile(unittest.TestCase):
    def test_csv_generic(self):
        data = b"supplier,product,price\nAcme,Widget,9.99"
        records = process_file(data)

        self.assertIsInstance(records, list)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["title"], "Acme")
        self.assertEqual(records[0]["status"], "Parsed")
        self.assertEqual(records[0]["due_date"], None)
        self.assertIsInstance(records[0]["details"], dict)

    def test_csv_invoice_fields(self):
        data = (
            b"carrier_name,invoice_number,invoice_date,due_date,total_billed_amount\n"
            b"RoadRunner,INV-100,2025-02-01,2025-03-01,1234.56"
        )
        records = process_file(data)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["title"], "RoadRunner")
        self.assertEqual(records[0]["due_date"], "2025-03-01")
        self.assertEqual(records[0]["details"]["invoice_number"], "INV-100")

    def test_plain_text_fallback(self):
        data = (
            b"carrier_name: Acme Freight, invoice_number: INV-200, "
            b"due_date: 2025-04-01"
        )
        records = process_file(data)

        self.assertTrue(records)
        self.assertIn(records[0]["status"], STATUSES)

    def test_exact_statuses_present(self):
        expected = [
            "Flagged overbill",
            "Approved to pay",
            "Recovered",
            "Written off",
            "Missing rate card",
            "Missing document",
            "Low extraction confidence",
        ]
        for status in expected:
            self.assertIn(status, STATUSES)


if __name__ == "__main__":
    unittest.main()
