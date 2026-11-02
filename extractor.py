import argparse
import os
import re

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

# Columns where only the number is kept (unit is in the column name)
NUMERIC_COLS = {
    "apogee_km", "perigee_km", "inclination_deg", "raan_deg", "eccentricity",
    "arg_perigee_deg", "period_min", "mean_motion_rev_day",
    "length_m", "diameter_m", "span_m", "dry_mass_kg", "launch_mass_kg",
    "radar_cross_section",
}

TSV_COLUMNS = ["norad_id", "url", "name", "summary"] + list(LABEL_TO_COL.values())

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

# '1,410.5 kg' -> '1410.5', value without a number is kept as it is
def parse_number(value):
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", value)
    return match.group(0).replace(",", "") if match else value

def extract_satellite(norad_id, html_content):
    data = dict.fromkeys(TSV_COLUMNS, "")
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
        if col is None:
            continue
        value = clean(value)
        data[col] = parse_number(value) if col in NUMERIC_COLS else value
    return data

# NORAD IDs already in the output TSV (first column), so an interrupted run can continue
def read_parsed_ids(output_tsv):
    with open(output_tsv, 'r', encoding='utf-8') as f:
        f.readline()  # header
        return {int(line.split('\t', 1)[0]) for line in f}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input_dir', type=str, required=True, help='Directory with scraped .html files')
    parser.add_argument('--output_tsv', type=str, required=False, default="./satellites.tsv", help='Output TSV file with extracted data')
    args = parser.parse_args()

    # New output file starts with the header
    if not os.path.exists(args.output_tsv) or os.path.getsize(args.output_tsv) == 0:
        with open(args.output_tsv, 'w', encoding='utf-8') as f:
            f.write("\t".join(TSV_COLUMNS) + '\n')

    # NORAD ID = file name without .html, leading zeros dropped (00043.html -> 43)
    html_files = {int(name[:-5]): os.path.join(args.input_dir, name)
                  for name in os.listdir(args.input_dir) if name.endswith('.html') and name[:-5].isdigit()}
    parsed_ids = read_parsed_ids(args.output_tsv)
    awaiting_ids = sorted(set(html_files) - parsed_ids)
    print(f"Total files to parse: {len(awaiting_ids)} (already parsed: {len(parsed_ids)})")

    extracted_count = 0
    # Line-buffered, so every finished row is on disk if the run is interrupted
    with open(args.output_tsv, 'a', encoding='utf-8', buffering=1) as out:
        for norad_id in awaiting_ids:
            with open(html_files[norad_id], 'r', encoding='utf-8', errors='replace') as f:
                data = extract_satellite(norad_id, f.read())
            # Only satellite pages have a title
            if not data["name"]:
                print(f"[!] Not a satellite page, skipping: {html_files[norad_id]}")
                continue
            out.write("\t".join(data[col] for col in TSV_COLUMNS) + '\n')
            extracted_count += 1

    print(f"Done. Extracted {extracted_count} of {len(awaiting_ids)} files.")

if __name__ == "__main__":
    main()
