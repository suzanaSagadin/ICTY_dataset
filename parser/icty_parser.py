"""
ICTY Transcript Parser

Parses ICTY court transcripts from .doc files into structured JSONL format.
Handles speaker segmentation, Q/A linking, role classification, and procedural notes.
"""

import csv
import re
import json
import logging
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ARCHIVE_DIR = Path(__file__).parent.parent / "data" / "archive"

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

# ---------------------------------------------------------------------------
# Accused Name Loading
# ---------------------------------------------------------------------------

def _normalize_accused_name(name: str) -> str:
    """Normalize a name: uppercase, strip accents."""
    name = name.upper()
    name = unicodedata.normalize("NFKD", name)
    return "".join(c for c in name if not unicodedata.combining(c))


def load_accused_names(pleas_path: Path, case_mapping_path: Path | None = None) -> dict[str, dict]:
    """Load accused names from pleas.csv, with fallback from case_mapping.csv.

    Returns {case_folder: {"last_names": {LASTNAME, ...},
                           "full_names": {FIRSTNAME LASTNAME, ...}}}

    For folders without CIS data (no pleas.csv entry), extracts accused names
    from the case_mapping.csv ``name`` column as fallback.
    """
    result = {}
    with open(pleas_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            case = row["case"]
            accused = row.get("accused", "")
            if not accused:
                continue
            normalized = _normalize_accused_name(accused)
            parts = normalized.split()
            if not parts:
                continue
            if case not in result:
                result[case] = {"last_names": set(), "full_names": set()}
            result[case]["last_names"].add(parts[-1])
            result[case]["full_names"].add(normalized)

    # Fallback: for folders not in pleas.csv, extract names from case_mapping.csv
    if case_mapping_path and case_mapping_path.exists():
        with open(case_mapping_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                folder = row.get("folder", "")
                if not folder or folder in result:
                    continue
                name = row.get("name", "")
                if not name:
                    continue
                # Strip "et al.", "et al", "and Others" suffixes
                clean = re.sub(r"\s+et\s+al\.?$", "", name, flags=re.IGNORECASE)
                clean = re.sub(r"\s+and\s+Others$", "", clean, flags=re.IGNORECASE)
                # Split on " and ", " & ", ", " to get individual names
                parts_list = re.split(r"\s+(?:and|&)\s+|,\s*", clean)
                entry = {"last_names": set(), "full_names": set()}
                for part in parts_list:
                    part = part.strip()
                    if not part:
                        continue
                    normalized = _normalize_accused_name(part)
                    words = normalized.split()
                    if not words:
                        continue
                    # Find last name: skip trailing initials like "D." or "M."
                    last = words[-1]
                    if len(last) <= 2 and len(words) > 1:
                        last = words[-2]
                    if len(last) <= 2:
                        continue  # skip entries that are not real names
                    entry["last_names"].add(last)
                    entry["full_names"].add(normalized)
                if entry["last_names"]:
                    result[folder] = entry

    return result


def load_case_id_mapping(case_mapping_path: Path) -> dict[str, str]:
    """Load folder → case_number mapping from case_mapping.csv.

    Returns {folder: case_number} for all rows that have both fields.
    """
    mapping = {}
    if not case_mapping_path.exists():
        return mapping
    with open(case_mapping_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            folder = row.get("folder", "").strip()
            case_number = row.get("case_number", "").strip()
            if folder and case_number:
                mapping[folder] = case_number
    return mapping


def check_is_accused(speaker: str, accused_info: dict | None) -> bool:
    """Check if a speaker is one of the accused.

    Handles patterns:
    - THE ACCUSED / THE ACCUSED PRALJAK  -> always True
    - MR. LASTNAME (matching accused)    -> True
    - FIRSTNAME LASTNAME [Resumed] etc.  -> True if full name matches
    """
    if accused_info is None:
        return False

    s = _normalize_accused_name(speaker)

    # THE ACCUSED / THE APPELLANT is always the accused
    if s == "THE ACCUSED" or s.startswith("THE ACCUSED "):
        return True
    if s == "THE APPELLANT" or s.startswith("THE APPELLANT "):
        return True

    last_names = accused_info["last_names"]
    full_names = accused_info["full_names"]

    # MR. LASTNAME — check last name (only MR., not MS./MRS./MISS,
    # since all ICTY accused are male and MS. can be counsel)
    # Handles: MR. SIMIC, MR. J. SIMIC, MR. DI FAZIO
    m = re.match(r"^MR\.?\s+(?:[A-Z]\.\s+)?(.+)$", s)
    if m:
        name_part = m.group(1).strip()
        # Check the full remaining part and also just the last word
        if name_part in last_names:
            return True
        last_word = name_part.split()[-1] if name_part.split() else ""
        return last_word in last_names

    # Named speaker (e.g. TIHOMIR BLASKIC, DUSKO TADIC (RESUMED))
    # Strip suffixes like [Resumed], (Resumed), [Recalled]
    core = re.sub(r"\s*[\(\[].*$", "", s).strip()
    # Strip title prefixes like GENERAL, DR.
    core = re.sub(r"^(?:GENERAL|DR\.?|COLONEL|CAPTAIN|MAJOR|LIEUTENANT)\s+", "", core)
    if core in full_names:
        return True

    return False


# ---------------------------------------------------------------------------
# File Discovery
# ---------------------------------------------------------------------------

def discover_files(archive_dir: Path = ARCHIVE_DIR,
                   cases: list[str] | None = None) -> list[tuple[str, Path]]:
    """Walk the archive and return (case_slug, doc_path) for eligible transcripts."""
    results = []
    for case_dir in sorted(archive_dir.iterdir()):
        if not case_dir.is_dir() or case_dir.name.startswith("_"):
            continue
        if cases and case_dir.name not in cases:
            continue
        transcripts_dir = case_dir / "transcripts"
        if not transcripts_dir.is_dir():
            continue
        for doc_path in sorted(transcripts_dir.iterdir()):
            if not doc_path.is_file():
                continue
            if doc_path.suffix.lower() not in (".doc", ".docx"):
                continue
            name = doc_path.name
            # Skip French transcripts
            if name.startswith("Compte_rendu"):
                continue
            # Skip correction files
            if name.startswith("Correction"):
                continue
            results.append((case_dir.name, doc_path))
    return results


_MONTH_NAMES = "|".join(MONTHS.keys())
# Also accept 3-letter abbreviations (with optional period): Jan, Feb, Sept, Oct.
_MONTH_ABBREVS = {k[:3]: v for k, v in MONTHS.items()}
_MONTH_ABBREVS["sept"] = 9  # handle "Sept" as well as "Sep"
# French month names
_FRENCH_MONTHS = {
    "janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "aout": 8, "août": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12, "décembre": 12,
}
_ALL_MONTH_NAMES = _MONTH_NAMES + "|" + "|".join(_FRENCH_MONTHS.keys())


def _resolve_month(s: str) -> int | None:
    """Resolve a month string (full name or abbreviation) to a month number."""
    s = s.lower().rstrip(".")
    m = MONTHS.get(s)
    if m:
        return m
    m = _FRENCH_MONTHS.get(s)
    if m:
        return m
    return _MONTH_ABBREVS.get(s)


def parse_date_from_filename(filename: str) -> str | None:
    """Try to extract a date (YYYY-MM-DD) from a transcript filename."""
    # Later format: Hearing_DD_Month_YYYY (tolerates hash suffix before .doc)
    m = re.search(
        r'(\d{1,2})_(' + _MONTH_NAMES + r')_(\d{4})(?:_[0-9a-f]{4,8})?\.doc',
        filename, re.IGNORECASE,
    )
    if m:
        day, month_str, year = m.group(1), m.group(2), m.group(3)
        month = _resolve_month(month_str)
        if month:
            try:
                return datetime(int(year), month, int(day)).strftime("%Y-%m-%d")
            except ValueError:
                pass

    # Early format: Hearing_Month_DDth,_YYYY (month name before day)
    # Use explicit month alternation to avoid greedy \w+ swallowing prefix
    # Tolerates trailing alpha on year (e.g. 1999h) and hash suffixes
    m = re.search(
        r'(' + _MONTH_NAMES + r')_(\d{1,2})(?:st|nd|rd|th)?,?_(\d{4})[a-z]?(?:_[0-9a-f]{4,8})?\.doc',
        filename, re.IGNORECASE,
    )
    if m:
        month_str, day, year = m.group(1), m.group(2), m.group(3)
        month = _resolve_month(month_str)
        if month:
            try:
                return datetime(int(year), month, int(day)).strftime("%Y-%m-%d")
            except ValueError:
                pass

    # Abbreviated month format: DD_Mon.YYYY or DD_Sept.YYYY
    m = re.search(
        r'(\d{1,2})_([A-Za-z]{3,4})\.(\d{4})(?:_[0-9a-f]{4,8})?\.doc',
        filename, re.IGNORECASE,
    )
    if m:
        day, month_str, year = m.group(1), m.group(2), m.group(3)
        month = _resolve_month(month_str)
        if month:
            try:
                return datetime(int(year), month, int(day)).strftime("%Y-%m-%d")
            except ValueError:
                pass

    # Parenthesized case_id in filename — date still present before it
    # e.g. Hearing_3_February_1999_(IT-95-14_1-AR73).doc
    m = re.search(
        r'(\d{1,2})_(' + _MONTH_NAMES + r')_(\d{4})_?\(',
        filename, re.IGNORECASE,
    )
    if m:
        day, month_str, year = m.group(1), m.group(2), m.group(3)
        month = _resolve_month(month_str)
        if month:
            try:
                return datetime(int(year), month, int(day)).strftime("%Y-%m-%d")
            except ValueError:
                pass

    # Ordinal day before month: DDth_Month_YYYY or DDth_Month,_YYYY
    m = re.search(
        r'(\d{1,2})(?:st|nd|rd|th)_(?:of_)?(' + _ALL_MONTH_NAMES + r'),?_(\d{4})',
        filename, re.IGNORECASE,
    )
    if m:
        day, month_str, year = m.group(1), m.group(2), m.group(3)
        month = _resolve_month(month_str)
        if month:
            try:
                return datetime(int(year), month, int(day)).strftime("%Y-%m-%d")
            except ValueError:
                pass

    # Month_DDth,YYYY — flexible separator before year (e.g. August_12th,1999, January_28th.,_2000)
    # Require day to not be followed by more digits (avoid matching year digits as day)
    m = re.search(
        r'(' + _ALL_MONTH_NAMES + r')_(\d{1,2})(?:st|nd|rd|th)?\.?,?[_ ]?(\d{4})(?!\d)',
        filename, re.IGNORECASE,
    )
    if m:
        month_str, day, year = m.group(1), m.group(2), m.group(3)
        month = _resolve_month(month_str)
        if month:
            try:
                return datetime(int(year), month, int(day)).strftime("%Y-%m-%d")
            except ValueError:
                pass

    # No-separator format: DDmonth_YYYY (e.g. 01april_1997)
    m = re.search(
        r'(\d{1,2})(' + _ALL_MONTH_NAMES + r')[_.](\d{4})',
        filename, re.IGNORECASE,
    )
    if m:
        day, month_str, year = m.group(1), m.group(2), m.group(3)
        month = _resolve_month(month_str)
        if month:
            try:
                return datetime(int(year), month, int(day)).strftime("%Y-%m-%d")
            except ValueError:
                pass

    # DD_Month_YYYY with French month names (e.g. 25_Avril_2006)
    m = re.search(
        r'(\d{1,2})_(' + _ALL_MONTH_NAMES + r')_(\d{4})',
        filename, re.IGNORECASE,
    )
    if m:
        day, month_str, year = m.group(1), m.group(2), m.group(3)
        month = _resolve_month(month_str)
        if month:
            try:
                return datetime(int(year), month, int(day)).strftime("%Y-%m-%d")
            except ValueError:
                pass

    return None


# ---------------------------------------------------------------------------
# Text Extraction
# ---------------------------------------------------------------------------

def convert_batch_to_text(batch: list[tuple[str, Path]],
                          txt_dir: Path) -> dict[Path, str]:
    """Convert a batch of .doc files to text via LibreOffice headless.

    Saves .txt files to txt_dir/{case_slug}/ and skips files already converted.
    Returns {doc_path: text} for all available files (cached + newly converted).
    """
    results = {}
    to_convert = []

    # Check for already-converted files
    for case_slug, doc_path in batch:
        cached_path = txt_dir / case_slug / (doc_path.stem + ".txt")
        if cached_path.exists():
            try:
                text = cached_path.read_text(encoding="utf-8", errors="replace")
                if text.startswith("\ufeff"):
                    text = text[1:]
                results[doc_path] = text
            except Exception as e:
                logger.error(f"Failed to read cached {cached_path}: {e}")
                to_convert.append((case_slug, doc_path))
        else:
            to_convert.append((case_slug, doc_path))

    if not to_convert:
        return results

    # Convert remaining files via LibreOffice
    doc_paths_to_convert = [p for _, p in to_convert]
    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = [
            "libreoffice", "--headless",
            "--convert-to", "txt:Text",
            "--outdir", tmpdir,
        ] + [str(p) for p in doc_paths_to_convert]
        try:
            subprocess.run(cmd, capture_output=True, timeout=600)
        except subprocess.TimeoutExpired:
            logger.error("LibreOffice timeout on batch")
            return results

        for case_slug, doc_path in to_convert:
            txt_path = Path(tmpdir) / (doc_path.stem + ".txt")
            if txt_path.exists():
                try:
                    text = txt_path.read_text(encoding="utf-8", errors="replace")
                    if text.startswith("\ufeff"):
                        text = text[1:]
                    results[doc_path] = text
                    # Save to cache
                    cache_dir = txt_dir / case_slug
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    (cache_dir / (doc_path.stem + ".txt")).write_text(
                        text, encoding="utf-8"
                    )
                except Exception as e:
                    logger.error(f"Failed to read {txt_path}: {e}")
            else:
                logger.warning(f"No text output for {doc_path.name}")

    return results


def convert_single_doc(doc_path: Path) -> str | None:
    """Convert a single .doc file to text. Useful for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = [
            "libreoffice", "--headless",
            "--convert-to", "txt:Text",
            "--outdir", tmpdir,
            str(doc_path),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=120)
        except subprocess.TimeoutExpired:
            logger.error(f"LibreOffice timeout on {doc_path.name}")
            return None

        txt_path = Path(tmpdir) / (doc_path.stem + ".txt")
        if txt_path.exists():
            text = txt_path.read_text(encoding="utf-8", errors="replace")
            if text.startswith("\ufeff"):
                text = text[1:]
            return text
    return None


# ---------------------------------------------------------------------------
# Line Number Stripping
# ---------------------------------------------------------------------------

LINE_NUMBER_RE = re.compile(r"^\s*(\d{1,2})\s{2,}")


def strip_line_numbers(text: str) -> list[str]:
    """Strip court-reporter line numbers and return cleaned lines.

    Removes the 1-25 page line numbers, collapses inter-page blank lines,
    and normalizes whitespace.
    """
    raw_lines = text.split("\n")
    cleaned = []
    for line in raw_lines:
        # Try to strip line number prefix
        m = LINE_NUMBER_RE.match(line)
        if m:
            # Everything after the line number + whitespace
            content = line[m.end():]
            cleaned.append(content)
        else:
            stripped = line.strip()
            if stripped:
                cleaned.append(stripped)
            # Skip blank lines (inter-page gaps)
    return cleaned


# ---------------------------------------------------------------------------
# Regex Patterns
# ---------------------------------------------------------------------------

# Speaker labels
SPEAKER_RE = re.compile(
    r"^("
    r"(?:JUDGE\s+[\w'-]+(?:\s+[\w'-]+){0,3})"     # JUDGE ORIE, JUDGE WEINBERG DE ROCA
    r"|(?:THE\s+PRESIDING\s+JUDGE)"                # THE PRESIDING JUDGE
    r"|(?:(?:MR|MS|MRS)\.?\s+(?:[A-Z]\.\s+)?[\w'-]+(?:\s+[\w'-]+){0,2})"  # MR. DI FAZIO, MS. DEL PONTE, MR. J. SIMIC
    r"|(?:MISS\s+(?:[A-Z]\.\s+)?[\w'-]+(?:\s+[\w'-]+){0,2})"  # MISS HOLLIS
    r"|(?:DR\.?\s+[\w'-]+(?:\s+[\w'-]+){0,2})"     # DR. FOLNEGOVIC-SMALC
    r"|(?:THE\s+(?:WITNESS|REGISTRAR|INTERPRETER))"
    r"|(?:THE\s+ACCUSED(?:\s+(?:GENERAL\s+)?[\w'-]+)?)"  # THE ACCUSED / THE ACCUSED PRALJAK / THE ACCUSED GENERAL BLASKIC
    r"|(?:THE\s+APPELLANT(?:\s+[\w'-]+)?)"          # THE APPELLANT / THE APPELLANT KORDIC
    r"|(?:THE\s+USHER)"                             # THE USHER
    r"|(?:THE\s+REGISTRY)"                          # THE REGISTRY
    r"|(?:THE\s+COURT\s+REPORTER)"                  # THE COURT REPORTER
    r"|(?:THE\s+(?:FRENCH\s+)?INTERPRETER)"         # THE FRENCH INTERPRETER (also matches THE INTERPRETER)
    r"|(?:THE\s+PROSECUTION(?:\s*[-\u2013\u2014]\s*[\w'.]+(?:\s+[\w'.]+){0,3})?)"  # THE PROSECUTION - MARK HARMON
    r")"
    r"\s*(\[.*?\])?\s*:\s*",                        # Optional [Interpretation] + colon
    re.IGNORECASE,
)

# Q/A markers
QA_RE = re.compile(r"^\s*([QA])\.\s+(.*)", re.DOTALL)

# Examination headers — match both noun and past-participle forms:
#   "Examination by Mr. Nice:", "Cross-examined by Mr. Lukic:",
#   "Further cross-examination by Ms. Korner:",
#   "Questioned by the Court:", "Questioned by Mr. Saxon:"
EXAM_HEADER_RE = re.compile(
    r"^\s*"
    r"((?:Further\s+)?(?:Cross-|Re-(?:cross-)?)?[Ee]xamin\w+|Questioned)"
    r"\s+by\s+"
    r"("
        r"(?:Mr|Ms|Mrs|Miss)\.{0,2}\s+(?:[A-Z]\.\s+)?[\w'-]+(?:\s+[\w'-]+){0,2}"  # Mr. Nice / Ms. Del Ponte / MR.. MEDDEGODA
        r"|Dr\.{0,2}\s+[\w'-]+(?:\s+[\w'-]+){0,2}"  # Dr. Folnegovic-Smalc
        r"|the\s+Court"                            # the Court
        r"|Judge\s+[\w'-]+(?:\s+[\w'-]+){0,3}"     # Judge Orie / Judge Karibi Whyte
        r"|the\s+[Aa]ccused(?:\s+[\w'-]+)?"        # the Accused / the Accused Praljak
        r"|the\s+\w+(?:\s+\w+){0,2}"              # the Appeals Chamber, the defence
        r"|Assigned\s+\w+"                          # Assigned Counsel
    r")"
    r"\s*[:.]?\s*"                                       # optional colon or period
    r"(?:[\[(](?:Continued|Cont'd|Contd\.?|Resumed|Interpretation)[)\]]?\s*[:.]?\s*)?"  # [Continued], [Cont'd], [Contd.], trailing : or .
    r"$",
    re.IGNORECASE,
)

# Witness declaration
WITNESS_DECL_RE = re.compile(
    r"^\s*WITNESS:\s+(.+)",
    re.IGNORECASE,
)


def _valid_witness_name(name: str) -> bool:
    """Check that a witness name from WITNESS: line is plausible.

    Rejects quoted text, sentence fragments, and bare single characters
    that are not valid pseudonyms.
    """
    if not name:
        return False
    # Reject quoted text
    if name.startswith('"') or name.startswith("'"):
        return False
    # Reject text that looks like a sentence
    lower = name.lower()
    if any(w in lower for w in (" the ", " that ", " you ", " was ", " is ",
                                " are ", " it ", " do ", " did ")):
        return False
    # Reject if it contains question marks or double dashes (sentence fragments)
    if "?" in name or "--" in name:
        return False
    # Reject bare single characters
    if len(name) == 1:
        return False
    # Reject if a period appears mid-text (sentence, not abbreviation/initial)
    # Valid: "PW-108.", "WITNESS M.", "ANTO PESA."
    # Invalid: "Yes.", "I see.", "1995.  Don't waste..."
    # Heuristic: reject if period is followed by more text (space + word)
    if re.search(r"\.\s+\w", name):
        return False
    # Reject single words that end in punctuation and aren't codes/pseudonyms
    # e.g. "Yes.", 'Yes."' but not "ANTO PESA." or "PW-108."
    stripped = name.rstrip('."\' ')
    if " " not in stripped and "-" not in stripped and not stripped.isupper() and not stripped.isdigit():
        if name != stripped:  # had trailing punctuation
            return False
    # Strip trailing suffix like (Resumed), [Resumed] for core name check
    core = re.sub(r"\s*[\(\[].*?[\)\]]\s*$", "", name).strip()
    # Reject if core is very short lowercase or mixed-case fragments
    # Valid cores start with uppercase or are codes (all-caps, digits, hyphens)
    if core and not core[0].isupper() and not core[0].isdigit() and not core.startswith("("):
        return False
    # Reject if core contains lowercase words (sentence fragments like "I see.", "Thank you.")
    if core and " " in core:
        words = core.rstrip('."').split()
        if any(w[0].islower() for w in words if len(w) > 1):
            return False
    return True

# Procedural annotations (standalone bracketed or parenthesized text)
PROCEDURAL_RE = re.compile(r"^\s*(?:\[(.+?)\]|\((.+?)\))\s*$")

# Session breaks
SESSION_BREAK_RE = re.compile(
    r"^\s*---\s+(Upon commencing|Recess taken|On resuming|Whereupon)"
    r".*",
    re.IGNORECASE,
)

# Date on first line
DATE_RE = re.compile(
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
    r",?\s+(\d{1,2})(?:st|nd|rd|th)?\s+(\w+)\s+(\d{4})",
    re.IGNORECASE,
)
# Alternate date: "24th June 1996" without day-of-week
DATE_ALT_RE = re.compile(
    r"(\d{1,2})(?:st|nd|rd|th)?\s+(\w+)\s+(\d{4})",
)

# Case ID — match regular hyphens and en-dash variants (U+2010–U+2014)
_DASH = r"[\-\u2010\u2011\u2012\u2013\u2014]"
# Also match missing-dash form like IT-9512 (conversion artifact for IT-95-12)
CASE_ID_RE = re.compile(rf"IT{_DASH}\d+{_DASH}?\d+(?:[/{_DASH}]\w+)*")

# Redacted pages
REDACTED_RE = re.compile(
    r"(?:Pages?\s+\d+.*?redacted|^\s*\(redacted\)\s*$)",
    re.IGNORECASE,
)

# Session type from bracketed or parenthesized text
SESSION_TYPE_RE = re.compile(
    r"[\[\(](Open session|Private session|Closed session|Status Conference"
    r"|Initial Appearance|Sentencing Hearing|In open session|In private session"
    r"|In closed session)[\]\)]",
    re.IGNORECASE,
)

# Hearing adjourned
ADJOURNED_RE = re.compile(
    r"^\s*\((?:The\s+)?hearing\s+adjourned",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Role Classification
# ---------------------------------------------------------------------------

def classify_role(speaker: str) -> str:
    """Classify a speaker label into a role category."""
    s = speaker.upper().strip()
    if s.startswith("JUDGE") or s == "THE PRESIDING JUDGE":
        return "judge"
    if s == "THE WITNESS":
        return "witness"
    if s == "THE REGISTRAR" or s == "THE REGISTRY":
        return "registrar"
    if s == "THE INTERPRETER" or s == "THE FRENCH INTERPRETER":
        return "interpreter"
    if s == "THE ACCUSED" or s.startswith("THE ACCUSED "):
        return "accused"
    if s == "THE APPELLANT" or s.startswith("THE APPELLANT "):
        return "accused"
    if s == "THE COURT":
        return "judge"
    if s == "THE USHER":
        return "procedural"
    if s == "THE COURT REPORTER":
        return "procedural"
    if s.startswith("THE PROSECUTION"):
        return "prosecution"
    if s.startswith("DR.") or s.startswith("DR "):
        return "counsel"
    # MR./MS./MRS./MISS — counsel, side unknown at this point
    return "counsel"


def classify_examination_type(exam_str: str) -> str:
    """Normalize examination type string."""
    e = exam_str.lower().strip()
    if "cross-" in e and "re-" in e:
        return "re-cross"
    if "cross-" in e:
        return "cross"
    if e.startswith("re-") or "re-examin" in e:
        return "re-direct"
    if e.startswith("questioned"):
        return "questioned"
    return "direct"


# ---------------------------------------------------------------------------
# Line-Number Run Stripping
# ---------------------------------------------------------------------------

# Matches a run of 4+ ascending integers (1-25 range, separated by spaces)
_LINE_NUM_RUN_RE = re.compile(
    r"(?:^|\s)(\d{1,2}(?:\s+\d{1,2}){3,})\s*$"
)


def _strip_line_number_runs(text: str) -> str:
    """Remove trailing sequences of ascending line numbers from text.

    These appear when redacted/blank pages leave bare line numbers (1-25)
    that get appended to the previous utterance.
    """
    m = _LINE_NUM_RUN_RE.search(text)
    if not m:
        return text
    nums = m.group(1).split()
    # Verify they're ascending consecutive integers in the 1-25 range
    try:
        ints = [int(n) for n in nums]
    except ValueError:
        return text
    if all(ints[i] == ints[i - 1] + 1 for i in range(1, len(ints))) and 1 <= ints[0] <= 25:
        text = text[:m.start()].rstrip()
    return text


# ---------------------------------------------------------------------------
# Parser State Machine
# ---------------------------------------------------------------------------

@dataclass
class Utterance:
    id: int
    speaker: str
    role: str
    type: str  # "statement", "question", "answer"
    text: str
    is_accused: bool = False
    examination: str | None = None
    reply_to: int | None = None


@dataclass
class ProceduralNote:
    after_utterance: int
    text: str


@dataclass
class ParserState:
    utterances: list[Utterance] = field(default_factory=list)
    procedural_notes: list[ProceduralNote] = field(default_factory=list)

    # Current utterance being accumulated
    current_speaker: str | None = None
    current_role: str | None = None
    current_is_accused: bool = False
    current_type: str = "statement"
    current_text_lines: list[str] = field(default_factory=list)
    current_examination: str | None = None
    current_reply_to: int | None = None

    # Examination tracking
    current_examiner: str | None = None
    current_exam_type: str | None = None
    last_question_id: int | None = None

    # Metadata
    session_type: str | None = None
    case_id: str | None = None
    date: str | None = None
    witness_name: str | None = None
    last_counsel_speaker: str | None = None  # fallback examiner when no exam header

    # Counters
    utterance_counter: int = 0

    # Counsel role resolution
    counsel_sides: dict = field(default_factory=dict)  # "MR. X" -> "prosecution"/"defence"

    def flush_utterance(self):
        """Finalize the current utterance and add it to the list."""
        if self.current_speaker is None or not self.current_text_lines:
            return

        self.utterance_counter += 1
        text = " ".join(self.current_text_lines).strip()
        # Normalize internal whitespace
        text = re.sub(r"\s+", " ", text)
        # Strip trailing ascending line-number runs (redacted page artifacts)
        # Matches sequences like "11 12 13 14 ... 25" at the end or inline
        text = _strip_line_number_runs(text)

        if not text:
            return

        utt = Utterance(
            id=self.utterance_counter,
            speaker=self.current_speaker,
            role=self.current_role or "unknown",
            type=self.current_type,
            text=text,
            is_accused=self.current_is_accused,
            examination=self.current_examination,
            reply_to=self.current_reply_to,
        )
        self.utterances.append(utt)

        # Track last question for reply_to linking
        if self.current_type == "question":
            self.last_question_id = self.utterance_counter

        self.current_text_lines = []
        self.current_reply_to = None
        self.current_examination = None

    def add_procedural_note(self, text: str):
        last_id = self.utterance_counter
        self.procedural_notes.append(ProceduralNote(
            after_utterance=last_id,
            text=text,
        ))


def parse_transcript(lines: list[str], case_slug: str,
                     filename_date: str | None = None,
                     accused_info: dict | None = None) -> dict:
    """Parse cleaned transcript lines into a structured dict."""
    state = ParserState()

    # Extract metadata from early lines
    for line in lines[:30]:
        if state.date is None:
            m = DATE_RE.search(line)
            if m:
                day, month_str, year = m.group(1), m.group(2).lower(), m.group(3)
                month = MONTHS.get(month_str)
                if month:
                    try:
                        state.date = datetime(int(year), month, int(day)).strftime("%Y-%m-%d")
                    except ValueError:
                        pass
        if state.case_id is None:
            m = CASE_ID_RE.search(line)
            if m:
                state.case_id = m.group(0)

        m = SESSION_TYPE_RE.search(line)
        if m:
            state.session_type = m.group(1)

    if state.date is None:
        state.date = filename_date

    # Fallback: search full text for case ID if not found in header
    if state.case_id is None:
        for line in lines:
            m = CASE_ID_RE.search(line)
            if m:
                state.case_id = m.group(0)
                break

    # Normalize any dash variants in case_id to regular hyphens
    if state.case_id:
        state.case_id = re.sub(r"[\u2010\u2011\u2012\u2013\u2014]", "-", state.case_id)

    # Main parse loop
    for line in lines:
        # --- Examination header ---
        m = EXAM_HEADER_RE.match(line)
        if m:
            state.flush_utterance()
            exam_type_str = m.group(1)
            examiner = m.group(2).strip()
            state.current_examiner = examiner.upper()
            state.current_exam_type = classify_examination_type(exam_type_str)
            state.add_procedural_note(line.strip())
            continue

        # --- Witness declaration ---
        m = WITNESS_DECL_RE.match(line)
        if m:
            state.flush_utterance()
            name = m.group(1).strip()
            if _valid_witness_name(name):
                state.witness_name = name
            state.add_procedural_note(line.strip())
            continue

        # --- Procedural annotation (standalone brackets) ---
        m = PROCEDURAL_RE.match(line)
        if m:
            state.flush_utterance()
            note_text = m.group(0).strip()
            state.add_procedural_note(note_text)
            # Track session type changes
            sm = SESSION_TYPE_RE.search(note_text)
            if sm:
                state.session_type = sm.group(1)
            continue

        # --- Session break ---
        m = SESSION_BREAK_RE.match(line)
        if m:
            state.flush_utterance()
            state.add_procedural_note(line.strip())
            continue

        # --- Hearing adjourned ---
        m = ADJOURNED_RE.match(line)
        if m:
            state.flush_utterance()
            state.add_procedural_note(line.strip())
            continue

        # --- Redacted ---
        m = REDACTED_RE.match(line)
        if m:
            state.flush_utterance()
            state.add_procedural_note(line.strip())
            continue

        # --- Speaker label ---
        m = SPEAKER_RE.match(line)
        if m:
            state.flush_utterance()
            speaker = m.group(1).strip().upper()
            # Collapse internal whitespace ("MR.  ZECEVIC" -> "MR. ZECEVIC")
            speaker = re.sub(r"\s+", " ", speaker)
            # Normalize speaker: remove trailing period artifacts
            speaker = re.sub(r"\.\s*$", "", speaker)
            # Standardize MR./MS. with period
            speaker = re.sub(r"^(MR|MS|MRS)\s", r"\1. ", speaker)

            interpretation = m.group(2)  # e.g., "[Interpretation]"
            rest = line[m.end():].strip()

            # Check for THE WITNESS: NAME declaration form
            # If speaker is THE WITNESS and rest looks like a standalone name
            # (1-4 uppercase words, possibly with period/parenthesized suffix),
            # treat as a witness declaration rather than an utterance
            if speaker == "THE WITNESS" and rest:
                name_candidate = re.sub(r"\s*[\(\[].*$", "", rest).strip().rstrip(".")
                if (name_candidate
                    and _valid_witness_name(name_candidate)
                    and len(name_candidate.split()) <= 4
                    and name_candidate == name_candidate.upper()
                    and not any(w in name_candidate for w in ("YES", "NO", "THANK", "I "))):
                    state.witness_name = name_candidate
                    state.add_procedural_note(f"THE WITNESS: {rest}")
                    continue

            state.current_speaker = speaker
            state.current_role = classify_role(speaker)
            state.current_is_accused = check_is_accused(speaker, accused_info)
            state.current_type = "statement"
            state.current_text_lines = [rest] if rest else []

            # Track last counsel speaker as fallback examiner
            if state.current_role == "counsel":
                state.last_counsel_speaker = speaker

            # Check if this speaker line is followed by Q. on the same line
            # (sometimes "MR. IVETIC:  Q.  question text")
            qa_m = QA_RE.match(rest)
            if qa_m:
                qa_type = qa_m.group(1).upper()
                qa_text = qa_m.group(2).strip()
                if qa_type == "Q":
                    if state.current_examiner is None:
                        state.current_examiner = speaker
                    state.current_type = "question"
                    state.current_examination = state.current_exam_type
                    state.current_text_lines = [qa_text] if qa_text else []
                elif qa_type == "A":
                    witness = state.witness_name or "THE WITNESS"
                    state.current_speaker = witness
                    state.current_role = "witness"
                    state.current_is_accused = check_is_accused(witness, accused_info)
                    state.current_type = "answer"
                    state.current_reply_to = state.last_question_id
                    state.current_text_lines = [qa_text] if qa_text else []
            continue

        # --- Q/A marker ---
        m = QA_RE.match(line)
        if m:
            state.flush_utterance()
            qa_type = m.group(1).upper()
            qa_text = m.group(2).strip()

            if qa_type == "Q":
                speaker = state.current_examiner or state.last_counsel_speaker or "UNKNOWN EXAMINER"
                state.current_speaker = speaker
                state.current_role = classify_role(speaker) if speaker != "UNKNOWN EXAMINER" else "counsel"
                state.current_is_accused = check_is_accused(speaker, accused_info)
                state.current_type = "question"
                state.current_examination = state.current_exam_type
                state.current_text_lines = [qa_text] if qa_text else []
            elif qa_type == "A":
                witness = state.witness_name or "THE WITNESS"
                state.current_speaker = witness
                state.current_role = "witness"
                state.current_is_accused = check_is_accused(witness, accused_info)
                state.current_type = "answer"
                state.current_reply_to = state.last_question_id
                state.current_text_lines = [qa_text] if qa_text else []
            continue

        # --- Continuation line ---
        stripped = line.strip()
        if stripped and state.current_speaker is not None:
            state.current_text_lines.append(stripped)

    # Flush final utterance
    state.flush_utterance()

    # Build output
    result = {
        "case": case_slug,
        "case_id_base": None,  # populated by caller from case_mapping
        "case_id_transcript": state.case_id,
        "date": state.date,
        "utterances": [],
        "procedural_notes": [],
    }

    for utt in state.utterances:
        u = {
            "id": utt.id,
            "speaker": utt.speaker,
            "role": utt.role,
            "is_accused": utt.is_accused,
            "type": utt.type,
            "text": utt.text,
        }
        if utt.examination:
            u["examination"] = utt.examination
        if utt.reply_to is not None:
            u["reply_to"] = utt.reply_to
        result["utterances"].append(u)

    for note in state.procedural_notes:
        result["procedural_notes"].append({
            "after_utterance": note.after_utterance,
            "text": note.text,
        })

    return result


# ---------------------------------------------------------------------------
# Full Pipeline
# ---------------------------------------------------------------------------

def process_file(case_slug: str, doc_path: Path, text: str,
                 accused_info: dict | None = None,
                 case_id_base: str | None = None) -> dict | None:
    """Process a single transcript: strip lines, parse, return structured dict."""
    filename_date = parse_date_from_filename(doc_path.name)
    cleaned_lines = strip_line_numbers(text)

    if not cleaned_lines:
        logger.warning(f"Empty after line stripping: {doc_path.name}")
        return None

    result = parse_transcript(cleaned_lines, case_slug, filename_date, accused_info)
    result["case_id_base"] = case_id_base
    result["source_file"] = doc_path.name

    return result
