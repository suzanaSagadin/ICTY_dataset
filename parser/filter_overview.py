#!/usr/bin/env python3
"""
ICTY Transcript Filter Overview
===============================
Builds an overview of the transcript archive to help filter cases:
  - language of every transcript (English vs. other)
  - per-case breakdown and a list of "clean" cases
  - cases whose defendant entered a "not guilty" plea, split into clean vs.
    those with a potential problem (non-English / image-only / undetermined)

Language detection is content-based: each transcript's text is run through
langdetect. Text comes from the existing data/converted_txt cache when
available; uncached files (e.g. the French "Compte rendu" transcripts, which
the main parser skips) are converted on the fly with LibreOffice. Image-only
scans (.tif) cannot be read as text and are reported as "not analyzed".

Plea data comes from the parsed Case Information Sheets in data/cis_parsed/
(the `plea` field of each *_cis.json).

Outputs (in data/reports/):
  filter_overview.txt                 human-readable summary
  filter_overview_by_case.csv         per-case counts
  non_english_transcripts.csv         one row per non-English transcript
  not_analyzed_transcripts.csv        image/unsupported files
  undetermined_transcripts.csv        too little text to detect
  not_guilty_cases.csv                "not guilty" plea cases, clean vs. problem

Usage:
  python filter_overview.py                       # all cases
  python filter_overview.py --cases limaj galic   # specific cases
  python filter_overview.py --limit 50            # first N files (testing)

Requirements: langdetect, tqdm, LibreOffice (soffice) on PATH.
"""

import argparse
import csv
import logging
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from langdetect import DetectorFactory, LangDetectException, detect_langs

from icty_parser import convert_batch_to_text, strip_line_numbers

# Deterministic detection
DetectorFactory.seed = 0

logger = logging.getLogger("language_report")

PROJECT_ROOT = Path(__file__).parent.parent
ARCHIVE_DIR = PROJECT_ROOT / "data" / "archive"
TXT_CACHE_DIR = PROJECT_ROOT / "data" / "converted_txt"
CIS_DIR = PROJECT_ROOT / "data" / "cis_parsed"
REPORT_DIR = PROJECT_ROOT / "data" / "reports"

# A case counts as "not guilty" when the defendant entered a not-guilty plea,
# read from the `plea` field of the parsed Case Information Sheet.
NOT_GUILTY_PLEA = "not guilty"

# Extensions we can turn into text for analysis
TEXT_EXTS = {".doc", ".docx", ".pdf"}
# Image-only scans: no text without OCR
IMAGE_EXTS = {".tif", ".tiff"}

# Minimum cleaned characters needed for a trustworthy verdict
MIN_CHARS = 200
# Characters of body text fed to the detector
SAMPLE_CHARS = 6000

# ISO 639-1 -> display name (languages plausibly seen in ICTY material)
LANG_NAMES = {
    "en": "English", "fr": "French", "de": "German", "nl": "Dutch",
    "sr": "Serbian", "hr": "Croatian", "bs": "Bosnian", "sl": "Slovenian",
    "mk": "Macedonian", "sq": "Albanian", "it": "Italian", "es": "Spanish",
    "ru": "Russian", "pt": "Portuguese", "ca": "Catalan", "ro": "Romanian",
}


def lang_name(code: str) -> str:
    return LANG_NAMES.get(code, code)


def load_pleas(cis_dir: Path) -> dict[str, str]:
    """Map case_slug -> plea (e.g. "not guilty") from parsed Case Info Sheets."""
    import json
    pleas = {}
    if not cis_dir.is_dir():
        logger.warning(f"CIS dir not found: {cis_dir} (plea filtering disabled)")
        return pleas
    for f in sorted(cis_dir.glob("*_cis.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning(f"Could not read {f.name}: {e}")
            continue
        slug = d.get("case_slug")
        if slug:
            pleas[slug] = (d.get("plea") or "").strip().lower()
    return pleas


def discover_transcripts(archive_dir: Path, cases=None):
    """Return (case_slug, path) for every transcript file in the archive."""
    found = []
    for case_dir in sorted(archive_dir.iterdir()):
        if not case_dir.is_dir() or case_dir.name.startswith("_"):
            continue
        if cases and case_dir.name not in cases:
            continue
        tdir = case_dir / "transcripts"
        if not tdir.is_dir():
            continue
        for p in sorted(tdir.iterdir()):
            if p.is_file():
                found.append((case_dir.name, p))
    return found


def cached_text(case_slug: str, doc_path: Path) -> str | None:
    """Read text from the converted_txt cache if present."""
    cache_path = TXT_CACHE_DIR / case_slug / (doc_path.stem + ".txt")
    if cache_path.exists():
        try:
            text = cache_path.read_text(encoding="utf-8", errors="replace")
            return text[1:] if text.startswith("﻿") else text
        except OSError as e:
            logger.warning(f"Could not read cache {cache_path}: {e}")
    return None


def detect(text: str):
    """Return (lang_code, confidence) or (None, 0.0) if undetectable."""
    cleaned = " ".join(strip_line_numbers(text))
    if len(cleaned) < MIN_CHARS:
        return None, 0.0
    try:
        ranked = detect_langs(cleaned[:SAMPLE_CHARS])
    except LangDetectException:
        return None, 0.0
    if not ranked:
        return None, 0.0
    top = ranked[0]
    return top.lang, round(top.prob, 4)


def build_report(archive_dir, cases, limit, batch_size):
    transcripts = discover_transcripts(archive_dir, cases)
    if limit:
        transcripts = transcripts[:limit]
    logger.info(f"Found {len(transcripts)} transcript files")

    # Resolve text for each file: cache, then batch-convert the uncached ones.
    texts: dict[Path, str] = {}
    to_convert = []
    images, other = [], []
    for case_slug, path in transcripts:
        ext = path.suffix.lower()
        if ext in IMAGE_EXTS:
            images.append((case_slug, path))
            continue
        if ext not in TEXT_EXTS:
            other.append((case_slug, path))
            continue
        text = cached_text(case_slug, path)
        if text is not None:
            texts[path] = text
        else:
            to_convert.append((case_slug, path))

    if to_convert:
        logger.info(f"Converting {len(to_convert)} uncached files via LibreOffice...")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            for i in range(0, len(to_convert), batch_size):
                batch = to_convert[i:i + batch_size]
                logger.info(f"  batch {i // batch_size + 1}: {len(batch)} files")
                texts.update(convert_batch_to_text(batch, tmp_dir))

    # Classify every file
    rows = []  # all results
    for case_slug, path in transcripts:
        ext = path.suffix.lower()
        if ext in IMAGE_EXTS:
            rows.append((case_slug, path, "not_analyzed", 0.0,
                         "image scan (TIFF) - no text without OCR"))
            continue
        if ext not in TEXT_EXTS:
            rows.append((case_slug, path, "not_analyzed", 0.0,
                         f"unsupported extension {ext}"))
            continue
        text = texts.get(path)
        if not text:
            rows.append((case_slug, path, "undetermined", 0.0,
                         "conversion produced no text"))
            continue
        code, conf = detect(text)
        if code is None:
            rows.append((case_slug, path, "undetermined", conf,
                         "too little text to detect"))
        else:
            rows.append((case_slug, path, code, conf, ""))
    return rows


def write_outputs(rows, report_dir: Path, pleas: dict[str, str] | None = None):
    report_dir.mkdir(parents=True, exist_ok=True)
    pleas = pleas or {}

    non_english = [
        r for r in rows
        if r[2] not in ("en", "not_analyzed", "undetermined")
    ]
    non_english.sort(key=lambda r: (r[2], r[0], r[1].name))

    not_analyzed = [r for r in rows if r[2] == "not_analyzed"]
    undetermined = [r for r in rows if r[2] == "undetermined"]
    not_analyzed.sort(key=lambda r: (r[0], r[1].name))
    undetermined.sort(key=lambda r: (r[0], r[1].name))

    def write_csv(name, subset):
        path = report_dir / name
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["case", "language_code", "language", "confidence",
                        "reason", "filename", "path"])
            for case_slug, p, code, conf, reason in subset:
                w.writerow([case_slug, code, lang_name(code), conf, reason,
                            p.name, str(p.relative_to(PROJECT_ROOT))])
        return path

    csv_path = write_csv("non_english_transcripts.csv", non_english)
    tif_csv = write_csv("not_analyzed_transcripts.csv", not_analyzed)
    undet_csv = write_csv("undetermined_transcripts.csv", undetermined)

    # Counts
    lang_counts = Counter(r[2] for r in rows)
    by_case_lang = defaultdict(Counter)
    for case_slug, _, code, _, _ in non_english:
        by_case_lang[case_slug][code] += 1

    # Per-case breakdown: total / english / non-english / not_analyzed / undetermined
    case_stats = defaultdict(lambda: {"total": 0, "english": 0, "non_english": 0,
                                      "not_analyzed": 0, "undetermined": 0})
    for case_slug, _, code, _, _ in rows:
        s = case_stats[case_slug]
        s["total"] += 1
        if code == "en":
            s["english"] += 1
        elif code == "not_analyzed":
            s["not_analyzed"] += 1
        elif code == "undetermined":
            s["undetermined"] += 1
        else:
            s["non_english"] += 1

    # A "clean" case = every transcript is confidently English (nothing else)
    clean_cases = sorted(
        c for c, s in case_stats.items()
        if s["total"] > 0 and s["english"] == s["total"]
    )

    # Per-case CSV
    by_case_csv = report_dir / "filter_overview_by_case.csv"
    with open(by_case_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["case", "total", "english", "non_english",
                    "not_analyzed", "undetermined", "clean"])
        for case_slug in sorted(case_stats):
            s = case_stats[case_slug]
            w.writerow([case_slug, s["total"], s["english"], s["non_english"],
                        s["not_analyzed"], s["undetermined"],
                        "yes" if case_slug in clean_cases else "no"])

    # Not-guilty-plea cases, split by clean vs potential problem
    not_guilty = sorted(
        slug for slug, plea in pleas.items()
        if plea == NOT_GUILTY_PLEA
    )
    ng_clean, ng_problem, ng_no_transcripts = [], [], []
    for slug in not_guilty:
        if slug not in case_stats:
            ng_no_transcripts.append(slug)
        elif slug in clean_cases:
            ng_clean.append(slug)
        else:
            ng_problem.append(slug)

    def problem_detail(slug):
        s = case_stats[slug]
        parts = []
        if s["non_english"]:
            parts.append(f"{s['non_english']} non-English")
        if s["not_analyzed"]:
            parts.append(f"{s['not_analyzed']} image/unanalyzable")
        if s["undetermined"]:
            parts.append(f"{s['undetermined']} undetermined")
        return ", ".join(parts)

    not_guilty_csv = report_dir / "not_guilty_cases.csv"
    with open(not_guilty_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["case", "plea", "status", "total", "english",
                    "non_english", "not_analyzed", "undetermined", "problem_detail"])
        for slug in not_guilty:
            s = case_stats.get(slug)
            if s is None:
                w.writerow([slug, pleas[slug], "no_transcripts",
                            0, 0, 0, 0, 0, ""])
            else:
                status = "clean" if slug in clean_cases else "potential_problem"
                w.writerow([slug, pleas[slug], status, s["total"], s["english"],
                            s["non_english"], s["not_analyzed"], s["undetermined"],
                            problem_detail(slug) if status == "potential_problem" else ""])

    # Human-readable summary
    lines = []
    lines.append("=" * 70)
    lines.append("ICTY TRANSCRIPT FILTER OVERVIEW")
    lines.append("=" * 70)
    lines.append(f"Total transcript files scanned: {len(rows)}")
    lines.append("")
    lines.append("BY LANGUAGE / STATUS:")
    for code, n in lang_counts.most_common():
        label = {
            "not_analyzed": "not analyzed (image/unsupported)",
            "undetermined": "undetermined (too little text)",
        }.get(code, lang_name(code))
        lines.append(f"  {label:45s} {n:6d}")

    lines.append("")
    lines.append("BY CASE (total / english / non-eng / not-analyzed / undetermined):")
    lines.append("-" * 70)
    lines.append(f"  {'case':30s} {'tot':>5s} {'eng':>5s} {'n-en':>5s} "
                 f"{'n/a':>5s} {'und':>5s}")
    for case_slug in sorted(case_stats):
        s = case_stats[case_slug]
        flag = "" if case_slug in clean_cases else "  *"
        lines.append(f"  {case_slug:30s} {s['total']:5d} {s['english']:5d} "
                     f"{s['non_english']:5d} {s['not_analyzed']:5d} "
                     f"{s['undetermined']:5d}{flag}")
    lines.append("  (* = case has at least one non-English / unanalyzable file)")

    lines.append("")
    lines.append(f"CLEAN CASES - all transcripts confidently English "
                 f"({len(clean_cases)} of {len(case_stats)}):")
    lines.append("-" * 70)
    if clean_cases:
        for case_slug in clean_cases:
            lines.append(f"  {case_slug} ({case_stats[case_slug]['total']})")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("#" * 70)
    lines.append(f"NOT-GUILTY PLEA CASES: {len(not_guilty)}")
    lines.append("#" * 70)
    lines.append("")
    lines.append(f"NOT GUILTY & CLEAN - all transcripts confidently English "
                 f"({len(ng_clean)}):")
    lines.append("-" * 70)
    if ng_clean:
        for slug in ng_clean:
            lines.append(f"  {slug} ({case_stats[slug]['total']})")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append(f"NOT GUILTY & (POTENTIAL) PROBLEM - has non-English / image / "
                 f"undetermined transcripts ({len(ng_problem)}):")
    lines.append("-" * 70)
    if ng_problem:
        for slug in ng_problem:
            lines.append(f"  {slug} ({case_stats[slug]['total']}): {problem_detail(slug)}")
    else:
        lines.append("  (none)")

    if ng_no_transcripts:
        lines.append("")
        lines.append(f"NOT GUILTY plea but no transcripts in archive ({len(ng_no_transcripts)}):")
        lines.append("-" * 70)
        for slug in ng_no_transcripts:
            lines.append(f"  {slug}")

    lines.append("")
    lines.append(f"NON-ENGLISH TRANSCRIPTS: {len(non_english)}")
    lines.append("-" * 70)
    if non_english:
        for case_slug in sorted(by_case_lang):
            counts = by_case_lang[case_slug]
            detail = ", ".join(f"{n} {lang_name(c)}" for c, n in counts.most_common())
            lines.append(f"  {case_slug:30s} {detail}")
        lines.append("")
        lines.append("FILES:")
        for case_slug, path, code, conf, _ in non_english:
            lines.append(f"  [{lang_name(code):8s} {conf:.2f}] {case_slug}/{path.name}")
    else:
        lines.append("  (none detected)")

    lines.append("")
    lines.append(f"NOT ANALYZED - IMAGE/UNSUPPORTED ({len(not_analyzed)}):")
    lines.append("-" * 70)
    if not_analyzed:
        for case_slug, path, _, _, reason in not_analyzed:
            lines.append(f"  {case_slug}/{path.name}  [{reason}]")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append(f"UNDETERMINED - TOO LITTLE TEXT ({len(undetermined)}):")
    lines.append("-" * 70)
    if undetermined:
        for case_slug, path, _, _, reason in undetermined:
            lines.append(f"  {case_slug}/{path.name}  [{reason}]")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("=" * 70)
    summary = "\n".join(lines)

    (report_dir / "filter_overview.txt").write_text(summary, encoding="utf-8")
    print("\n" + summary)
    print(f"\nNon-English CSV:   {csv_path}")
    print(f"Not-analyzed CSV:  {tif_csv}")
    print(f"Undetermined CSV:  {undet_csv}")
    print(f"By-case CSV:       {by_case_csv}")
    print(f"Not-guilty CSV:    {not_guilty_csv}")
    print(f"Summary written:   {report_dir / 'filter_overview.txt'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", nargs="+", help="Only scan these case slugs")
    ap.add_argument("--limit", type=int, help="Scan at most N files (testing)")
    ap.add_argument("--archive-dir", type=Path, default=ARCHIVE_DIR)
    ap.add_argument("--cis-dir", type=Path, default=CIS_DIR,
                    help="Parsed Case Information Sheets (for not-guilty plea filtering)")
    ap.add_argument("--output-dir", type=Path, default=REPORT_DIR)
    ap.add_argument("--batch-size", type=int, default=50,
                    help="Files per LibreOffice conversion batch")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    if not args.archive_dir.is_dir():
        logger.error(f"Archive dir not found: {args.archive_dir}")
        return 1

    rows = build_report(args.archive_dir, args.cases, args.limit, args.batch_size)
    pleas = load_pleas(args.cis_dir)
    write_outputs(rows, args.output_dir, pleas)
    return 0


if __name__ == "__main__":
    sys.exit(main())
