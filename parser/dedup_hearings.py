#!/usr/bin/env python3
"""
Detect duplicate hearings across case folders and pick a canonical owner.

UCR files the same hearing under multiple related case numbers (joined and
severed cases), so the per-case scraper downloaded many hearings more than
once — 15.4% of all parsed utterances are duplicate copies. This script
groups parsed transcripts by full-text hash and, for each duplicate group,
decides which case folder "owns" the hearing. Downstream consumers keep only
the canonical copy.

Decision rules, in order:

- ``manual``             — from the overrides CSV (wins over everything).
- ``case_id_transcript`` — the case number printed in the transcript matches
                           exactly one member folder's case number.
- ``pleas_data``         — exactly one member folder has accused/plea data in
                           pleas.csv (prefer it, since downstream analysis
                           joins on pleas).
- ``date_range``         — the hearing date falls within the date range of
                           exactly one member folder's non-duplicated files.
- ``pair_majority``      — the folder that won the most already-decided
                           groups with the same folder combination.
- ``within_folder``      — all copies in one folder: keep the shortest
                           filename (the one without a hash suffix).
- ``first_member``       — no signal; alphabetically first folder, flagged
                           ``needs_review``.

Output: one CSV row per file that is part of a duplicate group. Files not in
the CSV are unique. Filter downstream with ``is_canonical == "yes"`` (or:
drop rows whose (case, source_file) appears with ``is_canonical == "no"``).

Files with zero utterances (fully closed sessions) are ignored — their empty
text would hash-collide without being meaningful duplicates.

Manual overrides: a CSV with columns ``case, source_file, canonical_case``;
the named file's whole group is assigned to ``canonical_case``.

Usage:
    python parser/dedup_hearings.py
"""

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
PARSED_DIR = PROJECT_ROOT / "data_250625" / "transcripts_parsed"
PLEAS_CSV = PROJECT_ROOT / "data_250625" / "reports" / "pleas.csv"
OUT_CSV = PROJECT_ROOT / "data_250625" / "reports" / "hearing_dedup.csv"
OVERRIDES_CSV = PROJECT_ROOT / "data_250625" / "reports" / "hearing_dedup_overrides.csv"

FIELDNAMES = [
    "group_id", "date", "n_utterances", "case", "source_file",
    "case_id_base", "case_id_transcript",
    "canonical_case", "is_canonical", "decision", "needs_review",
]


def load_overrides(path: Path) -> dict[tuple[str, str], str]:
    overrides = {}
    if not path.exists():
        return overrides
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row["case"].strip(), row["source_file"].strip())
            overrides[key] = row["canonical_case"].strip()
    return overrides


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--parsed-dir", type=Path, default=PARSED_DIR)
    ap.add_argument("--pleas", type=Path, default=PLEAS_CSV)
    ap.add_argument("--out", type=Path, default=OUT_CSV)
    ap.add_argument("--overrides", type=Path, default=OVERRIDES_CSV)
    args = ap.parse_args()

    with open(args.pleas, encoding="utf-8") as f:
        pleas_cases = {row["case"] for row in csv.DictReader(f)}
    overrides = load_overrides(args.overrides)

    # ---- scan corpus: hash every non-empty file ----
    groups = defaultdict(list)   # (date, hash) -> [member dict]
    dates_by_case = defaultdict(list)   # all file dates per folder
    for case_dir in sorted(d for d in args.parsed_dir.iterdir() if d.is_dir()):
        for jf in sorted(case_dir.glob("*.json")):
            doc = json.loads(jf.read_text(encoding="utf-8"))
            us = doc.get("utterances", [])
            if not us:
                continue
            h = hashlib.md5("\x00".join(u["text"] for u in us).encode()).hexdigest()
            member = {
                "case": case_dir.name,
                "source_file": jf.name,
                "date": doc.get("date") or "",
                "n_utterances": len(us),
                "case_id_base": doc.get("case_id_base") or "",
                "case_id_transcript": doc.get("case_id_transcript") or "",
            }
            groups[(member["date"], h)].append(member)
            dates_by_case[case_dir.name].append(member["date"])

    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}

    # date range of each folder's NON-duplicated files (for the date_range rule)
    dup_dates_by_case = defaultdict(set)
    for members in dup_groups.values():
        for m in members:
            dup_dates_by_case[m["case"]].add(m["date"])
    unique_range = {}
    for case, dates in dates_by_case.items():
        uniq = [d for d in dates if d and d not in dup_dates_by_case[case]]
        if uniq:
            unique_range[case] = (min(uniq), max(uniq))

    # ---- decide canonical owner per group ----
    def decide(members) -> tuple[str, str]:
        """Returns (canonical_case, decision) or ("", "") if undecided."""
        for m in members:
            if (m["case"], m["source_file"]) in overrides:
                return overrides[(m["case"], m["source_file"])], "manual"

        cases = sorted({m["case"] for m in members})
        if len(cases) == 1:
            keep = min(members, key=lambda m: len(m["source_file"]))
            return keep["case"], "within_folder"

        tid = next((m["case_id_transcript"] for m in members
                    if m["case_id_transcript"]), "")
        if tid:
            owners = sorted({m["case"] for m in members
                             if m["case_id_base"] == tid})
            if len(owners) == 1:
                return owners[0], "case_id_transcript"

        in_pleas = sorted(c for c in cases if c in pleas_cases)
        if len(in_pleas) == 1:
            return in_pleas[0], "pleas_data"

        date = members[0]["date"]
        if date:
            in_range = sorted(c for c in cases if c in unique_range
                              and unique_range[c][0] <= date <= unique_range[c][1])
            if len(in_range) == 1:
                return in_range[0], "date_range"

        return "", ""

    decided = {}   # group key -> (canonical, decision)
    undecided = []
    for key, members in dup_groups.items():
        canonical, decision = decide(members)
        if canonical:
            decided[key] = (canonical, decision)
        else:
            undecided.append(key)

    # pair majority: use decided groups of the same folder combination
    majority = defaultdict(Counter)
    for key, (canonical, _) in decided.items():
        cases = tuple(sorted({m["case"] for m in dup_groups[key]}))
        majority[cases][canonical] += 1
    for key in undecided:
        cases = tuple(sorted({m["case"] for m in dup_groups[key]}))
        counts = majority[cases]
        if counts:
            decided[key] = (counts.most_common(1)[0][0], "pair_majority")
        else:
            decided[key] = (cases[0], "first_member")

    # ---- write report ----
    rows = []
    for key, members in sorted(dup_groups.items(),
                               key=lambda kv: (kv[1][0]["case"], kv[0][0])):
        canonical, decision = decided[key]
        gid = hashlib.md5(repr(key).encode()).hexdigest()[:10]
        for m in sorted(members, key=lambda m: (m["case"], m["source_file"])):
            rows.append({
                "group_id": gid,
                **{k: m[k] for k in ("date", "n_utterances", "case",
                                     "source_file", "case_id_base",
                                     "case_id_transcript")},
                "canonical_case": canonical,
                "is_canonical": "yes" if m["case"] == canonical else "no",
                "decision": decision,
                "needs_review": "yes" if decision == "first_member" else "no",
            })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    # ---- summary ----
    n_excess = sum(int(r["n_utterances"]) for r in rows if r["is_canonical"] == "no")
    by_decision = Counter(r["decision"] for r in rows if r["is_canonical"] == "yes")
    print(f"Wrote {len(rows)} rows ({len(dup_groups)} duplicate groups) to {args.out}")
    print(f"Excess utterances marked non-canonical: {n_excess:,}\n")
    print(f"{'decision':20s} {'groups':>7s}")
    for d, n in by_decision.most_common():
        print(f"{d:20s} {n:7d}")
    review = sum(1 for r in rows if r["needs_review"] == "yes" and r["is_canonical"] == "yes")
    if review:
        print(f"\n{review} groups decided by first_member fallback — review these.")


if __name__ == "__main__":
    main()
