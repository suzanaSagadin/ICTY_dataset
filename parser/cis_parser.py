#!/usr/bin/env python3
"""
Case Information Sheet (CIS) PDF parser for ICTY cases.

Extracts structured metadata from CIS PDFs using pdfplumber text extraction.
Outputs one JSON per PDF + a combined summary CSV for statistical analysis.

Usage:
    python parser/cis_parser.py [--archive-dir data/archive] [--output-dir data/cis_parsed]
"""

import argparse
import csv
import json
import logging
import re
import sys
from pathlib import Path

import pdfplumber

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

# Case ID pattern like IT-95-14/1, IT-02-54, MICT-13-55 etc.
RE_CASE_ID = re.compile(r"((?:IT|MICT)-[\d]{2,4}-[\d]+(?:/\d+)?)", re.IGNORECASE)

# Sentence: "X years' imprisonment" or "life imprisonment"
RE_SENTENCE = re.compile(
    r"(?:sentenced?\s+to\s+)(.+?imprisonment)",
    re.IGNORECASE,
)
RE_SENTENCE_YEARS = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s+years?['\u2019]?\s*imprisonment", re.IGNORECASE
)
RE_SENTENCE_LIFE = re.compile(r"life\s+imprisonment", re.IGNORECASE)


def _years(match_group: str) -> int | float:
    """7.5 stays a float, whole years become int."""
    val = float(match_group)
    return int(val) if val.is_integer() else val

# Date patterns (e.g. "8 January 1960", "10 November 1995")
RE_DATE = re.compile(
    r"(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{4})",
    re.IGNORECASE,
)

# Statistics table entries
RE_STAT_LINE = re.compile(
    r"(Trial days|"
    r"(?:Witnesses|Prosecution witnesses|Defence witnesses|Chamber witnesses)"
    r".*?|"
    r"(?:Prosecution|Defence|Chamber[s]?)\s+(?:exhibits|witnesses))"
    r"\s+(\d+)",
    re.IGNORECASE,
)

# Charges categories
CHARGE_KEYWORDS = [
    "genocide",
    "crimes against humanity",
    "grave breaches",
    "violations of the laws or customs of war",
    "war crimes",
]

CRIME_TYPES = [
    "murder",
    "extermination",
    "enslavement",
    "deportation",
    "imprisonment",
    "torture",
    "rape",
    "sexual assault",
    "sexual violence",
    "persecutions",
    "persecution",
    "inhumane acts",
    "inhuman treatment",
    "outrages upon personal dignity",
    "wilful killing",
    "wilfully causing great suffering",
    "cruel treatment",
    "plunder",
    "wanton destruction",
    "unlawful attacks on civilians",
    "terror",
    "taking of hostages",
    "hostage-taking",
    "humiliating and degrading treatment",
    "forcible transfer",
    "unlawful confinement",
    "unlawful detention",
    "destruction of cultural property",
    "superior responsibility",
]


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def extract_full_text(pdf_path: Path) -> str:
    """Extract all text from a PDF, page by page."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
    return "\n\n".join(pages)


def find_case_id(text: str) -> str | None:
    m = RE_CASE_ID.search(text)
    return m.group(1) if m else None


def find_case_name(text: str) -> str | None:
    """Extract the quoted case name like 'LAŠVA VALLEY' or 'BRČKO'.

    These are geographic or operation names, not defendant aliases.
    """
    # Known aliases that appear quoted but are not case cluster names
    ALIAS_NAMES = {"ARKAN", "TUTA", "ŠTELA", "ŠVABO"}

    m = re.search(r'["\u201c]([A-Z\s\u0100-\u017f&]+?)["\u201d]', text[:600])
    if m:
        name = m.group(1).strip()
        if name in ALIAS_NAMES:
            return None
        return name
    return None


def find_defendant_name(text: str) -> str | None:
    """Extract the large defendant name from the first page."""
    lines = text.split("\n")
    # The defendant name is typically in ALL CAPS, large font, in the first ~15 lines
    # after the case ID line, and before the biographical table
    candidates = []
    for line in lines[2:15]:
        line = line.strip()
        # Skip headers, short lines, the case info sheet header
        if not line or len(line) < 3:
            continue
        if "CASE INFORMATION SHEET" in line.upper():
            continue
        if "NOT AN OFFICIAL DOCUMENT" in line.upper():
            continue
        if RE_CASE_ID.search(line) and len(line) < 40:
            continue
        # All-caps name lines (allowing diacritics)
        if re.match(r'^[A-Z\u0100-\u017F\s\-]+$', line) and len(line) > 3:
            candidates.append(line)
    if candidates:
        # Deduplicate: sometimes the name appears twice (header + body)
        name = " ".join(candidates).strip()
        words = name.split()
        # Check if the name is just repeated exactly (e.g. "ZLATKO ALEKSOVSKI ZLATKO ALEKSOVSKI")
        half = len(words) // 2
        if half > 0 and words[:half] == words[half:]:
            name = " ".join(words[:half])
        else:
            # Check for partial trailing duplicate (e.g. "MILAN KOVAČEVIĆ KOVAČEVIĆ"
            # or "MOMIR NIKOLIĆ MOMIR") - remove repeated surname/firstname at end
            for trim in range(1, min(3, len(words))):
                suffix = words[-trim:]
                if suffix == words[:trim] or suffix == words[len(words)-2*trim:len(words)-trim]:
                    name = " ".join(words[:len(words)-trim])
                    break
        # Also filter out header text that leaked into name
        name = re.sub(r'\s*S E I N F O R M A T I O N S H E E T\s*', ' ', name).strip()
        return name
    return None


def find_sentence(text: str) -> dict:
    """Extract sentence information.

    The first "sentenced to ..." match is the header line with the primary
    defendant's final sentence. Life/years are derived from that sentence text
    only — "life imprisonment" elsewhere in the sheet (related cases,
    prosecution appeal requests, overturned trial sentences) must not set the
    flag.
    """
    result = {"sentence_text": None, "sentence_years": None, "life_sentence": False}

    m = RE_SENTENCE.search(text)
    if m:
        result["sentence_text"] = m.group(1).strip()

    # Also check the "Sentence" table field (NEW format)
    m_field = re.search(r"Sentence\s+(.+?)(?:\n|$)", text, re.IGNORECASE)
    if m_field and not result["sentence_text"]:
        result["sentence_text"] = m_field.group(1).strip()[:100]

    sentence_text = result["sentence_text"] or ""
    if RE_SENTENCE_LIFE.search(sentence_text):
        result["life_sentence"] = True
        result["sentence_years"] = None
    else:
        # Look for years in the final sentence
        m = RE_SENTENCE_YEARS.search(sentence_text)
        if m:
            result["sentence_years"] = _years(m.group(1))
        else:
            # Try to find "two and a half years" etc.
            m2 = re.search(
                r"sentenced?\s+to\s+([\w\s]+?)\s+years?",
                text,
                re.IGNORECASE,
            )
            if m2:
                word_to_num = {
                    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                    "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
                    "thirty": 30, "forty": 40, "fifty": 50,
                }
                num_text = m2.group(1).strip().lower()
                for word, val in word_to_num.items():
                    if word in num_text:
                        result["sentence_years"] = val
                        break

    return result


def find_key_dates(text: str) -> dict:
    """Extract key dates from the biographical/timeline table."""
    dates = {}
    # Key fields typically found in the first-page table
    patterns = {
        "born": r"Born\s+(.+?)(?:\n|$)",
        "indictment_date": r"Indictment\s+(.+?)(?:\n|$)",
        "arrested_date": r"Arrested\s+(.+?)(?:\n|$)",
        "transferred_date": r"Transferred\s+to\s+ICTY\s+(.+?)(?:\n|$)",
        "initial_appearance": r"Initial\s+appearance\s+(.+?)(?:\n|$)",
        "trial_commenced": r"Commenced?\s+(.+?)(?:\n|$)",
        "trial_judgement": r"(?:Trial\s+Chamber\s+(?:sentencing\s+)?[Jj]udgement)\s+(.+?)(?:\n|$)",
        "appeals_judgement": r"(?:Appeals\s+Chamber\s+[Jj]udgement)\s+(.+?)(?:\n|$)",
        "sentence_field": r"Sentence\s+(.+?)(?:\n|$)",
        "guilty_plea": r"Guilty\s+plea\s+(.+?)(?:\n|$)",
        "closing_arguments": r"Closing\s+arguments\s+(.+?)(?:\n|$)",
        "commencement": r"Commencement\s+of\s+Trial\s+(.+?)(?:\n|$)",
    }

    for key, pat in patterns.items():
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            raw = m.group(1).strip()
            # Extract first recognizable date from the value
            dm = RE_DATE.search(raw)
            if dm:
                dates[key] = dm.group(1)
            elif len(raw) < 100 and not raw.startswith("The "):
                # Only keep short values that look like table entries, not narrative
                dates[key] = raw[:80]

    return dates


def find_statistics(text: str) -> dict:
    """Extract trial statistics (trial days, witnesses, exhibits)."""
    stats = {}
    normalize = {
        "trial days": "trial_days",
        "witnesses called by prosecution": "prosecution_witnesses",
        "prosecution witnesses": "prosecution_witnesses",
        "witnesses called by defence": "defence_witnesses",
        "defence witnesses": "defence_witnesses",
        "witnesses called by chambers": "chamber_witnesses",
        "chamber witnesses": "chamber_witnesses",
        "prosecution exhibits": "prosecution_exhibits",
        "defence exhibits": "defence_exhibits",
        "chambers exhibits": "chamber_exhibits",
        "chamber exhibits": "chamber_exhibits",
    }

    for m in RE_STAT_LINE.finditer(text):
        label = m.group(1).strip().lower()
        value = int(m.group(2))
        key = normalize.get(label)
        if key:
            stats[key] = value

    return stats


def find_charges(text: str) -> dict:
    """Extract charge categories and specific crime types."""
    text_lower = text.lower()
    result = {
        "charge_categories": [],
        "crime_types": [],
        "convicted_of_text": None,
    }

    # Extract the "Convicted of:" block
    m = re.search(
        r"Convicted\s+of:\s*\n(.+?)(?:\n\s*Born|\n\s*Indictment|\n\s*\w+\s+\d)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if m:
        result["convicted_of_text"] = m.group(1).strip()

    # Check for charge categories
    for kw in CHARGE_KEYWORDS:
        if kw in text_lower:
            result["charge_categories"].append(kw)

    # Check for specific crime types
    for crime in CRIME_TYPES:
        if crime in text_lower:
            result["crime_types"].append(crime)

    # Deduplicate
    result["charge_categories"] = sorted(set(result["charge_categories"]))
    result["crime_types"] = sorted(set(result["crime_types"]))

    return result


def find_related_cases(text: str) -> list[str]:
    """Extract related case names from the RELATED CASES section."""
    m = re.search(
        r"RELATED\s+CASES\s*\n(.+?)(?:\nINDICTMENT|\nTHE\s+TRIAL|\nPLEA|\n\s*$)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return []

    block = m.group(1)
    cases = []
    for line in block.strip().split("\n"):
        line = line.strip()
        if line and len(line) > 3:
            cases.append(line)
    return cases


def find_plea(text: str) -> str | None:
    """Determine plea type: guilty or not guilty. See find_plea_note."""
    return find_plea_note(text)[0]


def find_plea_note(text: str) -> tuple[str | None, str | None]:
    """Determine the initial plea and a note about later changes.

    Returns (plea, note). The plea is the one entered at (or closest to) the
    initial appearance; an accused who first pleaded not guilty and later
    changed to guilty stays "not guilty" with a note recording the change.

    Tolerates quotes/punctuation between "pleaded" and the plea (CIS sheets
    often write pleaded "not guilty" with curly quotes). Also treats refusal
    to plead as "not guilty": under ICTY Rule 62 the Trial Chamber enters a
    not-guilty plea on the accused's behalf when they decline or fail to plead.
    Cases that never reached arraignment (died/withdrawn) match nothing and
    return (None, None).
    """
    text_lower = text.lower()
    # Optional quote/space between the verb and the plea word
    q = r"[\s\"'‘’“”]*"
    has_guilty_plea = bool(
        re.search(rf"pleaded?{q}guilty", text_lower)
        # CIS field label ("Guilty plea 17 January 2007") and prose variants
        or re.search(r"(?<!not\s)guilty\s+plea\s+(?:was\s+)?entered", text_lower)
        or re.search(r"entered\s+a\s+guilty\s+plea", text_lower)
        or re.search(r"(?<!not\s)guilty\s+plea\s+\d", text_lower)
    )
    has_not_guilty = bool(
        re.search(rf"pleaded?{q}not{q}guilty", text_lower)
        or re.search(r"plea\s+of\s+not\s+guilty", text_lower)
    )
    has_plea_agreement = "plea agreement" in text_lower
    # e.g. Kunarac: pleaded guilty to one count, then withdrew it
    withdrew_guilty_plea = bool(re.search(
        r"withdrew\s+(?:his|her|the)\s+guilty\s+plea"
        r"|guilty\s+plea\s+was\s+withdrawn",
        text_lower,
    ))
    # Rule 62: accused refused/failed/declined to plead -> court enters not guilty
    refused_to_plead = bool(re.search(
        r"no\s+plea\s+entered"
        r"|(?:did\s+not|failed\s+to|refused\s+to|declined\s+to)\s+enter\s+a\s+plea",
        text_lower,
    ))

    if withdrew_guilty_plea and has_not_guilty and not has_plea_agreement:
        return "not guilty", "initial guilty plea withdrawn"
    if has_not_guilty and (has_guilty_plea or has_plea_agreement):
        # Changed plea during proceedings (common pattern): the initial
        # not-guilty plea counts, the later change is noted
        return "not guilty", "later changed plea to guilty"
    if has_plea_agreement or has_guilty_plea:
        return "guilty", None
    if has_not_guilty or refused_to_plead:
        return "not guilty", None
    return None, None


def find_outcome(text: str) -> str | None:
    """Determine case outcome.

    For multi-defendant cases or cases with appeals reversals, the *final*
    outcome takes precedence (appeals > trial chamber).
    """
    text_lower = text.lower()

    # Check for withdrawal / transfer (case never completed)
    if "indictment was withdrawn" in text_lower or "indictment withdrew" in text_lower:
        return "withdrawn"
    if "transferred to" in text_lower and "national jurisdiction" in text_lower:
        return "transferred"

    # Deceased *before* trial/judgement means case didn't complete.
    # But if there's a sentence/conviction, deceased is just a post-sentencing note.
    is_deceased = bool(re.search(r"deceased", text_lower[:2000]))
    has_sentence = bool(re.search(r"sentenced|imprisonment|life\s+imprisonment", text_lower))
    has_conviction = bool(re.search(r"found guilty|convicted", text_lower))
    # Only return deceased if there's no sentence/conviction
    if is_deceased and not has_sentence and not has_conviction:
        return "deceased"

    # Appeals verdict overrides trial conviction. Only the ~250 chars after
    # the first "Appeals Chamber Judgement" label are inspected: that is the
    # field line of the (primary) accused; a wider window reaches into
    # co-accused blocks and statistics tables
    appeals_section = ""
    m = re.search(r"appeals?\s+chamber\s+judgement(.*)", text_lower, re.DOTALL)
    if m:
        appeals_section = m.group(1)[:250]

    if appeals_section:
        # A re-sentencing means the conviction stood, however many counts
        # were reversed (Blaškić: "reversed the majority of the ...
        # convictions and sentenced Blaškić to 9 years")
        resentenced = re.search(
            r"sentenced\s+(?:\w+\s+)?to|sentence\s+(?:was\s+)?reduced\s+to|sentence\s+\d",
            appeals_section,
        )
        if resentenced:
            return "convicted"
        if re.search(r"re-?trial\s+ordered", appeals_section):
            # Outcome is whatever the re-trial decided, if the sheet has it
            m_rt = re.search(r"re-?trial\s+judgement[^\n]{0,100}", text_lower)
            if m_rt and "acquit" in m_rt.group(0):
                return "acquitted"
            if m_rt and re.search(r"convict|sentenc|guilty", m_rt.group(0)):
                return "convicted"
            return "retrial ordered"
        if re.search(
            r"acquitted|verdicts?\s+of\s+acquittal|reversed.*convictions?|entered.*acquittal",
            appeals_section,
        ):
            # Check if ALL defendants were acquitted or just some
            if "found not guilty" in text_lower[:1500] or re.search(r"all.*acquit|reversed.*all|acquitted\s+on\s+all", appeals_section):
                return "acquitted"
            # Partial acquittal — still check if anyone was convicted
            if not re.search(r"(?:affirm|upheld|maintain).*convict", appeals_section):
                return "acquitted"

    # Standard checks
    if "acquitted on all" in text_lower or "acquitted of all" in text_lower:
        return "acquitted"
    if "found not guilty" in text_lower[:1500]:
        return "acquitted"
    if re.search(r"found guilty|convicted", text_lower):
        return "convicted"
    if "guilty plea" in text_lower or "pleaded guilty" in text_lower:
        return "convicted"
    return None


def find_arrest_authority(text: str) -> str | None:
    """Who arrested the defendant."""
    m = re.search(
        r"Arrested\s+.+?by\s+([\w\s]+?)(?:authorities|police|forces)",
        text,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip() + " authorities"
    # Surrendered voluntarily
    if re.search(r"surrendered|voluntary", text[:3000], re.IGNORECASE):
        return "voluntary surrender"
    return None


def find_sentence_served_country(text: str) -> str | None:
    """Where the sentence was served."""
    m = re.search(
        r"transferred\s+to\s+(\w+)\s+to\s+serve", text, re.IGNORECASE
    )
    return m.group(1) if m else None


def find_defendants(text: str) -> list[dict]:
    """Extract all defendants from multi-defendant CIS sheets.

    Multi-defendant PDFs have repeated blocks with name, role, and outcome
    per person (e.g. Gotovina, Čermak, Markač).
    """
    defendants = []

    # Pattern: ALL-CAPS NAME (optionally followed by "Found guilty/not guilty" on same line)
    # or NAME on its own line followed by role/outcome on next line
    blocks = re.split(
        r"(?=(?:^|\n)[A-Z\u0100-\u017F]{2,}\s+[A-Z\u0100-\u017F]{2,}(?:\s+[A-Z\u0100-\u017F]+)*"
        r"(?:\s+(?:Found|Convicted)\s|\s*\n"
        r"(?:Found |Sentenced |Commander |President |Member |Minister |Colonel |General |"
        r"Assistant |Chief |Head |Director |From |Deputy |Convicted |Charged )))",
        text[:8000],
    )

    for block in blocks:
        if len(block.strip()) < 20:
            continue

        lines = block.strip().split("\n")
        # First line should be the name in ALL CAPS (possibly with "Found ..." appended)
        name_line = lines[0].strip()
        name_clean = re.sub(r'\s+(?:Found|Convicted)\s+.*$', '', name_line).strip()
        if not re.match(r'^[A-Z\u0100-\u017F\s\-]+$', name_clean) or len(name_clean) < 4:
            continue

        d = {"name": name_clean}

        block_lower = block.lower()
        if "found not guilty" in block_lower or "acquitted" in block_lower:
            d["final_outcome"] = "acquitted"
        elif "found guilty" in block_lower or "convicted" in block_lower:
            d["final_outcome"] = "convicted"

        # Extract sentence per defendant
        m = RE_SENTENCE_YEARS.search(block)
        if m:
            d["sentence_years"] = _years(m.group(1))
        elif RE_SENTENCE_LIFE.search(block):
            d["sentence_years"] = None
            d["life_sentence"] = True

        if len(d) > 1:  # has more than just name
            defendants.append(d)

    return defendants


# ---------------------------------------------------------------------------
# Main parse function
# ---------------------------------------------------------------------------


def parse_cis(pdf_path: Path, case_slug: str) -> dict:
    """Parse a single CIS PDF and return structured data."""
    text = extract_full_text(pdf_path)

    if not text or len(text) < 100:
        log.warning("  Insufficient text extracted from %s", pdf_path.name)
        return {"case_slug": case_slug, "error": "no_text", "pdf": str(pdf_path)}

    case_id = find_case_id(text)
    case_name = find_case_name(text)
    defendant = find_defendant_name(text)
    sentence = find_sentence(text)
    dates = find_key_dates(text)
    stats = find_statistics(text)
    charges = find_charges(text)
    related = find_related_cases(text)
    defendants = find_defendants(text)
    final_outcome = find_outcome(text)

    # Determine trial chamber outcome (before appeals) from the
    # "Trial Chamber Judgement <date>, sentenced to/acquitted ..." field
    # lines (one per accused in joint cases); a bare whole-text "convicted"
    # search also hits related cases or contempt proceedings (e.g. Šešelj,
    # acquitted at trial but convicted of contempt three times)
    text_lower = text.lower()
    judgement_snippets = [
        text_lower[m.end():m.end() + 200]
        for m in re.finditer(r"trial\s+chamber\s+(?:sentencing\s+)?judgement", text_lower)
    ]
    if any(re.search(r"sentenc|convicted|found\s+guilty", s) for s in judgement_snippets):
        trial_outcome = "convicted"
    elif any("acquit" in s for s in judgement_snippets):
        trial_outcome = "acquitted"
    elif re.search(r"found guilty|convicted", text_lower):
        trial_outcome = "convicted"
    elif "guilty plea" in text_lower or "pleaded guilty" in text_lower:
        trial_outcome = "convicted"
    elif "acquitted on all" in text_lower or "acquitted of all" in text_lower:
        trial_outcome = "acquitted"
    else:
        trial_outcome = final_outcome

    # If final outcome is acquittal but trial convicted, the sentence was overturned
    if final_outcome == "acquitted" and trial_outcome == "convicted":
        sentence["sentence_trial_chamber"] = sentence.get("sentence_text")
        sentence["sentence_text"] = None
        sentence["sentence_years"] = None
        sentence["life_sentence"] = False
    elif final_outcome == "acquitted":
        sentence["sentence_text"] = None
        sentence["sentence_years"] = None
        sentence["life_sentence"] = False

    return {
        "case_slug": case_slug,
        "case_id": case_id,
        "case_name": case_name,
        "defendant_name": defendant,
        "defendants": defendants if len(defendants) > 1 else [],
        "defendant_count": max(len(defendants), 1),
        "pdf_file": pdf_path.name,
        "pdf_pages": _count_pages(pdf_path),
        "full_text_length": len(text),
        # Sentence
        **sentence,
        # Outcome
        "plea": (plea_and_note := find_plea_note(text))[0],
        "plea_note": plea_and_note[1],
        "trial_outcome": trial_outcome,
        "final_outcome": final_outcome,
        "arrest_authority": find_arrest_authority(text),
        "sentence_served_country": find_sentence_served_country(text),
        # Key dates
        **dates,
        # Trial stats
        **stats,
        # Charges
        **charges,
        # Related
        "related_cases": related,
        "related_cases_count": len(related),
        # Raw text for further analysis
        "full_text": text,
    }


def _count_pages(pdf_path: Path) -> int:
    with pdfplumber.open(pdf_path) as pdf:
        return len(pdf.pages)


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------


def find_all_cis(archive_dir: Path) -> list[tuple[str, Path]]:
    """Find all CIS PDFs and return (case_slug, pdf_path) pairs."""
    results = []
    for case_dir in sorted(archive_dir.iterdir()):
        if not case_dir.is_dir():
            continue
        cis_dir = case_dir / "case_info_sheets"
        if not cis_dir.exists():
            continue
        for pdf in sorted(cis_dir.glob("cis[_-]*.pdf")):
            results.append((case_dir.name, pdf))
    return results


def main():
    parser = argparse.ArgumentParser(description="Parse ICTY Case Information Sheet PDFs")
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=Path("data/archive"),
        help="Path to archive directory (default: data/archive)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/cis_parsed"),
        help="Output directory (default: data/cis_parsed)",
    )
    args = parser.parse_args()

    archive_dir = args.archive_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    cis_files = find_all_cis(archive_dir)
    log.info("Found %d CIS PDFs in %s", len(cis_files), archive_dir)

    all_records = []
    errors = []

    for i, (case_slug, pdf_path) in enumerate(cis_files, 1):
        log.info("[%d/%d] Parsing %s ...", i, len(cis_files), pdf_path.name)
        try:
            record = parse_cis(pdf_path, case_slug)
            all_records.append(record)

            # Save individual JSON (without full_text to keep files small)
            record_slim = {k: v for k, v in record.items() if k != "full_text"}
            json_path = output_dir / f"{case_slug}_cis.json"
            json_path.write_text(
                json.dumps(record_slim, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as e:
            log.error("  FAILED: %s - %s", pdf_path.name, e)
            errors.append({"case_slug": case_slug, "pdf": str(pdf_path), "error": str(e)})

    # -----------------------------------------------------------------------
    # Combined outputs
    # -----------------------------------------------------------------------

    # 1. Full JSON (with text) for downstream NLP
    full_json_path = output_dir / "all_cases_full.json"
    full_json_path.write_text(
        json.dumps(all_records, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log.info("Wrote full JSON: %s", full_json_path)

    # 2. Summary JSON (no full_text) for quick loading
    summary_records = [{k: v for k, v in r.items() if k != "full_text"} for r in all_records]
    summary_path = output_dir / "all_cases_summary.json"
    summary_path.write_text(
        json.dumps(summary_records, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log.info("Wrote summary JSON: %s", summary_path)

    # 3. CSV for statistical analysis
    csv_fields = [
        "case_slug", "case_id", "case_name", "defendant_name", "all_defendants", "defendant_count",
        "sentence_text", "sentence_years", "life_sentence",
        "plea", "plea_note", "trial_outcome", "final_outcome", "arrest_authority", "sentence_served_country",
        "born", "indictment_date", "arrested_date", "transferred_date",
        "initial_appearance", "trial_commenced", "commencement",
        "closing_arguments", "trial_judgement", "appeals_judgement",
        "guilty_plea",
        "trial_days", "prosecution_witnesses", "defence_witnesses",
        "chamber_witnesses", "prosecution_exhibits", "defence_exhibits",
        "chamber_exhibits",
        "charge_categories", "crime_types",
        "related_cases_count",
    ]
    csv_path = output_dir / "all_cases.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        for r in all_records:
            row = {**r}
            # Build all_defendants from defendants list, filtering out header artifacts
            defs = r.get("defendants", [])
            if defs:
                names = [d["name"] for d in defs
                         if d["name"] not in ("NOT AN OFFICIAL DOCUMENT", "CASE INFORMATION SHEET")]
                row["all_defendants"] = "; ".join(names)
            else:
                row["all_defendants"] = r.get("defendant_name", "")
            # Flatten lists to semicolon-separated strings for CSV
            for list_field in ("charge_categories", "crime_types", "related_cases"):
                if list_field in row and isinstance(row[list_field], list):
                    row[list_field] = "; ".join(row[list_field])
            writer.writerow(row)
    log.info("Wrote CSV: %s", csv_path)

    # 4. Print summary
    print("\n" + "=" * 70)
    print(f"  CIS PARSING COMPLETE")
    print(f"  Parsed:  {len(all_records)} PDFs")
    print(f"  Errors:  {len(errors)}")
    print(f"  Output:  {output_dir}")
    print("=" * 70)

    # Quick stats
    outcomes = {}
    for r in all_records:
        o = r.get("final_outcome", "unknown") or "unknown"
        outcomes[o] = outcomes.get(o, 0) + 1
    print(f"\n  Outcomes: {outcomes}")

    sentences = [r["sentence_years"] for r in all_records if r.get("sentence_years")]
    if sentences:
        print(f"  Sentences: min={min(sentences)}y, max={max(sentences)}y, "
              f"mean={sum(sentences)/len(sentences):.1f}y (n={len(sentences)})")

    life = sum(1 for r in all_records if r.get("life_sentence"))
    if life:
        print(f"  Life sentences: {life}")

    pleas = {}
    for r in all_records:
        p = r.get("plea", "unknown") or "unknown"
        pleas[p] = pleas.get(p, 0) + 1
    print(f"  Pleas: {pleas}")

    all_charges = set()
    for r in all_records:
        all_charges.update(r.get("charge_categories", []))
    print(f"  Charge categories found: {sorted(all_charges)}")

    all_crimes = set()
    for r in all_records:
        all_crimes.update(r.get("crime_types", []))
    print(f"  Crime types found: {sorted(all_crimes)}")

    trial_days = [r["trial_days"] for r in all_records if r.get("trial_days")]
    if trial_days:
        print(f"  Trial days: min={min(trial_days)}, max={max(trial_days)}, "
              f"mean={sum(trial_days)/len(trial_days):.0f} (n={len(trial_days)})")

    if errors:
        print(f"\n  Errors:")
        for e in errors:
            print(f"    {e['case_slug']}: {e['error']}")

    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
