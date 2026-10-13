#!/usr/bin/env python3
"""
Reintegration for the classification pipeline.

Merges the LLM's per-utterance output (run.py's *_output.json) back into
the original preprocessed file (preprocessing.py's extracted.json),
producing one combined JSON where every utterance carries its final
labeled sentences -- or, for a tolerated failure, an explicit failure
marker instead.

Validation the LLM's output already went through, before this script
ever runs -- NOT repeated here, and only applicable to status="ok"
records:
- PRESPLIT mode: run.py enforced that the returned sentence ids form an
  exact match to the ids sent (no missing, no extra, no duplicates)
  before writing any output.
- WHOLE mode: run.py enforced (via
  postprocess.verify_whole_mode_reconstruction) that the returned
  sentences, concatenated in order, reconstruct the original utterance
  text exactly, before writing any output.

Tolerated failures (Settings.max_line_errors in run.py): an output
record can carry status="failed" with output.sentences=None, produced
when a permanently-failing utterance was tolerated rather than blocking
the whole document. This script passes such records through as-is --
sentences=None, error attached -- rather than attempting to join
sentences that were never generated.

What this script DOES do:
- Match utterances between the two files by unique_id, and fail loudly
  if either file has an utterance the other doesn't (defends against
  reintegrating mismatched/stale file pairs -- a cheap, different check
  from the content-integrity checks above).
- For status="ok" records:
  - PRESPLIT mode: join each {id, label} pair from the LLM output
    against the source utterance's {id, text, start_char, end_char}
    from the preprocessed file, since the LLM never saw or returned
    sentence text in this mode by design.
  - WHOLE mode: the LLM output already carries {text, label}; this
    script additionally recovers {start_char, end_char} for each
    sentence by locating it in the utterance text (the same
    sequential-match logic postprocess.verify_whole_mode_reconstruction
    already proved works), so both modes end up in the same final
    per-sentence shape: {id, text, start_char, end_char, label}.
- For status="failed" records: pass through as a record with
  sentences=None and the original error, no join attempted.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

from helpers import load_json, save_json


# ======================================================================
# Loading / matching
# ======================================================================

def _index_by_unique_id(records: List[Dict[str, Any]], *, what: str) -> Dict[str, Dict[str, Any]]:
    """Build a unique_id -> record index, rejecting duplicates."""
    index: Dict[str, Dict[str, Any]] = {}
    for r in records:
        uid = r.get("unique_id")
        if not uid:
            raise ValueError(f"A {what} record has no unique_id: {r!r}")
        if uid in index:
            raise ValueError(f"Duplicate unique_id in {what}: {uid}")
        index[uid] = r
    return index


def _validate_matching_ids(
    extracted_ids: set,
    output_ids: set,
) -> None:
    missing_from_output = extracted_ids - output_ids
    extra_in_output = output_ids - extracted_ids

    if missing_from_output or extra_in_output:
        raise ValueError(
            "The preprocessed file and the LLM output file do not "
            "cover the same set of utterances -- refusing to "
            "reintegrate a mismatched pair.\n"
            f"  In preprocessed file but missing from LLM output "
            f"({len(missing_from_output)}): {sorted(missing_from_output)[:10]}"
            f"{' ...' if len(missing_from_output) > 10 else ''}\n"
            f"  In LLM output but not in preprocessed file "
            f"({len(extra_in_output)}): {sorted(extra_in_output)[:10]}"
            f"{' ...' if len(extra_in_output) > 10 else ''}"
        )


# ======================================================================
# Per-mode sentence joining (status="ok" records only)
# ======================================================================

def _join_presplit_sentences(
    utterance: Dict[str, Any],
    output_sentences: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Join {id, label} pairs from the LLM output against the source
    utterance's {id, text, start_char, end_char}."""
    source_by_id = {s["id"]: s for s in utterance.get("sentences", [])}

    joined: List[Dict[str, Any]] = []
    for out_sent in output_sentences:
        sid = out_sent["id"]
        source = source_by_id.get(sid)
        if source is None:
            raise ValueError(
                f"{utterance['unique_id']}: LLM output references sentence "
                f"id {sid!r} which does not exist in the preprocessed "
                f"utterance's sentences. This should be impossible if "
                f"run.py's id-completeness check passed -- the two files "
                f"likely do not belong together."
            )
        joined.append({
            "id": sid,
            "text": source["text"],
            "start_char": source["start_char"],
            "end_char": source["end_char"],
            "label": out_sent["label"],
        })

    return joined


def _join_whole_sentences(
    utterance: Dict[str, Any],
    output_sentences: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Recover {start_char, end_char} for each {text, label} pair by
    locating it in the utterance text, same sequential-match approach
    postprocess.verify_whole_mode_reconstruction already validated.
    Assigns sequential ids (s0, s1, ...) so WHOLE mode output ends up
    in the same shape as PRESPLIT mode."""
    original_text = utterance["text"]
    cursor = 0
    joined: List[Dict[str, Any]] = []

    for i, out_sent in enumerate(output_sentences):
        text = out_sent["text"]
        idx = original_text.find(text, cursor)
        if idx == -1:
            raise ValueError(
                f"{utterance['unique_id']}: sentence {i} ({text!r}) not "
                f"found verbatim in the utterance text at or after "
                f"position {cursor}. This should be impossible if "
                f"run.py's reconstruction check passed -- the two files "
                f"likely do not belong together."
            )
        start_char = idx
        end_char = idx + len(text)
        joined.append({
            "id": f"s{i}",
            "text": text,
            "start_char": start_char,
            "end_char": end_char,
            "label": out_sent["label"],
        })
        cursor = end_char

    return joined


# ======================================================================
# Main reintegration
# ======================================================================

def reintegrate(
    extracted: Dict[str, Any],
    output_records: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Merge LLM output back into the preprocessed structure.

    Returns a new dict with the same top-level shape as the preprocessed
    file (doc_id, mode, utterances), each utterance now carrying:
    - status: "ok" or "failed"
    - sentences: [{id, text, start_char, end_char, label}, ...] when ok,
      None when failed
    - error: present only when status="failed", carrying the original
      error_type / error_message / finish_reason from run.py
    """
    mode = extracted.get("mode")
    if mode not in ("whole", "presplit"):
        raise ValueError(f"Preprocessed file has invalid mode: {mode!r}")

    utterances = extracted.get("utterances")
    if not isinstance(utterances, list):
        raise ValueError("Preprocessed file must contain an 'utterances' list.")

    extracted_by_id = _index_by_unique_id(utterances, what="preprocessed file")
    output_by_id = _index_by_unique_id(output_records, what="LLM output file")

    _validate_matching_ids(set(extracted_by_id), set(output_by_id))

    join_fn = _join_presplit_sentences if mode == "presplit" else _join_whole_sentences

    result_utterances: List[Dict[str, Any]] = []
    for uid, utterance in extracted_by_id.items():
        output_record = output_by_id[uid]
        status = output_record.get("status", "ok")

        base = {
            "unique_id": uid,
            "doc_id": extracted.get("doc_id"),
            "mode": mode,
            "text": utterance["text"],
            "previous_utterance": utterance.get("previous_utterance"),
            "following_utterance": utterance.get("following_utterance"),
        }

        if status == "failed":
            base["status"] = "failed"
            base["sentences"] = None
            base["error"] = output_record.get("error", {})
            result_utterances.append(base)
            continue

        output_sentences = output_record.get("output", {}).get("sentences", [])

        base["status"] = "ok"
        base["sentences"] = join_fn(utterance, output_sentences)
        result_utterances.append(base)

    # Preserve source order rather than dict-insertion order.
    order = [u["unique_id"] for u in utterances]
    result_utterances.sort(key=lambda u: order.index(u["unique_id"]))

    failed_count = sum(1 for u in result_utterances if u["status"] == "failed")

    return {
        "doc_id": extracted.get("doc_id"),
        "mode": mode,
        "utterances": result_utterances,
        "failed_count": failed_count,
    }


# ======================================================================
# CLI
# ======================================================================

def _auto_locate_from_run_dir(run_dir: Path) -> tuple[Path, Path]:
    """Given a pipeline.py run directory, locate extracted.json and its
    matching *_output.json using the fixed 01_preprocessing/02_llm
    layout."""
    extracted_file = run_dir / "01_preprocessing" / "extracted.json"
    if not extracted_file.exists():
        raise SystemExit(f"Expected preprocessed file not found: {extracted_file}")

    output_file = run_dir / "02_llm" / f"{extracted_file.stem}_output.json"
    if not output_file.exists():
        raise SystemExit(
            f"Expected LLM output file not found: {output_file}\n"
            f"(Has stage 02 completed successfully for this run?)"
        )

    return extracted_file, output_file


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Merge LLM classification output back into the preprocessed file."
    )
    ap.add_argument(
        "--run-dir", type=str,
        help="A pipeline.py run directory -- auto-locates extracted.json and *_output.json within it.",
    )
    ap.add_argument("--extracted_file", type=str, help="Preprocessed file (overrides --run-dir)")
    ap.add_argument("--llm_output_file", type=str, help="LLM *_output.json file (overrides --run-dir)")
    ap.add_argument("--out_file", type=str, help="Where to write the reintegrated JSON")

    args = ap.parse_args()

    if args.extracted_file and args.llm_output_file:
        extracted_file = Path(args.extracted_file)
        output_file = Path(args.llm_output_file)
    elif args.run_dir:
        extracted_file, output_file = _auto_locate_from_run_dir(Path(args.run_dir))
    else:
        raise SystemExit(
            "Provide --run-dir, or both --extracted_file and --llm_output_file."
        )

    if args.out_file:
        out_path = Path(args.out_file)
    elif args.run_dir:
        out_path = Path(args.run_dir) / "03_reintegration" / "reintegrated.json"
    else:
        out_path = extracted_file.with_name(f"{extracted_file.stem}_reintegrated.json")

    extracted = load_json(str(extracted_file))
    output_records = load_json(str(output_file))

    if not isinstance(output_records, list):
        raise SystemExit(
            f"{output_file} must contain a JSON list of output records, "
            f"got {type(output_records).__name__}."
        )

    result = reintegrate(extracted, output_records)

    save_json(result, str(out_path))

    n = len(result["utterances"])
    n_failed = result.get("failed_count", 0)
    n_sentences = sum(
        len(u["sentences"]) for u in result["utterances"] if u["sentences"]
    )
    print(
        f"Reintegrated {n} utterance(s) ({n_failed} failed), "
        f"{n_sentences} sentence(s) [mode={result['mode']}] -> {out_path}"
    )


if __name__ == "__main__":
    main()
