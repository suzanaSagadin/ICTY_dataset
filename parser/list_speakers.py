#!/usr/bin/env python3
"""
List all unique speakers per case with their roles and utterance counts.

Usage:
    python list_speakers.py --parsed-dir data_250625/transcripts_parsed
    python list_speakers.py --parsed-dir data_250625/transcripts_parsed --case tadic
    python list_speakers.py --csv ../data_250625/reports/speakers_all.csv --recheck-accused
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_PARSED_DIR = PROJECT_ROOT / "data_250625" / "transcripts_parsed"


def collect_speakers(parsed_dir, cases=None, accused_by_case=None):
    """Collect all unique (speaker, role) pairs per case with counts.

    If accused_by_case is provided, re-checks is_accused on the fly
    instead of relying on the stored value in the JSON.
    """
    from icty_parser import check_is_accused

    results = {}

    for case_dir in sorted(parsed_dir.iterdir()):
        if not case_dir.is_dir():
            continue
        if cases and case_dir.name not in cases:
            continue

        accused_info = accused_by_case.get(case_dir.name) if accused_by_case else None
        speakers = defaultdict(lambda: {"count": 0, "is_accused": False, "roles": defaultdict(int)})

        for json_file in case_dir.glob("*.json"):
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for utt in data.get("utterances", []):
                speaker = utt.get("speaker", "")
                role = utt.get("role", "unknown")
                speakers[speaker]["count"] += 1
                speakers[speaker]["roles"][role] += 1

                if not speakers[speaker]["is_accused"]:
                    if accused_by_case is not None:
                        speakers[speaker]["is_accused"] = check_is_accused(speaker, accused_info)
                    else:
                        speakers[speaker]["is_accused"] = utt.get("is_accused", False)

        results[case_dir.name] = speakers

    return results


def print_table(results):
    """Print results as a readable table."""
    for case, speakers in results.items():
        print(f"\n{'=' * 80}")
        print(f"  {case} ({len(speakers)} unique speakers)")
        print(f"{'=' * 80}")
        print(f"  {'Speaker':<40} {'Role':<15} {'Count':>8}")
        print(f"  {'-' * 40} {'-' * 15} {'-' * 8}")

        for speaker, info in sorted(
            speakers.items(), key=lambda x: -x[1]["count"]
        ):
            roles_str = ", ".join(
                f"{r}({c})" for r, c in sorted(
                    info["roles"].items(), key=lambda x: -x[1]
                )
            )
            print(f"  {speaker:<40} {roles_str:<15} {info['count']:>8}")


def write_csv(results, output_path):
    """Write results to CSV."""
    import csv as csv_mod
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv_mod.writer(f)
        writer.writerow(["case", "speaker", "role", "count", "is_accused"])
        for case, speakers in results.items():
            for speaker, info in sorted(
                speakers.items(), key=lambda x: -x[1]["count"]
            ):
                for role, count in sorted(
                    info["roles"].items(), key=lambda x: -x[1]
                ):
                    writer.writerow([case, speaker, role, count, int(info["is_accused"])])


def main():
    parser = argparse.ArgumentParser(
        description="List all speakers per case with roles"
    )
    parser.add_argument(
        "--parsed-dir", type=Path, default=DEFAULT_PARSED_DIR,
        help="Directory with parsed transcript JSONs"
    )
    parser.add_argument(
        "--case", nargs="+",
        help="Only show these cases"
    )
    parser.add_argument(
        "--csv", type=Path,
        help="Write results to CSV file"
    )
    parser.add_argument(
        "--recheck-accused", action="store_true",
        help="Re-check is_accused using pleas.csv + case_mapping.csv instead of stored values"
    )
    parser.add_argument(
        "--pleas", type=Path,
        default=PROJECT_ROOT / "data_250625" / "cis_parsed" / "pleas.csv",
        help="Path to pleas.csv (used with --recheck-accused)"
    )
    parser.add_argument(
        "--case-mapping", type=Path,
        default=PROJECT_ROOT / "scraper" / "case_mapping.csv",
        help="Path to case_mapping.csv (used with --recheck-accused)"
    )
    args = parser.parse_args()

    accused_by_case = None
    if args.recheck_accused:
        from icty_parser import load_accused_names
        mapping_path = args.case_mapping if args.case_mapping.exists() else None
        accused_by_case = load_accused_names(args.pleas, mapping_path)
        print(f"Loaded accused names for {len(accused_by_case)} cases")

    results = collect_speakers(args.parsed_dir, args.case, accused_by_case)

    if args.csv:
        write_csv(results, args.csv)
        print(f"Written to {args.csv}")
        total_speakers = sum(len(s) for s in results.values())
        print(f"Total: {len(results)} cases, {total_speakers} unique speakers")
    else:
        print_table(results)


if __name__ == "__main__":
    main()
