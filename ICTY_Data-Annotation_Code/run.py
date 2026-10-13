"""
Classification pipeline entry point.

Processes either:
- all JSON files in an input directory, or
- one single JSON input file

through the LLM, and saves output per file.

Expected input JSON structure per file (as produced by preprocessing.py):

{
  "doc_id": "...",
  "mode": "whole" | "presplit",
  "utterances": [...]
}

Unlike the NER pipeline, there is no document-level reconciliation step
after per-utterance processing (no wordbreak-style boundary problem
exists here).

Failure tolerance (Settings.max_line_errors):
- None (default): zero tolerance. Any utterance that permanently fails
  (after exhausting the parser's fixer/retry logic) leaves the whole
  document incomplete -- no *_output.json is written, matching the
  original strict behavior.
- N >= 0: up to N permanently-failing utterances per document are
  tolerated. The document still completes and a *_output.json is
  written, with each failed utterance represented by an explicit
  placeholder record (status="failed", sentences=None) rather than
  being silently omitted -- so downstream consumers can see exactly
  which utterances have no result and why. Full failure details
  (unique_id, error type/message, finish_reason where available) are
  additionally written to a companion *_failed.json file for
  traceability. Utterances that were never attempted at all (e.g.
  continue_on_error=False stopped the loop early) do NOT count as
  tolerated failures -- that still leaves the document incomplete.

  Tolerated failures are never written into the checkpoint's "records"
  list -- only real successes are. This means a later --resume run
  naturally retries exactly the previously-failed utterances, and can
  upgrade a tolerated-complete document into a fully-complete one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from settings import Settings
from models.classification import (
    PresplitClassificationOutput,
    WholeClassificationOutput,
)
from prompts.classification import build_system_prompt, build_user_message
from provider import call_llm, stop_server, DHInfraTruncatedResponse
from parser import parse_and_validate
from postprocess import (
    build_output_utterance,
    build_failed_output_utterance,
    verify_whole_mode_reconstruction,
)
from helpers import save_raw, save_prompt, load_json, save_json


def _get_run_output_dir(
    settings: Settings,
    explicit_run_dir: Optional[str] = None,
) -> str:
    """
    Create and return the processing output directory.

    When explicit_run_dir is supplied, that exact directory is used. Without
    it, the original standalone timestamped behavior is preserved.
    """
    if explicit_run_dir:
        run_dir = Path(explicit_run_dir)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        model_name = settings.llm.display_name
        run_dir = Path(settings.output_dir) / model_name / f"processing_{timestamp}"

    run_dir.mkdir(parents=True, exist_ok=True)
    return str(run_dir)


def _find_input_files(input_dir: str) -> List[Path]:
    """Find all JSON files in the input directory, sorted by name."""
    d = Path(input_dir)

    if not d.is_dir():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    files = sorted(d.glob("*.json"))

    if not files:
        raise FileNotFoundError(f"No .json files found in: {input_dir}")

    return files


def _resolve_input_files(args: argparse.Namespace, settings: Settings) -> List[Path]:
    """
    Resolve input source.

    Supports:
    - --input-dir: process all .json files in a directory
    - --input-file: process one specific .json file
    """
    if args.input_file:
        input_file = Path(args.input_file)

        if not input_file.is_file():
            raise FileNotFoundError(f"Input file not found: {input_file}")

        if input_file.suffix.lower() != ".json":
            raise ValueError(f"Input file must be a .json file: {input_file}")

        return [input_file]

    input_dir = args.input_dir or settings.input_dir
    return _find_input_files(input_dir)


def _load_input_payload(
    input_file: Path,
) -> Tuple[Optional[str], str, List[Dict[str, Any]]]:
    """
    Load one preprocessed input JSON file.

    Expected structure (see preprocessing.py):
        {"doc_id": ..., "mode": "whole" | "presplit", "utterances": [...]}

    Returns:
        (doc_id, mode, utterances)
    """
    data = load_json(str(input_file))

    if not isinstance(data, dict):
        raise ValueError(
            f"Unsupported JSON structure in {input_file.name}: "
            f"expected an object, got {type(data).__name__}"
        )

    doc_id = data.get("doc_id")
    mode = data.get("mode")
    utterances = data.get("utterances")

    if mode not in ("whole", "presplit"):
        raise ValueError(
            f"Input file {input_file.name} has invalid 'mode' "
            f"(expected 'whole' or 'presplit', got {mode!r})."
        )

    if not isinstance(utterances, list):
        raise ValueError(
            f"Input file {input_file.name} must contain an 'utterances' list."
        )

    return doc_id, mode, utterances


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 checksum of one input file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_save_json(data: Any, path: Path) -> None:
    """Atomically replace a JSON file so an interruption cannot corrupt it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")

    with tmp_path.open("w", encoding="utf-8", newline="") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())

    os.replace(tmp_path, path)


def _checkpoint_path(run_dir: str, file_stem: str) -> Path:
    return Path(run_dir) / f"{file_stem}_checkpoint.json"


def _output_path(run_dir: str, file_stem: str) -> Path:
    return Path(run_dir) / f"{file_stem}_output.json"


def _failed_output_path(run_dir: str, file_stem: str) -> Path:
    return Path(run_dir) / f"{file_stem}_failed.json"


def _record_unique_id(record: Dict[str, Any]) -> str:
    """Return unique_id from a first-pass output record."""
    if not isinstance(record, dict):
        raise ValueError("Checkpoint record must be a JSON object.")

    output = record.get("output")
    if not isinstance(output, dict):
        raise ValueError("Checkpoint record must contain an 'output' object.")

    unique_id = output.get("unique_id")
    if not unique_id:
        raise ValueError("Checkpoint output record has no unique_id.")

    return str(unique_id)


def _validate_input_unique_ids(utterances: List[Dict[str, Any]]) -> List[str]:
    """Return source-order unique_ids and reject missing or duplicate identifiers."""
    ids: List[str] = []
    seen: set[str] = set()

    for index, utterance in enumerate(utterances):
        unique_id = utterance.get("unique_id")
        if not unique_id:
            raise ValueError(
                f"Input utterance {index} has no unique_id; resumable "
                f"processing requires stable unique_id values."
            )

        unique_id = str(unique_id)
        if unique_id in seen:
            raise ValueError(f"Duplicate unique_id in input: {unique_id}")

        seen.add(unique_id)
        ids.append(unique_id)

    return ids


def _new_checkpoint(
    *,
    input_file: Path,
    input_sha256: str,
    doc_id: Optional[str],
    mode: str,
    total_utterances: int,
) -> Dict[str, Any]:
    now = datetime.now().astimezone().isoformat()
    return {
        "checkpoint_version": 1,
        "status": "in_progress",
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
        "input_file": str(input_file.resolve()),
        "input_sha256": input_sha256,
        "doc_id": doc_id,
        "mode": mode,
        "total_utterances": total_utterances,
        "records": [],
    }


def _load_and_validate_checkpoint(
    checkpoint_path: Path,
    *,
    input_file: Path,
    input_sha256: str,
    doc_id: Optional[str],
    mode: str,
    input_unique_ids: List[str],
) -> Dict[str, Any]:
    """Load and validate a checkpoint for the unchanged input document.

    Besides validating saved first-pass output records, this also validates
    any optional parser-recovery provenance stored in the checkpoint.
    """
    checkpoint = load_json(str(checkpoint_path))

    if not isinstance(checkpoint, dict):
        raise ValueError(f"Invalid checkpoint structure: {checkpoint_path}")

    if checkpoint.get("checkpoint_version") != 1:
        raise ValueError(
            f"Unsupported checkpoint version in {checkpoint_path}: "
            f"{checkpoint.get('checkpoint_version')!r}"
        )

    if checkpoint.get("input_sha256") != input_sha256:
        raise ValueError(
            "Checkpoint input checksum does not match the current "
            "preprocessed file. The input changed after the checkpoint "
            "was created; refusing to mix results."
        )

    if checkpoint.get("doc_id") != doc_id:
        raise ValueError(
            f"Checkpoint doc_id {checkpoint.get('doc_id')!r} does not "
            f"match current doc_id {doc_id!r}."
        )

    if checkpoint.get("mode") != mode:
        raise ValueError(
            f"Checkpoint mode {checkpoint.get('mode')!r} does not match "
            f"current mode {mode!r}. Whole-mode and presplit-mode "
            f"checkpoints are not interchangeable -- they use different "
            f"prompts and output schemas."
        )

    if int(checkpoint.get("total_utterances", -1)) != len(input_unique_ids):
        raise ValueError(
            "Checkpoint utterance count does not match the current "
            "preprocessed input."
        )

    records = checkpoint.get("records")

    if not isinstance(records, list):
        raise ValueError("Checkpoint 'records' must be a list.")

    input_id_set = set(input_unique_ids)

    # --------------------------------------------------------------
    # Validate checkpoint output records
    # --------------------------------------------------------------
    seen_record_ids: set[str] = set()

    for record in records:
        unique_id = _record_unique_id(record)

        if unique_id not in input_id_set:
            raise ValueError(
                "Checkpoint contains unique_id not present in current "
                f"input: {unique_id}"
            )

        if unique_id in seen_record_ids:
            raise ValueError(f"Duplicate unique_id in checkpoint: {unique_id}")

        seen_record_ids.add(unique_id)

    # --------------------------------------------------------------
    # Validate optional parser-recovery provenance
    # --------------------------------------------------------------
    parser_recoveries = checkpoint.get("parser_recoveries", [])

    if not isinstance(parser_recoveries, list):
        raise ValueError("Checkpoint 'parser_recoveries' must be a list.")

    seen_recovery_ids: set[str] = set()

    for recovery in parser_recoveries:
        if not isinstance(recovery, dict):
            raise ValueError("Each parser recovery record must be a JSON object.")

        recovery_unique_id = recovery.get("unique_id")

        if not recovery_unique_id:
            raise ValueError("Parser recovery record has no unique_id.")

        recovery_unique_id = str(recovery_unique_id)

        if recovery_unique_id not in input_id_set:
            raise ValueError(
                "Parser recovery contains unique_id not present in "
                f"current input: {recovery_unique_id}"
            )

        if recovery_unique_id not in seen_record_ids:
            raise ValueError(
                "Parser recovery exists for an utterance that has no "
                f"successful checkpoint record: {recovery_unique_id}"
            )

        if recovery_unique_id in seen_recovery_ids:
            raise ValueError(
                f"Duplicate parser recovery record for unique_id: {recovery_unique_id}"
            )

        recovery_type = recovery.get("type")

        if recovery_type not in {"fixer", "schema_retry"}:
            raise ValueError(
                f"Unknown parser recovery type {recovery_type!r} for "
                f"unique_id {recovery_unique_id}"
            )

        utterance_index = recovery.get("utterance_index")

        if not isinstance(utterance_index, int):
            raise ValueError(
                "Parser recovery record has invalid utterance_index for "
                f"{recovery_unique_id}: {utterance_index!r}"
            )

        if utterance_index < 0 or utterance_index >= len(input_unique_ids):
            raise ValueError(
                "Parser recovery utterance_index is outside the input "
                f"range for {recovery_unique_id}: {utterance_index}"
            )

        if input_unique_ids[utterance_index] != recovery_unique_id:
            raise ValueError(
                "Parser recovery utterance_index/unique_id mismatch: "
                f"index {utterance_index} corresponds to "
                f"{input_unique_ids[utterance_index]!r}, not "
                f"{recovery_unique_id!r}."
            )

        seen_recovery_ids.add(recovery_unique_id)

    return checkpoint


def _save_checkpoint(checkpoint: Dict[str, Any], path: Path) -> None:
    checkpoint["updated_at"] = datetime.now().astimezone().isoformat()
    _atomic_save_json(checkpoint, path)


def process_utterance(
    utterance: Dict[str, Any],
    settings: Settings,
    run_dir: str,
    utterance_index: int,
    total_utterances: int,
    system_prompt: str,
    file_prefix: str,
    mode: str,
    doc_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Process one utterance through the LLM and validation.

    Returns:
        (first_pass_output, parser_recovery_metadata)

    parser_recovery_metadata is None when the LLM output parsed and
    validated normally.

    Raises on any completeness/integrity failure (id mismatch in
    PRESPLIT mode, failed reconstruction in WHOLE mode, empty/truncated
    LLM response) -- there is no placeholder fallback at this level; the
    caller (process_file) decides whether the failure is tolerated based
    on Settings.max_line_errors.
    """
    unique_id = utterance.get("unique_id", f"utterance_{utterance_index}")

    if settings.verbose:
        print(f"    [{utterance_index + 1}/{total_utterances}] {unique_id} [mode={mode}]")

    user_message = build_user_message(utterance, mode=mode)

    name = f"{file_prefix}_utterance_{utterance_index:04d}"

    if settings.save_prompts:
        save_prompt(run_dir, name, system_prompt, user_message)

    start_time = time.time()

    try:
        reasoning, content = call_llm(settings.llm, system_prompt, user_message)
    except DHInfraTruncatedResponse as exc:
        # The LLM returned no content at all (typically finish_reason=
        # 'length', budget exhausted before an answer was produced).
        # call_llm() never returns in this case, so without this the
        # reasoning trace would be lost entirely -- persist it here,
        # before the exception propagates and fails the utterance.
        if settings.save_raw_output:
            save_raw(run_dir, name, content="", reasoning=exc.reasoning)
        if settings.verbose:
            print(f"             LLM call failed: {exc}")
        raise

    elapsed = time.time() - start_time

    if settings.save_raw_output:
        save_raw(run_dir, name, content, reasoning=reasoning)

    if settings.verbose:
        print(f"             LLM responded in {elapsed:.1f}s ({len(content)} chars)")

    model_class = (
        PresplitClassificationOutput if mode == "presplit" else WholeClassificationOutput
    )

    classification_output, parser_recovery = parse_and_validate(
        content,
        model_class,
        settings,
        name=name,
        output_dir=run_dir,
        original_system=system_prompt,
        original_user=user_message,
    )

    # ------------------------------------------------------------------
    # Mode-specific completeness / integrity checks. Both are hard
    # failures at this level -- there is no placeholder fallback here;
    # the caller decides tolerance.
    # ------------------------------------------------------------------
    if mode == "presplit":
        expected_ids = {s["id"] for s in utterance["sentences"]}
        returned_ids = [s.id for s in classification_output.sentences]
        returned_id_set = set(returned_ids)

        if len(returned_ids) != len(returned_id_set):
            raise ValueError(f"{unique_id}: duplicate sentence id(s) in LLM response.")

        if returned_id_set != expected_ids:
            missing = expected_ids - returned_id_set
            extra = returned_id_set - expected_ids
            raise ValueError(
                f"{unique_id}: sentence id mismatch. "
                f"missing={sorted(missing)} extra={sorted(extra)}"
            )
    else:  # whole
        verify_whole_mode_reconstruction(
            utterance["text"],
            classification_output.sentences,
        )

    if settings.verbose:
        label_counts: Dict[str, int] = {}
        for s in classification_output.sentences:
            label_counts[s.label] = label_counts.get(s.label, 0) + 1
        print(f"             Labels: {label_counts}")

    if settings.verbose and parser_recovery is not None:
        print(f"             Parser recovery used: {parser_recovery.get('type', 'unknown')}")

    first_pass_output = build_output_utterance(
        utterance,
        classification_output,
        mode=mode,
        doc_id=doc_id,
    )

    return first_pass_output, parser_recovery


def process_file(
    input_file: Path,
    settings: Settings,
    run_dir: str,
    *,
    resume: bool = False,
) -> Dict[str, Any]:
    """Process one preprocessed document with durable per-utterance checkpoints.

    A checkpoint stores first-pass results immediately after each
    successfully parsed, validated utterance. Failed utterances are never
    stored in the checkpoint -- only real successes -- so a later --resume
    naturally retries exactly the ones that failed.

    Parser-recovery provenance is stored only when an utterance required a
    schema retry or fixer call.

    Three outcomes per document:
    - fully_complete: every utterance succeeded. *_output.json written,
      no *_failed.json.
    - tolerated_complete: some utterances permanently failed, but the
      failure count is within Settings.max_line_errors and every
      utterance was at least attempted. *_output.json IS written, with
      failed utterances represented as explicit placeholder records
      (status="failed"). A companion *_failed.json records full failure
      details for traceability.
    - incomplete: failures exceed tolerance (or tolerance is None), or
      not every utterance was even attempted. No *_output.json is
      written, matching the original strict behavior.
    """
    file_stem = input_file.stem

    print(f"\n  Processing file: {input_file.name}")

    doc_id, mode, utterances = _load_input_payload(input_file)

    input_unique_ids = _validate_input_unique_ids(utterances)
    utterances_by_id: Dict[str, Dict[str, Any]] = {
        str(u["unique_id"]): u for u in utterances
    }

    input_sha256 = _sha256_file(input_file)

    checkpoint_path = _checkpoint_path(run_dir, file_stem)
    output_path = _output_path(run_dir, file_stem)
    failed_output_path = _failed_output_path(run_dir, file_stem)

    print(f"    {len(utterances)} utterance(s), mode={mode}")
    print(f"    Checkpoint: {checkpoint_path}")

    system_prompt = build_system_prompt(mode)

    # ==============================================================
    # Load or create checkpoint
    # ==============================================================

    if resume:
        if checkpoint_path.is_file():
            checkpoint = _load_and_validate_checkpoint(
                checkpoint_path,
                input_file=input_file,
                input_sha256=input_sha256,
                doc_id=doc_id,
                mode=mode,
                input_unique_ids=input_unique_ids,
            )

            resumed_records = len(checkpoint["records"])

            print(f"    Resume mode: loaded {resumed_records}/{len(utterances)} completed utterance(s)")

        else:
            if output_path.exists():
                raise FileExistsError(
                    "--resume found a final output but no matching "
                    f"checkpoint: {output_path}. Refusing to guess the "
                    "provenance of that output."
                )

            checkpoint = _new_checkpoint(
                input_file=input_file,
                input_sha256=input_sha256,
                doc_id=doc_id,
                mode=mode,
                total_utterances=len(utterances),
            )

            _save_checkpoint(checkpoint, checkpoint_path)

            resumed_records = 0

            print(
                "    Resume mode: no checkpoint existed for this document; "
                "starting it as a new document within the resumed run."
            )

    else:
        if checkpoint_path.exists():
            raise FileExistsError(
                f"Checkpoint already exists for {input_file.name}: "
                f"{checkpoint_path}\n"
                "Use --resume to continue that run, or remove/rename the "
                "checkpoint before intentionally starting this document "
                "again in the same run directory."
            )

        checkpoint = _new_checkpoint(
            input_file=input_file,
            input_sha256=input_sha256,
            doc_id=doc_id,
            mode=mode,
            total_utterances=len(utterances),
        )

        _save_checkpoint(checkpoint, checkpoint_path)

        resumed_records = 0

    # ==============================================================
    # Restore saved first-pass results
    # ==============================================================

    records_by_id: Dict[str, Dict[str, Any]] = {
        _record_unique_id(record): record for record in checkpoint["records"]
    }

    errors: List[str] = []
    error_records: List[Dict[str, Any]] = []
    newly_processed = 0
    skipped_from_checkpoint = 0

    file_start = time.time()

    # ==============================================================
    # Process missing utterances
    # ==============================================================

    for i, utterance in enumerate(utterances):
        unique_id = str(utterance["unique_id"])

        if unique_id in records_by_id:
            skipped_from_checkpoint += 1

            if settings.verbose:
                print(f"    [{i + 1}/{len(utterances)}] {unique_id} -- checkpoint hit, skipping LLM call")

            continue

        try:
            result, parser_recovery = process_utterance(
                utterance=utterance,
                settings=settings,
                run_dir=run_dir,
                utterance_index=i,
                total_utterances=len(utterances),
                system_prompt=system_prompt,
                file_prefix=file_stem,
                mode=mode,
                doc_id=doc_id,
            )

            result_unique_id = _record_unique_id(result)

            if result_unique_id != unique_id:
                raise ValueError(
                    f"Processed utterance returned unique_id {result_unique_id!r}, "
                    f"expected {unique_id!r}."
                )

            # ------------------------------------------------------
            # Store successful first-pass result
            # ------------------------------------------------------

            records_by_id[unique_id] = result

            checkpoint["records"] = [
                records_by_id[source_id]
                for source_id in input_unique_ids
                if source_id in records_by_id
            ]

            # ------------------------------------------------------
            # Store parser recovery only when something unusual
            # actually occurred.
            # ------------------------------------------------------

            if parser_recovery is not None:
                recovery_record = {
                    "unique_id": unique_id,
                    "utterance_index": i,
                    **parser_recovery,
                }

                checkpoint.setdefault("parser_recoveries", []).append(recovery_record)

            checkpoint["status"] = "in_progress"

            # Save both the successful record and any recovery
            # provenance atomically after every utterance.
            _save_checkpoint(checkpoint, checkpoint_path)

            newly_processed += 1

        except Exception as exc:
            error_msg = f"Utterance {i} ({unique_id}): {exc}"

            errors.append(error_msg)

            error_records.append({
                "unique_id": unique_id,
                "utterance_index": i,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "finish_reason": getattr(exc, "finish_reason", None),
            })

            print(f"      ERROR: {error_msg}")

            if not settings.continue_on_error:
                break

    file_elapsed = time.time() - file_start

    processed_count = len(records_by_id)
    failed_count = len(errors)

    # Every utterance was either successfully processed (in records_by_id)
    # or recorded as a permanent failure (in errors) -- as opposed to some
    # utterances never having been attempted at all, e.g. because
    # continue_on_error=False stopped the loop early. Only the former
    # case is eligible for tolerance.
    all_attempted = (processed_count + failed_count) == len(utterances)

    fully_complete = processed_count == len(utterances) and not errors

    within_tolerance = (
        settings.max_line_errors is not None
        and failed_count <= settings.max_line_errors
    )

    tolerated_complete = (
        not fully_complete
        and all_attempted
        and failed_count > 0
        and within_tolerance
    )

    complete = fully_complete or tolerated_complete

    # ==============================================================
    # Final output
    # ==============================================================

    if fully_complete:
        final_results = [records_by_id[uid] for uid in input_unique_ids]

        save_json(final_results, str(output_path))

        checkpoint["status"] = "complete"
        checkpoint["completed_at"] = datetime.now().astimezone().isoformat()
        checkpoint["final_output_file"] = str(output_path)

        _save_checkpoint(checkpoint, checkpoint_path)

        print(
            f"    Saved complete output: {len(final_results)} results -> "
            f"{output_path} ({file_elapsed:.1f}s)"
        )

    elif tolerated_complete:
        failed_by_id = {rec["unique_id"]: rec for rec in error_records}

        final_results = []
        for uid in input_unique_ids:
            if uid in records_by_id:
                final_results.append(records_by_id[uid])
            else:
                fail_rec = failed_by_id[uid]
                final_results.append(
                    build_failed_output_utterance(
                        utterances_by_id[uid],
                        mode=mode,
                        doc_id=doc_id,
                        error_type=fail_rec["error_type"],
                        error_message=fail_rec["error_message"],
                        finish_reason=fail_rec["finish_reason"],
                    )
                )

        save_json(final_results, str(output_path))

        save_json(
            {
                "doc_id": doc_id,
                "input_file": str(input_file),
                "total_utterances": len(utterances),
                "failed_count": failed_count,
                "tolerance": settings.max_line_errors,
                "failures": error_records,
            },
            str(failed_output_path),
        )

        checkpoint["status"] = "complete_with_tolerated_failures"
        checkpoint["completed_at"] = datetime.now().astimezone().isoformat()
        checkpoint["final_output_file"] = str(output_path)
        checkpoint["tolerated_failures"] = error_records

        _save_checkpoint(checkpoint, checkpoint_path)

        print(
            f"    Saved output with {failed_count} tolerated failure(s) "
            f"(tolerance={settings.max_line_errors}) -> {output_path}\n"
            f"    Failure details -> {failed_output_path}"
        )

    else:
        if output_path.exists():
            raise FileExistsError(
                "Processing is incomplete, but a final output file "
                f"already exists: {output_path}. Refusing to leave an "
                "ambiguous result in place."
            )

        checkpoint["status"] = "incomplete"
        checkpoint["completed_at"] = None

        _save_checkpoint(checkpoint, checkpoint_path)

        reason = (
            "exceeds tolerance" if (failed_count > 0 and settings.max_line_errors is not None)
            else "not all utterances were attempted" if not all_attempted
            else "no tolerance configured (max_line_errors=None)"
        )

        print(
            f"    Incomplete: {processed_count}/{len(utterances)} "
            f"validated utterance(s), {failed_count} failed ({reason}). "
            f"No *_output.json was written."
        )

    # ==============================================================
    # Per-file metadata
    # ==============================================================

    parser_recoveries = checkpoint.get("parser_recoveries", [])

    file_metadata: Dict[str, Any] = {
        "file": input_file.name,
        "input_path": str(input_file),
        "doc_id": doc_id,
        "mode": mode,
        "total_utterances": len(utterances),
        "processed": processed_count,
        "failed": failed_count,
        "unprocessed": max(0, len(utterances) - processed_count - failed_count),
        "complete": complete,
        "tolerated_complete": tolerated_complete,
        "max_line_errors": settings.max_line_errors,
        "errors": len(errors),
        "elapsed_seconds": round(file_elapsed, 2),
        "error_details": errors if errors else None,
        "resume_requested": resume,
        "records_loaded_from_checkpoint": resumed_records,
        "records_skipped_from_checkpoint": skipped_from_checkpoint,
        "new_records_processed": newly_processed,
        "checkpoint_file": str(checkpoint_path),
        "output_file": str(output_path) if complete else None,
        "failed_output_file": str(failed_output_path) if tolerated_complete else None,
        "input_sha256": input_sha256,
    }

    if parser_recoveries:
        file_metadata["parser_recoveries"] = parser_recoveries
        file_metadata["parser_recovery_summary"] = {
            "total": len(parser_recoveries),
            "schema_retries": sum(r.get("type") == "schema_retry" for r in parser_recoveries),
            "fixer_calls": sum(r.get("type") == "fixer" for r in parser_recoveries),
        }

    return file_metadata


def main() -> int:
    """Run the classification pipeline on either a directory of JSON files or one JSON file."""
    parser = argparse.ArgumentParser(description="Classification LLM-processing pipeline")

    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument("--input-dir", type=str, help="Directory with input JSON files")
    input_group.add_argument("--input-file", type=str, help="Single input JSON file to process")

    parser.add_argument(
        "--output-dir",
        type=str,
        help="Base output directory used by standalone timestamped runs.",
    )
    parser.add_argument(
        "--run-dir",
        type=str,
        help=(
            "Exact output directory for this processing run. Intended for "
            "pipeline.py and disables run.py's additional timestamp folder."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Explicitly resume from existing per-document checkpoint files "
            "in the selected run directory. Completed unique_ids are skipped."
        ),
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help=(
            "Return exit code 0 even when individual utterances fail beyond "
            "tolerance. Incomplete documents remain checkpoint-only and do "
            "not receive *_output.json files."
        ),
    )

    args = parser.parse_args()

    settings = Settings()

    if args.input_dir:
        settings.input_dir = args.input_dir

    if args.output_dir:
        settings.output_dir = args.output_dir

    input_files = _resolve_input_files(args, settings)

    if args.input_file:
        print(f"\n  Input file: {input_files[0]}")
    else:
        print(f"\n  Input directory: {settings.input_dir}")

    print(f"  Found {len(input_files)} JSON file(s): {', '.join(f.name for f in input_files)}")

    run_dir = _get_run_output_dir(settings, args.run_dir)
    print(f"  Output directory: {run_dir}")

    print(f"\n  Model: {settings.llm.display_name}")
    print(f"  Provider: {settings.llm.provider}")
    print(f"  Failure tolerance (max_line_errors): {settings.max_line_errors}")

    file_results: List[Dict[str, Any]] = []
    pipeline_start = time.time()

    try:
        for input_file in input_files:
            file_meta = process_file(
                input_file,
                settings,
                run_dir,
                resume=args.resume,
            )
            file_results.append(file_meta)

    finally:
        stop_server()

    pipeline_elapsed = time.time() - pipeline_start

    total_expected_utterances = sum(f["total_utterances"] for f in file_results)
    total_processed_utterances = sum(f["processed"] for f in file_results)
    total_failed_utterances = sum(f["failed"] for f in file_results)
    total_errors = sum(f["errors"] for f in file_results)
    incomplete_files = sum(not f["complete"] for f in file_results)
    tolerated_files = sum(1 for f in file_results if f.get("tolerated_complete"))

    # A file only counts as a hard failure when it never produced an
    # output file at all (complete=False). A file that completed via
    # tolerated failures (complete=True, tolerated_complete=True) is a
    # successful pipeline run by design -- that is the entire point of
    # max_line_errors -- even though its error/failed counts are > 0.
    strict_failure = incomplete_files > 0

    all_parser_recoveries: List[Dict[str, Any]] = []

    for file_meta in file_results:
        for recovery in file_meta.get("parser_recoveries", []):
            all_parser_recoveries.append(
                {"file": file_meta["file"], "doc_id": file_meta.get("doc_id"), **recovery}
            )

    metadata = {
        "timestamp": datetime.now().isoformat(),
        "model": settings.llm.display_name,
        "provider": settings.llm.provider,
        "input_mode": "file" if args.input_file else "directory",
        "input_file": str(input_files[0]) if args.input_file else None,
        "input_dir": None if args.input_file else str(settings.input_dir),
        "run_dir": str(run_dir),
        "resume_requested": args.resume,
        "max_line_errors": settings.max_line_errors,
        "files": file_results,
        "summary": {
            "files_expected": len(input_files),
            "files_processed": len(file_results),
            "incomplete_files": incomplete_files,
            "tolerated_files": tolerated_files,
            "utterances_expected": total_expected_utterances,
            "utterances_processed": total_processed_utterances,
            "utterances_failed_tolerated": total_failed_utterances,
            "utterance_errors": total_errors,
            "records_loaded_from_checkpoint": sum(
                f.get("records_loaded_from_checkpoint", 0) for f in file_results
            ),
            "new_records_processed": sum(f.get("new_records_processed", 0) for f in file_results),
            "complete": not strict_failure,
        },
        "overall_status": "failed" if strict_failure else "success",
        "total_elapsed_seconds": round(pipeline_elapsed, 2),
    }

    if all_parser_recoveries:
        metadata["parser_recoveries"] = all_parser_recoveries
        metadata["summary"]["parser_recoveries"] = len(all_parser_recoveries)
        metadata["summary"]["schema_retries"] = sum(
            r.get("type") == "schema_retry" for r in all_parser_recoveries
        )
        metadata["summary"]["fixer_calls"] = sum(
            r.get("type") == "fixer" for r in all_parser_recoveries
        )

    save_json(metadata, str(Path(run_dir) / "processing_metadata.json"))

    tolerated_note = f", {total_failed_utterances} tolerated failure(s)" if total_failed_utterances else ""
    print(
        f"\n  Done in {pipeline_elapsed:.1f}s — "
        f"{len(input_files)} files, "
        f"{total_processed_utterances}/{total_expected_utterances} utterances succeeded"
        f"{tolerated_note}, "
        f"{total_errors} total error(s)"
    )

    if strict_failure:
        print("  Overall result: FAILED (incomplete LLM processing)")
        return 0 if args.allow_partial else 1

    if tolerated_files:
        print(f"  Overall result: SUCCESS ({tolerated_files} file(s) completed with tolerated failures)")
    else:
        print("  Overall result: SUCCESS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
