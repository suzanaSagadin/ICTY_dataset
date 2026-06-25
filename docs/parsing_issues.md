# Transcript Parsing Issues

Running log of bugs and quirks found in the parsed transcript JSON
(`data/parsed/<case>/*.json`), with concrete evidence from the source
documents. Each entry records what is wrong, a reproducible example, the root
cause (where known), and the impact on analysis.

Status legend: 🔴 open · 🟡 investigating · 🟢 fixed

---

## 1. 🟢 Examiner attribution lost — bare `Q.` lines become `UNKNOWN EXAMINER`

**Summary.** When a witness is questioned, the original transcript names the
examiner once in a header line (`Examined by Mr. Nice:`,
`Cross-examined by Mr. Milosevic:`) and then uses bare `Q.` / `A.` labels for
the back-and-forth. The parser **drops the header** and tags every bare `Q.`
line with the invented speaker **`UNKNOWN EXAMINER`** (role `counsel`). The
string `EXAMINER` does not appear anywhere in the source documents — it is
fabricated by the parser.

**Example.** Case `slobodan_milosevic`,
`Public_Transcript_of_Hearing_12_June_2002` (witness: Ambassador William
Walker).

Original document (from `data/converted_txt/.../12_June_2002.txt`):

```
                     WITNESS:  WILLIAM WALKER [Resumed]
                     Examined by Mr. Nice: [Continued]
   Q.  Ambassador, I've been given an indication...
   A.  I will try.
...
                     Cross-examined by Mr. Milosevic:
   Q.  [Interpretation] Is it true that, by Clinton, in June 1993, you
       were nominated and appointed ambassador to Panama...?
   A.  My "nomination" is a bit overstating it...
```

Parsed JSON:

```
id=18  [counsel] UNKNOWN EXAMINER: Ambassador, I've been given an indication...   ← really Mr. Nice
id=97  [counsel] UNKNOWN EXAMINER: Is it true that, by Clinton, in June 1993...   ← really Mr. Milosevic
```

**Root cause (confirmed in code).** `EXAM_HEADER_RE` in
[`parser/icty_parser.py:235`](../parser/icty_parser.py) only matches the **noun**
form `...examinATION by <name>:`, but the transcripts overwhelmingly use the
**past-participle** form `...examinED by <name>:`:

```python
EXAM_HEADER_RE = re.compile(
    r"^\s*((?:Cross-e|Re-(?:cross-)?e|E|Re-e)xamination)\s+by\s+"   # "examinATION"
    r"((?:Mr|Ms|Mrs|Miss)\.?\s+[\w'-]+)\s*:", re.IGNORECASE)
```

So `Cross-examined by Mr. Lukic:` never matches, `state.current_examiner` stays
`None`, and the `Q.`-handling branch falls back to `"UNKNOWN EXAMINER"`
([`icty_parser.py:544`](../parser/icty_parser.py)). Both header forms occur in
the corpus — `examined by` in **5,645** files, `examination by` in **6,079** —
which is why some files are attributed correctly and others are not.

**Fix.** Accept both forms, e.g.
`(?:Cross-|Re-(?:cross-)?)?[Ee]xamin(?:ation|ed)\s+by`, plus the
`Questioned by the Court:` variant (see issue #6 evidence). Then attribute
subsequent `Q.` lines to the captured examiner.

**Impact (significant).**
- **Conflation:** the *same* label `UNKNOWN EXAMINER` denotes different people
  in one file — Mr. Nice (prosecution) before the cross-examination header, and
  Milošević (the accused) after it. They cannot be told apart from the JSON
  alone.
- **Self-representation is invisible:** a self-represented accused conducting a
  cross-examination is tagged `UNKNOWN EXAMINER`, not by name and not as
  `accused`, so neither a name filter nor the `accused` role catches it. This
  systematically undercounts the very behaviour we are trying to find.
- In `12_June_2002` alone, `UNKNOWN EXAMINER` accounts for **225** of the
  `counsel` utterances (vs. 59 `MR. MILOSEVIC`, 26 `MR. NICE`, 6 `MR. KAY`).

**Second confirming example.** Case `todorovic`,
`Public_Transcript_of_Hearing_16_January_2002` (witness: `WITNESS M`). The
original contains the headers `Cross-examined by Mr. Lukic:` (offset 15746) and
`Cross-examined by Mr. Lazarevic: [Continued]` (offset 25456); `EXAMINER` again
appears nowhere in the source. The parsed JSON tags **149** of the `counsel`
utterances as `UNKNOWN EXAMINER`, e.g. the questions to `WITNESS M` (id=38, 40)
that belong to **Mr. Lukic**:

```
id=38 [counsel] UNKNOWN EXAMINER: [Interpretation] Good afternoon, sir.   ← really Mr. Lukic
id=39 [witness] WITNESS M [Resumed]: Good afternoon.
id=40 [counsel] UNKNOWN EXAMINER: I assume you've heard what the Trial Chamber is interested in...
```

This is not limited to self-represented defendants — it affects ordinary
attorney cross-examinations too, so the impact is archive-wide.

**Archive-wide scale.** `UNKNOWN EXAMINER` appears in **6,519 of 16,505** parsed
files, totalling **~1,485,412** utterances — i.e. roughly the entire `counsel`
question stream of every examination is mis-attributed. Most affected cases:

| Case | `UNKNOWN EXAMINER` utterances |
|---|---|
| kordic_cerkez / aleksovski / blaskic | 108,298 (each; shared joint-trial transcripts) |
| slobodan_milosevic | 101,587 |
| todorovic / milan_simic / simic | 52,265 (each) |
| dragomir_milosevic / galic | 46,259 (each) |
| oric | 39,236 |

**Suggested fix.** Parse the `(Examined|Cross-examined|Re-examined|Further ...
examined) by <name>:` header lines, set the current examiner from `<name>`, and
attribute subsequent `Q.` lines to that examiner (and `A.` lines to the current
witness) until the next header.

**Fix applied (2026-06-25).** `EXAM_HEADER_RE` now matches both `examin*ation*`
and `examin*ed*` forms, plus `Further ...`, `Questioned by the Court:`, and
`Questioned by Mr./Ms. X:`. Also recognises `Judge X` and `the Court` as
examiners. Result: `UNKNOWN EXAMINER` dropped from **~1,485,412 utterances in
6,519 files** to **~45,303 utterances in 515 files** (97% reduction). The
residual cases are transcripts where Q/A starts without any formal examination
header (judge says "go ahead" informally).

---

## 2. 🟡 Self-represented accused split across two labels (`accused` vs `counsel`)

**Summary.** Defendants who represented themselves appear under two different
speaker labels depending on what they are doing, so their courtroom speech is
split across two roles:

- `THE ACCUSED` → role `accused` (general statements, requests)
- `MR. <SURNAME>` → role `counsel` (conducting examination / argument)

This is partly *correct* transcript convention rather than a pure bug, but it
means role totals **undercount** self-representation, and it interacts with
issue #1 (much of the questioning is additionally hidden under
`UNKNOWN EXAMINER`).

**Evidence (whole-case counts).**

| Defendant | `THE ACCUSED` (accused) | `MR. SURNAME` (counsel) |
|---|---|---|
| Slobodan Milošević | 11,292 | 11,408 |
| Karadžić | 23,399 | 70,326 |
| Tolimir | 7,071 | 20,688 |
| Šešelj | 4,784 | 13,128 |

Karadžić additionally testified in his own defence — 50 utterances under
`RADOVAN KARADZIC [Resumed]` / `RADOVAN KARADZIC`, tagged `witness`.

**Suggested fix.** Add a derived, label-independent flag (e.g.
`self_represented` per case, and/or `is_accused_speaking` per utterance) so all
turns by a self-represented defendant can be identified regardless of whether
the surface label is `THE ACCUSED`, `MR. SURNAME`, `UNKNOWN EXAMINER`, or
`<NAME> [Resumed]`.

---

## 3. 🟡 Speaker-name spelling variants not normalised

**Summary.** The same speaker appears under slightly different surface strings,
which fragments any aggregation keyed on the raw `speaker` field.

**Examples.**
- `MR. TOLIMIR` vs `MR.  TOLIMIR` (double space)
- `MS. SESELJ` (1 occurrence) — almost certainly an OCR/typo for `MR. SESELJ`
- Witness names with/without the `[Resumed]` suffix
  (`WILLIAM WALKER [Resumed]` vs `THE WITNESS` for the same person within one
  examination).

**Impact.** Counts grouped by raw speaker string are slightly fragmented; needs
whitespace/typo normalisation and ideally witness-identity resolution
(`THE WITNESS` → the named current witness).

**Suggested fix.** Normalise whitespace and obvious typos; resolve `THE WITNESS`
to the active named witness using the examination context (ties in with #1).

---

## 4. 🔴 Multi-word judge surnames not recognised → utterances merged into the previous speaker

**Summary.** The speaker regex only allows a **single** word after `JUDGE`
(`JUDGE\s+[\w'-]+`), so judges whose surname is two words are never matched as a
speaker line. The colon then falls in the wrong place, `SPEAKER_RE` fails
entirely, and the line is treated as a **continuation of the preceding
speaker** — silently merging the judge's words into someone else's utterance.

**Evidence.** Affected judge labels and their occurrence counts in the source
texts:

| Judge label | lines |
|---|---|
| `JUDGE KARIBI WHYTE:` | 3,383 |
| `JUDGE EL MAHDI:` | 3,272 |
| `JUDGE ODIO BENITO:` | 190 |
| `JUDGE FASSI FIHRI:` | 157 |
| `JUDGE NIETO NAVIA:` | 99 |
| `JUDGE MARTIN CANIVELL:` | 33 |
| `JUDGE NINIAN STEPHEN:` | 4 |

Observed merge (case `mucic`/Čelebići-era, judge Karibi-Whyte):

```
[counsel] MS. MCMURREY: Thank you very much. JUDGE KARIBI WHYTE: This session is closing for today. (6.00 pm) (Tribunal adjourned) ...
```

Here an entire judge turn (and the adjournment) is glued onto counsel's
utterance. Cases with these judges (early Tadić, Čelebići/`mucic`, `aleksovski`,
`blaskic`, `naletilic_martinovic`, …) systematically lose judge utterances.

**Suggested fix.** Allow multi-word names after `JUDGE`
(e.g. `JUDGE\s+[\w'-]+(?:\s+[\w'-]+)*`), being careful not to swallow the body
text — anchor on the trailing colon.

---

## 5. 🔴 `[Interpretation]` leaks in as bracket-only noise utterances

**Summary.** `SPEAKER_RE` expects an optional `[...]` marker **before** the
colon (`MR. X [Interpretation]:`), but the transcripts place it **after**
(`MR. X: [Interpretation]`). The marker is therefore not consumed and becomes
the utterance body, producing junk utterances whose entire text is
`[Interpretation]` (or similar).

**Evidence.** ~4,479 such utterances in an 800-file sample (≈1% of all
utterances), e.g. `MR. LAZAREVIC: [Interpretation]`. These also break examiner
continuity: a `MR. X: [Interpretation]` line followed by `Q.` yields an empty/
noise utterance plus a `Q.` that is not attributed to `MR. X` (feeds issue #1).

**Suggested fix.** Recognise a trailing `[Interpretation]` (and similar
language/voiceover markers) after the colon, strip it from the body, and treat
the following `Q.` as spoken by that named speaker.

---

## 6. 🔴 Case-insensitive `WITNESS:` over-matches stray `witness:` lines, poisoning all later answers

**Summary.** `WITNESS_DECL_RE` is `re.IGNORECASE` and greedily captures the rest
of the line (`^\s*WITNESS:\s+(.+)`). A stray lowercase `witness:` occurring
mid-transcript (OCR/formatting artefact) is matched as a witness declaration,
and the **question text after it becomes the witness name**. Every subsequent
`A.` answer is then attributed to that bogus "name" until the next declaration.

**Evidence.** Case `delic`, `Public_Transcript_of_Hearing_24_September_2007`.
Source line:

```
6    witness:  At that time during the war, what was your understanding when
```

Result: `witness_name = "At that time during the war, what was your
understanding when"`, and answers are emitted with that 60-char string as their
`speaker`. (Also note the legitimate declaration two lines later is
`WITNESS: SEAD DELIC [Resumed]` under the header `Questioned by the Court:` — a
header form issue #1's regex also misses.)

**Suggested fix.** Make the witness declaration **case-sensitive** (`WITNESS:`),
and validate the captured name (e.g. mostly uppercase, short, no sentence
punctuation) before accepting it.

---

## 7. 🟡 Redacted-page line numbers and session tails leak into utterance text

**Summary.** `strip_line_numbers` only removes a leading 1–2 digit number when
followed by **2+ spaces**. On redacted/blank pages the lines are bare numbers
with no body, so the digits survive and get appended to whatever speaker is
"open", producing runs like `1 2 3 4 ... 25` and `Page N redacted` inside an
utterance. Session/adjournment tails behave similarly.

**Evidence.**

```
[judge]   JUDGE ORIE: 1 2 3 4 5 6 7 8 9 10 11 Page 18433 redacted. Private session. 12 13 ... 25
[judge]   JUDGE KWON: to be reconvened on Tuesday, the 8th day of March, 2011, at 9.00 a.m. 15 16 ... 25
[witness] THE WITNESS: to be reconvened on Wednesday, the 12th day of November, 2008, at 2.15 p.m. 13 14 ...
```

**Suggested fix.** Strip bare standalone line numbers (number-only lines), and
detect inline `Page N redacted` / `to be reconvened ...` tails as procedural
notes rather than utterance body. Note the existing `REDACTED_RE` only fires on
a whole-line match, so inline redaction markers slip through.

---

# How to check the parser systematically

A repeatable methodology for finding more issues (used to produce the entries
above):

1. **Three-way diff per file.** For a transcript, line up
   `archive/*.doc` → `converted_txt/*.txt` → `parsed/*.json`. The `.txt` is
   ground truth for *content*; the `.json` is what we ship. Most bugs show up as
   a mismatch between a `.txt` structural marker and how the `.json` rendered
   it.

2. **Audit each regex against the real corpus, not assumptions.** For every
   pattern in `icty_parser.py` (speaker, Q/A, exam header, witness decl,
   redaction, session break), `grep` the actual variants in `converted_txt/`
   and confirm the regex matches them. Issue #1 (examined vs examination) and
   #4 (multi-word judges) were both regexes that looked right but didn't match
   the data.

3. **Invariant checks over all 16,505 JSON files.** Flag utterances that
   violate expected invariants and eyeball the top offenders:
   - `type == "question"` but `role != "counsel"`
   - `type == "answer"` but `role != "witness"`
   - `role == "unknown"`
   - speaker length > 40 chars (sentence captured as a speaker → #6)
   - text that is bracket-only `^\[.*\]$` (→ #5)
   - text containing `redacted` or a run of ≥6 bare numbers (→ #7)
   - `speaker == "UNKNOWN EXAMINER"` (→ #1)
   - witness speaker containing `[` (→ #3)

4. **Speaker/role inventory.** Build a global `Counter` of `(speaker, role)` and
   of distinct speaker strings per case; near-duplicates and surprising roles
   surface normalisation gaps (#2, #3) and mis-classification.

5. **Round-trip / coverage check.** Compare counts of structural markers in the
   `.txt` (`grep -c` for `Q.`, `A.`, examination headers, `WITNESS:`) against
   the corresponding counts in the `.json`. Large gaps indicate dropped or
   merged content (e.g. #4 merges, #1 mis-tags).

6. **Reconciliation after a fix.** Re-run the parser on a fixed set of probe
   files (Milošević 12 June 2002, Todorović 16 Jan 2002, a Karibi-Whyte file, a
   Delić file) and confirm the specific anomaly counts drop to ~0 without
   regressing the others.

## 8. 🔴 Cross-case contamination — transcripts filed under the wrong case folder

**Summary.** Some case folders in `data/parsed/` contain transcripts that belong
to an entirely different case. The scraper pulls transcripts by case-number
prefix from the ICTY website; when two cases share a prefix (e.g. a sub-case
that was later renumbered) or are cross-referenced on the same page, all
transcripts end up in the same download and are placed in the wrong folder.

**Evidence.**

| Folder | Expected case | Contaminating `case_id` | Belongs to | Files |
|---|---|---|---|---|
| `blagojevic_old/` | IT-02-60 (Blagojević) | IT-98-33 | Krstić | ~23 |
| `martic/` | IT-03-72 (Martić) | IT-95-11 | wrong/old case number | ~22 |
| `jokic_old/` | IT-01-44 (Jokić) | IT-98-33 | Krstić | 1 |
| `obrenovic_old/` | IT-01-43 (Obrenović) | IT-98-33 | Krstić | 1 |
| `karadzic/` | IT-05-88 (Karadžić) | IT-94-1, IT-08-91, IT-96-93, IT-98-30 | various | 5 |
| `dokmanovic/` | IT-95-13 (Dokmanović) | IT-94-1 | Tadić? | 1 |
| `dragan_nikolic/` | IT-94-2 (Nikolić) | IT-92-4 | unrelated | 1 |
| `dragomir_milosevic/` | IT-98-29 (D. Milošević) | IT-05-87 | Milutinović | 1 |

**Root cause.** Blagojević was originally indicted as a sub-case of Krstić
(`IT-98-33/1`) before being renumbered `IT-02-60`. The ICTY website lists all
`IT-98-33*` transcripts together, so the scraper downloaded the Krstić trial and
appeal transcripts into the Blagojević folder. The same mechanism affects
`jokic_old/` and `obrenovic_old/`. The `martic/` folder likely has a similar
renumbering history (IT-95-11 → IT-03-72). Single-file outliers in other folders
are probably cross-references or shared hearings that appear on both case pages.

**Not contamination.** Folders like `aleksovski/blaskic/kordic_cerkez` sharing
IT-94-14 / IT-95-14 transcripts, and `popovic/tolimir` sharing IT-05-88 /
IT-05-85, are **joint trials** — those are expected and correct.

**Suggested fix.** After scraping, validate each transcript's `case_id` against
the expected case for that folder and either move mismatched files to the correct
folder or flag them for manual review. For the Krstić sub-case pattern, filter
on the full case number (e.g. `IT-98-33/1` vs bare `IT-98-33`).

---

# How to check the parser systematically

Probe files used so far:
- `slobodan_milosevic/Public_Transcript_of_Hearing_12_June_2002` — #1, #5
- `todorovic/Public_Transcript_of_Hearing_16_January_2002` — #1
- `delic/Public_Transcript_of_Hearing_24_September_2007` — #6
- any `mucic` / `aleksovski` file with Karibi-Whyte / El Mahdi — #4
