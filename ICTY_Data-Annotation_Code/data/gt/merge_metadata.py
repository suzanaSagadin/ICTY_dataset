"""
01_merge_metadata.py

Merges a CSV of classified sentences with the metadata stored in the
utterance-level JSON file.

  * CSV  (sentence level): columns like  ,text,unique_id,sentence_index,token_count,label
  * JSON (utterance level): list of objects, each with a "unique-id" key plus
                            metadata (case, case_id, date, speaker, role, ...)

For every CSV row, the JSON record with the same unique id is looked up and all
of its fields that are NOT already present in the CSV row are added to it.
Several CSV rows (sentences) may share one unique id; each gets the metadata.

Output format follows the extension of the output path (.json or .csv).

Usage:
    python merge_metadata.py sentences.csv utterances.json merged.json
    python merge_metadata.py sentences.csv utterances.json merged.csv

Optional:
    --exclude FIELD [FIELD ...]   JSON fields NOT to copy over
                                  (e.g. --exclude utterances previous_utterance following_utterance)
    --delimiter ";"               CSV delimiter (default: auto-detect , ; or tab)
"""

import argparse
import csv
import json
import sys
from pathlib import Path

CSV_ID_KEY = "unique_id"    # id column in the CSV
JSON_ID_KEY = "unique-id"   # id key in the JSON


def detect_delimiter(path: Path) -> str:
    """Pick the most frequent of , ; tab in the header line."""
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        header = f.readline()
    counts = {d: header.count(d) for d in [",", ";", "\t"]}
    return max(counts, key=counts.get)


def load_json_index(json_path: Path) -> dict:
    """Load the JSON list and index the records by their unique id."""
    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Expected the JSON file to contain a list of objects.")

    index = {}
    duplicates = 0
    for rec in data:
        uid = rec.get(JSON_ID_KEY)
        if uid is None:
            continue
        if uid in index:
            duplicates += 1   # keep the first occurrence
            continue
        index[uid] = rec

    if duplicates:
        print(f"Warning: {duplicates} duplicate '{JSON_ID_KEY}' values in the JSON "
              f"(first occurrence used).", file=sys.stderr)
    return index


def merge(csv_path: str, json_path: str, out_path: str,
          exclude=None, delimiter=None) -> None:
    csv_path, json_path, out_path = Path(csv_path), Path(json_path), Path(out_path)
    exclude = set(exclude or [])

    for p in (csv_path, json_path):
        if not p.exists():
            raise FileNotFoundError(f"Input file not found: {p}")

    index = load_json_index(json_path)
    print(f"Loaded {len(index)} utterances from '{json_path}'.")

    delimiter = delimiter or detect_delimiter(csv_path)

    merged = []
    unmatched_ids = set()
    unmatched_rows = 0
    matched_rows = 0

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=delimiter, restval="")
        fieldnames = reader.fieldnames or []

        if CSV_ID_KEY not in fieldnames:
            raise KeyError(f"Column '{CSV_ID_KEY}' not found in CSV. "
                           f"Columns found: {fieldnames}")

        for row in reader:
            record = {}
            for k, v in row.items():
                if k is None:
                    continue
                # the unnamed first column (pandas index) gets a readable name
                record["row_index" if k == "" else k] = v

            uid = record[CSV_ID_KEY]
            source = index.get(uid)

            if source is None:
                unmatched_rows += 1
                unmatched_ids.add(uid)
            else:
                matched_rows += 1
                for key, value in source.items():
                    if key == JSON_ID_KEY or key in exclude or key in record:
                        continue
                    record[key] = value

            merged.append(record)

    # ---- write output -------------------------------------------------------
    if out_path.suffix.lower() == ".csv":
        all_keys = []
        for rec in merged:
            for k in rec:
                if k not in all_keys:
                    all_keys.append(k)
        with out_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=all_keys, restval="")
            writer.writeheader()
            writer.writerows(merged)
    else:
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)

    total = len(merged)
    print(f"Wrote {total} rows to '{out_path}'.")
    print(f"Matched:   {matched_rows} of {total} CSV rows found metadata in the JSON.")
    print(f"Unmatched: {unmatched_rows} of {total} CSV rows ({len(unmatched_ids)} unique ids).")

    if total and matched_rows == 0:
        print("WARNING: No CSV row found a match anywhere in the JSON. The ids probably "
              "differ in format. Compare an id from each file:\n"
              f"  CSV  '{CSV_ID_KEY}': {next(iter(unmatched_ids), None)!r}\n"
              f"  JSON '{JSON_ID_KEY}': {next(iter(index), None)!r}", file=sys.stderr)
    elif unmatched_rows:
        print(f"Examples of unmatched ids: {sorted(unmatched_ids)[:5]}", file=sys.stderr)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Merge sentence CSV with utterance JSON metadata.")
    ap.add_argument("csv_file")
    ap.add_argument("json_file")
    ap.add_argument("output_file")
    ap.add_argument("--exclude", nargs="*", default=[],
                    help="JSON fields that should not be copied into the output")
    ap.add_argument("--delimiter", default=None, help="CSV delimiter (default: auto-detect)")
    args = ap.parse_args()

    merge(args.csv_file, args.json_file, args.output_file,
          exclude=args.exclude, delimiter=args.delimiter)
