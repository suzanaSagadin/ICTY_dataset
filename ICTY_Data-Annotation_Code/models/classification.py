"""Pydantic models for the sentence-level agency classification pipeline.

These models define the structure the LLM must return. Two output shapes
exist depending on the extraction mode set by preprocessing.py:

- PRESPLIT mode: sentences were split and given short local ids upstream.
  The LLM returns only {id, label} pairs -- it never re-emits sentence
  text. Reintegration matches purely on id.

- WHOLE mode: the full utterance is handed to the LLM as one block of
  text, and the LLM is trusted to find sentence boundaries itself. The
  LLM returns {text, label} pairs. Because the LLM regenerates the text
  itself here, run.py additionally verifies (via
  postprocess.verify_whole_mode_reconstruction) that the returned
  sentences reconstruct the original utterance exactly and in order --
  catching both altered wording and silently dropped content.

Both modes share the same three-way label set. "none" is a legitimate,
required outcome (not an omission) for any sentence that exhibits
neither agency nor externalization -- every sentence must receive exactly
one of the three labels, in both modes.
"""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field

Label = Literal["agency", "externalization", "none"]


class PresplitSentenceLabel(BaseModel):
    """One label for one pre-identified sentence (PRESPLIT mode)."""

    id: str = Field(
        ...,
        description=(
            "The sentence id being classified, e.g. 's0'. Must match an id "
            "supplied in CURRENT_UTTERANCE exactly -- do not invent, alter, "
            "or omit ids."
        ),
    )
    label: Label = Field(
        ...,
        description=(
            "'agency' or 'externalization' if the sentence exhibits either. "
            "'none' if it exhibits neither -- 'none' is a required, valid "
            "answer, not something to avoid."
        ),
    )

    class Config:
        extra = "forbid"


class PresplitClassificationOutput(BaseModel):
    """Complete classification result for one utterance (PRESPLIT mode).

    Must contain exactly one entry per sentence id supplied in the input
    -- no more, no fewer, no duplicates. run.py enforces this after
    parsing.
    """

    sentences: List[PresplitSentenceLabel] = Field(
        default_factory=list,
        description=(
            "One entry per sentence id in CURRENT_UTTERANCE. No id may be "
            "missing, repeated, or invented."
        ),
    )

    class Config:
        extra = "forbid"

    @classmethod
    def get_example_output(cls) -> dict:
        return {
            "sentences": [
                {"id": "s0", "label": "agency"},
                {"id": "s1", "label": "none"},
                {"id": "s2", "label": "externalization"},
            ]
        }


class WholeSentenceLabel(BaseModel):
    """One label for one sentence the LLM itself identified (WHOLE mode)."""

    text: str = Field(
        ...,
        description=(
            "The exact sentence text, copied verbatim from CURRENT_UTTERANCE "
            "-- do not paraphrase, correct, abbreviate, or reorder."
        ),
    )
    label: Label = Field(
        ...,
        description=(
            "'agency' or 'externalization' if the sentence exhibits either. "
            "'none' if it exhibits neither -- 'none' is a required, valid "
            "answer, not something to avoid."
        ),
    )

    class Config:
        extra = "forbid"


class WholeClassificationOutput(BaseModel):
    """Complete classification result for one utterance (WHOLE mode).

    The sentences, concatenated in order, must reconstruct
    CURRENT_UTTERANCE exactly -- no altered wording, no skipped content.
    run.py enforces this after parsing.
    """

    sentences: List[WholeSentenceLabel] = Field(
        default_factory=list,
        description=(
            "Every sentence in CURRENT_UTTERANCE, in order, each with "
            "exactly one label."
        ),
    )

    class Config:
        extra = "forbid"

    @classmethod
    def get_example_output(cls) -> dict:
        return {
            "sentences": [
                {
                    "text": "I ordered the withdrawal of the unit from the village.",
                    "label": "agency",
                },
                {
                    "text": "We were cut off from communication and could not intervene.",
                    "label": "externalization",
                },
                {"text": "Your Honours, thank you.", "label": "none"},
            ]
        }
