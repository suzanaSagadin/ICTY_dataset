"""Compare old (manually checked) pleas.csv with new (auto-parsed) pleas.csv.

Maps old icty.org slugs to new folder names via case_mapping.csv, then diffs
the plea data row by row.

Usage:
    python parser/compare_pleas.py \
        --old archive/cis_parsed/pleas.csv \
        --new data_250625/reports/pleas.csv \
        --mapping scraper/case_mapping.csv \
        --output data_250625/reports/pleas_comparison.csv
"""

import argparse
import csv
import unicodedata
from pathlib import Path


def normalize_name(name: str) -> str:
    return " ".join(name.upper().split())


def load_pleas(path: Path) -> dict[str, list[dict]]:
    """Load pleas.csv, return {case: [rows]} with deduplicated rows."""
    cases = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = r["case"]
            row = {
                "accused": normalize_name(r["accused"]),
                "plea": r["plea"].strip().lower(),
                "note": r.get("note", "").strip(),
            }
            if key not in cases:
                cases[key] = []
            if row not in cases[key]:
                cases[key].append(row)
    return cases


def build_slug_to_folder(mapping_path: Path) -> dict[str, str]:
    """Build old_slug -> new_folder mapping from case_mapping.csv."""
    result = {}
    with open(mapping_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["icty_slug"]:
                for slug in r["icty_slug"].split("; "):
                    result[slug] = r["folder"]
    return result


def main():
    ap = argparse.ArgumentParser(description="Compare old and new pleas.csv")
    ap.add_argument("--old", type=Path, required=True, help="Old (manually checked) pleas.csv")
    ap.add_argument("--new", type=Path, required=True, help="New (auto-parsed) pleas.csv")
    ap.add_argument("--mapping", type=Path, required=True, help="case_mapping.csv")
    ap.add_argument("--output", type=Path, default=None, help="Output comparison CSV")
    args = ap.parse_args()

    slug_to_folder = build_slug_to_folder(args.mapping)
    old_pleas = load_pleas(args.old)
    new_pleas = load_pleas(args.new)

    rows = []

    for old_slug, old_rows in sorted(old_pleas.items()):
        new_folder = slug_to_folder.get(old_slug, old_slug)

        if new_folder not in new_pleas:
            for r in old_rows:
                rows.append({
                    "case_old": old_slug,
                    "case_new": new_folder,
                    "accused": r["accused"],
                    "old_plea": r["plea"],
                    "new_plea": "",
                    "old_note": r["note"],
                    "new_note": "",
                    "status": "MISSING_IN_NEW",
                })
            continue

        new_rows = new_pleas[new_folder]
        matched_new = set()

        for old_r in old_rows:
            found = False
            for i, new_r in enumerate(new_rows):
                if i in matched_new:
                    continue
                if normalize_name(old_r["accused"]) == normalize_name(new_r["accused"]):
                    matched_new.add(i)
                    found = True
                    if old_r["plea"] == new_r["plea"] and old_r["note"] == new_r["note"]:
                        status = "MATCH"
                    elif old_r["plea"] != new_r["plea"]:
                        status = "PLEA_CHANGED"
                    else:
                        status = "NOTE_CHANGED"
                    rows.append({
                        "case_old": old_slug,
                        "case_new": new_folder,
                        "accused": old_r["accused"],
                        "old_plea": old_r["plea"],
                        "new_plea": new_r["plea"],
                        "old_note": old_r["note"],
                        "new_note": new_r["note"],
                        "status": status,
                    })
                    break
            if not found:
                rows.append({
                    "case_old": old_slug,
                    "case_new": new_folder,
                    "accused": old_r["accused"],
                    "old_plea": old_r["plea"],
                    "new_plea": "",
                    "old_note": old_r["note"],
                    "new_note": "",
                    "status": "NAME_NOT_MATCHED",
                })

        for i, new_r in enumerate(new_rows):
            if i not in matched_new:
                rows.append({
                    "case_old": old_slug,
                    "case_new": new_folder,
                    "accused": new_r["accused"],
                    "old_plea": "",
                    "new_plea": new_r["plea"],
                    "old_note": "",
                    "new_note": new_r["note"],
                    "status": "NEW_ACCUSED",
                })

    # Cases in new but not in old at all
    old_folders = set(slug_to_folder.get(s, s) for s in old_pleas)
    for new_folder, new_rows in sorted(new_pleas.items()):
        if new_folder not in old_folders:
            for r in new_rows:
                rows.append({
                    "case_old": "",
                    "case_new": new_folder,
                    "accused": r["accused"],
                    "old_plea": "",
                    "new_plea": r["plea"],
                    "old_note": "",
                    "new_note": r["note"],
                    "status": "NEW_CASE",
                })

    # Summary
    from collections import Counter
    counts = Counter(r["status"] for r in rows)
    print(f"\nPleas comparison: {len(rows)} total rows")
    for status, count in sorted(counts.items()):
        print(f"  {status:20s} {count}")

    need_review = sum(c for s, c in counts.items() if s != "MATCH")
    print(f"\n  Need manual review: {need_review}")
    print(f"  Already verified:   {counts.get('MATCH', 0)}")

    out = args.output or args.new.parent / "pleas_comparison.csv"
    fields = ["status", "case_old", "case_new", "accused", "old_plea", "new_plea", "old_note", "new_note"]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["status"] != "MATCH", r["status"], r["case_new"])))
    print(f"\n  Written to: {out}")


if __name__ == "__main__":
    main()
