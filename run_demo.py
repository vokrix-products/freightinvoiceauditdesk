from processor import process_file


def main():
    test_bytes = b"supplier,product,price\nAcme,Widget,9.99"
    results = process_file(test_bytes)

    assert isinstance(results, list)
    assert len(results) == 1

    record = results[0]
    assert record["title"] == "Acme"
    assert record["status"] == "Parsed"
    assert isinstance(record["details"], dict)
    assert record["due_date"] is None or isinstance(record["due_date"], str)

    print("demo ok")


if __name__ == "__main__":
    main()
