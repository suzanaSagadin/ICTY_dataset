"""
Prompt builder for the classification pipeline.

The user message shape depends on extraction mode (set once per input
file by preprocessing.py):

- PRESPLIT mode: sentences (with local ids) are listed explicitly. The
  LLM returns {id, label} pairs only -- see models.classification.
- WHOLE mode: the utterance is sent as one block of text. The LLM must
  find its own sentence boundaries and return {text, label} pairs that
  reconstruct the utterance exactly -- see
  postprocess.verify_whole_mode_reconstruction for how that is checked.

"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from models.classification import (
    PresplitClassificationOutput,
    WholeClassificationOutput,
)


def _build_few_shot_examples(mode: str) -> str:
    """Build mode-appropriate few-shot examples.

    FABRICATED content -- no real transcript material.
    """
    if mode == "presplit":
        return """Example 1

CURRENT_UTTERANCE:
{
  "sentences": [
    {"id": "s0", "text": "I gave the order to move the unit forward."},
    {"id": "s1", "text": "We had no choice but to follow the commander's instructions."},
    {"id": "s2", "text": "Yes, I believe so."}
  ]
}

Output:
{"sentences": [
  {"id": "s0", "label": "agency"},
  {"id": "s1", "label": "externalization"},
  {"id": "s2", "label": "none"}
]}

--------------------------------------------------

Example 2

CURRENT_UTTERANCE:
{
  "sentences": [
    {"id": "s0", "text": "Shots were fired near the village that night."},
    {"id": "s1", "text": "I will answer your questions as best as I can."},
    {"id": "s2", "text": "I chose to warn the villagers before the attack began."}
  ]
}

Output:
{"sentences": [
  {"id": "s0", "label": "none"},
  {"id": "s1", "label": "none"},
  {"id": "s2", "label": "agency"}
]}

s0 is "none" despite describing a wartime event: it states nothing
about the speaker's own agency or externalization. s1 is "none" despite
being the speaker's own statement: it is procedural, not tied to a
wartime action or event."""

    else:  # whole
        return """Example 1

CURRENT_UTTERANCE:
"I gave the order to move the unit forward. We had no choice but to follow the commander's instructions. Yes, I believe so."

Output:
{"sentences": [
  {"text": "I gave the order to move the unit forward.", "label": "agency"},
  {"text": "We had no choice but to follow the commander's instructions.", "label": "externalization"},
  {"text": "Yes, I believe so.", "label": "none"}
]}

--------------------------------------------------

Example 2

CURRENT_UTTERANCE:
"Shots were fired near the village that night. I will answer your questions as best as I can. I chose to warn the villagers before the attack began."

Output:
{"sentences": [
  {"text": "Shots were fired near the village that night.", "label": "none"},
  {"text": "I will answer your questions as best as I can.", "label": "none"},
  {"text": "I chose to warn the villagers before the attack began.", "label": "agency"}
]}

The first sentence is "none" despite describing a wartime event: it
states nothing about the speaker's own agency or externalization. The
second is "none" despite being the speaker's own statement: it is
procedural, not tied to a wartime action or event. Note the sentences
above, concatenated with the original spacing, reconstruct
CURRENT_UTTERANCE exactly -- your own output must do the same."""


def build_system_prompt(mode: str) -> str:
    """Build the system prompt for the given mode ('presplit' or 'whole')."""
    if mode == "presplit":
        example_output = json.dumps(
            PresplitClassificationOutput.get_example_output(), indent=2
        )
        format_instructions = (
            "You receive CURRENT_UTTERANCE as a list of sentences, each "
            "with a short id (e.g. 's0'). Return exactly one label per id "
            "-- no id may be missing, repeated, or invented."
        )
    elif mode == "whole":
        example_output = json.dumps(
            WholeClassificationOutput.get_example_output(), indent=2
        )
        format_instructions = (
            "You receive CURRENT_UTTERANCE as a single block of text. "
            "Split it into sentences yourself and return each sentence's "
            "exact text (copied verbatim, not paraphrased) together with "
            "its label. Your sentences, concatenated in order, must cover "
            "the entire utterance -- do not skip or alter any part of it."
        )
    else:
        raise ValueError(f"Unknown mode: {mode!r}")

    few_shot_examples = _build_few_shot_examples(mode)

    return f"""
You are an expert in Political Psychology and agency detection in
accused testimony.

{format_instructions}

Every sentence must get exactly one of three labels: "agency",
"externalization", or "none". "none" is a required, valid answer -- do
not avoid it, and do not guess "agency" or "externalization" when
genuinely uncertain.

Return ONLY valid JSON.

You may use PREVIOUS_UTTERANCE and NEXT_UTTERANCE only to interpret
CURRENT_UTTERANCE. Never label anything in them.

Expected JSON shape:

{example_output}

--------------------------------------------------
SCOPE
--------------------------------------------------

Annotate how the speaker framed agency while recounting events,
actions, or opinions relating to the war.

A sentence may only be labeled "agency" or "externalization" if it
refers to an action, event, or opinion concerning the war. Any sentence
that does not -- regardless of how strongly it otherwise matches the
definitions below -- is labeled "none".

--------------------------------------------------
CONTEXT
--------------------------------------------------

The utterances are drawn from transcripts of the Yugoslav War trials
held at the ICTY in The Hague. Every speaker is an accused individual
who entered a plea of not guilty at the start of their trial -- some
may have since changed their plea, but the speaker is always someone
who was indicted and initially contested guilt, not a judge,
prosecutor, or unrelated witness.

The same accused individual may appear under different procedural
roles across transcripts: as the accused in their own trial, as a
witness testifying in a co-defendant's case, or -- for those who
represented themselves -- as counsel. This role label does not change
who is speaking or how their utterances should be classified; treat
all three roles identically.

--------------------------------------------------
WHAT IS AGENCY
--------------------------------------------------

The state of being active, usually in service of a goal, or of having
the power and capability to produce an effect or exert influence -- the
ability to act even beyond the knowledge of contextual limitations.

Includes acknowledgement of responsibility for acts committed.

--------------------------------------------------
WHAT IS EXTERNALIZATION
--------------------------------------------------

Based on Rauschenbach et al. (2016) and APA / Cambridge Dictionary of
Psychology definitions. Four sub-patterns, all counting as
"externalization":

- Informational uncertainty: lack of agency through missing
  information or limited scope of action. Includes rumor-driven ingroup
  narratives of threat (the "security dilemma": rumors of violence
  against one's community driving escalation).
- Constrained action: agency limited by equivocal facts; construction
  of a threat; reciprocated violence amid confusion; an outgroup
  described as unstructured/disorganized; a leader "constrained by
  community needs" or morally compelled to fight for group survival.
- Submission to powerful external forces: framing oneself as a mere
  spectator; displacing responsibility for an act onto invisible,
  impersonal, or uncontrollable forces.
- Group-membership survival: being one part of a larger process;
  internalizing group membership and not questioning group values, as a
  form of loyalty.

--------------------------------------------------
WHAT IS NONE
--------------------------------------------------

A sentence is "none" whenever it satisfies any of the following:

- It describes something that happened during the war, but states
  nothing about the speaker's own agency or externalization.
- It states something about the speaker's agency or externalization,
  but does not correspond to an event, action, or opinion of the
  wartime period.
- It does neither.

--------------------------------------------------
FEW-SHOT EXAMPLES
--------------------------------------------------

{few_shot_examples}

--------------------------------------------------
FINAL RULE
--------------------------------------------------

The scope rule overrides lexical cues: a sentence that lexically
resembles "agency" or "externalization" but does not concern the war is
always "none".

If genuinely uncertain between "agency" and "externalization" for a
war-related sentence, prefer "none" rather than guessing.
"""


def _context_or_none(text: Optional[str]) -> Optional[str]:
    """Return the raw context text, or None if absent/empty."""
    return text if text else None


def build_user_message(
    current_utterance: Dict[str, Any],
    *,
    mode: str,
) -> str:
    """Build the user message for the given utterance and mode."""
    payload: Dict[str, Any] = {
        "PREVIOUS_UTTERANCE": _context_or_none(
            current_utterance.get("previous_utterance")
        ),
        "NEXT_UTTERANCE": _context_or_none(
            current_utterance.get("following_utterance")
        ),
    }

    if mode == "presplit":
        payload["task"] = (
            "Classify each sentence in CURRENT_UTTERANCE. Use "
            "PREVIOUS_UTTERANCE and NEXT_UTTERANCE only for "
            "interpretation. Return exactly one label per sentence id -- "
            "no more, no fewer."
        )
        payload["CURRENT_UTTERANCE"] = {
            "sentences": [
                {"id": s["id"], "text": s["text"]}
                for s in current_utterance["sentences"]
            ]
        }
    elif mode == "whole":
        payload["task"] = (
            "Classify CURRENT_UTTERANCE sentence by sentence. Use "
            "PREVIOUS_UTTERANCE and NEXT_UTTERANCE only for "
            "interpretation. Copy each sentence's text verbatim; "
            "together they must reconstruct CURRENT_UTTERANCE exactly."
        )
        payload["CURRENT_UTTERANCE"] = current_utterance["text"]
    else:
        raise ValueError(f"Unknown mode: {mode!r}")

    return (
        "Classify CURRENT_UTTERANCE as instructed in the system prompt.\n\n"
        + json.dumps(payload, indent=2, ensure_ascii=False)
    )
