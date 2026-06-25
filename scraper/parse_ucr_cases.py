"""
Parse UCR case numbers from the HTML dropdown on ucr.irmct.org.

Usage:
    python3 parse_ucr_cases.py ucr_cases.html
    python3 parse_ucr_cases.py ucr_cases.html --all   # include sub-cases (appeals, interlocutory, etc.)

Input: HTML file containing the <option>/<optgroup> elements from the UCR search dropdown.
Output: CSV file with columns: ucr_id, case_number, name, type
"""

import re
import csv
import sys
import html
from pathlib import Path


def parse_ucr_cases(html_text, include_subcases=False):
    """Extract case numbers and names from UCR HTML options."""
    results = []

    # Parse each <option> element
    for match in re.finditer(
        r'<option\s+value="(sC_\d+)">(.*?)</option>', html_text
    ):
        ucr_id = match.group(1)
        text = html.unescape(match.group(2)).strip()

        # Skip "Select All"
        if ucr_id == "sC_000":
            continue

        # Parse: "IT-94-1-A [Tadic (Appeal)]" -> case_number, name, type
        m = re.match(r"(IT-[^\[]+?)\s*\[(.+)\]", text)
        if not m:
            continue

        case_number = m.group(1).strip()
        name_part = m.group(2).strip()

        # Extract proceeding type from parentheses if present
        type_match = re.match(r"(.+?)\s*\(([^)]+)\)\s*$", name_part)
        if type_match:
            name = type_match.group(1).strip()
            proc_type = type_match.group(2).strip()
        else:
            name = name_part
            proc_type = "Trial"

        # Determine if this is a base case or sub-case
        # Base cases match optgroup labels and have simple suffixes like /1, /2
        # Sub-cases have procedural suffixes like -A, -AR73, -ES, -R77, etc.
        base_number = re.match(r"(IT-\d+-\d+(?:/\d+)?(?:&\d+(?:/\d+)?)*)", case_number)
        is_subcase = bool(re.search(
            r"-(A\b|AR|ES|R\d|R77|Abis|Tbis|Misc|R75|R61|R65|R33|R71|108bis|BIS)",
            case_number[len(base_number.group(1)):] if base_number else ""
        ))

        if not include_subcases and is_subcase:
            continue

        results.append({
            "ucr_id": ucr_id,
            "case_number": case_number,
            "name": name,
            "type": proc_type,
        })

    return results


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <html_file> [--all]")
        sys.exit(1)

    html_file = Path(sys.argv[1])
    include_all = "--all" in sys.argv

    html_text = html_file.read_text(encoding="utf-8")
    cases = parse_ucr_cases(html_text, include_subcases=include_all)

    # Write CSV
    out_file = html_file.with_suffix(".csv")
    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["ucr_id", "case_number", "name", "type"])
        writer.writeheader()
        writer.writerows(cases)

    print(f"Extracted {len(cases)} cases -> {out_file}")

    # Also print summary
    for c in cases:
        print(f"  {c['case_number']:<30s} {c['name']}")

    # Write unique base case IDs CSV
    base_cases = {}
    for c in cases:
        m = re.match(r"(IT-\d+-\d+(?:[/&]\d+(?:/\d+)?)*)", c["case_number"])
        if m:
            base_id = m.group(1)
            if base_id not in base_cases:
                base_cases[base_id] = c["name"]

    base_file = html_file.with_name("ucr_base_cases.csv")
    with open(base_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["case_number", "name"])
        writer.writeheader()
        for case_num in sorted(base_cases, key=lambda k: base_cases[k].lower()):
            writer.writerow({"case_number": case_num, "name": base_cases[case_num]})

    print(f"Extracted {len(base_cases)} unique base case IDs -> {base_file}")


if __name__ == "__main__":
    main()
