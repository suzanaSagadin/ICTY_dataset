# TODO

## In progress

- [ ] **Parse transcripts** (DOC/DOCX → JSON) — currently running
  ```
  .venv/bin/python3 parser/run_parser.py --archive-dir archive_250625/ --output-dir data_250625/transcripts_parsed --txt-dir data_250625/transcripts_txt
  ```

## Next steps

- [ ] **Check transcript parser quality** on new archive results
- [x] **Update `dataset_creation/chunk_extracton.py`** — paths updated to `data_250625/`, accused detection now uses parser's `is_accused` flag instead of re-implementing matching
- [ ] **Update `dataset_overview.md`** with parser results once available
- [ ] **Have colleague review 22 new/changed pleas rows** — see `data_250625/cis_parsed/pleas_comparison.csv`
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
