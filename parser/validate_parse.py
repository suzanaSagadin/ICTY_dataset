"""
Validate parsed ICTY transcripts against source text files.

Produces a baseline report of known issues (orphan speakers, embedded labels,
null metadata, etc.) for before/after comparison during parser fixes.
"""

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Broad speaker pattern — intentionally more permissive than the parser's
# SPEAKER_RE so we can detect lines the parser misses (orphans).
# ---------------------------------------------------------------------------

BROAD_SPEAKER_RE = re.compile(
    r"^("
    r"JUDGE\s+[\w'-]+(?:\s+[\w'-]+){0,3}"
    r"|THE\s+PRESIDING\s+JUDGE"
    r"|(?:MR|MS|MRS|MISS)\.?\s+(?:[A-Z]\.\s+)?[\w'-]+(?:\s+[\w'-]+){0,2}"
    r"|DR\.?\s+[\w'-]+(?:\s+[\w'-]+){0,2}"
    r"|THE\s+(?:WITNESS|REGISTRAR|INTERPRETER|ACCUSED|APPELLANT"
    r"|USHER|REGISTRY|COURT\s+REPORTER|FRENCH\s+INTERPRETER)"
    r"(?:\s+[\w'-]+(?:\s+[\w'-]+){0,2})?"
    r")"
    r"\s*(?:\[.*?\])?\s*:\s",
    re.IGNORECASE,
)

# Specific labels to count in txt (the known offenders)
OFFENDER_PATTERNS = {
    "MR. DI FAZIO": re.compile(r"MR\.?\s+DI\s+FAZIO\s*:", re.IGNORECASE),
    "MS. DEL PONTE": re.compile(r"MS\.?\s+DEL\s+PONTE\s*:", re.IGNORECASE),
    "MR. DE ROUX": re.compile(r"MR\.?\s+DE\s+ROUX\s*:", re.IGNORECASE),
    "THE APPELLANT": re.compile(r"THE\s+APPELLANT(?:\s+[\w'-]+)?\s*:", re.IGNORECASE),
    "DR. *": re.compile(r"DR\.?\s+[\w'-]+\s*:", re.IGNORECASE),
    "THE USHER": re.compile(r"THE\s+USHER\s*:", re.IGNORECASE),
    "THE REGISTRY": re.compile(r"THE\s+REGISTRY\s*:", re.IGNORECASE),
    "THE COURT REPORTER": re.compile(r"THE\s+COURT\s+REPORTER\s*:", re.IGNORECASE),
    "THE FRENCH INTERPRETER": re.compile(r"THE\s+FRENCH\s+INTERPRETER\s*:", re.IGNORECASE),
    "initials (MR/MS X. NAME)": re.compile(
        r"(?:MR|MS|MRS)\.?\s+[A-Z]\.\s+[\w'-]+\s*:", re.IGNORECASE
    ),
}

# Embedded speaker label in utterance text (signature of a merge bug)
EMBEDDED_SPEAKER_RE = re.compile(
    r"(?:MR|MS|MRS|MISS)\.?\s+[\w'-]+(?:\s+[\w'-]+){0,2}\s*:"
    r"|JUDGE\s+[\w'-]+\s*:"
    r"|THE\s+(?:WITNESS|ACCUSED|APPELLANT|REGISTRAR|INTERPRETER)\s*:",
    re.IGNORECASE,
)

# Exam header pattern (broader than parser's, to detect leaks)
EXAM_IN_TEXT_RE = re.compile(
    r"(?:Cross-|Re-)?[Ee]xamin\w+\s+by\s+(?:Mr|Ms|Mrs|Miss|Judge|the)",
    re.IGNORECASE,
)

# Parenthesized procedural notes (pre-2002 style)
PAREN_PROCEDURAL_RE = re.compile(r"^\s*\(.+?\)\s*$")


def validate_corpus(json_dir: Path, txt_dir: Path) -> dict:
    """Run all validation checks across the corpus."""
    stats = {
        "total_files": 0,
        "total_utterances": 0,
        "null_date": 0,
        "null_case_id_base": 0,
        "null_case_id_transcript": 0,
        "is_accused_true": 0,
        "answers_with_reply_to": 0,
        "total_answers": 0,
        "unknown_examiner_utterances": 0,
        "orphan_speaker_lines": 0,
        "embedded_label_utterances": 0,
        "exam_header_in_text": 0,
        "paren_procedural_in_text": 0,
        "offender_in_txt": Counter(),
        "offender_in_json": Counter(),
        "speaker_counts": Counter(),
        "orphan_examples": [],
        "embedded_examples": [],
    }

    case_dirs = sorted(json_dir.iterdir()) if json_dir.is_dir() else []

    for case_dir in case_dirs:
        if not case_dir.is_dir():
            continue
        case_slug = case_dir.name

        for json_path in sorted(case_dir.glob("*.json")):
            stats["total_files"] += 1

            # Load JSON
            try:
                with open(json_path, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue

            # Metadata nulls
            if data.get("date") is None:
                stats["null_date"] += 1
            if data.get("case_id_base") is None:
                stats["null_case_id_base"] += 1
            if data.get("case_id_transcript") is None:
                stats["null_case_id_transcript"] += 1

            # Utterance-level stats
            json_speakers = set()
            for utt in data.get("utterances", []):
                stats["total_utterances"] += 1
                speaker = utt.get("speaker", "")
                stats["speaker_counts"][speaker] += 1
                json_speakers.add(speaker)

                if utt.get("is_accused"):
                    stats["is_accused_true"] += 1

                if utt.get("type") == "answer":
                    stats["total_answers"] += 1
                    if utt.get("reply_to") is not None:
                        stats["answers_with_reply_to"] += 1

                if speaker == "UNKNOWN EXAMINER":
                    stats["unknown_examiner_utterances"] += 1

                text = utt.get("text", "")

                # Embedded speaker labels in text
                if EMBEDDED_SPEAKER_RE.search(text):
                    stats["embedded_label_utterances"] += 1
                    if len(stats["embedded_examples"]) < 20:
                        stats["embedded_examples"].append({
                            "file": json_path.name,
                            "case": case_slug,
                            "utt_id": utt.get("id"),
                            "speaker": speaker,
                            "text_snippet": text[:200],
                        })

                # Exam headers leaked into text
                if EXAM_IN_TEXT_RE.search(text) and len(text) < 120:
                    stats["exam_header_in_text"] += 1

                # Parenthesized procedural notes in text
                # Check for embedded ( ... ) patterns
                if re.search(r"\([A-Z][a-z].*?\)", text):
                    stats["paren_procedural_in_text"] += 1

            # Check offender counts in JSON speakers
            for label, pattern in OFFENDER_PATTERNS.items():
                for sp in json_speakers:
                    if pattern.match(sp + ":"):
                        stats["offender_in_json"][label] += 1

            # Load corresponding txt for orphan analysis
            txt_path = txt_dir / case_slug / (json_path.stem + ".txt")
            if txt_path.exists():
                try:
                    txt_content = txt_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue

                for txt_line in txt_content.split("\n"):
                    stripped = txt_line.strip()
                    if not stripped:
                        continue

                    # Check offender patterns in source txt
                    for label, pattern in OFFENDER_PATTERNS.items():
                        if pattern.search(stripped):
                            stats["offender_in_txt"][label] += 1

                    # Orphan detection: line matches broad speaker pattern
                    # but the speaker isn't in JSON
                    m = BROAD_SPEAKER_RE.match(stripped)
                    if m:
                        found_speaker = re.sub(r"\s+", " ", m.group(1).strip().upper())
                        # Normalize
                        found_speaker = re.sub(r"^(MR|MS|MRS)\s", r"\1. ", found_speaker)
                        if found_speaker not in json_speakers:
                            stats["orphan_speaker_lines"] += 1
                            if len(stats["orphan_examples"]) < 30:
                                stats["orphan_examples"].append({
                                    "file": json_path.name,
                                    "case": case_slug,
                                    "speaker": found_speaker,
                                    "line": stripped[:150],
                                })

    return stats


def print_report(stats: dict):
    """Print a human-readable summary."""
    print("=" * 70)
    print("ICTY PARSER VALIDATION REPORT")
    print("=" * 70)

    print(f"\nTotal files:          {stats['total_files']:,}")
    print(f"Total utterances:     {stats['total_utterances']:,}")

    print(f"\n--- Metadata ---")
    print(f"Null date:            {stats['null_date']:,}")
    print(f"Null case_id_base:    {stats['null_case_id_base']:,}")
    print(f"Null case_id_transcript: {stats['null_case_id_transcript']:,}")

    print(f"\n--- Speaker issues ---")
    print(f"Orphan speaker lines: {stats['orphan_speaker_lines']:,}")
    print(f"Embedded labels:      {stats['embedded_label_utterances']:,}")
    print(f"UNKNOWN EXAMINER:     {stats['unknown_examiner_utterances']:,}")

    print(f"\n--- Offender labels (in source txt → in JSON speakers) ---")
    all_labels = sorted(set(stats["offender_in_txt"]) | set(stats["offender_in_json"]))
    for label in all_labels:
        txt_count = stats["offender_in_txt"].get(label, 0)
        json_count = stats["offender_in_json"].get(label, 0)
        status = "OK" if json_count > 0 else "MISSING"
        print(f"  {label:35s}  txt={txt_count:>6,}  json={json_count:>4}  [{status}]")

    print(f"\n--- Q/A linkage ---")
    print(f"Total answers:        {stats['total_answers']:,}")
    print(f"With reply_to:        {stats['answers_with_reply_to']:,}")
    rate = (stats['answers_with_reply_to'] / stats['total_answers'] * 100
            if stats['total_answers'] else 0)
    print(f"Link rate:            {rate:.1f}%")

    print(f"\n--- Accused ---")
    print(f"is_accused=True:      {stats['is_accused_true']:,}")

    print(f"\n--- Text quality ---")
    print(f"Exam headers in text: {stats['exam_header_in_text']:,}")
    print(f"Paren proc. in text:  {stats['paren_procedural_in_text']:,}")

    if stats["orphan_examples"]:
        print(f"\n--- Orphan examples (first {len(stats['orphan_examples'])}) ---")
        for ex in stats["orphan_examples"][:15]:
            print(f"  [{ex['case']}] {ex['speaker']}")
            print(f"    {ex['line'][:120]}")

    if stats["embedded_examples"]:
        print(f"\n--- Embedded label examples (first {len(stats['embedded_examples'])}) ---")
        for ex in stats["embedded_examples"][:10]:
            print(f"  [{ex['case']}] utt {ex['utt_id']} by {ex['speaker']}:")
            print(f"    {ex['text_snippet'][:120]}")

    # Top 20 speakers
    print(f"\n--- Top 30 speakers by utterance count ---")
    for speaker, count in stats["speaker_counts"].most_common(30):
        print(f"  {speaker:40s} {count:>8,}")


def save_report(stats: dict, output_path: Path):
    """Save machine-readable report."""
    # Convert Counters to dicts for JSON serialization
    serializable = dict(stats)
    serializable["offender_in_txt"] = dict(stats["offender_in_txt"])
    serializable["offender_in_json"] = dict(stats["offender_in_json"])
    serializable["speaker_counts"] = dict(stats["speaker_counts"].most_common(100))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to {output_path}")


def main():
    json_dir = Path("data_250625/transcripts_parsed")
    txt_dir = Path("data_250625/transcripts_txt")
    output_path = Path("logs/validation_report.json")

    if len(sys.argv) > 1:
        json_dir = Path(sys.argv[1])
    if len(sys.argv) > 2:
        txt_dir = Path(sys.argv[2])

    if not json_dir.exists():
        print(f"ERROR: JSON directory not found: {json_dir}")
        sys.exit(1)

    print(f"Scanning {json_dir} ...")
    stats = validate_corpus(json_dir, txt_dir)
    print_report(stats)
    save_report(stats, output_path)


if __name__ == "__main__":
    main()
