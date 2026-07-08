# TODO

## Current state (2026-07-05)

Pipeline is complete and rerun end-to-end: **250,529 accused utterances**
(deduped, counsel collisions excluded) in `dataset_creation/accused_utterances.csv`.
Rebuild order: `run_parser.py` → `dedup_hearings.py` → `chunk_extracton.py` (see README).

## Open items

- [ ] **Wire `accused_attribution.csv` into `chunk_extracton.py`** as an `accused_name` column (named label → direct; bare label → attribution table)
- [ ] **Regression test suite** — pin ~8 known-bad files as golden tests (plan below)
- [ ] **Review 22 new/changed pleas rows** — see `data_250625/reports/pleas_comparison.csv`

## Next steps (older)

- [x] **Check transcript parser quality** on new archive results — done 2026-07-05: validator re-run clean; utterance-recognition audit + fixes (see "Data quality findings" below)
- [x] **Update `dataset_creation/chunk_extracton.py`** — paths updated to `data_250625/`, accused detection now uses parser's `is_accused` flag instead of re-implementing matching
- [x] **Update `dataset_overview.md`** with parser results once available
- [x] **Review the 5 flagged `jankovic_g_et_al` files** (25 bare utterances) — done 2026-07-08: recorded in `data_250625/reports/accused_attribution_overrides.csv` (method `manual`). 15th_April_2005 → Gojko Janković (registrar misread case call as Stanković; Judge El Mahdi corrected on the record — Janković's initial appearance); 4th_March_2005_90da15 → Radovan Stanković (status conference); the three 2007 appeal hearings (15 Oct, 31 Oct, 27 Aug) → Dragan Zelenović (`THE APPELLANT`). All 294 bare utterances in the case now resolved; case dropped off `needs_review`. Regenerate the report with a **full** run (`python parser/attribute_accused.py`, no `--case` — `--case` rewrites the whole CSV with only that case's rows).
- [ ] **Manually check 16 cases with icty_slug but no CIS file on icty.org:**
  - IT-01-43  Obrenovic (obrenovic_old)
  - IT-01-44  Jokic (jokic_old)
  - IT-01-45  Gotovina (gotovina_old)
  - IT-01-46  Ademi (ademi_old)
  - IT-02-53  Blagojevic et al. (blagojevic_53)
  - IT-02-56  Nikolic M. (nikolic_old)
  - IT-02-57  Popovic (popovic_old)
  - IT-02-58  Beara (beara)
  - IT-02-63  Nikolic D. (drago_nikolic)
  - IT-03-70  Pavkovic et al. (pavkovic)
  - IT-04-76  Norac (norac)
  - IT-05-86  Pandurevic (pandurevic_trbic)
  - IT-95-4   Meakic et al. (mejakic_old)
  - IT-95-8   Sikirica et al. (sikirica)
  - IT-95-8/1 Banovic et al. (fustar)
  - IT-98-33/1 Blagojevic (blagojevic_old)

## Known issues (from old archive, check if still relevant)

- [ ] Decide how to record the 3 `unknown` pleas (`bobetko`, `marinic`, `raznatovic` — no plea was ever entered)
- [ ] **Infer `case_id` for transcripts that have none** — in old archive 37% had `null` case_id. Check if this persists in new data.
- [ ] Duplicate rows in `pleas.csv` — cases with both `cis_X.pdf` and `cis_X_en.pdf` produce duplicate entries

## Plea filtering feasibility

Filtering accused utterances by plea (e.g. excluding guilty pleas) is **not straightforward** due to gaps between `pleas.csv` and the `is_accused` speaker data. Analysis (2026-07-01):

**Guilty pleas are rare** — only 5 entries (3 people) across 134 total:
- `babic`: Milan Babic (guilty, 22 utterances)
- `erdemovic`: Drazen Erdemovic (guilty, 44 utterances)
- `jankovic_g_et_al`: Radovan Stankovic (guilty, 296 utterances — other 2 accused pled not guilty)

That's 362 utterances out of 384,299 `is_accused=True` total (<0.1%).

**But matching accused speakers to their plea entry has three problems:**

1. **94,474 utterances (24.6%) have no plea at all** — 23 transcript folders (sub-cases, old case numbers like `beara`, `blagojevic`, `ademi`, `norac`) are not in `pleas.csv`. Their accused are tracked under parent/joint case folders.

2. **8,149 utterances (2.1%) are ambiguous in multi-accused cases** — speaker is just `THE ACCUSED` or `THE APPELLANT` with no name, so we can't tell which accused is speaking. Worst: `popovic_et_al` (7,100 ambiguous, 7 accused).

3. **Name format mismatch** — `pleas.csv` uses full names with diacritics (`ZLATKO ALEKSOVSKI`, `RADOVAN KARADŽIĆ`), speakers use labels (`MR. ALEKSOVSKI`, `THE ACCUSED`, `THE APPELLANT KORDIC`). Matching requires last-name extraction and fuzzy diacritic handling.

**Decision pending:** Given guilty pleas affect <0.1% of data, plea filtering may not be worth the complexity. If needed, the simplest approach would be to hard-code exclusion of the 3 guilty-plea cases/accused rather than building general plea-matching infrastructure.

**Update (2026-07-05):** Attribution of bare `THE ACCUSED`/`THE APPELLANT` labels is now solved by `parser/attribute_accused.py` → `data_250625/reports/accused_attribution.csv` (one row per transcript file with accused utterances; see dataset overview for method details). Findings:

- Only **one** multi-accused case has non-uniform pleas: `jankovic_g_et_al` (Stanković guilty; Janković, Zelenović not guilty). Its hearings are per-accused (separate arrests), and the registrar's case call resolves 269/294 bare utterances; the remaining 25 (5 files) were resolved manually on 2026-07-08 (see `accused_attribution_overrides.csv`) — all 294 now attributed.
- **Caveat on "guilty pleas are rare":** that counted *initial* pleas. `pleas.csv` notes show **18 more accused later changed plea to guilty** (Plavšić, both Obrenović entries, M. Nikolić, D. Nikolić, Zelenović, Jelisić, Banović, Bralo, Češić, Deronjić, Jokić, Mrđa, Rajić, M. Simić, Todorović, …). If "ever pled guilty" is the filter, ~21 people are affected, not 3 — but nearly all are single-accused cases, so attribution stays trivial.
- Remaining work tracked under "Open items" at the top: wire `accused_attribution.csv` into `chunk_extracton.py`. (The 5 flagged `jankovic_g_et_al` files were resolved 2026-07-08 — see `accused_attribution_overrides.csv`.)

---

## Data quality findings (2026-07-05 error hunt)

Validator re-run: no regressions vs. documented final state. Three new error classes found beyond the validator's coverage:

### 1. Cross-folder duplicate hearings — BIG

UCR files the same hearing under multiple related case numbers (joined/severed cases), so the per-case scraper downloaded them multiple times. Verified by full-text hash: **937,925 excess utterances (15.4% of corpus)** across 1,912 duplicate groups. In `accused_utterances.csv`, 225,397 rows (58.7%) sit in duplicated files → **~112k redundant rows (~29% of the dataset)**. Top pairs by excess utterances:

| Pair | Excess utterances |
|---|---|
| karadzic_and_mladic_95-5 ↔ 95-5_18 | 311,996 (537 identical files) |
| blaskic ↔ kordic_and_cerkez | 153,443 |
| popovic_et_al ↔ zdravko_tolimir | 112,115 |
| dokmanovic ↔ mrksic | 103,905 |
| dordevic_vlastimir ↔ milutinovic_et_al_05-87 | 97,148 |
| galic ↔ milosevic_dragomir | 71,415 |
| lukic_et_al ↔ vasiljevic | 53,316 |
| aleksovski ↔ blaskic | 26,034 |

- [x] **Built dedup table** — `parser/dedup_hearings.py` → `data_250625/reports/hearing_dedup.csv` (1,854 groups; ownership via transcript case number > pleas data > date range > pair majority; 1 group on alphabetical fallback: Milošević 01-50/01-51 status conf, either owner fine). `chunk_extracton.py` skips non-canonical copies.
- Note: the overview's claim that the new scraper avoids duplication only holds for *identical case numbers*, not related-case filings.
- Within-folder duplicates are negligible (8 files, 1,253 excess utterances).

### 2. `is_accused` false positives — counsel sharing the accused's surname

The `MR. <lastname>` rule matches defense counsel with the same surname as their client:

- `milutinovic_et_al_05-87` / `MR. LUKIC` = **Branko Lukić, counsel** for accused Sreten Lukić — 8,853 rows (conducts 109 examinations; appearance lines confirm)
- `popovic_et_al` / `MR. NIKOLIC` = **Predrag Nikolić, attorney on Beara's team** — 1,077 rows (self-introductions in transcripts confirm)

~10k rows ≈ 2.6% of `accused_utterances.csv`. (`MR. JELISIC` in jelisic_and_cesic checked and genuine: 67 statements, no examinations.)

- [x] **Fixed via `parser/counsel_exclusions.csv`** — checked by `check_is_accused()` (takes effect on next re-parse) AND applied directly in `chunk_extracton.py` (effective now). Verified: counsel self-introduce ("I am Branko Lukic; I represent General Sreten Lukic"). Dataset after dedup + exclusions: 384,299 → **250,529 rows**.

### 3. Files parsing to 0 utterances — NOT an error

346 files parse to zero utterances; sampled ones are fully closed/private sessions ("Page 8113 to 8243 redacted - in closed session"). Correct behavior, just documenting.

### 4. Utterance recognition audit (2026-07-05)

- **Text coverage**: 400-file sample, parsed output vs. line-number-stripped source txt — median coverage 103% (speaker names counted on output side), no file below 94%. No text loss.
- **Merged turns**: utterances containing another speaker's label mid-text and *not* inside a quote: 369 of 6.07M (0.006%). 277 caused by 1996-era line-number format (`23<TAB>JUDGE STEPHEN:` — single tab, `LINE_NUMBER_RE` required 2+ spaces), 255 of those in `tadic`.
- [x] **Fixed `LINE_NUMBER_RE`** to accept a single tab after the number. Re-parse recovered +23,654 utterances (6,071,387 → 6,095,041); suspicious merges 369 → 101; inline-line-number merges 277 → 12. Accused dataset unchanged (merged turns were witness/judge/counsel speech): still **250,529 rows**.
- Residual 101 merges (top: stakic 17) are same-line quirks like `[Trial Chamber confers]JUDGE LIU:` (no space after bracket) and `* THE WITNESS:` (stray asterisk) — rare, not worth new parser rules; many mid-text labels are legitimate read-back quotes that *should* stay embedded.

---

## Parser fix plan

Ordered by impact and dependency. See detailed problem descriptions below each item.

### Phase 0 — Baseline & guardrail ✅

- [x] **Built `parser/validate_parse.py`** — baseline saved to `logs/validation_baseline.json`

### Phase 1 — Speaker regex ✅

- [x] **#1 Multi-token speaker labels** — broadened MR/MS/MRS/MISS to `{0,2}` extra tokens. MR. DI FAZIO: 0→327 files (31,733 utterances).
- [x] **#2 THE APPELLANT** — added to speaker regex, `classify_role` (→ accused), `check_is_accused`. 0→574 files.
- [x] **#3 Initials preserved** — `(?:[A-Z]\.\s+)?` group added. MR. J. SIMIC ≠ MR. K. SIMIC now distinct. 0→196 files.
- [x] **#4 New role labels** — DR., THE USHER (76 files), THE REGISTRY, THE COURT REPORTER (65), THE FRENCH INTERPRETER (12), THE PROSECUTION.
- [x] **#1 extra** — `THE ACCUSED GENERAL BLASKIC` now matches (orphans: 1→0).

### Phase 2 — Metadata ✅

- [x] **#8 Filename date parsing** — rewrote with month alternation, hash suffix support, abbreviated months, French months, ordinal DDth_Month, no-separator `01april_1997`, etc. Null dates: 227→2 (remaining: `10_September.doc` with no year, `Please_see_IT-96-23` redirect file).
- [x] **#9 Dual case_id** — `case_id_base` (from `case_mapping.csv`, 100% populated) + `case_id_transcript` (from text). `CASE_ID_RE` broadened for missing-dash form. Updated `chunk_extracton.py` and `extract_speakers.py`.

### Phase 3 — Text-cleaning ✅

- [x] **#5 Exam headers** — `EXAM_HEADER_RE` now handles: `Cont'd`/`Contd.`, trailing period, `MR..` typo, multi-word examiner names. Exam-in-text: 1,251→771.
- [x] **#6 Parenthesized procedural notes** — `PROCEDURAL_RE` now matches `( … )` alongside `[ … ]`. `SESSION_TYPE_RE` extended for parens. Paren-in-text: 12,401→8,212.
- [x] **#7 THE WITNESS: NAME declarations** — standalone `THE WITNESS: ANTO JURIC` lines now set `witness_name` instead of creating utterance with name as text.
- [x] **#11 Line-number runs** — `_strip_line_number_runs()` removes trailing ascending `N N+1 N+2...25` sequences from utterance text.

### Phase 4 — Investigation ✅

- [x] **#10 Cross-case contamination** — 540 disagreements across 46 folders. **All are joint trial sub-case mismatches** (e.g. IT-95-14/2 → IT-95-14) or multi-case hearings (Milosevic IT-99-37/IT-01-50/IT-01-51). **No genuine misfiling found.** Report at `logs/contamination_report.md`.
- [x] **#12 UNKNOWN EXAMINER** — down from 2,102 to 441, across 10 cases (top: galic 165, milosevic_dragomir 165). Residual is genuinely informal exam starts. Low priority.

### Phase 5 — Regeneration ✅

- [x] **Full re-parse** completed (6,071,387 utterances across 12,651 files).
- [x] **`speakers_all.csv` regenerated** — MR. DI FAZIO, THE APPELLANT now appear; UNKNOWN EXAMINER at 441 (was 547,815 in stale CSV).
- [x] **`chunk_extracton.py` updated** to use `case_id_base`.

### Overall results

| Metric | Baseline | Final | Change |
|---|---|---|---|
| Total utterances | 6,057,128 | 6,071,387 | +14,259 recovered |
| Orphan speakers | 1 | 0 | Fixed |
| Embedded labels | 20,220 | 6,029 | -70.2% |
| UNKNOWN EXAMINER | 2,102 | 441 | -79.0% |
| All 10 offender labels | ALL MISSING | ALL OK | Fixed |
| Null date | 227 | 2 | -99.1% |
| case_id_base 100% | N/A | 0 nulls | New field |
| Exam headers in text | 1,251 | 771 | -38.3% |
| Paren procedural in text | 12,401 | 8,212 | -33.8% |
| is_accused=True | 382,436 | 384,299 | +1,863 |

### Remaining (not yet done)

- [ ] **Regression test suite** — pin ~8 known-bad files as golden tests with asserted counts

#### Regression test plan

Create `parser/test_regression.py` using `pytest`. Each test picks a specific transcript txt file known to exercise a fixed bug, parses it through `process_file()`, and asserts concrete counts/values. No LibreOffice needed — tests run against cached `.txt` files.

**Test cases to pin:**

1. **Multi-word surname** — `simic_et_al/.../February_4,_2005.txt` (DI FAZIO-heavy)
   - Assert `MR. DI FAZIO` appears as a speaker
   - Assert no utterance has `MR. DI FAZIO` embedded in its `text` field

2. **THE APPELLANT** — `kordic_and_cerkez/.../19_May_2004.txt`
   - Assert `THE APPELLANT KORDIC` and `THE APPELLANT CERKEZ` appear as speakers
   - Assert both have `is_accused=True`

3. **Initials** — pick a `simic_et_al` file with `MR. J. SIMIC` and `MR. K. SIMIC`
   - Assert both appear as distinct speakers

4. **DR. speaker** — `kovacevic/.../7_December_2005.txt`
   - Assert `DR. ZAKIC` (or similar) appears as speaker

5. **Parenthesized procedural notes** — an early `aleksovski` or `mucic_et_al` file
   - Assert `(Open session)` or `(The accused entered court)` appear in `procedural_notes`, not in utterance `text`

6. **THE WITNESS: NAME declaration** — `aleksovski/.../August_24,_1998_bdcdaf.txt`
   - Assert `witness_name` is set (check that `THE WITNESS` answers are attributed correctly)
   - Assert no utterance has `text == "ANTO JURIC"`

7. **Exam header variants** — a `blaskic` file with period-terminated exam headers
   - Assert exam header lines are in `procedural_notes`, not in utterance `text`

8. **Date parsing edge cases** — unit-test `parse_date_from_filename()` directly:
   - `..._August_24,_1998_bdcdaf.doc` → `1998-08-24` (hash suffix + Month_DD format)
   - `..._25th_February_2003.doc` → `2003-02-25` (ordinal DDth_Month)
   - `..._01_Oct.1998.doc` → `1998-10-01` (abbreviated month)
   - `..._01april_1997.doc` → `1997-04-01` (no-separator)
   - `..._25_Avril_2006.doc` → `2006-04-25` (French month)

9. **Line-number run stripping** — unit-test `_strip_line_number_runs()`:
   - `"adjourned at 4.06 p.m. 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25"` → `"adjourned at 4.06 p.m."`
   - `"normal text without numbers"` → unchanged

10. **Dual case_id** — any file in a folder present in `case_mapping.csv`
    - Assert `case_id_base` is not null
    - Assert `case_id_base` matches the expected value from `case_mapping.csv`

**Structure:** One `test_` function per case above. Use `@pytest.fixture` for shared setup (load accused names, case mapping). Keep tests fast — each parses a single file from cached txt.
