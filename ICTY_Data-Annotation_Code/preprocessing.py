#!/usr/bin/env python3
"""
Preprocessing for the sentence-level binary classification pipeline.

Reads a JSON file (or directory of JSON files) of utterance-transcript
records and extracts, per utterance, exactly what the LLM step needs:

- unique_id            -> carried through for RE-INTEGRATION ONLY. Never a
                           labeling target itself.
- text                  -> the utterance to be tagged.
- previous_utterance    -> CONTEXT ONLY, never tagged.
- following_utterance   -> CONTEXT ONLY, never tagged.

Two extraction modes, selected with --presplit-sentences:

1. WHOLE mode (default). The full utterance text is handed to the LLM
   as-is. No "sentences" key is added. The LLM is expected to find
   sentence boundaries and tag each sentence itself. Try this first.

2. PRESPLIT mode (--presplit-sentences). Utterances are split into
   sentences here, deterministically, and each sentence gets a short,
   utterance-local id (s0, s1, ...) plus character offsets into the
   utterance text. The LLM is then only ever asked to return one label
   per given id -- never to re-emit or re-segment text itself. Use this
   as the fallback if whole-utterance tagging proves unreliable; it is
   the mechanically safer mode since reintegration becomes an exact-ID
   lookup instead of fuzzy text matching.

Both modes produce the same top-level structure, differing only in
whether each utterance object carries a "sentences" list, so run.py only
needs to branch on that key's presence.

Input formats accepted:
    1) A bare JSON list of utterance records (as in the source transcript
       files).
    2) A JSON object with the list under one of a few common keys
       ("utterances", "data", "records").
Dependencies: none (standard library only).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# Sentence splitting (heuristic, dependency-free)
# ============================================================

# Words that commonly precede a period without ending a sentence.
# Matched case-insensitively against the token immediately before the
# period. Extend this list if the test run reveals more false splits.
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st",
    "gen", "col", "capt", "lt", "sgt", "rev", "hon",
    "judge", "justice", "no", "vs", "etc", "fig", "vol",
    "pp", "co", "inc", "ltd", "art", "para", "eg", "ie",
}

# A sentence boundary candidate: one or more terminal punctuation marks,
# optionally followed by a closing quote/bracket, then whitespace.
_BOUNDARY_RE = re.compile(r'[.!?]+[\'")\]]*\s+')

_WORD_BEFORE_RE = re.compile(r'([A-Za-z]+)\.?$')


def _is_abbreviation(text: str, boundary_start: int) -> bool:
    """Check whether the token right before a candidate boundary is a
    known abbreviation (so the boundary should NOT be treated as a
    sentence end)."""
    preceding = text[:boundary_start]
    m = _WORD_BEFORE_RE.search(preceding)
    if not m:
        return False
    return m.group(1).lower() in _ABBREVIATIONS


def _is_decimal_number(text: str, period_pos: int) -> bool:
    """Check whether a '.' at period_pos sits inside a number like '3.5'."""
    if period_pos <= 0 or period_pos + 1 >= len(text):
        return False
    return text[period_pos - 1].isdigit() and text[period_pos + 1].isdigit()


def split_into_sentences(text: str) -> List[Dict[str, Any]]:
    """
    Split text into sentences, returning a list of
    {"text": ..., "start_char": ..., "end_char": ...} in document order.

    Offsets are relative to the ORIGINAL (unstripped) input string, with
    each sentence's own leading/trailing whitespace excluded from the
    returned span.

    Heuristic and dependency-free: guards against common title
    abbreviations (Dr., Mr., ...) and decimal numbers, but is not a full
    NLP sentence tokenizer. Spot-check output on real data.
    """
    if not text or not text.strip():
        return []

    sentences: List[Dict[str, Any]] = []
    seg_start = 0

    for m in _BOUNDARY_RE.finditer(text):
        boundary_start = m.start()

        # Skip decimal points (e.g. "3.5") -- only relevant for the
        # single-'.' case, cheap to check regardless.
        if text[boundary_start] == "." and _is_decimal_number(text, boundary_start):
            continue

        # Skip known abbreviations (Dr., Mr., etc.) -- only applies to a
        # trailing single '.', not '!'/'?' or multi-char punctuation runs.
        if m.group(0).lstrip()[0:1] == "." and _is_abbreviation(text, boundary_start):
            continue

        seg_end = m.end()
        raw_segment = text[seg_start:seg_end]
        stripped = raw_segment.strip()
        if stripped:
            lstrip_offset = len(raw_segment) - len(raw_segment.lstrip())
            abs_start = seg_start + lstrip_offset
            abs_end = abs_start + len(stripped)
            sentences.append(
                {"text": stripped, "start_char": abs_start, "end_char": abs_end}
            )
        seg_start = seg_end

    tail_raw = text[seg_start:]
    tail_stripped = tail_raw.strip()
    if tail_stripped:
        lstrip_offset = len(tail_raw) - len(tail_raw.lstrip())
        abs_start = seg_start + lstrip_offset
        abs_end = abs_start + len(tail_stripped)
        sentences.append(
            {"text": tail_stripped, "start_char": abs_start, "end_char": abs_end}
        )

    return sentences


def assign_sentence_ids(sentences: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach short, utterance-local ids ('s0', 's1', ...) in order."""
    for i, sent in enumerate(sentences):
        sent["id"] = f"s{i}"
    return sentences


# ============================================================
# Record loading / normalization
# ============================================================

_LIST_KEYS = ("utterances_list", "utterances", "data", "records")


def load_records(path: Path) -> List[Dict[str, Any]]:
    """Load utterance records from a JSON file.

    Accepts either a bare list, or a dict wrapping the list under one of
    a few common keys.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in _LIST_KEYS:
            if key in data and isinstance(data[key], list):
                return data[key]
        raise ValueError(
            f"{path.name}: dict input but no list found under any of "
            f"{_LIST_KEYS}. Pass --list-key to specify explicitly."
        )

    raise ValueError(
        f"{path.name}: expected a JSON list or an object wrapping one, "
        f"got {type(data).__name__}."
    )


def extract_utterance_object(
    record: Dict[str, Any],
    *,
    presplit: bool,
) -> Dict[str, Any]:
    """Build the lean per-utterance object the LLM step consumes.

    Only pulls: unique_id (reintegration key), the utterance text, and
    previous/following utterance text for context. Everything else in
    the source record (case metadata, speakers, role, type, ...) is
    intentionally dropped here -- add fields back if a later prompt
    design needs them.
    """
    unique_id = record.get("unique-id") or record.get("unique_id")
    if not unique_id:
        raise ValueError(
            f"Record for utterance_id={record.get('utterance_id')!r} has "
            f"no 'unique-id' -- cannot reintegrate without it."
        )

    text = record.get("utterances", "")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"Record {unique_id!r} has empty/missing 'utterances' text.")

    obj: Dict[str, Any] = {
        "unique_id": unique_id,
        "text": text,
        "previous_utterance": record.get("previous_utterance"),
        "following_utterance": record.get("following_utterance"),
    }

    if presplit:
        sentences = assign_sentence_ids(split_into_sentences(text))
        obj["sentences"] = sentences

    return obj


# ============================================================
# Selection helpers (for testing on a subset, e.g. 3 utterances)
# ============================================================

def _parse_id_list_arg(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def select_records(
    records: List[Dict[str, Any]],
    *,
    unique_ids: Optional[List[str]] = None,
    random_sample: Optional[int] = None,
    random_seed: int = 42,
    limit: Optional[int] = None,
    skip: int = 0,
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Apply optional selection. Returns (selected_records, sampling_info).

    sampling_info is None unless --random_sample was used, in which case
    it records the seed/count/pool size for reproducibility.
    """
    modes_used = sum([bool(unique_ids), random_sample is not None, bool(limit or skip)])
    if modes_used > 1:
        raise ValueError(
            "Use only one selection mode at a time: --unique_ids OR "
            "--random_sample OR --limit/--skip."
        )

    if unique_ids:
        wanted = set(unique_ids)
        selected = [r for r in records if (r.get("unique-id") or r.get("unique_id")) in wanted]
        missing = wanted - {(r.get("unique-id") or r.get("unique_id")) for r in selected}
        if missing:
            raise ValueError(f"unique_id(s) not found in input: {sorted(missing)}")
        return selected, None

    if random_sample is not None:
        if random_sample > len(records):
            raise ValueError(
                f"--random_sample {random_sample} exceeds available record "
                f"count {len(records)}."
            )
        rng = random.Random(random_seed)
        selected = rng.sample(records, random_sample)
        sampling_info = {
            "seed": random_seed,
            "n_sampled": random_sample,
            "n_pool": len(records),
        }
        return selected, sampling_info

    selected = records[skip:]
    if limit is not None:
        selected = selected[:limit]
    return selected, None


# ============================================================
# Per-file processing
# ============================================================

def process_file(
    in_path: Path,
    *,
    presplit: bool,
    unique_ids: Optional[List[str]] = None,
    random_sample: Optional[int] = None,
    random_seed: int = 42,
    limit: Optional[int] = None,
    skip: int = 0,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Returns (output_data, metadata). metadata carries run/selection info
    that does NOT belong in the LLM-facing output JSON (e.g. sampling seed)."""
    records = load_records(in_path)
    records, sampling_info = select_records(
        records,
        unique_ids=unique_ids,
        random_sample=random_sample,
        random_seed=random_seed,
        limit=limit,
        skip=skip,
    )

    utterances = [extract_utterance_object(r, presplit=presplit) for r in records]

    output_data: Dict[str, Any] = {
        "doc_id": in_path.stem,
        "mode": "presplit" if presplit else "whole",
        "utterances": utterances,
    }

    metadata: Dict[str, Any] = {
        "input_file": str(in_path),
        "doc_id": in_path.stem,
        "mode": output_data["mode"],
        "n_utterances_extracted": len(utterances),
        "sampling": sampling_info,  # None unless --random_sample was used
    }

    return output_data, metadata


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ============================================================
# CLI
# ============================================================

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Extract utterance + context data for the classification LLM step."
    )
    ap.add_argument("--in_file", type=str, help="Single input JSON file")
    ap.add_argument("--out_file", type=str, help="Output JSON file (with --in_file)")
    ap.add_argument(
        "--metadata_file",
        type=str,
        default=None,
        help=(
            "Where to write run metadata (selection mode, sampling seed, "
            "counts). Default: <out_file stem>_metadata.json alongside "
            "the output. Never embedded in the LLM-facing output JSON."
        ),
    )
    ap.add_argument("--in_dir", type=str, help="Directory of input JSON files")
    ap.add_argument("--out_dir", type=str, help="Output directory (with --in_dir)")
    ap.add_argument("--glob", type=str, default="*.json", help="Glob for --in_dir (default: *.json)")

    ap.add_argument(
        "--presplit-sentences",
        action="store_true",
        help=(
            "Pre-split each utterance into sentences with local ids "
            "(s0, s1, ...) before sending to the LLM. Default: off -- "
            "the whole utterance is sent as-is."
        ),
    )

    ap.add_argument(
        "--unique_ids",
        type=str,
        default=None,
        help='Comma-separated exact unique-id values to select, e.g. for a small test run',
    )
    ap.add_argument(
        "--random_sample",
        type=int,
        default=None,
        help="Randomly select N records (e.g. 200 out of 18000). Mutually exclusive with --unique_ids/--limit/--skip.",
    )
    ap.add_argument(
        "--random_seed",
        type=int,
        default=42,
        help="Seed for --random_sample, for reproducibility (default: 42)",
    )
    ap.add_argument("--limit", type=int, default=None, help="Only take the first N selected records")
    ap.add_argument("--skip", type=int, default=0, help="Skip the first N records before selecting")

    args = ap.parse_args()

    unique_ids = _parse_id_list_arg(args.unique_ids)

    if args.in_file:
        in_path = Path(args.in_file)
        if not in_path.exists():
            raise SystemExit(f"Input file not found: {in_path}")

        out_path = Path(args.out_file) if args.out_file else in_path.with_name(
            f"{in_path.stem}_extracted.json"
        )

        output_data, metadata = process_file(
            in_path,
            presplit=args.presplit_sentences,
            unique_ids=unique_ids,
            random_sample=args.random_sample,
            random_seed=args.random_seed,
            limit=args.limit,
            skip=args.skip,
        )
        write_json(out_path, output_data)

        metadata_path = (
            Path(args.metadata_file) if args.metadata_file
            else out_path.with_name(f"{out_path.stem}_metadata.json")
        )
        write_json(metadata_path, metadata)

        sample_note = (
            f" (random sample, seed={metadata['sampling']['seed']}, "
            f"pool={metadata['sampling']['n_pool']})"
            if metadata["sampling"] else ""
        )
        print(
            f"Wrote {len(output_data['utterances'])} utterance object(s) "
            f"[mode={output_data['mode']}]{sample_note} -> {out_path}"
        )
        print(f"Wrote run metadata -> {metadata_path}")
        return

    if args.in_dir:
        if not args.out_dir:
            raise SystemExit("--out_dir is required when using --in_dir")

        in_dir = Path(args.in_dir)
        out_dir = Path(args.out_dir)
        files = sorted(in_dir.glob(args.glob))
        if not files:
            raise SystemExit(f"No files matched {args.glob} in {in_dir}")

        total = 0
        per_file_metadata = []
        for p in files:
            try:
                output_data, metadata = process_file(
                    p,
                    presplit=args.presplit_sentences,
                    unique_ids=unique_ids,
                    random_sample=args.random_sample,
                    random_seed=args.random_seed,
                    limit=args.limit,
                    skip=args.skip,
                )
                out_path = out_dir / f"{p.stem}_extracted.json"
                write_json(out_path, output_data)
                metadata["output_file"] = str(out_path)
                per_file_metadata.append(metadata)
                total += len(output_data["utterances"])
                sample_note = (
                    f" (random sample, seed={metadata['sampling']['seed']})"
                    if metadata["sampling"] else ""
                )
                print(
                    f"[OK] {p.name}: {len(output_data['utterances'])} utterance(s) "
                    f"[mode={output_data['mode']}]{sample_note} -> {out_path.name}"
                )
            except Exception as exc:
                per_file_metadata.append({"input_file": str(p), "status": "failed", "error": str(exc)})
                print(f"[FAIL] {p.name}: {exc}")

        metadata_path = (
            Path(args.metadata_file) if args.metadata_file
            else out_dir / "run_metadata.json"
        )
        write_json(metadata_path, {"files": per_file_metadata, "total_utterances_extracted": total})

        print(f"Done. Total utterance objects extracted: {total}")
        print(f"Wrote run metadata -> {metadata_path}")
        return

    raise SystemExit("Provide either --in_file or --in_dir.")


if __name__ == "__main__":
    main()
