import argparse
import re
from datetime import datetime

from extractor import NUMERIC_COLS

# columns where 0 really means "unknown" (e.g. debris with no known size/mass)
ZERO_IS_UNKNOWN = ["length_m", "diameter_m", "span_m", "dry_mass_kg", "launch_mass_kg"]

# orbit class thresholds, altitude in km
LEO_MAX_APOGEE = 2000
GEO_ALT = 35786
GEO_TOLERANCE = 500

DERIVED_COLUMNS = ["orbit_class", "status", "age_years", "object_type"]


# returns (header, rows as dicts)
def read_tsv(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        # strip only the newline, strip() would eat trailing empty columns
        header = f.readline().rstrip("\n").split("\t")
        rows = [dict(zip(header, line.rstrip("\n").split("\t"))) for line in f]
    return header, rows


def write_tsv(file_path, header, rows):
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\t".join(header) + "\n")
        for row in rows:
            f.write("\t".join(row[c] for c in header) + "\n")


# float or None when the value is not a number
def to_float(s):
    try:
        return float(s)
    except ValueError:
        return None


# LEO / MEO / GEO / HEO from altitudes in km, '' when unknown
def orbit_class(apogee, perigee):
    if apogee is None or perigee is None:
        return ""
    if apogee <= LEO_MAX_APOGEE:
        return "LEO"
    if perigee < LEO_MAX_APOGEE:
        return "HEO"  # highly elliptical: dips into LEO, goes far out
    if GEO_ALT - GEO_TOLERANCE <= perigee and apogee <= GEO_ALT + GEO_TOLERANCE:
        return "GEO"
    if apogee < GEO_ALT - GEO_TOLERANCE:
        return "MEO"
    return "HEO"  # everything above/around GEO that is not circular GEO


# guess the object type from its name and summary text
def object_type(name, summary):
    name = name.upper()
    if name.startswith("UNKNOWN"):
        return "unknown"
    if re.search(r"\bDEB\b", name) or summary.startswith("Space debris"):
        return "debris"
    if re.search(r"\bR/B\b", name) or summary.startswith("A rocket body"):
        return "rocket_body"
    return "payload"


# clean one row and fill all derived columns, age is counted up to today
def normalize_row(r, today):
    if r["intl_designator"].upper() == "UNKNOWN":
        r["intl_designator"] = ""

    for c in ZERO_IS_UNKNOWN:
        if to_float(r[c]) == 0:
            r[c] = ""
    for c in NUMERIC_COLS:
        if r[c] and to_float(r[c]) is None:
            r[c] = ""  # non-numeric junk in a numeric column ('Unknown')

    # 'May 24, 2014' -> '24.05.2014', empty when it can not be parsed
    try:
        launch = datetime.strptime(r["launch_date"], "%B %d, %Y")
        r["launch_date"] = launch.strftime("%d.%m.%Y")
    except ValueError:
        launch = None
        r["launch_date"] = ""

    r["orbit_class"] = orbit_class(to_float(r["apogee_km"]), to_float(r["perigee_km"]))
    r["object_type"] = object_type(r["name"], r["summary"])

    # decayed if no orbit data, otherwise in_orbit; age only for objects still in orbit
    alive = r["orbit_class"] != ""
    r["status"] = "in_orbit" if alive else "decayed"
    r["age_years"] = f"{(today - launch).days / 365.25:.1f}" if alive and launch else ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_tsv", type=str, required=True, help="TSV produced by extractor.py")
    parser.add_argument("--output_tsv", type=str, default="./data_clean.tsv")
    args = parser.parse_args()

    header, rows = read_tsv(args.input_tsv)
    today = datetime.now()
    for r in rows:
        normalize_row(r, today)

    write_tsv(args.output_tsv, header + DERIVED_COLUMNS, rows)

    # short report to sanity-check the output
    print(f"rows written: {len(rows)} -> {args.output_tsv}")
    for col in ("object_type", "orbit_class", "status"):
        counts = {}
        for r in rows:
            value = r[col] or "(empty)"
            counts[value] = counts.get(value, 0) + 1
        print(f"{col}: {counts}")


if __name__ == "__main__":
    main()
