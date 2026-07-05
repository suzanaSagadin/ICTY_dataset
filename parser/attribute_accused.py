#!/usr/bin/env python3
"""
Attribute bare THE ACCUSED / THE APPELLANT utterances to a specific accused.

For every parsed transcript JSON that contains utterances by an accused,
determine *which* accused the hearing concerns, so that bare speaker labels
(``THE ACCUSED`` / ``THE APPELLANT`` without a name) can be resolved to a
person. Named labels (``THE ACCUSED PRALJAK``, ``MR. MILOSEVIC``) never need
this; the attribution matters only for multi-accused cases.

Attribution methods, in order:

- ``single_accused``  — the case has one accused; bare labels are trivially his.
- ``case_call``       — the registrar's case call names exactly one accused
                        ("... the Prosecutor versus Radovan Stankovic").
                        Highest confidence.
- ``header_scan``     — no case call found, but exactly one accused surname
                        appears in the first utterances of the hearing.
                        Flagged ``needs_review``.
- ``joint_hearing``   — the case call says "et al" / "and others", i.e. all
                        accused are present; bare labels are genuinely
                        ambiguous and are NOT attributed.
- ``header_multi``    — several accused surnames in the header; the dominant
                        surname by whole-file mention count is suggested but
                        flagged ``needs_review``.
- ``mention_count``   — no case call found; the dominant surname by
                        whole-file mention count is suggested but flagged
                        ``needs_review``.
- ``manual``          — taken from the overrides CSV (wins over everything).
- ``unresolved``      — no signal.

Output: one CSV row per transcript file that contains at least one
``is_accused`` utterance, plus summary statistics on stdout. When a file is
attributed to an accused with a pleas.csv entry, the row also carries
``plea_initial`` (the pleas.csv ``plea`` column), ``plea_final`` (derived from
the ``note`` column, e.g. "later changed plea to guilty" -> guilty) and
``plea_note``. Plea data is joined at run time, so re-running this script
after editing pleas.csv refreshes the attribution CSV.

Manual overrides: a CSV with columns ``case, source_file, attributed_accused,
note`` (matched on case + source_file). Overrides survive re-runs; edit the
overrides file, never the generated one.

Usage:
    python parser/attribute_accused.py
    python parser/attribute_accused.py --case jankovic_g_et_al
"""

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from icty_parser import load_accused_names, _normalize_accused_name

PROJECT_ROOT = Path(__file__).parent.parent
PARSED_DIR = PROJECT_ROOT / "data_250625" / "transcripts_parsed"
PLEAS_CSV = PROJECT_ROOT / "data_250625" / "reports" / "pleas.csv"
CASE_MAPPING_CSV = PROJECT_ROOT / "scraper" / "case_mapping.csv"
OUT_CSV = PROJECT_ROOT / "data_250625" / "reports" / "accused_attribution.csv"
OVERRIDES_CSV = PROJECT_ROOT / "data_250625" / "reports" / "accused_attribution_overrides.csv"

# Utterances scanned for the registrar's case call at the top of a hearing
HEADER_UTTERANCES = 15
# Joint-trial markers in the case call ("the Prosecutor versus Popovic et al")
JOINT_RE = re.compile(r"\bET\s+AL\b|\bAND\s+OTHERS\b")
# The registrar's case call ("This is case number IT-96-23/2-I, the Prosecutor
# of the Tribunal against Radovan Stankovic"). References to *other* cases
# elsewhere in the header ("as in Kunarac et al") must not affect attribution,
# so the case call, when present, wins.
CASE_CALL_RE = re.compile(r"\bPROSECUTOR\b.*\b(?:VERSUS|AGAINST|V)\b")

FIELDNAMES = [
    "case", "source_file", "case_id_base", "date", "n_accused_in_case",
    "n_bare", "n_named", "named_accused_speakers",
    "attributed_accused", "plea_initial", "plea_final", "plea_note",
    "method", "evidence", "needs_review",
]

# note strings like "later changed plea to guilty" -> final plea "guilty"
PLEA_CHANGE_RE = re.compile(r"changed\s+plea\s+to\s+(\w+(?:\s+\w+)?)", re.IGNORECASE)


def load_pleas(pleas_path: Path) -> dict[tuple[str, str], dict]:
    """Load per-accused plea info keyed by (case, normalized full name).

    ``plea_initial`` is the pleas.csv ``plea`` column (initial plea by
    convention); ``plea_final`` is derived from the ``note`` column and equals
    the initial plea when no change is recorded.
    """
    pleas = {}
    with open(pleas_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            accused = row.get("accused", "")
            if not accused:
                continue
            key = (row["case"], _normalize_accused_name(accused))
            note = (row.get("note") or "").strip()
            m = PLEA_CHANGE_RE.search(note)
            pleas[key] = {
                "plea_initial": row.get("plea", ""),
                "plea_final": m.group(1).lower() if m else row.get("plea", ""),
                "plea_note": note,
            }
    return pleas


def is_bare_label(speaker: str) -> bool:
    s = _normalize_accused_name(speaker).strip()
    return s in ("THE ACCUSED", "THE APPELLANT")


def load_overrides(path: Path) -> dict[tuple[str, str], str]:
    overrides = {}
    if not path.exists():
        return overrides
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row["case"].strip(), row["source_file"].strip())
            overrides[key] = row["attributed_accused"].strip()
    return overrides


def surname_pattern(last_names: set[str]) -> re.Pattern:
    names = sorted(last_names, key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b")


def attribute_file(doc: dict, accused_info: dict | None,
                   last_to_full: dict[str, str]) -> dict:
    """Return attribution fields for one parsed transcript."""
    utterances = doc.get("utterances", [])

    n_bare = 0
    named_speakers = set()
    for u in utterances:
        if not u.get("is_accused"):
            continue
        if is_bare_label(u.get("speaker", "")):
            n_bare += 1
        else:
            named_speakers.add(u.get("speaker", ""))
    n_named = sum(
        1 for u in utterances
        if u.get("is_accused") and not is_bare_label(u.get("speaker", ""))
    )

    result = {
        "n_bare": n_bare,
        "n_named": n_named,
        "named_accused_speakers": "; ".join(sorted(named_speakers)),
        "attributed_accused": "",
        "method": "unresolved",
        "evidence": "",
        "needs_review": "no",
    }

    if accused_info is None:
        result["method"] = "no_accused_data"
        result["needs_review"] = "yes" if n_bare else "no"
        return result

    last_names = accused_info["last_names"]

    if len(accused_info["full_names"]) == 1:
        result["attributed_accused"] = next(iter(accused_info["full_names"]))
        result["method"] = "single_accused"
        return result

    pat = surname_pattern(last_names)
    # "POPOVIC ET AL" — a joint marker only when tied to one of THIS case's
    # accused, so "as in the Kunarac et al. case" does not trigger it
    joint_pat = re.compile(pat.pattern + r"\.?,?\s*(?:ET\s+AL|AND\s+OTHERS)\b")

    # Header scan: opening utterances, giving priority to the case call
    header_names = set()
    call_names = set()
    call_joint = False
    header_joint = False
    evidence = ""
    for u in utterances[:HEADER_UTTERANCES]:
        text = _normalize_accused_name(u.get("text", ""))
        hits = set(pat.findall(text))
        if hits:
            header_names |= hits
            if not evidence:
                evidence = u.get("text", "")[:120]
        if joint_pat.search(text):
            header_joint = True
        if CASE_CALL_RE.search(text):
            call_names |= hits
            if JOINT_RE.search(text):
                call_joint = True
            if hits or JOINT_RE.search(text):
                evidence = u.get("text", "")[:120]

    if call_joint or (not call_names and header_joint):
        result["method"] = "joint_hearing"
        result["evidence"] = evidence
        result["needs_review"] = "yes" if n_bare else "no"
        return result

    if len(call_names) == 1:
        last = next(iter(call_names))
        result["attributed_accused"] = last_to_full.get(last, last)
        result["method"] = "case_call"
        result["evidence"] = evidence
        return result

    if len(call_names) >= 2:
        # several accused named in the case call itself: a joint hearing
        result["method"] = "joint_hearing"
        result["evidence"] = evidence
        result["needs_review"] = "yes" if n_bare else "no"
        return result

    if len(header_names) == 1:
        # weaker than a case call: in a joint trial, one surname in the
        # opening chatter does not mean the hearing concerns only that accused
        last = next(iter(header_names))
        result["attributed_accused"] = last_to_full.get(last, last)
        result["method"] = "header_scan"
        result["evidence"] = evidence
        result["needs_review"] = "yes" if n_bare else "no"
        return result

    # No unique case call: fall back to whole-file surname mention counts
    counts = Counter()
    for u in utterances:
        counts.update(pat.findall(_normalize_accused_name(u.get("text", ""))))
    top = counts.most_common(2)
    if top and top[0][1] >= 5 and (len(top) == 1 or top[0][1] >= 3 * top[1][1]):
        last = top[0][0]
        result["attributed_accused"] = last_to_full.get(last, last)
        result["method"] = "header_multi" if header_names else "mention_count"
        result["evidence"] = f"mentions: {dict(counts.most_common(4))}"
        result["needs_review"] = "yes" if n_bare else "no"
    else:
        result["method"] = "unresolved"
        result["evidence"] = f"mentions: {dict(counts.most_common(4))}" if counts else ""
        result["needs_review"] = "yes" if n_bare else "no"
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--parsed-dir", type=Path, default=PARSED_DIR)
    ap.add_argument("--pleas", type=Path, default=PLEAS_CSV)
    ap.add_argument("--mapping", type=Path, default=CASE_MAPPING_CSV)
    ap.add_argument("--out", type=Path, default=OUT_CSV)
    ap.add_argument("--overrides", type=Path, default=OVERRIDES_CSV)
    ap.add_argument("--case", help="restrict to one case folder")
    args = ap.parse_args()

    accused_names = load_accused_names(args.pleas, args.mapping)
    pleas = load_pleas(args.pleas)
    overrides = load_overrides(args.overrides)

    rows = []
    case_dirs = sorted(d for d in args.parsed_dir.iterdir() if d.is_dir())
    if args.case:
        case_dirs = [d for d in case_dirs if d.name == args.case]

    for case_dir in case_dirs:
        case = case_dir.name
        accused_info = accused_names.get(case)
        last_to_full = {}
        if accused_info:
            for full in accused_info["full_names"]:
                last_to_full[full.split()[-1]] = full

        for jf in sorted(case_dir.glob("*.json")):
            doc = json.loads(jf.read_text(encoding="utf-8"))
            if not any(u.get("is_accused") for u in doc.get("utterances", [])):
                continue
            res = attribute_file(doc, accused_info, last_to_full)

            key = (case, jf.name)
            if key in overrides:
                res["attributed_accused"] = overrides[key]
                res["method"] = "manual"
                res["needs_review"] = "no"

            plea_key = (case, _normalize_accused_name(res["attributed_accused"]))
            res.update(pleas.get(
                plea_key,
                {"plea_initial": "", "plea_final": "", "plea_note": ""},
            ))

            rows.append({
                "case": case,
                "source_file": jf.name,
                "case_id_base": doc.get("case_id_base") or "",
                "date": doc.get("date") or "",
                "n_accused_in_case": len(accused_info["full_names"]) if accused_info else 0,
                **res,
            })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    # ---- summary ----
    by_method = Counter(r["method"] for r in rows)
    bare_by_method = Counter()
    for r in rows:
        bare_by_method[r["method"]] += r["n_bare"]
    total_bare = sum(r["n_bare"] for r in rows)

    print(f"Wrote {len(rows)} rows to {args.out}\n")
    print(f"{'method':16s} {'files':>7s} {'bare utterances':>16s}")
    for m, n in by_method.most_common():
        print(f"{m:16s} {n:7d} {bare_by_method[m]:16d}")
    print(f"{'TOTAL':16s} {len(rows):7d} {total_bare:16d}")

    review = [r for r in rows if r["needs_review"] == "yes"]
    if review:
        print(f"\n{len(review)} files flagged needs_review "
              f"({sum(r['n_bare'] for r in review)} bare utterances):")
        by_case = defaultdict(list)
        for r in review:
            by_case[r["case"]].append(r)
        for case, rs in sorted(by_case.items()):
            print(f"  {case}: {len(rs)} files, "
                  f"{sum(r['n_bare'] for r in rs)} bare utterances")


if __name__ == "__main__":
    main()
