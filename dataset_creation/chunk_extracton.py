# LB: This script extracts utterances spoken by the accused from JSON files in case folders and saves them to a CSV file
#  LB: It extracts based on the role or name of the speaker, and includes relevant information such as case ID, date, and source file.


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
## LB: just some messages so I know whats going on when I run this script

rows = []

total_folders = 0
total_files = 0
total_utterances = 0
# LB: preparing the counters for the number of folders, files and utterances

# LB: Looping through the case folders in the base directory
for case_folder in base_dir.iterdir():

    if not case_folder.is_dir():
        continue

    total_folders += 1
    # LB: skipping non-directory files and counting the number of case folders
    case_from_folder = case_folder.name


    print(f"\nProcessing case: {case_from_folder}")

    # LB:split the name by underscore and remove the "old" part, then convert to uppercase for matching
    name_parts = [
        part for part in case_from_folder.split("_")
        if part.lower() != "old"
    ]

    possible_accused_names = [part.upper() for part in name_parts]

    print(f"Possible accused names: {possible_accused_names}")

    case_file_count = 0
    case_utterance_count = 0
    # LB: Looping through the JSON files in the case folder
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
         
        # LB: Extracting relevant information from the JSON data
        case = case_from_folder
        case_id = data.get("case_id")
        date = data.get("date")

        matching_utterances = 0
        # LB: Looping through the utterances in the JSON data
        for utt in data.get("utterances", []):

            speaker = utt.get("speaker", "")
            role = utt.get("role", "")
        # LB: Checking if the speaker is the accused based on the role or name matching
            is_accused = (
                speaker == "THE ACCUSED"
                or role == "accused"
                or speaker in [f"MR. {name}" for name in possible_accused_names]
            )
        # LB: If the speaker is identified as the accused, append the relevant information to the rows list
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
# LB: Creating a DataFrame from the collected rows and converting the date column to datetime format
df = pd.DataFrame(rows)

df["date"] = pd.to_datetime(df["date"], errors="coerce")

output_path = script_dir / "accused_utterances.csv"

print(f"Saving dataframe to:\n{output_path}")
# LB: Saving the DataFrame to a CSV file with UTF-8 encoding and without the index
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