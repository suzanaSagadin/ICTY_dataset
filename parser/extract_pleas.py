"""Build data/cis_parsed/pleas.csv: one row per accused (not per case).

Reads data/cis_parsed/all_cases_full.json. For single-defendant cases the
case-level plea is used as-is. For multi-defendant cases the accused are taken
from the "The Prosecutor v. ..." line of the CIS, the full text is split at
each name's occurrences, and find_plea_note() runs on each accused's own sections.

OCR note: the roster line often renders "ć" as "d" (e.g. "Popovid"), while the
uppercase section headers are spelled correctly, so names are matched with a
character-class regex that tolerates both.
"""

import csv
import json
import logging
import re
from pathlib import Path

from cis_parser import find_plea_note

logger = logging.getLogger(__name__)

CIS_DIR = Path(__file__).resolve().parent.parent / "data" / "cis_parsed"

ROSTER_RE = re.compile(r"The\s+Prosecutor\s+v\.?\s+")

# Characters the PDF extraction confuses with each other (ć -> d, č -> ĉ, đ -> ð, ...)
_C, _S, _Z, _D = "[CĆČĈD]", "[SŠŜ]", "[ZŽẐ]", "[DĐÐð]"
FUZZY = {"C": _C, "Ć": _C, "Č": _C, "Ĉ": _C, "D": "[DĆĐÐð]", "Đ": _D, "Ð": _D,
         "S": _S, "Š": _S, "Z": _Z, "Ž": _Z}

# All-caps lines that are section titles, not names of accused
NOT_A_NAME = {"CASE", "INFORMATION", "SHEET", "NOT", "AN", "OFFICIAL", "DOCUMENT",
              "THE", "TRIAL", "INDICTMENT", "RELATED", "CASES", "PLEA", "SENTENCE",
              "JUDGEMENT", "APPEALS", "STATISTICS", "REFERRAL", "PROCEEDINGS",
              "REQUEST", "FOR", "REVIEW"}
_CAPS_WORD = r"[A-ZĆČŠŽĐĈŜẐÐð]{2,}"
CAPS_NAME_RE = re.compile(rf"^({_CAPS_WORD}(?:[ \t]+{_CAPS_WORD}){{1,3}})[ \t]*$", re.M)
# Layouts like "DRAGOLJUB KUNARAC Convicted of torture ..." (name + verdict inline)
INLINE_NAME_RE = re.compile(
    rf"^({_CAPS_WORD}(?:[ \t]+{_CAPS_WORD}){{1,3}})[ \t]+"
    r"(?=Convicted|Found|Acquitted|Charges|Indicted|Sentenced)", re.M)
# Narrative sections: text after these headers (until the next accused's name)
# may discuss other people's pleas, so it must not count for any accused
SECTION_RE = re.compile(
    r"^(?:INDICTMENT|THE\s+TRIAL|RELATED\s+CASES|STATISTICS|TRIAL\s+JUDGEMENT"
    r"|APPEALS|SENTENCING|REFERRAL|REQUEST\s+FOR\s+REVIEW)[A-Z ]*$", re.M)


def parse_roster(text: str) -> tuple[list[str], int]:
    """Return (accused names, end position of the roster line).

    The name list may wrap over several lines (sometimes mid-name); it always
    ends with "& Last Accused", so lines are consumed until one contains "&".
    """
    m = ROSTER_RE.search(text)
    if not m:
        return [], 0
    lines = text[m.end():m.end() + 400].split("\n")
    taken, last_name_follows = [], False
    for line in lines[:4]:
        taken.append(line)
        if last_name_follows:
            break
        if line.rstrip().endswith("&"):
            last_name_follows = True
            continue
        # "and" only joins names on the roster's first line; on later lines it
        # is usually prose ("arrest and detention ...")
        if "&" in line or (len(taken) == 1 and re.search(r"\band\b", line)):
            break
    else:
        taken = lines[:1]  # no conjunction -> single accused, first line only
    blob = " ".join(" ".join(taken).split())
    names = [n.strip() for n in re.split(r",|&|\band\b", blob) if n.strip()]
    end = m.end() + sum(len(l) + 1 for l in taken)
    return names, end


def caps_header_roster(text: str) -> list[str]:
    """Fallback for CIS layouts without a "Prosecutor v." line: the accused
    appear as all-caps header lines, alone or followed by the verdict.
    Returns deduplicated names."""
    for pattern in (CAPS_NAME_RE, INLINE_NAME_RE):
        names, seen = [], set()
        for m in pattern.finditer(text):
            words = m.group(1).split()
            if set(words) & NOT_A_NAME:
                continue
            key = " ".join(words).replace("Ć", "C").replace("Č", "C")
            if key not in seen:
                seen.add(key)
                names.append(" ".join(words).title())
        if len(names) >= 2:
            return names
    return names


def name_pattern(name: str) -> re.Pattern:
    parts = []
    for ch in name.upper():
        if ch == " ":
            parts.append(r"\s+")
        else:
            parts.append(FUZZY.get(ch, re.escape(ch)))
    return re.compile("".join(parts))


def pleas_per_accused(text: str, names: list[str], start: int) -> list[tuple[str, str | None]]:
    """Split text at every occurrence of every accused's name; for each
    accused, run find_plea_note over the concatenation of their own sections."""
    marks = []  # (position, accused index, matched spelling); index -1 = discard
    for i, name in enumerate(names):
        for m in name_pattern(name).finditer(text, start):
            # repeated page headers contain the name too; they would
            # re-attribute whatever narrative follows them to this accused
            line = text[text.rfind("\n", 0, m.start()) + 1:]
            if "INFORMATION SHEET" in line.split("\n", 1)[0]:
                continue
            marks.append((m.start(), i, m.group(0)))
    for m in SECTION_RE.finditer(text, start):
        marks.append((m.start(), -1, ""))
    marks.sort()

    sections: dict[int, list[str]] = {i: [] for i in range(len(names))}
    spelling: dict[int, str] = {}
    for j, (pos, i, matched) in enumerate(marks):
        if i == -1:
            continue
        end = marks[j + 1][0] if j + 1 < len(marks) else len(text)
        sections[i].append(text[pos:end])
        spelling.setdefault(i, " ".join(matched.split()))

    return [(spelling.get(i, names[i].upper()), *find_plea_note("\n".join(sections[i])))
            for i in range(len(names))]


def main() -> None:
    cases = json.loads((CIS_DIR / "all_cases_full.json").read_text())
    rows = []
    for case in cases:
        slug = case["case_slug"]
        names, start = parse_roster(case["full_text"])
        if len(names) <= 1 and ((case.get("defendant_count") or 1) > 1
                                or not case.get("defendant_name")):
            names, start = caps_header_roster(case["full_text"]), 0
        if not names:
            names = [case.get("defendant_name") or ""]
        single = len(names) == 1
        for name, plea, note in pleas_per_accused(case["full_text"], names, start):
            if single:
                # plea from the accused's own sections, contaminated
                # case-level plea only as a last resort
                plea = plea or case.get("plea")
                name = name if name.strip() else (case.get("defendant_name") or "")
            rows.append((slug, name.replace("Ð", "Đ").replace("ð", "Đ"),
                         plea or "unknown", note or ""))
        if not single:
            logger.info(f"{slug}: {len(names)} accused")

    out = CIS_DIR / "pleas.csv"
    with out.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["case", "accused", "plea", "note"])
        w.writerows(rows)
    print(f"{out}: {len(rows)} accused in {len(cases)} cases")


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Extract pleas from parsed CIS data")
    ap.add_argument("--cis-dir", type=Path, default=CIS_DIR,
                    help="Directory with all_cases_full.json (default: data/cis_parsed)")
    args = ap.parse_args()
    CIS_DIR = args.cis_dir
    main()
