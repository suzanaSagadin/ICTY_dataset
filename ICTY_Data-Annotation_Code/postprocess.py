"""
Post-processing for the classification pipeline.

Deliberately small: there is no XML, no character-span reconciliation,
and no cross-boundary entity merging to worry about (compare this to
the NER pipeline's postprocess.py, which existed almost entirely to
solve those problems). This module does three things:

1. verify_whole_mode_reconstruction -- WHOLE mode only. Confirms the
   LLM's returned sentences reconstruct the original utterance text
   exactly and in order: every sentence's text must appear verbatim at
   the expected position, and nothing but whitespace may separate
   consecutive sentences (or follow the last one). This single check
   catches both altered wording and silently dropped content.

2. build_output_utterance -- wraps a validated classification result
   together with its unique_id into the record shape written to the
   final per-document output JSON. Marked status="ok".

3. build_failed_output_utterance -- builds a placeholder record for an
   utterance that permanently failed processing (after exhausting the
   parser's fixer/retry logic), for use when Settings.max_line_errors
   allows the document to still complete despite the failure. Marked
   status="failed", with sentences=None and the error attached, so the
   gap is explicit rather than the utterance being silently omitted
   from the output.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Union

from models.classification import (
    PresplitClassificationOutput,
    WholeClassificationOutput,
    WholeSentenceLabel,
)


def verify_whole_mode_reconstruction(
    original_text: str,
    sentences: Sequence[WholeSentenceLabel],
) -> None:
    """Raise ValueError unless `sentences`, concatenated in order, exactly
    reconstruct `original_text` (ignoring only whitespace gaps between
    sentences).

    Catches two distinct failure modes in one pass:
    - altered wording: a sentence's text does not appear verbatim at or
      after the position where the previous sentence ended.
    - dropped content: non-whitespace text between two matched sentences
      (or after the last one) that was never labeled.
    """
    cursor = 0

    for i, sent in enumerate(sentences):
        text = sent.text
        idx = original_text.find(text, cursor)

        if idx == -1:
            raise ValueError(
                f"Sentence {i} ({text!r}) was not found verbatim in the "
                f"original utterance at or after position {cursor}. The "
                f"LLM may have altered the wording, reordered sentences, "
                f"or fabricated text."
            )

        skipped = original_text[cursor:idx]
        if skipped.strip():
            raise ValueError(
                f"Content was skipped before sentence {i}: {skipped!r}. "
                f"Every part of the utterance must be labeled, including "
                f"content that gets label 'none'."
            )

        cursor = idx + len(text)

    trailing = original_text[cursor:]
    if trailing.strip():
        raise ValueError(
            f"Trailing content after the last labeled sentence was never "
            f"labeled: {trailing!r}."
        )


def build_output_utterance(
    utterance: Dict[str, Any],
    classification_output: Union[
        PresplitClassificationOutput,
        WholeClassificationOutput,
    ],
    *,
    mode: str,
    doc_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Wrap a validated classification result into the final per-utterance
    output record.

    Note: for PRESPLIT mode, sentence text is deliberately NOT re-added
    here from the input -- that join happens downstream (e.g. in the
    doccano export step) by matching on id against the original
    preprocessed file, keeping this record minimal and unambiguous
    about what the LLM actually returned.
    """
    return {
        "unique_id": utterance["unique_id"],
        "doc_id": doc_id,
        "mode": mode,
        "status": "ok",
        "output": {
            "unique_id": utterance["unique_id"],
            "sentences": [s.model_dump() for s in classification_output.sentences],
        },
    }


def build_failed_output_utterance(
    utterance: Dict[str, Any],
    *,
    mode: str,
    doc_id: Optional[str],
    error_type: str,
    error_message: str,
    finish_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a placeholder output record for an utterance that permanently
    failed processing, for use when Settings.max_line_errors allows the
    document to still complete.

    Mirrors build_output_utterance's top-level shape (unique_id, doc_id,
    mode, status, output.unique_id) so both success and failure records
    can be consumed uniformly downstream -- callers should branch on
    "status" rather than assuming "output.sentences" is always a list.
    """
    return {
        "unique_id": utterance["unique_id"],
        "doc_id": doc_id,
        "mode": mode,
        "status": "failed",
        "output": {
            "unique_id": utterance["unique_id"],
            "sentences": None,
        },
        "error": {
            "error_type": error_type,
            "error_message": error_message,
            "finish_reason": finish_reason,
        },
    }
