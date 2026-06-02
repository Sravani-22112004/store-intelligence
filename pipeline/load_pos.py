"""
load_pos.py — Load POS transaction data from the Brigade Road CSV into the database.
"""
import argparse
import csv
import sys
import os
from datetime import datetime
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from database import Base, DBTransaction

DEFAULT_DB = os.getenv("DATABASE_URL", "postgresql://store:store123@localhost:5432/storedb")


def load_pos(csv_path: str, store_id: str, db_url: str):
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    # Clear existing transactions for this store first
    db.execute(text("DELETE FROM pos_transactions WHERE store_id = :sid"), {"sid": store_id})
    db.commit()

    loaded = 0
    skipped = 0
    seen_ids = set()

    with open(csv_path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        print(f"Columns: {reader.fieldnames}")

        for i, row in enumerate(reader):
            try:
                # Build a unique transaction_id from row index if duplicates exist
                raw_id = (row.get('transaction_id') or row.get('order_id') or
                          row.get('bill_no') or row.get('Invoice No') or str(i))
                txn_id = f"{raw_id}_{i}"  # make unique by appending row index

                # Parse timestamp
                ts_str = row.get('timestamp') or row.get('order_timestamp') or ''
                if not ts_str:
                    date_str = (row.get('order_date') or row.get('Date') or
                                row.get('Txn Date') or '').strip()
                    time_str = (row.get('order_time') or row.get('Time') or
                                row.get('Txn Time') or '00:00:00').strip()
                    ts_str = f"{date_str} {time_str}".strip()

                ts = None
                for fmt in [
                    '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d %H:%M:%S',
                    '%d-%m-%Y %H:%M:%S', '%d/%m/%Y %H:%M:%S',
                    '%d-%m-%Y %H:%M', '%Y-%m-%d',
                    '%d-%m-%Y', '%d/%m/%Y',
                ]:
                    try:
                        ts = datetime.strptime(ts_str.strip(), fmt)
                        break
                    except ValueError:
                        continue

                if not ts:
                    print(f"  Skipping row {i} — cannot parse timestamp: '{ts_str}'")
                    skipped += 1
                    continue

                # Parse amount — try multiple column names
                amount_raw = (row.get('basket_value_inr') or row.get('total_amount') or
                              row.get('GMV') or row.get('NMV') or row.get('Amount') or
                              row.get('Net Amount') or row.get('Total') or '0')
                amount = float(str(amount_raw).replace(',', '').replace('₹', '').strip() or '0')

                txn = DBTransaction(
                    store_id=store_id,
                    transaction_id=txn_id,
                    timestamp=ts,
                    basket_value_inr=amount,
                )
                db.add(txn)
                loaded += 1

                # Commit in batches
                if loaded % 100 == 0:
                    db.commit()
                    print(f"  Loaded {loaded} rows...")

            except Exception as e:
                print(f"  Error row {i}: {e}")
                skipped += 1

    db.commit()
    db.close()
    print(f"\n✅ Loaded {loaded} transactions, skipped {skipped}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--csv', required=True)
    parser.add_argument('--store-id', default='STORE_BLR_002')
    parser.add_argument('--db-url', default=DEFAULT_DB)
    args = parser.parse_args()
    load_pos(args.csv, args.store_id, args.db_url)
