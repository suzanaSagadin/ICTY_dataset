""" 
LB: 
- this script extracts utterances spoken by the accused from JSON files in case folders and saves them to a CSV file
- it identifies accused speakers using the accused names stored in pleas.csv, as well as the role "accused" and the label "THE ACCUSED" and "MR. LASTNAME" as the speaker, if the speaker is in pleas.csv or folder names
- the output contains one row per accused utterance together with metadata such as case, case ID, date and source file
- THE inclusion of the information from parsed.csv (names) made a difference of about 5937 rows of utterances, which is a lot
In total then we have:
Case folders processed: 82
JSON files processed:   16495
Rows in dataframe:      187449
Accused utterances:     187449

"""
from pathlib import Path
import json
import unicodedata
import pandas as pd


# LB: function for removing accents and special characters from names
# LB: example: BLAGOJEVIĆ -> BLAGOJEVIC
def normalize_name(name):
    name = str(name).upper()
    name = unicodedata.normalize("NFKD", name)
    name = "".join(
        char for char in name
        if not unicodedata.combining(char)
    )
    return name


# LB: function for extracting the last name from a full name
# LB: example: "VIDOJE BLAGOJEVIĆ" -> "BLAGOJEVIC"
def get_last_name(full_name):
    normalized = normalize_name(full_name)
    return normalized.split()[-1]


# LB: path setup
script_dir = Path(__file__).resolve().parent
project_dir = script_dir.parent

base_dir = project_dir / "data" / "parsed"
pleas_path = project_dir / "data" / "cis_parsed" / "pleas.csv"

print("=" * 60)
print("Starting accused utterance extraction")
print(f"Looking for case folders in:\n{base_dir}")
print(f"Using accused names from:\n{pleas_path}")
print("=" * 60)
# LB: just some messages so I know what is going on when I run this script


# LB: loading the accused names from pleas.csv
pleas_df = pd.read_csv(pleas_path)

# LB: creating a dictionary where:
# LB: key = case name
# LB: value = list of accused last names
case_to_accused_names = {}

for case_name, group in pleas_df.groupby("case"):

    last_names = (
        group["accused"]
        .dropna()
        .apply(get_last_name)
        .unique()
        .tolist()
    )

    case_to_accused_names[case_name] = last_names

print(f"Loaded accused names for {len(case_to_accused_names)} cases.")


rows = []

total_folders = 0
total_files = 0
total_utterances = 0

# LB: preparing counters for the number of folders, files and utterances


# LB: looping through the case folders in the parsed directory
for case_folder in base_dir.iterdir():

    if not case_folder.is_dir():
        continue

    total_folders += 1

    # LB: skipping non-directory files and counting case folders
    case_from_folder = case_folder.name

    print(f"\nProcessing case: {case_from_folder}")

    # LB: getting all accused last names for the current case from pleas.csv
    possible_accused_names = case_to_accused_names.get(
        case_from_folder,
        []
    )

    # LB: fallback in case a case is missing from pleas.csv
    if not possible_accused_names:

        name_parts = [
            part for part in case_from_folder.split("_")
            if part.lower() != "old"
        ]

        possible_accused_names = [
            normalize_name(part)
            for part in name_parts
        ]

    print(f"Possible accused names: {possible_accused_names}")

    case_file_count = 0
    case_utterance_count = 0

    # LB: looping through the JSON files in the current case folder
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

        # LB: extracting case-level metadata from the JSON file
        case = case_from_folder
        case_id = data.get("case_id")
        date = data.get("date")

        matching_utterances = 0

        # LB: creating possible speaker labels such as:
        # LB: MR. BLAGOJEVIC
        # LB: MR. JOKIC
        accused_speaker_labels = [
            f"MR. {name}"
            for name in possible_accused_names
        ]

        # LB: looping through all utterances in the transcript
        for utt in data.get("utterances", []):

            speaker_original = utt.get("speaker", "")
            speaker = normalize_name(speaker_original)

            role = utt.get("role", "")

            # LB: checking whether the utterance belongs to the accused
            is_accused = (
                speaker == "THE ACCUSED"
                or role == "accused"
                or speaker in accused_speaker_labels
            )

            # LB: if the utterance belongs to the accused,store it together with the metadata
            if is_accused:

                rows.append({
                    "case": case,
                    "case_id": case_id,
                    "date": date,
                    "utterances": utt.get("text"),
                    "speaker": speaker_original,
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
print(f"Rows in dataframe:      {len(df)}")
print(f"Accused utterances:     {total_utterances}")
print(f"CSV output file:        {csv_path}")
print(f"Pickle output file:     {pickle_path}")
print("=" * 60)

print("\nFirst five rows:")
print(df.head())