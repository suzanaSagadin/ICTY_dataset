"""
ICTY Transcript Parser

Parses ICTY court transcripts from .doc files into structured JSONL format.
Handles speaker segmentation, Q/A linking, role classification, and procedural notes.
"""

import re
import json
import logging
import subprocess
import tempfile
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
            if doc_path.suffix.lower() != ".doc":
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


def parse_date_from_filename(filename: str) -> str | None:
    """Try to extract a date (YYYY-MM-DD) from a transcript filename."""
    # Later format: Hearing_DD_Month_YYYY
    m = re.search(r'(\d{1,2})_(\w+)_(\d{4})\.doc', filename, re.IGNORECASE)
    if m:
        day, month_str, year = m.group(1), m.group(2).lower(), m.group(3)
        month = MONTHS.get(month_str)
        if month:
            try:
                return datetime(int(year), month, int(day)).strftime("%Y-%m-%d")
            except ValueError:
                pass

    # Early format: Hearing_Month_DDth,_YYYY
    m = re.search(r'(\w+)_(\d{1,2})(?:st|nd|rd|th),?_(\d{4})\.doc', filename, re.IGNORECASE)
    if m:
        month_str, day, year = m.group(1).lower(), m.group(2), m.group(3)
        month = MONTHS.get(month_str)
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
    r"|(?:MR\.?\s+[\w'-]+)"                         # MR. IVETIC
    r"|(?:MS\.?\s+[\w'-]+)"                         # MS. UERTZ-RETZLAFF
    r"|(?:MRS\.?\s+[\w'-]+)"                        # MRS. X
    r"|(?:MISS\s+[\w'-]+)"                          # MISS HOLLIS
    r"|(?:THE\s+(?:WITNESS|REGISTRAR|INTERPRETER|ACCUSED))"
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
        r"(?:Mr|Ms|Mrs|Miss)\.?\s+[\w'-]+"       # Mr. Nice
        r"|the\s+Court"                            # the Court
        r"|Judge\s+[\w'-]+(?:\s+[\w'-]+){0,3}"     # Judge Orie / Judge Karibi Whyte
        r"|the\s+\w+(?:\s+\w+){0,2}"              # the Appeals Chamber, the defence
        r"|Assigned\s+\w+"                          # Assigned Counsel
    r")"
    r"\s*:?\s*",                                    # colon is sometimes missing
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

# Procedural annotations (standalone bracketed text)
PROCEDURAL_RE = re.compile(r"^\s*\[(.+?)\]\s*$")

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
CASE_ID_RE = re.compile(rf"IT{_DASH}\d+{_DASH}\d+(?:[/{_DASH}]\w+)*")

# Redacted pages
REDACTED_RE = re.compile(
    r"(?:Pages?\s+\d+.*?redacted|^\s*\(redacted\)\s*$)",
    re.IGNORECASE,
)

# Session type from bracketed text
SESSION_TYPE_RE = re.compile(
    r"\[(Open session|Private session|Closed session|Status Conference"
    r"|Initial Appearance|Sentencing Hearing)\]",
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
    if s == "THE REGISTRAR":
        return "registrar"
    if s == "THE INTERPRETER":
        return "interpreter"
    if s == "THE ACCUSED":
        return "accused"
    if s == "THE COURT":
        return "judge"
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
# Parser State Machine
# ---------------------------------------------------------------------------

@dataclass
class Utterance:
    id: int
    speaker: str
    role: str
    type: str  # "statement", "question", "answer"
    text: str
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

        if not text:
            return

        utt = Utterance(
            id=self.utterance_counter,
            speaker=self.current_speaker,
            role=self.current_role or "unknown",
            type=self.current_type,
            text=text,
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
                     filename_date: str | None = None) -> dict:
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

            state.current_speaker = speaker
            state.current_role = classify_role(speaker)
            state.current_type = "statement"
            state.current_text_lines = [rest] if rest else []

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
                    state.current_speaker = state.witness_name or "THE WITNESS"
                    state.current_role = "witness"
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
                speaker = state.current_examiner or "UNKNOWN EXAMINER"
                state.current_speaker = speaker
                state.current_role = classify_role(speaker) if speaker != "UNKNOWN EXAMINER" else "counsel"
                state.current_type = "question"
                state.current_examination = state.current_exam_type
                state.current_text_lines = [qa_text] if qa_text else []
            elif qa_type == "A":
                state.current_speaker = state.witness_name or "THE WITNESS"
                state.current_role = "witness"
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
        "case_id": state.case_id,
        "date": state.date,
        "utterances": [],
        "procedural_notes": [],
    }

    for utt in state.utterances:
        u = {
            "id": utt.id,
            "speaker": utt.speaker,
            "role": utt.role,
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

def process_file(case_slug: str, doc_path: Path, text: str) -> dict | None:
    """Process a single transcript: strip lines, parse, return structured dict."""
    filename_date = parse_date_from_filename(doc_path.name)
    cleaned_lines = strip_line_numbers(text)

    if not cleaned_lines:
        logger.warning(f"Empty after line stripping: {doc_path.name}")
        return None

    result = parse_transcript(cleaned_lines, case_slug, filename_date)
    result["source_file"] = doc_path.name

    return result
