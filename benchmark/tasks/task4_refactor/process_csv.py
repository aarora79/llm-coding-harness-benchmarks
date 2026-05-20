"""Monolithic CSV processor — refactor into separate functions."""
import csv
import sqlite3
from datetime import datetime


def process_csv_to_db(csv_path, db_path):
    """Read CSV, validate rows, transform data, write to SQLite.

    This function does too many things. Refactor it into smaller,
    single-responsibility functions.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY,
            date TEXT,
            amount REAL,
            category TEXT,
            description TEXT,
            is_valid INTEGER
        )
    """)

    valid_count = 0
    invalid_count = 0
    total_amount = 0.0

    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Validate date
            try:
                parsed_date = datetime.strptime(row.get("date", ""), "%Y-%m-%d")
                date_str = parsed_date.strftime("%Y-%m-%d")
            except ValueError:
                invalid_count += 1
                cursor.execute(
                    "INSERT INTO transactions (date, amount, category, description, is_valid) VALUES (?, ?, ?, ?, ?)",
                    (row.get("date", ""), 0, row.get("category", ""), row.get("description", ""), 0),
                )
                continue

            # Validate amount
            try:
                amount = float(row.get("amount", 0))
                if amount < 0:
                    raise ValueError("Negative amount")
            except (ValueError, TypeError):
                invalid_count += 1
                cursor.execute(
                    "INSERT INTO transactions (date, amount, category, description, is_valid) VALUES (?, ?, ?, ?, ?)",
                    (date_str, 0, row.get("category", ""), row.get("description", ""), 0),
                )
                continue

            # Validate category
            valid_categories = {"food", "transport", "utilities", "entertainment", "other"}
            category = row.get("category", "").lower().strip()
            if category not in valid_categories:
                category = "other"

            # Transform description
            description = row.get("description", "").strip()[:100]
            if not description:
                description = f"Transaction on {date_str}"

            # Write to database
            cursor.execute(
                "INSERT INTO transactions (date, amount, category, description, is_valid) VALUES (?, ?, ?, ?, ?)",
                (date_str, amount, category, description, 1),
            )
            valid_count += 1
            total_amount += amount

    conn.commit()
    conn.close()

    return {
        "valid": valid_count,
        "invalid": invalid_count,
        "total_amount": round(total_amount, 2),
    }
