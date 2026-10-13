"""
premature_stop.py

Extracts a "premature" output file directly from a classification-pipeline
checkpoint, without touching the original input file or the checkpoint
itself. This lets you get results for whatever has been processed so far
without breaking the checkpoint's sha256/count validation, which is what
would happen if you tried to trim the input file instead.

Why this works:
  Every time process_file() saves a new successful record, it rewrites
  checkpoint["records"] as:

      [records_by_id[source_id] for source_id in input_unique_ids
       if source_id in records_by_id]

  ...i.e. checkpoint["records"] is *already* exactly the list that would be
  written to the real *_output.json if the run were complete right now,
  in correct input order. The only difference from a real completed
  output is that it's a subset. So we just read that list out and dump it,
  as-is, with no reconstruction needed.

This script never modifies the checkpoint or input file, so the real run
can still be resumed normally afterward.

previous_utterance / following_utterance:
  The checkpoint's records only contain the model's output (unique_id,
  label, ...) -- the surrounding-context fields live in the *input* JSON
  that was fed to the classification step (the "_extracted.json" from the
  preprocessing stage), not in the checkpoint. So to include them, this
  script also loads that input file (by default, the path the checkpoint
  itself recorded as "input_file" when the run started) and merges
  previous_utterance/following_utterance into each record's "output"
  object by matching on unique_id.

Usage:
    python premature_stop.py --checkpoint path/to/foo_checkpoint.json
    python premature_stop.py --checkpoint path/to/foo_checkpoint.json --output path/to/foo_premature_output.json
    python premature_stop.py --checkpoint path/to/foo_checkpoint.json --input path/to/foo_extracted.json
    python premature_stop.py --checkpoint path/to/foo_checkpoint.json --no-context   # skip merging context
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def _atomic_save_json(data: Any, path: Path) -> None:
    """Atomically write JSON so an interruption can't corrupt the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")

    with tmp_path.open("w", encoding="utf-8", newline="") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())

    os.replace(tmp_path, path)


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_context_lookup(input_path: Path) -> Dict[str, Dict[str, Any]]:
    """Build unique_id -> {previous_utterance, following_utterance} from
    the extraction-stage input JSON (the file the classification run
    consumed)."""
    with input_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if isinstance(payload, dict) and isinstance(payload.get("utterances"), list):
        utterances = payload["utterances"]
    elif isinstance(payload, list):
        utterances = payload
    else:
        sys.exit(
            f"Don't recognize input file structure: {input_path}\n"
            "Expected a dict with an 'utterances' list, or a bare list."
        )

    lookup: Dict[str, Dict[str, Any]] = {}
    for utterance in utterances:
        if not isinstance(utterance, dict):
            continue
        unique_id = utterance.get("unique_id")
        if not unique_id:
            continue
        lookup[str(unique_id)] = {
            "previous_utterance": utterance.get("previous_utterance"),
            "following_utterance": utterance.get("following_utterance"),
        }

    return lookup


def _merge_context(records: list, context: Dict[str, Dict[str, Any]]) -> None:
    """Merge previous_utterance/following_utterance into each record's
    'output' object in place, matched by unique_id."""
    missing = []

    for record in records:
        output = record.get("output") if isinstance(record, dict) else None
        if not isinstance(output, dict):
            continue

        unique_id = output.get("unique_id")
        ctx = context.get(str(unique_id)) if unique_id else None

        if ctx is None:
            missing.append(unique_id)
            continue

        output["previous_utterance"] = ctx.get("previous_utterance")
        output["following_utterance"] = ctx.get("following_utterance")

    if missing:
        print(
            f"  WARNING: no matching input utterance found for "
            f"{len(missing)} record(s); left without context: {missing}"
        )


def _default_output_path(checkpoint_path: Path) -> Path:
    """Derive a default output filename that can't collide with the real
    *_output.json the pipeline would eventually write on a real completed
    run (so a later --resume run is never blocked by this file existing)."""
    name = checkpoint_path.name
    suffix = "_checkpoint.json"
    if name.endswith(suffix):
        stem = name[: -len(suffix)]
    else:
        stem = checkpoint_path.stem
    return checkpoint_path.with_name(f"{stem}_premature_output.json")


def extract_premature_output(
    checkpoint_path: Path,
    output_path: Path,
    *,
    input_path: Optional[Path],
    include_context: bool,
) -> None:
    if not checkpoint_path.is_file():
        sys.exit(f"Checkpoint file not found: {checkpoint_path}")

    with checkpoint_path.open("r", encoding="utf-8") as handle:
        checkpoint = json.load(handle)

    if not isinstance(checkpoint, dict):
        sys.exit(f"Invalid checkpoint structure (expected a JSON object): {checkpoint_path}")

    if checkpoint.get("checkpoint_version") != 1:
        sys.exit(
            f"Unsupported checkpoint_version in {checkpoint_path}: "
            f"{checkpoint.get('checkpoint_version')!r}"
        )

    records = checkpoint.get("records")
    if not isinstance(records, list):
        sys.exit(f"Checkpoint 'records' is missing or not a list: {checkpoint_path}")

    total_utterances = checkpoint.get("total_utterances")
    doc_id = checkpoint.get("doc_id")
    mode = checkpoint.get("mode")
    status = checkpoint.get("status")

    if output_path.exists():
        sys.exit(
            f"Output file already exists, refusing to overwrite: {output_path}\n"
            "Remove it first or pass a different --output path."
        )

    print(f"Checkpoint: {checkpoint_path}")
    print(f"  doc_id={doc_id!r}  mode={mode!r}  status={status!r}")

    # ------------------------------------------------------------------
    # Optionally merge previous_utterance/following_utterance context
    # from the input file the classification run consumed.
    # ------------------------------------------------------------------
    if include_context:
        resolved_input_path = input_path
        explicit = input_path is not None

        if resolved_input_path is None:
            recorded = checkpoint.get("input_file")
            if recorded:
                resolved_input_path = Path(recorded)

        if resolved_input_path is None:
            print(
                "  WARNING: no --input given and checkpoint has no recorded "
                "'input_file'; writing output WITHOUT previous/following "
                "utterance context."
            )
        elif not resolved_input_path.is_file():
            msg = f"Input file not found: {resolved_input_path}"
            if explicit:
                sys.exit(msg)
            print(f"  WARNING: {msg} (from checkpoint's recorded path)")
            print("  Writing output WITHOUT previous/following utterance context.")
            print("  Pass --input explicitly to point at its current location.")
        else:
            recorded_sha256 = checkpoint.get("input_sha256")
            if recorded_sha256 and _sha256_file(resolved_input_path) != recorded_sha256:
                print(
                    f"  WARNING: {resolved_input_path} does not match the "
                    "checksum recorded in the checkpoint -- it may have "
                    "changed since this run started. Context merged from "
                    "it below may not exactly reflect what the model saw."
                )

            context = _load_context_lookup(resolved_input_path)
            _merge_context(records, context)
            print(f"  Merged previous/following utterance context from: {resolved_input_path}")

    _atomic_save_json(records, output_path)

    processed = len(records)
    if isinstance(total_utterances, int):
        print(f"  {processed}/{total_utterances} utterance(s) included (premature/partial)")
    else:
        print(f"  {processed} utterance(s) included (premature/partial)")
    print(f"Wrote premature output -> {output_path}")
    print(
        "Note: the checkpoint and input file were not modified. "
        "The real run can still be resumed normally later."
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extract a premature/partial output file directly from a "
            "classification-pipeline checkpoint, without touching the "
            "checkpoint or input file."
        )
    )
    parser.add_argument(
        "--checkpoint",
        required=True,
        type=str,
        help="Path to the *_checkpoint.json file to read from.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help=(
            "Path to write the premature output JSON to. Defaults to "
            "<checkpoint stem>_premature_output.json next to the "
            "checkpoint (never the real *_output.json name, so a later "
            "--resume run is never blocked by this file)."
        ),
    )
    parser.add_argument(
        "--input",
        type=str,
        default=None,
        help=(
            "Path to the extraction-stage input JSON (the file the "
            "classification run consumed), used to pull in "
            "previous_utterance/following_utterance context. Defaults to "
            "the path the checkpoint itself recorded as 'input_file' when "
            "the run started."
        ),
    )
    parser.add_argument(
        "--no-context",
        action="store_true",
        help=(
            "Skip merging previous_utterance/following_utterance context "
            "entirely; write only what's in the checkpoint, as before."
        ),
    )

    args = parser.parse_args()

    checkpoint_path = Path(args.checkpoint)
    output_path = Path(args.output) if args.output else _default_output_path(checkpoint_path)
    input_path = Path(args.input) if args.input else None

    extract_premature_output(
        checkpoint_path,
        output_path,
        input_path=input_path,
        include_context=not args.no_context,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
