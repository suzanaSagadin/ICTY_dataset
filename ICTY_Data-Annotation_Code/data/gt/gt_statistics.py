"""
02_statistics.py

Creates descriptive statistics from the merged ground-truth CSV
(one row = one classified sentence).

Answers:
  1. How many unique cases are featured?
  2. How are the labels (agency / externalization / none) distributed per case?
  3. How do the roles (accused / witness / ...) correspond to the labels,
     regardless of case?

Expected CSV columns (at least):  case, role, label
(also used if present: unique_id, for counting utterances)

Usage:
    python gt_statistics.py merged.csv
    python gt_statistics.py merged.csv --out-dir stats

By default the tables are printed AND saved as CSV files in a folder named
"<input name>_stats" next to the input file. Use --no-save to only print.
"""

import argparse
import csv
import sys
from pathlib import Path

import pandas as pd

LABEL_ORDER = ["agency", "externalization", "none"]

# Numeric ground-truth codes -> label names
LABEL_MAP = {"0": "none", "1": "agency", "2": "externalization"}
MISSING = "(missing)"


def detect_delimiter(path: Path) -> str:
    """Pick the most frequent of , ; tab in the header line."""
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        header = f.readline()
    counts = {d: header.count(d) for d in [",", ";", "\t"]}
    return max(counts, key=counts.get)


def load(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(
        csv_path,
        sep=detect_delimiter(csv_path),
        encoding="utf-8-sig",
        dtype=str,
        keep_default_na=False,   # keep "none" as text, not as NaN
        usecols=lambda c: c in {"case", "role", "label", "unique_id"},
    )
    for col in ("case", "role", "label"):
        if col not in df.columns:
            raise KeyError(f"Column '{col}' not found in CSV.")
        df[col] = df[col].str.strip().replace("", MISSING)
    # "1.0" -> "1", then map numeric codes to names (text labels pass through)
    df["label"] = df["label"].str.lower().str.replace(r"\.0$", "", regex=True)
    df["label"] = df["label"].replace(LABEL_MAP)
    return df


def label_table(df: pd.DataFrame, by: str) -> pd.DataFrame:
    """Counts of each label per value of `by`, with a Total column and row."""
    table = pd.crosstab(df[by], df["label"])

    # standard labels first (always shown, even with 0), then any unexpected ones
    extra = [c for c in table.columns if c not in LABEL_ORDER]
    table = table.reindex(columns=LABEL_ORDER + extra, fill_value=0)

    table["Total"] = table.sum(axis=1)
    table = table.sort_values("Total", ascending=False)
    table.loc["TOTAL"] = table.sum()
    table.index.name = by
    return table


def percent_table(counts: pd.DataFrame) -> pd.DataFrame:
    """Row percentages (each row sums to 100 across the label columns)."""
    label_cols = [c for c in counts.columns if c != "Total"]
    pct = counts[label_cols].div(counts["Total"], axis=0) * 100
    return pct.round(1).fillna(0)


def show(title: str, table: pd.DataFrame) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    print(table.to_string())


def main(csv_path: str, out_dir: str | None, save: bool) -> None:
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {csv_path}")

    df = load(csv_path)
    pd.set_option("display.width", 200)

    # ---- overview -----------------------------------------------------------
    n_cases = df["case"].nunique()
    n_sentences = len(df)
    n_utterances = df["unique_id"].nunique() if "unique_id" in df.columns else None

    print(f"\nFile: {csv_path}")
    print(f"Unique cases:      {n_cases}")
    print(f"Sentences (rows):  {n_sentences}")
    if n_utterances is not None:
        print(f"Unique utterances: {n_utterances}")

    overall = df["label"].value_counts().rename_axis("label").to_frame("count")
    overall["percent"] = (overall["count"] / n_sentences * 100).round(1)
    show("Overall label distribution (sentences)", overall)

    # ---- labels per case ----------------------------------------------------
    by_case = label_table(df, "case")
    by_case_pct = percent_table(by_case)
    show("Labels per case (sentence counts)", by_case)
    show("Labels per case (row percentages)", by_case_pct)

    # ---- labels per role (all cases pooled) ---------------------------------
    by_role = label_table(df, "role")
    by_role_pct = percent_table(by_role)
    show("Labels per role, all cases pooled (sentence counts)", by_role)
    show("Labels per role, all cases pooled (row percentages)", by_role_pct)

    # ---- save ---------------------------------------------------------------
    if save:
        out = Path(out_dir) if out_dir else csv_path.with_name(csv_path.stem + "_stats")
        out.mkdir(parents=True, exist_ok=True)
        overall.to_csv(out / "labels_overall.csv", encoding="utf-8-sig")
        by_case.to_csv(out / "labels_per_case_counts.csv", encoding="utf-8-sig")
        by_case_pct.to_csv(out / "labels_per_case_percent.csv", encoding="utf-8-sig")
        by_role.to_csv(out / "labels_per_role_counts.csv", encoding="utf-8-sig")
        by_role_pct.to_csv(out / "labels_per_role_percent.csv", encoding="utf-8-sig")
        print(f"\nTables saved to: {out.resolve()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Statistics on cases, labels and roles.")
    ap.add_argument("csv_file")
    ap.add_argument("--out-dir", default=None, help="folder for the saved CSV tables")
    ap.add_argument("--no-save", action="store_true", help="only print, do not write files")
    args = ap.parse_args()
    main(args.csv_file, args.out_dir, save=not args.no_save)
