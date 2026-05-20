"""Tests that must still pass after refactoring."""
import os
import csv
import tempfile
import sqlite3
from process_csv import process_csv_to_db


def make_csv(rows):
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="")
    writer = csv.DictWriter(tmp, fieldnames=["date", "amount", "category", "description"])
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    tmp.close()
    return tmp.name


def test_valid_rows():
    csv_path = make_csv([
        {"date": "2024-01-15", "amount": "50.00", "category": "food", "description": "Groceries"},
        {"date": "2024-01-16", "amount": "25.50", "category": "transport", "description": "Bus pass"},
    ])
    db_path = tempfile.mktemp(suffix=".db")
    result = process_csv_to_db(csv_path, db_path)
    assert result["valid"] == 2
    assert result["invalid"] == 0
    assert result["total_amount"] == 75.50
    os.unlink(csv_path)
    os.unlink(db_path)


def test_invalid_date():
    csv_path = make_csv([
        {"date": "not-a-date", "amount": "10.00", "category": "food", "description": "Test"},
    ])
    db_path = tempfile.mktemp(suffix=".db")
    result = process_csv_to_db(csv_path, db_path)
    assert result["valid"] == 0
    assert result["invalid"] == 1
    os.unlink(csv_path)
    os.unlink(db_path)


def test_negative_amount():
    csv_path = make_csv([
        {"date": "2024-01-15", "amount": "-5.00", "category": "food", "description": "Refund"},
    ])
    db_path = tempfile.mktemp(suffix=".db")
    result = process_csv_to_db(csv_path, db_path)
    assert result["valid"] == 0
    assert result["invalid"] == 1
    os.unlink(csv_path)
    os.unlink(db_path)


def test_unknown_category_maps_to_other():
    csv_path = make_csv([
        {"date": "2024-01-15", "amount": "10.00", "category": "random", "description": "Test"},
    ])
    db_path = tempfile.mktemp(suffix=".db")
    result = process_csv_to_db(csv_path, db_path)
    assert result["valid"] == 1

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT category FROM transactions WHERE is_valid=1").fetchone()
    assert row[0] == "other"
    conn.close()
    os.unlink(csv_path)
    os.unlink(db_path)


def test_empty_csv():
    csv_path = make_csv([])
    db_path = tempfile.mktemp(suffix=".db")
    result = process_csv_to_db(csv_path, db_path)
    assert result["valid"] == 0
    assert result["invalid"] == 0
    assert result["total_amount"] == 0.0
    os.unlink(csv_path)
    os.unlink(db_path)
