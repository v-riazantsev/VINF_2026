import argparse
import datetime
import os
import re

from normalize import normalize_row, DERIVED_COLUMNS
from tsv_io import write_tsv

# Page URL = prefix + NORAD ID (the file name)
URL_PREFIX = "https://keeptrack.space/satellite/"

# Label shown on the page -> TSV column name
LABEL_TO_COL = {
    # Orbit Data
    "International Designator": "intl_designator",
    "Elset Source": "elset_source",
    "Apogee": "apogee_km",
    "Perigee": "perigee_km",
    "Inclination": "inclination_deg",
    "Right Ascension": "raan_deg",
    "Eccentricity": "eccentricity",
    "Argument of Perigee": "arg_perigee_deg",
    "Period": "period_min",
    "Mean Motion": "mean_motion_rev_day",
    # Launch
    "Launch Date": "launch_date",
    "Launch Site": "launch_site",
    "Launch Pad": "launch_pad",
    "Launch Vehicle": "launch_vehicle",
    # Spacecraft
    "Manufacturer": "manufacturer",
    "Bus": "bus",
    "Payload": "payload",
    "Length": "length_m",
    "Diameter": "diameter_m",
    "Span": "span_m",
    "Dry Mass": "dry_mass_kg",
    "Launch Mass": "launch_mass_kg",
    "Shape": "shape",
    "Radar Cross Section": "radar_cross_section",
    "Motor": "motor",
    "Equipment": "equipment",
    "Power System": "power_system",
    "ADCS": "adcs",
    "Life Expectancy": "life_expectancy",
    "Transmitter Frequency": "transmitter_frequency",
    # About
    "Owner": "owner",
    "Country": "country",
    "Constellation": "constellation",
    "Purpose": "purpose",
    "Mission": "mission",
    "Related Satellites": "related_satellites",
    "Major Events": "major_events",
}

EXTRACTED_COLUMNS = ["norad_id", "url", "name", "summary"] + list(LABEL_TO_COL.values())
OUTPUT_COLUMNS = EXTRACTED_COLUMNS + DERIVED_COLUMNS


# Repairs UTF-8 text that was decoded as latin-1/cp1252 ('Â°' -> '°').
# Correct text fails the round trip (encode or decode error) and is returned unchanged.
def fix_mojibake(text):
    for encoding in ("latin-1", "cp1252"):
        try:
            return text.encode(encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return text


# Removes inner tags, repairs encoding and collapses whitespace
def clean(text):
    text = re.sub(r"<[^>]+>", " ", text)
    text = fix_mojibake(text)
    return re.sub(r"\s+", " ", text).strip()


# Cleaned content of the first match of the pattern, "" when not found
def extract_first(pattern, html_content):
    match = re.search(pattern, html_content, re.S)
    return clean(match.group(1)) if match else ""


# HTML -> dict of raw text values (numbers and dates are cleaned later in normalize.py)
def extract_satellite(norad_id, html_content):
    data = dict.fromkeys(EXTRACTED_COLUMNS, "")
    data["norad_id"] = str(norad_id)
    data["url"] = URL_PREFIX + str(norad_id)
    data["name"] = extract_first(r'<span id="satellite-title"[^>]*>(.*?)</span>', html_content)
    data["summary"] = extract_first(r'<div id="satellite-summary"[^>]*>(.*?)</div>', html_content)

    # Every field is a pair: <p class="text-sm text-gray-500">LABEL</p> <p class="font-medium">VALUE</p>
    pairs = re.findall(r'<p class="text-sm text-gray-500"[^>]*>([^<]*)</p>\s*<p class="font-medium"[^>]*>(.*?)</p>',
                       html_content, re.S)
    for label, value in pairs:
        col = LABEL_TO_COL.get(label.strip())
        # Skip labels that are not in LABEL_TO_COL
        if col is not None:
            data[col] = clean(value)
    return data


# NORAD ID = file name without .html, leading zeros dropped (00043.html -> 43), None for other files
def norad_id_from_path(path):
    name = os.path.basename(path)
    if name.endswith(".html") and name[:-5].isdigit():
        return int(name[:-5])
    return None


# All satellite page files in the directory (file name = NORAD ID)
def list_pages(input_dir):
    input_dir = os.path.abspath(input_dir)
    return [os.path.join(input_dir, name) for name in os.listdir(input_dir) if norad_id_from_path(name) is not None]


# One page file -> normalized row, None when it is not a satellite page (only those have a title).
# Both modes call this function, so they produce the same rows.
def process_file(path, today):
    with open(path, "rb") as f:
        html_content = f.read().decode("utf-8", errors="replace")
    data = extract_satellite(norad_id_from_path(path), html_content)
    if not data["name"]:
        return None
    return normalize_row(data, today)


def run_local(paths, today):
    rows = [process_file(path, today) for path in paths]
    return [row for row in rows if row is not None]


def run_spark(paths, today):
    # imported here, so the local mode works without Spark installed
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.appName("satellite_extractor").getOrCreate()
    sc = spark.sparkContext

    rows = (sc.parallelize(paths)
              .map(lambda path: process_file(path, today))
              .filter(lambda row: row is not None)
              .collect())
    spark.stop()
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_dir", type=str, required=True, help="Directory with scraped .html files")
    parser.add_argument("--output_tsv", type=str, required=False, default="./data_clean.tsv", help="Output TSV with extracted and normalized data")
    parser.add_argument("--mode", type=str, required=False, default="local", choices=["local", "spark"], help="local = one process, spark = distributed")
    args = parser.parse_args()

    # one date for the whole run, used for age_years
    today = datetime.datetime.now()

    paths = list_pages(args.input_dir)
    if args.mode == "local":
        rows = run_local(paths, today)
    else:
        rows = run_spark(paths, today)

    # sorted by NORAD ID, so the outputs of both modes are identical
    rows.sort(key=lambda row: int(row["norad_id"]))
    write_tsv(args.output_tsv, OUTPUT_COLUMNS, rows)
    print(f"Done. Wrote {len(rows)} satellites to {args.output_tsv}")


if __name__ == "__main__":
    main()