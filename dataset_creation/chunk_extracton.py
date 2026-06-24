from pathlib import Path
import json
import pandas as pd

# Path setup
script_dir = Path(__file__).resolve().parent
base_dir = script_dir.parent / "data" / "parsed"

print("=" * 60)
print("Starting accused utterance extraction")
print(f"Looking for case folders in:\n{base_dir}")
print("=" * 60)

rows = []

total_folders = 0
total_files = 0
total_utterances = 0

for case_folder in base_dir.iterdir():

    if not case_folder.is_dir():
        continue

    total_folders += 1
    case_from_folder = case_folder.name

    print(f"\nProcessing case: {case_from_folder}")

    # Handle names such as popovic_old
    name_parts = [
        part for part in case_from_folder.split("_")
        if part.lower() != "old"
    ]

    possible_accused_names = [part.upper() for part in name_parts]

    print(f"Possible accused names: {possible_accused_names}")

    case_file_count = 0
    case_utterance_count = 0

    for json_file in case_folder.glob("*.json"):

        total_files += 1
        case_file_count += 1

        print(f"  Reading: {json_file.name}")

        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

        except Exception as e:
            print(f"    ERROR reading file: {e}")
            continue

        case = case_from_folder
        case_id = data.get("case_id")
        date = data.get("date")

        matching_utterances = 0

        for utt in data.get("utterances", []):

            speaker = utt.get("speaker", "")
            role = utt.get("role", "")

            is_accused = (
                speaker == "THE ACCUSED"
                or role == "accused"
                or speaker in [f"MR. {name}" for name in possible_accused_names]
            )

            if is_accused:

                rows.append({
                    "case": case,
                    "case_id": case_id,
                    "date": date,
                    "utterances": utt.get("text"),
                    "speaker": speaker,
                    "role": role,
                    "type": utt.get("type"),
                    "utterance_id": utt.get("id"),
                    "source_file": json_file.name
                })

                matching_utterances += 1
                total_utterances += 1
                case_utterance_count += 1

        print(
            f"    Found {matching_utterances} accused utterances"
        )

    print(
        f"Finished case '{case_from_folder}' "
        f"({case_file_count} files, "
        f"{case_utterance_count} accused utterances)"
    )

print("\n" + "=" * 60)
print("Creating dataframe...")
print("=" * 60)

df = pd.DataFrame(rows)

df["date"] = pd.to_datetime(df["date"], errors="coerce")

output_path = script_dir / "accused_utterances.csv"

print(f"Saving dataframe to:\n{output_path}")

df.to_csv(
    output_path,
    index=False,
    encoding="utf-8"
)

print("\n" + "=" * 60)
print("FINISHED")
print("=" * 60)
print(f"Case folders processed: {total_folders}")
print(f"JSON files processed:   {total_files}")
print(f"Rows in dataframe:      {len(df)}")
print(f"Accused utterances:     {total_utterances}")
print(f"Output file:            {output_path}")
print("=" * 60)

print("\nFirst five rows:")
print(df.head())