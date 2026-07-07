"""
LB:
- this script extracts utterances spoken by the accused from JSON files in case folders and saves them to a CSV file
- it uses the is_accused flag computed by the parser (from pleas.csv + case_mapping.csv + speaker patterns)
- the output contains one row per accused utterance together with metadata such as case, case ID, date and source file
- duplicate hearings (same content filed under multiple case numbers) are dropped
  using the canonical owner from data_250625/reports/hearing_dedup.csv
- known counsel sharing an accused's surname (parser/counsel_exclusions.csv) are
  dropped even where the stored is_accused flag is stale
- the parser labels the accused answering questions in the witness position as
  role "defendant" ("accused" = dock statements, "counsel" = questioning
  witnesses / addressing the court); this script passes roles through unchanged

"""
from pathlib import Path
import csv
import json
import pandas as pd


# LB: path setup
script_dir = Path(__file__).resolve().parent
project_dir = script_dir.parent

base_dir = project_dir / "data_250625" / "transcripts_parsed"

# non-canonical duplicate copies to skip: (case, source_file)
non_canonical = set()
dedup_path = project_dir / "data_250625" / "reports" / "hearing_dedup.csv"
if dedup_path.exists():
    with open(dedup_path, encoding="utf-8") as f:
        non_canonical = {
            (r["case"], r["source_file"])
            for r in csv.DictReader(f)
            if r["is_canonical"] == "no"
        }
    print(f"Loaded dedup table: {len(non_canonical)} duplicate copies will be skipped")
else:
    print(f"WARNING: no dedup table at {dedup_path} — duplicate hearings will be double-counted")

# counsel wrongly matching an accused surname: (case, speaker)
counsel_exclusions = set()
exclusions_path = project_dir / "parser" / "counsel_exclusions.csv"
if exclusions_path.exists():
    with open(exclusions_path, encoding="utf-8") as f:
        counsel_exclusions = {
            (r["case"], r["speaker"]) for r in csv.DictReader(f)
        }
    print(f"Loaded counsel exclusions: {sorted(counsel_exclusions)}")

print("=" * 60)
print("Starting accused utterance extraction")
print(f"Looking for case folders in:\n{base_dir}")
print("=" * 60)


rows = []

total_folders = 0
total_files = 0
total_utterances = 0
skipped_duplicates = 0
excluded_counsel = 0

# LB: preparing counters for the number of folders, files and utterances


# LB: looping through the case folders in the parsed directory
for case_folder in base_dir.iterdir():

    if not case_folder.is_dir():
        continue

    total_folders += 1

    # LB: skipping non-directory files and counting case folders
    case_from_folder = case_folder.name

    print(f"\nProcessing case: {case_from_folder}")

    case_file_count = 0
    case_utterance_count = 0

    # LB: looping through the JSON files in the current case folder
    for json_file in case_folder.glob("*.json"):

        # skipping non-canonical duplicate copies of hearings
        if (case_from_folder, json_file.name) in non_canonical:
            skipped_duplicates += 1
            continue

        total_files += 1
        case_file_count += 1

        try:

            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

        except Exception as e:

            print(f"    ERROR reading file: {e}")
            continue

        # LB: extracting case-level metadata from the JSON file
        case = case_from_folder
        case_id = data.get("case_id_base") or data.get("case_id_transcript") or data.get("case_id")
        date = data.get("date")

        matching_utterances = 0

        # LB: looping through all utterances in the transcript
        for utt in data.get("utterances", []):

            # LB: using the is_accused flag computed by the parser
            if utt.get("is_accused", False):

                # dropping known counsel who share an accused's surname
                if (case_from_folder, utt.get("speaker", "")) in counsel_exclusions:
                    excluded_counsel += 1
                    continue

                rows.append({
                    "case": case,
                    "case_id": case_id,
                    "date": date,
                    "utterances": utt.get("text"),
                    "speaker": utt.get("speaker", ""),
                    "role": utt.get("role", ""),
                    "type": utt.get("type"),
                    "utterance_id": utt.get("id"),
                    "source_file": json_file.name
                })

                matching_utterances += 1
                total_utterances += 1
                case_utterance_count += 1

        if matching_utterances:
            print(
                f"    {json_file.name}: {matching_utterances} accused utterances"
            )

    print(
        f"Finished case '{case_from_folder}' "
        f"({case_file_count} files, "
        f"{case_utterance_count} accused utterances)"
    )


print("\n" + "=" * 60)
print("Creating dataframe...")
print("=" * 60)

# LB: creating a DataFrame from the collected utterances
df = pd.DataFrame(rows)

# LB: converting the date column to datetime format
df["date"] = pd.to_datetime(df["date"], errors="coerce")

csv_path = script_dir / "accused_utterances.csv"
pickle_path = script_dir / "accused_utterances.pkl"

print(f"Saving CSV to:\n{csv_path}")

# LB: saving the dataframe as CSV
df.to_csv(
    csv_path,
    index=False,
    encoding="utf-8"
)

print(f"Saving pickle to:\n{pickle_path}")

# LB: saving the dataframe as a pickle file for faster loading later
df.to_pickle(pickle_path)


print("\n" + "=" * 60)
print("FINISHED")
print("=" * 60)
print(f"Case folders processed: {total_folders}")
print(f"JSON files processed:   {total_files}")
print(f"Duplicate copies skipped: {skipped_duplicates}")
print(f"Counsel utterances excluded: {excluded_counsel}")
print(f"Rows in dataframe:      {len(df)}")
print(f"Accused utterances:     {total_utterances}")
print(f"CSV output file:        {csv_path}")
print(f"Pickle output file:     {pickle_path}")
print("=" * 60)

print("\nFirst five rows:")
print(df.head())