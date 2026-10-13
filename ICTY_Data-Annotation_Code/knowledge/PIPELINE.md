# ICTY Agency/Externalization Classification Pipeline

This pipeline runs sentence-level classification over ICTY (Yugoslav War
Tribunal) accused-testimony transcripts, labeling each sentence as
`agency`, `externalization`, or `none`, via an LLM.

---

## 1. Data

### 1.1 Input

A JSON file (or directory of JSON files) of utterance-transcript
records — either a bare JSON list, or an object wrapping the list under
`utterances`, `data`, or `records`. Each record needs, at minimum:

| Source field | Meaning |
|---|---|
| `unique-id` / `unique_id` | Stable identifier, used only for reintegration — never a labeling target. |
| `utterances` | The utterance text to be classified. |
| `previous_utterance` | Prior utterance, for context only — never tagged. |
| `following_utterance` | Next utterance, for context only — never tagged. |

Every other field on the source record (case metadata, speaker, role,
type, ...) is intentionally dropped during preprocessing.

### 1.2 Two extraction modes

Set once per run via `preprocessing.py --presplit-sentences`:

- **WHOLE mode (default).** The full utterance is handed to the LLM as
  one block of text. The LLM finds its own sentence boundaries and
  returns `{text, label}` pairs. The pipeline then verifies the
  returned sentences, concatenated in order, reconstruct the original
  utterance exactly (`postprocess.verify_whole_mode_reconstruction`).
- **PRESPLIT mode.** Sentences are split deterministically upstream and
  given short local ids (`s0`, `s1`, ...) plus character offsets. The
  LLM only ever returns `{id, label}` pairs — never re-emits text.
  Mechanically safer (reintegration is an exact-id lookup instead of
  fuzzy text matching); the intended fallback if WHOLE mode proves
  unreliable.

### 1.3 Labels

Every sentence gets exactly one of three labels:

- **`agency`** — the speaker frames themselves as active, capable, or
  responsible for a wartime action, event, or opinion.
- **`externalization`** — the speaker frames agency as constrained,
  displaced, or absent (informational uncertainty, constrained action,
  submission to powerful external forces, group-membership survival).
- **`none`** — required, valid, and expected for any sentence that
  doesn't concern the war, or concerns the war but says nothing about
  the speaker's own agency/externalization. The scope rule ("must be
  war-related") always overrides lexical resemblance to the other two
  labels.

---

## 2. Pipeline stages and directory layout

`pipeline.py` orchestrates three stages into one timestamped run
directory:

```
data/output/<model>/processing_<timestamp>/
├── 00_input/               # copy of the raw source file (reproducibility)
│   └── source.json
├── 01_preprocessing/       # preprocessing.py output
│   ├── extracted.json
│   └── extracted_metadata.json
├── 02_llm/                 # run.py output
│   ├── extracted_output.json
│   ├── extracted_failed.json       # only if tolerated failures occurred
│   ├── extracted_checkpoint.json
│   ├── processing_metadata.json
│   └── log/                        # per-utterance prompts/raw/reasoning
├── 03_reintegration/       # reintegration.py output
│   └── reintegrated.json
├── pipeline.log
└── pipeline_metadata.json
```

Each stage runs as a real subprocess (`sys.executable`), so
`preprocessing.py`, `run.py`, and `reintegration.py` behave identically
whether invoked standalone or through the orchestrator. Stage 03 only
runs if stage 02 actually produced `02_llm/extracted_output.json` for
the document — checked by file existence, since a `tolerated_complete`
document (§4) still writes that file and should still be reintegrated,
while a genuinely `incomplete` document has nothing to reintegrate.

---

## 3. Script by script

### `settings.py`
Single source of pipeline configuration (`pydantic_settings`).

- **`LLMConfig`** — provider selection (`local` / `openai` / `anthropic`
  / `dhinfra`), model identity, generation params (`temperature`,
  `max_tokens`, `reasoning_budget`). For `local`, presets in
  `MODEL_PRESETS` auto-fill the HuggingFace repo/filename.
- **`FixerConfig`** — separate, usually cheaper LLM settings for the
  malformed-JSON auto-repair step in `parser.py`.
- **`LLM_BACKEND`** — the single switch (`"local"`, `"openai"`,
  `"anthropic"`, `"dhinfra"`) selecting which `LLM_CONFIGS` entry is
  active. Currently `"dhinfra"`.
- **`Settings.max_line_errors`** — per-document failure tolerance (see
  §4). `None` = zero tolerance (any failure blocks the document's
  output); `N ≥ 0` = up to N permanently-failed utterances are
  tolerated per document. Currently `100`.

### `preprocessing.py`
Reads raw transcript JSON and extracts exactly what the LLM step
needs: `unique_id`, `text`, `previous_utterance`, `following_utterance`,
and (PRESPLIT mode only) a deterministic `sentences` list. Includes:

- A dependency-free heuristic sentence splitter (`split_into_sentences`)
  that guards against title abbreviations (`Dr.`, `Mr.`, ...) and
  decimal numbers, but is not a full NLP tokenizer — spot-check on real
  data.
- Selection helpers for testing on a subset: `--unique_ids`,
  `--random_sample`/`--random_seed`, `--limit`/`--skip` (mutually
  exclusive).
- Writes both the LLM-facing output and a separate metadata file
  (sampling seed, counts) that is never embedded in the output itself.

### `models/classification.py`
Pydantic models defining the LLM's required output shape, one pair per
mode:

- **PRESPLIT:** `PresplitSentenceLabel {id, label}` →
  `PresplitClassificationOutput {sentences: [...]}`.
- **WHOLE:** `WholeSentenceLabel {text, label}` →
  `WholeClassificationOutput {sentences: [...]}`.

Both forbid extra fields (`extra = "forbid"`) and expose
`get_example_output()`, used to build the few-shot example embedded in
the system prompt.

### `prompts/classification.py`
Builds the system prompt (task definition, label criteria, few-shot
examples, mode-specific format instructions) and the per-utterance user
message (`CURRENT_UTTERANCE` + `PREVIOUS_UTTERANCE`/`NEXT_UTTERANCE` for
context only). The system prompt is built once per document (mode
doesn't change mid-document); the user message is built once per
utterance.

### `provider.py`
Direct HTTP calls to whichever backend `LLMConfig.provider` selects —
`urllib`-based throughout, no `requests`/`openai` SDK dependency except
where the `openai`/`anthropic` providers explicitly need their own
client libraries. Returns `(reasoning, content)` from every call.

- **`_call_local`** — starts/reuses a `llama-server` subprocess,
  POSTs to its OpenAI-compatible endpoint.
- **`_call_dhinfra`** — POSTs to the DHInfra OpenAI-compatible
  endpoint. Sends the reasoning cap as `thinking_token_budget` (the
  pipeline's internal name stays `reasoning_budget`). Reads the
  reasoning trace from the `reasoning` key (confirmed against the live
  API; `reasoning_content` is checked as a fallback in case this
  differs across models/versions). Raises `DHInfraTruncatedResponse`
  (carrying `reasoning` and `finish_reason`) when content comes back
  empty, so the caller can persist whatever reasoning trace exists
  before the exception propagates.
- **`_call_openai`** / **`_call_anthropic`** — thin wrappers around the
  respective official SDKs.
- **`extract_json_from_text`** — strips markdown fences and leaked
  `<think>...</think>` remnants, then extracts the first balanced
  `{...}`/`[...]` block (falling back to `ast.literal_eval` for
  Python-dict-style quoting).

### `parser.py`
Turns raw LLM text into a validated Pydantic object.

1. Extract JSON (`provider.extract_json_from_text`).
2. Parse + validate against the expected model class.
3. **Schema-echo recovery:** if the model returned the JSON *Schema*
   instead of data (`"$defs"` present), retry once with an explicit
   "you returned the schema, not data" correction.
4. **Fixer recovery:** on any other parse/validation failure, ask a
   (possibly separate, cheaper) fixer LLM to repair the malformed JSON
   and retry once. Not wrapped in a further try/except — a fixer
   failure propagates uncaught.

Every recovery path's raw content and any newly-fetched reasoning are
saved to the run's log; the successful attempt returns
`(validated_output, recovery_metadata | None)`.

### `postprocess.py`
- **`verify_whole_mode_reconstruction`** — WHOLE mode only. Confirms
  the LLM's sentences, concatenated in order, exactly reconstruct the
  original utterance (catches both altered wording and silently
  dropped content in one pass).
- **`build_output_utterance`** — wraps a validated result into the
  final per-utterance record, `status="ok"`.
- **`build_failed_output_utterance`** — builds a `status="failed"`
  placeholder record (`sentences=None`, error attached) for a
  permanently-failed utterance tolerated under `max_line_errors`.

### `run.py`
The core per-document processing loop.

- **`process_utterance`** — one utterance through the LLM (via
  `provider.call_llm`), then `parser.parse_and_validate`, then the
  mode-specific completeness check (id-set match for PRESPLIT, text
  reconstruction for WHOLE). Persists prompt/raw/reasoning logs
  unconditionally, including on a `DHInfraTruncatedResponse` failure
  (reasoning is saved before the exception propagates).
- **`process_file`** — per-document orchestration with durable
  per-utterance checkpointing (only real successes ever enter
  `checkpoint["records"]`, so `--resume` naturally retries exactly
  what failed). Three possible outcomes per document:
  - **`fully_complete`** — every utterance succeeded → `*_output.json`.
  - **`tolerated_complete`** — some utterances permanently failed, but
    the count is within `max_line_errors` and every utterance was at
    least attempted → `*_output.json` **is still written**, with
    failed entries as `status="failed"` placeholders, plus a companion
    `*_failed.json` (per-utterance `error_type`, `error_message`,
    `finish_reason`, tolerance value) for traceability.
  - **`incomplete`** — failures exceed tolerance, or not every
    utterance was attempted (e.g. `continue_on_error=False` stopped the
    loop early) → no `*_output.json` is written.
- **CLI flags:** `--input-dir`/`--input-file`, `--run-dir`, `--resume`,
  `--allow-partial` (return exit code 0 even on a hard failure).

### `helpers.py`
I/O utilities used throughout:

- **`save_raw`** — writes `<name>_raw.txt` (content only — no reasoning
  mixed in, so this always reflects exactly what `parser.py` receives)
  and, when reasoning is present, a separate `<name>_reasoning.txt`.
- **`save_prompt`** — writes `<name>_prompt.txt` (system + user
  message).
- **`load_json`** / **`save_json`** — plain JSON I/O.

### `reintegration.py`
Merges `run.py`'s `*_output.json` back into `preprocessing.py`'s
`extracted.json`, producing one combined document per utterance:

- **`status="ok"`** — PRESPLIT mode joins `{id, label}` against the
  source's `{id, text, start_char, end_char}`; WHOLE mode recovers
  `{start_char, end_char}` by locating each returned sentence text in
  the original utterance (same sequential-match logic already proven
  by `verify_whole_mode_reconstruction`). Both modes converge on
  `{id, text, start_char, end_char, label}`.
- **`status="failed"`** — passed through as-is: `sentences=None`, the
  original `error` object attached, no join attempted.
- Fails loudly if the preprocessed file and the output file don't cover
  exactly the same set of `unique_id`s (defends against reintegrating a
  mismatched/stale file pair).
- Invoked automatically by `pipeline.py` as stage 03 (see below). Can
  still be run manually via `python3 reintegration.py --run-dir
  <run_dir>` (auto-locates `01_preprocessing/extracted.json` and
  `02_llm/*_output.json`) or with explicit
  `--extracted_file`/`--llm_output_file`.

### `pipeline.py`
Orchestrates `preprocessing.py` → `run.py` → `reintegration.py` as
subprocesses, streaming output to both console and `pipeline.log`.
Copies the source input into `00_input/` for reproducibility (with a
SHA-256 logged). Validates LLM credentials/config before starting
(fails fast rather than after a long preprocessing run). Supports
`--resume` (reuses `00_input/` and `01_preprocessing/` if already
complete, resumes `02_llm/` via `run.py --resume`; stage 03 is cheap
pure-Python and always reruns rather than being skip-on-resume, since
re-running it is idempotent). Writes `pipeline_metadata.json`
summarizing all three stages' outcomes. Stage 03 is skipped (not
treated as a failure) when stage 02 produced no output file at all —
i.e. the document was genuinely incomplete, not just partially
tolerated.

---

## 4. Failure tolerance (`max_line_errors`)

Controls what happens when individual utterances permanently fail
(e.g. persistent `finish_reason='length'`, or errors that survive the
parser's fixer/retry logic):

| `max_line_errors` | Behavior |
|---|---|
| `None` | Zero tolerance — any utterance failure leaves the document incomplete; no `*_output.json`. (Original strict behavior.) |
| `N ≥ 0` | Up to N failed utterances per document are tolerated. Document still completes; failed utterances appear as `status="failed"` placeholders; full failure details go to a companion `*_failed.json`. |

Utterances that were never *attempted* at all (e.g. `continue_on_error
= False` stopped the loop early) never count toward this tolerance —
that always leaves the document incomplete regardless of the setting.

A tolerated-complete document can later be upgraded to fully-complete:
failed utterances are never written into the checkpoint, so `--resume`
retries exactly them.

---

## 5. Known gaps

- **`settings.py` retains an `NER_` environment-variable prefix**
  (`NER_LLAMA_SERVER_BIN`, and the module docstring's stated override
  convention) — a carryover from the sibling NER pipeline. Harmless but
  worth a deliberate decision (shared infra vs. rename) rather than
  leaving it as an unexamined relic.
