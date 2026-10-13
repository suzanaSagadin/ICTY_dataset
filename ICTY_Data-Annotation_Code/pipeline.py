"""
Classification pipeline orchestrator.

Chains three processing stages into one run:

    00_input/           -- copy of the raw source file, for reproducibility
    01_preprocessing/    -- preprocessing.py: extraction + optional presplit
    02_llm/              -- run.py: LLM classification + validation
    03_reintegration/    -- reintegration.py: merge LLM output back into
                             the preprocessed structure

Each stage is invoked as a real subprocess (same interpreter, via
sys.executable) so preprocessing.py, run.py, and reintegration.py keep
working identically whether run standalone or through this orchestrator.
Output from each stage is streamed to the console AND appended to a
run-wide log file.

Stage 03 only runs if stage 02 actually produced a *_output.json for the
document -- under Settings.max_line_errors, a document can complete
(fully or with tolerated failures, both producing an output file) or
remain genuinely incomplete (no output file at all, nothing to
reintegrate). reintegration.py itself already handles status="failed"
placeholder records from a tolerated-complete document -- it passes them
through as-is rather than attempting to join sentences that were never
generated.

Usage:
    python pipeline.py --input corpus.json
    python pipeline.py --input corpus.json --presplit-sentences --random_sample 200 --random_seed 42
    python pipeline.py --run-dir data/output/qwen3.5-397b/processing_20260908_142422 --resume
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from settings import Settings


class PipelineError(RuntimeError):
    """Raised when a pipeline stage fails or produces invalid output."""


class TeeLogger:
    """Writes to both stdout and a log file."""

    def __init__(self, log_path: Path) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = log_path.open("a", encoding="utf-8")

    def write(self, message: str = "") -> None:
        print(message)
        self._file.write(message + "\n")
        self._file.flush()

    def write_raw(self, text: str) -> None:
        """Write pre-formatted text (e.g. subprocess output) without
        adding an extra newline."""
        sys.stdout.write(text)
        sys.stdout.flush()
        self._file.write(text)
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def timestamp_now() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def iso_now() -> str:
    return datetime.now().astimezone().isoformat()


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ======================================================================
# Run directory layout
# ======================================================================

def build_run_paths(run_dir: Path) -> Dict[str, Path]:
    return {
        "run_dir": run_dir,
        "input_dir": run_dir / "00_input",
        "preprocessing_dir": run_dir / "01_preprocessing",
        "llm_dir": run_dir / "02_llm",
        "reintegration_dir": run_dir / "03_reintegration",
        "log_file": run_dir / "pipeline.log",
        "metadata_file": run_dir / "pipeline_metadata.json",
    }


def ensure_run_stage_directories(paths: Dict[str, Path]) -> None:
    for key in ("input_dir", "preprocessing_dir", "llm_dir", "reintegration_dir"):
        paths[key].mkdir(parents=True, exist_ok=True)


def create_run_dir(settings: Settings, explicit_run_dir: Optional[str]) -> Path:
    if explicit_run_dir:
        run_dir = Path(explicit_run_dir)
    else:
        run_dir = Path(settings.output_dir) / settings.llm.display_name / f"processing_{timestamp_now()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


# ======================================================================
# Pre-flight configuration check
# ======================================================================

def validate_llm_configuration(settings: Settings) -> List[str]:
    """Return a list of configuration problems (empty = OK).

    Catches obvious credential/config gaps before spending time on
    stage 01, so a misconfigured run fails immediately instead of after
    preprocessing a large corpus.
    """
    problems: List[str] = []
    llm = settings.llm

    if llm.provider in ("openai", "anthropic", "dhinfra"):
        if not llm.api_key:
            problems.append(
                f"provider={llm.provider!r} but no api_key is set "
                f"(check the corresponding environment variable)."
            )
        if llm.provider == "dhinfra" and not llm.api_url:
            problems.append("provider='dhinfra' but api_url is not set.")
        if not llm.model_name:
            problems.append(f"provider={llm.provider!r} but no model_name is set.")

    elif llm.provider == "local":
        if not llm.model_path and not llm.model_preset and not (llm.repo_id and llm.filename):
            problems.append(
                "provider='local' but none of model_path, model_preset, "
                "or repo_id+filename are set."
            )
    else:
        problems.append(f"Unknown provider: {llm.provider!r}")

    return problems


# ======================================================================
# Stage execution
# ======================================================================

def run_subprocess(
    cmd: List[str],
    *,
    logger: TeeLogger,
    stage_name: str,
) -> int:
    """Run a stage as a subprocess, streaming its output live to both
    the console and the pipeline log. Returns the process return code."""
    logger.write(f"\n{'=' * 70}")
    logger.write(f"STAGE: {stage_name}")
    logger.write(f"CMD:   {' '.join(cmd)}")
    logger.write("=" * 70)

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    assert process.stdout is not None
    for line in process.stdout:
        logger.write_raw(line)

    process.wait()
    return process.returncode


def run_preprocessing_stage(
    *,
    input_file: Path,
    preprocessing_dir: Path,
    presplit_sentences: bool,
    random_sample: Optional[int],
    random_seed: int,
    unique_ids: Optional[str],
    limit: Optional[int],
    skip: int,
    logger: TeeLogger,
) -> Dict[str, Any]:
    """Run preprocessing.py and return its metadata dict.

    Raises PipelineError if the stage fails or produces no usable
    output.
    """
    out_file = preprocessing_dir / "extracted.json"
    metadata_file = preprocessing_dir / "extracted_metadata.json"

    cmd = [
        sys.executable, "preprocessing.py",
        "--in_file", str(input_file),
        "--out_file", str(out_file),
        "--metadata_file", str(metadata_file),
    ]
    if presplit_sentences:
        cmd.append("--presplit-sentences")
    if random_sample is not None:
        cmd += ["--random_sample", str(random_sample), "--random_seed", str(random_seed)]
    if unique_ids:
        cmd += ["--unique_ids", unique_ids]
    if limit is not None:
        cmd += ["--limit", str(limit)]
    if skip:
        cmd += ["--skip", str(skip)]

    returncode = run_subprocess(cmd, logger=logger, stage_name="01_preprocessing")

    if returncode != 0:
        raise PipelineError(
            f"preprocessing.py exited with code {returncode}. See "
            f"{preprocessing_dir} for details."
        )

    if not out_file.exists():
        raise PipelineError(f"preprocessing.py reported success but {out_file} was not created.")

    if not metadata_file.exists():
        raise PipelineError(f"preprocessing.py reported success but {metadata_file} was not created.")

    metadata = read_json(metadata_file)

    extracted = read_json(out_file)
    n_utterances = len(extracted.get("utterances", []))
    if n_utterances == 0:
        raise PipelineError(
            f"preprocessing.py produced {out_file} with zero utterances -- "
            f"nothing for stage 02 to process."
        )

    logger.write(
        f"\n  Stage 01 complete: {n_utterances} utterance(s) extracted "
        f"[mode={extracted.get('mode')}] -> {out_file}"
    )

    return {
        "output_file": str(out_file),
        "metadata_file": str(metadata_file),
        "mode": extracted.get("mode"),
        "n_utterances": n_utterances,
        "sampling": metadata.get("sampling"),
    }


def run_llm_stage(
    *,
    extracted_file: Path,
    llm_dir: Path,
    resume: bool,
    allow_partial: bool,
    logger: TeeLogger,
) -> Dict[str, Any]:
    """Run run.py against the extracted file and return its processing
    metadata dict.

    Raises PipelineError if the stage fails or produces no metadata.
    """
    cmd = [
        sys.executable, "run.py",
        "--input-file", str(extracted_file),
        "--run-dir", str(llm_dir),
    ]
    if resume:
        cmd.append("--resume")
    if allow_partial:
        cmd.append("--allow-partial")

    returncode = run_subprocess(cmd, logger=logger, stage_name="02_llm")

    metadata_file = llm_dir / "processing_metadata.json"
    if not metadata_file.exists():
        raise PipelineError(
            f"run.py exited with code {returncode} and produced no "
            f"processing_metadata.json in {llm_dir}."
        )

    metadata = read_json(metadata_file)

    if returncode != 0 and not allow_partial:
        raise PipelineError(
            f"run.py exited with code {returncode} "
            f"(overall_status={metadata.get('overall_status')!r}). "
            f"See {llm_dir}/log/ for per-utterance details."
        )

    logger.write(
        f"\n  Stage 02 complete: overall_status="
        f"{metadata.get('overall_status')!r}, "
        f"{metadata.get('summary', {}).get('utterances_processed')}/"
        f"{metadata.get('summary', {}).get('utterances_expected')} utterances"
    )

    return {
        "metadata_file": str(metadata_file),
        "overall_status": metadata.get("overall_status"),
        "summary": metadata.get("summary"),
    }


def run_reintegration_stage(
    *,
    run_dir: Path,
    reintegration_dir: Path,
    logger: TeeLogger,
) -> Dict[str, Any]:
    """Run reintegration.py against the run directory and return a
    summary of the result.

    reintegration.py auto-locates 01_preprocessing/extracted.json and
    02_llm/*_output.json from --run-dir, so no explicit file paths are
    needed here -- this only makes sense because pipeline.py's directory
    layout matches what reintegration.py's --run-dir mode expects.

    Raises PipelineError if the stage fails or produces no output.
    """
    out_file = reintegration_dir / "reintegrated.json"

    cmd = [
        sys.executable, "reintegration.py",
        "--run-dir", str(run_dir),
        "--out_file", str(out_file),
    ]

    returncode = run_subprocess(cmd, logger=logger, stage_name="03_reintegration")

    if returncode != 0:
        raise PipelineError(
            f"reintegration.py exited with code {returncode}. See "
            f"{reintegration_dir} for details."
        )

    if not out_file.exists():
        raise PipelineError(
            f"reintegration.py reported success but {out_file} was not created."
        )

    result = read_json(out_file)
    n_utterances = len(result.get("utterances", []))
    n_failed = result.get("failed_count", 0)

    logger.write(
        f"\n  Stage 03 complete: {n_utterances} utterance(s) reintegrated "
        f"({n_failed} failed) [mode={result.get('mode')}] -> {out_file}"
    )

    return {
        "output_file": str(out_file),
        "mode": result.get("mode"),
        "n_utterances": n_utterances,
        "n_failed": n_failed,
    }


# ======================================================================
# Main
# ======================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="Classification pipeline orchestrator")

    parser.add_argument("--input", type=str, help="Raw source JSON file (required unless --resume)")
    parser.add_argument("--run-dir", type=str, help="Exact run directory to use or resume")
    parser.add_argument("--output-dir", type=str, help="Base output directory for a new timestamped run")
    parser.add_argument("--resume", action="store_true", help="Resume an existing run directory")
    parser.add_argument("--allow-partial", action="store_true", help="Pass through to run.py")

    # preprocessing.py pass-through
    parser.add_argument("--presplit-sentences", action="store_true")
    parser.add_argument("--random_sample", type=int, default=None)
    parser.add_argument("--random_seed", type=int, default=42)
    parser.add_argument("--unique_ids", type=str, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip", type=int, default=0)

    args = parser.parse_args()

    if not args.input and not args.resume:
        raise SystemExit("--input is required unless resuming with --resume --run-dir ...")
    if args.resume and not args.run_dir:
        raise SystemExit("--resume requires --run-dir to know which run to resume.")

    settings = Settings()
    if args.output_dir:
        settings.output_dir = args.output_dir

    run_dir = create_run_dir(settings, args.run_dir)
    paths = build_run_paths(run_dir)
    ensure_run_stage_directories(paths)

    logger = TeeLogger(paths["log_file"])
    logger.write(f"Pipeline run directory: {run_dir}")
    logger.write(f"Started: {iso_now()}")

    # ------------------------------------------------------------
    # Pre-flight config check
    # ------------------------------------------------------------
    problems = validate_llm_configuration(settings)
    if problems:
        logger.write("\nCONFIGURATION PROBLEMS (fix before running):")
        for p in problems:
            logger.write(f"  - {p}")
        logger.close()
        return 1

    logger.write(f"Model: {settings.llm.display_name} (provider={settings.llm.provider})")

    stage_results: Dict[str, Any] = {}
    overall_ok = True

    try:
        # ------------------------------------------------------------
        # Resolve / copy input for reproducibility
        # ------------------------------------------------------------
        input_copy_path = paths["input_dir"] / "source.json"

        if args.resume and input_copy_path.exists():
            input_file = input_copy_path
            logger.write(f"\nResume mode: reusing existing input copy at {input_file}")
        else:
            if not args.input:
                raise PipelineError(
                    "No --input given and no existing input copy found in "
                    f"{paths['input_dir']} to resume from."
                )
            src = Path(args.input)
            if not src.is_file():
                raise PipelineError(f"Input file not found: {src}")
            shutil.copy2(src, input_copy_path)
            input_file = input_copy_path
            logger.write(f"\nCopied input for reproducibility: {src} -> {input_file}")
            logger.write(f"  sha256: {sha256_file(input_file)}")

        # ------------------------------------------------------------
        # Stage 01: preprocessing
        # ------------------------------------------------------------
        extracted_file = paths["preprocessing_dir"] / "extracted.json"
        skip_preprocessing = args.resume and extracted_file.exists() and (
            paths["preprocessing_dir"] / "extracted_metadata.json"
        ).exists()

        if skip_preprocessing:
            logger.write(f"\nResume mode: stage 01 already complete, reusing {extracted_file}")
            extracted = read_json(extracted_file)
            stage_results["preprocessing"] = {
                "output_file": str(extracted_file),
                "mode": extracted.get("mode"),
                "n_utterances": len(extracted.get("utterances", [])),
                "skipped_resume": True,
            }
        else:
            stage_results["preprocessing"] = run_preprocessing_stage(
                input_file=input_file,
                preprocessing_dir=paths["preprocessing_dir"],
                presplit_sentences=args.presplit_sentences,
                random_sample=args.random_sample,
                random_seed=args.random_seed,
                unique_ids=args.unique_ids,
                limit=args.limit,
                skip=args.skip,
                logger=logger,
            )

        # ------------------------------------------------------------
        # Stage 02: LLM classification
        # ------------------------------------------------------------
        stage_results["llm"] = run_llm_stage(
            extracted_file=extracted_file,
            llm_dir=paths["llm_dir"],
            resume=args.resume,
            allow_partial=args.allow_partial,
            logger=logger,
        )
        overall_ok = stage_results["llm"]["overall_status"] == "success"

        # ------------------------------------------------------------
        # Stage 03: reintegration
        # ------------------------------------------------------------
        # Only makes sense if stage 02 actually produced an output file
        # for this document -- checked by file existence rather than
        # overall_status, since a tolerated-complete document (some
        # utterances failed but within Settings.max_line_errors) still
        # writes *_output.json and should still be reintegrated (its
        # status="failed" placeholder entries pass through as-is).
        llm_output_file = paths["llm_dir"] / f"{extracted_file.stem}_output.json"

        if llm_output_file.exists():
            stage_results["reintegration"] = run_reintegration_stage(
                run_dir=run_dir,
                reintegration_dir=paths["reintegration_dir"],
                logger=logger,
            )
        else:
            logger.write(
                "\nSkipping stage 03 (reintegration): no *_output.json was "
                "produced by stage 02 -- the document did not complete, "
                "even under the configured failure tolerance "
                f"(max_line_errors={settings.max_line_errors})."
            )
            stage_results["reintegration"] = {
                "skipped": True,
                "reason": "no stage 02 output file",
            }

    except PipelineError as exc:
        logger.write(f"\nPIPELINE FAILED: {exc}")
        overall_ok = False
        stage_results["error"] = str(exc)

    # ------------------------------------------------------------
    # Final metadata + summary
    # ------------------------------------------------------------
    pipeline_metadata = {
        "run_dir": str(run_dir),
        "started_at": None,  # set below from log start; kept simple here
        "finished_at": iso_now(),
        "model": settings.llm.display_name,
        "provider": settings.llm.provider,
        "resume": args.resume,
        "stages": stage_results,
        "overall_status": "success" if overall_ok else "failed",
    }
    write_json(paths["metadata_file"], pipeline_metadata)

    logger.write(f"\n{'=' * 70}")
    logger.write(f"PIPELINE {'SUCCESS' if overall_ok else 'FAILED'}")
    logger.write(f"Metadata: {paths['metadata_file']}")
    logger.write("=" * 70)
    logger.close()

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
