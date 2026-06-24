from pathlib import Path
import pandas as pd

script_dir = Path(__file__).resolve().parent

csv_path = script_dir / "accused_utterances.csv"

print(csv_path)
print(csv_path.exists())

df = pd.read_csv(csv_path)

df.to_pickle(script_dir / "accused_utterances.pkl")