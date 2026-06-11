# TODO

- [ ] **Revise `data/cis_parsed/pleas.csv` manually** — one row per accused
  (116 accused in 69 cases, built by `parser/extract_pleas.py`), pleas were
  extracted automatically, verify each row against the original CIS PDF.
- [ ] Decide how to record the 3 `unknown` rows (`bobetko`, `marini_zoran`,
  `zeljko_raznatovic` — no plea was ever entered).
- [ ] `sikirica`: CIS was never downloaded — re-scrape or add by hand. Its
  accused (incl. the guilty pleas of Sikirica, Došen and Kolundžija) are
  missing from pleas.csv.
- [ ] `data/cis_parsed/report.md` is stale (generated 2026-03-25, data
  regenerated since): says 37/22/10 pleas per case; current per-accused data
  says 96 not guilty / 17 guilty / 3 unknown. *(Pleas section updated
  2026-06-11; other report numbers checked against current data and still
  match.)*
