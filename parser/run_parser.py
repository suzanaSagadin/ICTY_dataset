#!/usr/bin/env python3
"""
CLI entry point for the ICTY Transcript Parser.

Usage:
    python run_parser.py                          # Parse all cases
    python run_parser.py --cases mladic karadzic   # Parse specific cases
    python run_parser.py --limit 10                # Parse first 10 files only
    python run_parser.py --workers 2               # Use 2 LibreOffice workers
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from tqdm import tqdm

from icty_parser import (
    ARCHIVE_DIR,
    convert_batch_to_text,
    discover_files,
    process_file,
)

PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "data" / "parsed"
TXT_DIR = PROJECT_ROOT / "data" / "converted_txt"
LOG_DIR = PROJECT_ROOT / "logs"


def setup_logging():
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_DIR / "parse_errors.log"),
            logging.StreamHandler(),
        ],
    )


def main():
    parser = argparse.ArgumentParser(description="Parse ICTY transcripts to JSONL")
    parser.add_argument("--cases", nargs="+", help="Only parse these case slugs")
    parser.add_argument("--limit", type=int, help="Max files to process")
    parser.add_argument("--workers", type=int, default=4,
                        help="Number of parallel LibreOffice workers (default: 4)")
    parser.add_argument("--batch-size", type=int, default=50,
                        help="Files per LibreOffice batch (default: 50)")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR,
                        help="Output directory for JSONL files")
    parser.add_argument("--archive-dir", type=Path, default=ARCHIVE_DIR,
                        help="Path to icty_archive directory")
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger(__name__)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Discover files
    logger.info("Discovering transcript files...")
    file_list = discover_files(args.archive_dir, args.cases)
    if args.limit:
        file_list = file_list[:args.limit]
    logger.info(f"Found {len(file_list)} eligible transcript files")

    if not file_list:
        logger.error("No files found. Check --archive-dir and --cases.")
        sys.exit(1)

    # Step 2+3: Convert and parse in batches (so output appears progressively)
    logger.info(f"Converting and parsing {len(file_list)} files "
                f"(batch size {args.batch_size})...")
    start_time = time.time()

    stats = {
        "total_files": len(file_list),
        "converted": 0,
        "conversion_failed": 0,
        "parsed": 0,
        "parse_failed": 0,
        "total_utterances": 0,
        "total_questions": 0,
        "total_answers": 0,
        "total_answers_linked": 0,
        "total_procedural_notes": 0,
        "cases_processed": 0,
    }

    # Process in batches: convert batch → parse batch → write output → next batch
    batches = [file_list[i:i + args.batch_size]
               for i in range(0, len(file_list), args.batch_size)]
    cases_seen = set()
    pbar = tqdm(total=len(file_list), desc="Converting & parsing")

    txt_dir = TXT_DIR
    txt_dir.mkdir(parents=True, exist_ok=True)

    for batch in batches:
        texts = convert_batch_to_text(batch, txt_dir)
        stats["converted"] += len(texts)
        stats["conversion_failed"] += len(batch) - len(texts)

        for case_slug, doc_path in batch:
            if doc_path not in texts:
                stats["parse_failed"] += 1
                pbar.update(1)
                continue

            text = texts[doc_path]
            try:
                result = process_file(case_slug, doc_path, text)
            except Exception as e:
                logger.error(f"Parse error in {doc_path.name}: {e}")
                stats["parse_failed"] += 1
                pbar.update(1)
                continue

            if result is None:
                stats["parse_failed"] += 1
                pbar.update(1)
                continue

            # Write one pretty JSON file per transcript, in case folder
            case_dir = args.output_dir / case_slug
            case_dir.mkdir(parents=True, exist_ok=True)
            out_name = doc_path.stem + ".json"
            outpath = case_dir / out_name
            with open(outpath, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            if case_slug not in cases_seen:
                cases_seen.add(case_slug)
                stats["cases_processed"] += 1

            # Collect stats
            stats["parsed"] += 1
            stats["total_utterances"] += len(result["utterances"])
            stats["total_procedural_notes"] += len(result["procedural_notes"])
            for utt in result["utterances"]:
                if utt["type"] == "question":
                    stats["total_questions"] += 1
                elif utt["type"] == "answer":
                    stats["total_answers"] += 1
                    if utt.get("reply_to") is not None:
                        stats["total_answers_linked"] += 1

            pbar.update(1)

    pbar.close()

    # Write quality report
    parse_time = time.time() - start_time
    stats["total_time_s"] = round(parse_time, 1)

    if stats["total_answers"] > 0:
        stats["qa_link_rate"] = round(
            stats["total_answers_linked"] / stats["total_answers"] * 100, 1
        )
    else:
        stats["qa_link_rate"] = 0.0

    report_path = LOG_DIR / "parse_report.json"
    with open(report_path, "w") as f:
        json.dump(stats, f, indent=2)

    # Print summary
    print("\n" + "=" * 60)
    print("ICTY Transcript Parser — Summary")
    print("=" * 60)
    print(f"  Files found:        {stats['total_files']}")
    print(f"  Converted to text:  {stats['converted']}")
    print(f"  Parsed:             {stats['parsed']}")
    print(f"  Failed:             {stats['parse_failed']}")
    print(f"  Cases:              {stats['cases_processed']}")
    print(f"  Total utterances:   {stats['total_utterances']}")
    print(f"  Questions:          {stats['total_questions']}")
    print(f"  Answers:            {stats['total_answers']}")
    print(f"  Q/A link rate:      {stats['qa_link_rate']}%")
    print(f"  Procedural notes:   {stats['total_procedural_notes']}")
    print(f"  Total time:         {stats['total_time_s']}s")
    print(f"  Report:             {report_path}")
    print(f"  Output:             {args.output_dir}/")
    print("=" * 60)


if __name__ == "__main__":
    main()
