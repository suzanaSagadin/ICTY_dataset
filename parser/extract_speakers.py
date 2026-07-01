#!/usr/bin/env python3
"""
Extract all unique speakers per case from parsed transcripts.

Produces a CSV with one row per case listing the case_slug, base case_id,
transcript counts by proceeding type (T, PT, A, etc.), and all unique
speaker names found across that case's transcripts.

Usage:
    python extract_speakers.py
    python extract_speakers.py --parsed-dir ../data/parsed --out ../data/reports/speakers_by_case.csv
"""

import argparse
import collections
import csv
import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
PARSED_DIR = PROJECT_ROOT / "data" / "parsed"
OUT_CSV = PROJECT_ROOT / "data" / "reports" / "speakers_by_case.csv"

# IT-95-14/1-PT -> base "IT-95-14/1", suffix "PT"
# IT-95-14     -> base "IT-95-14",   suffix None
_CASE_ID_RE = re.compile(r"^(IT-\d{2}-\d+(?:/\d+)?(?:/\d+)?)(?:-(.+))?$")

# Known proceeding-type suffixes (order used for CSV columns)
SUFFIX_COLS = ["T", "PT", "A", "I", "S", "R77", "R61", "other"]


def _split_case_id(case_id: str) -> tuple[str, str]:
    """Return (base_id, suffix). Suffix is normalized or 'other'."""
    m = _CASE_ID_RE.match(case_id)
    if not m:
        return case_id, "other"
    base = m.group(1)
    suffix = m.group(2)
    if suffix is None:
        return base, "other"
    # Normalize: map compound suffixes to their primary type
    s = suffix.upper()
    if "R77" in s:
        return base, "R77"
    if "R61" in s:
        return base, "R61"
    if s in ("T", "TBIS"):
        return base, "T"
    if s in ("PT",) or s.endswith("/PT"):
        return base, "PT"
    if s.startswith("A") and "R" not in s:
        return base, "A"
    if s == "I":
        return base, "I"
    if s == "S":
        return base, "S"
    if s == "R":
        return base, "other"
    for known in ("T", "PT", "A", "I", "S"):
        if s == known:
            return base, known
    return base, "other"


def collect_speakers(parsed_dir: Path) -> dict:
    """case_slug -> {"bases": set, "suffix_counts": Counter, "speakers": set}."""
    cases = {}
    n_files = 0
    for jf in sorted(parsed_dir.glob("**/*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        case = data.get("case")
        if not case:
            continue
        n_files += 1
        if case not in cases:
            cases[case] = {
                "bases": set(),
                "suffix_counts": collections.Counter(),
                "speakers": set(),
            }
        case_id = data.get("case_id_transcript") or data.get("case_id")
        if case_id:
            base, suffix = _split_case_id(case_id)
            cases[case]["bases"].add(base)
            cases[case]["suffix_counts"][suffix] += 1
        for u in data.get("utterances", []):
            cases[case]["speakers"].add(u["speaker"])
    return cases, n_files


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parsed-dir", type=Path, default=PARSED_DIR)
    ap.add_argument("--out", type=Path, default=OUT_CSV)
    args = ap.parse_args()

    cases, n_files = collect_speakers(args.parsed_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)

    header = ["case_slug", "case_id"] + [f"n_{s}" for s in SUFFIX_COLS] + ["speakers", "n_speakers"]

    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for case in sorted(cases):
            info = cases[case]
            base_id = "; ".join(sorted(info["bases"])) if info["bases"] else ""
            suffix_counts = [info["suffix_counts"].get(s, 0) for s in SUFFIX_COLS]
            speakers = sorted(info["speakers"])
            w.writerow([case, base_id] + suffix_counts + ["; ".join(speakers), len(speakers)])

    print(f"Read {n_files} transcripts across {len(cases)} cases.")
    print(f"Wrote {len(cases)} rows to {args.out}")


if __name__ == "__main__":
    main()
