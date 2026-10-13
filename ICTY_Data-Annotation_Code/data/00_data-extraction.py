"""
00_data_extraction.py

Converts a CSV file with conversation/utterance data into a JSON file
containing a list of objects (one per CSV row), suitable for feeding
into a data-annotation pipeline.

Expected CSV headers:
    case
    case_id
    date
    previous_utterance
    previous_utterance_speaker
    utterances
    speaker
    role
    type
    utterance_id
    following_utterance
    following_utterance_speaker
    source_file

For each row, a unique identifier is created by combining:
    case + "_" + source_file (with a trailing ".json" stripped) + "_" + utterance_id
and stored under the key "unique-id" in the resulting object.

Usage:
    python 00_data_extraction.py input.csv output.json
"""

import csv
import json
import sys
from pathlib import Path

# Headers we expect to find in the CSV (order doesn't matter for reading,
# but this list is used to validate that nothing is missing).
EXPECTED_HEADERS = [
    "case",
    "case_id",
    "date",
    "previous_utterance",
    "previous_utterance_speaker",
    "utterances",
    "speaker",
    "role",
    "type",
    "utterance_id",
    "following_utterance",
    "following_utterance_speaker",
    "source_file",
]


def csv_to_json(csv_path: str, json_path: str) -> None:
    csv_path = Path(csv_path)
    json_path = Path(json_path)

    if not csv_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {csv_path}")

    records = []

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        # Sanity check: warn about missing/extra headers, but don't hard-fail,
        # in case the CSV has slightly different column ordering or extras.
        missing = [h for h in EXPECTED_HEADERS if h not in reader.fieldnames]
        if missing:
            print(
                f"Warning: the following expected headers are missing from "
                f"the CSV and will be skipped: {missing}",
                file=sys.stderr,
            )

        for row_num, row in enumerate(reader, start=2):  # row 1 = header line
            record = {header: row.get(header, "") for header in EXPECTED_HEADERS}

            case = record.get("case", "")
            source_file = record.get("source_file", "")
            if source_file.endswith(".json"):
                source_file = source_file[: -len(".json")]
            utterance_id = record.get("utterance_id", "")

            record["unique-id"] = f"{case}_{source_file}_{utterance_id}"

            records.append(record)

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print(f"Converted {len(records)} rows from '{csv_path}' to '{json_path}'.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python csv_to_json.py <input_csv> <output_json>")
        sys.exit(1)

    input_csv = sys.argv[1]
    output_json = sys.argv[2]

    csv_to_json(input_csv, output_json)