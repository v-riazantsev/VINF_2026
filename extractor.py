import argparse
import os
import re

# Label shown on the page -> TSV column name
LABEL_TO_COL = {
    # Orbit Data
    "NORAD ID": "norad_id",
    "International Designator": "intl_designator",
    "Epoch": "epoch",
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

# Name/url/summary come from dedicated elements, not from label/value pairs
DATA_COLUMNS = ["url", "name", "summary"] + list(LABEL_TO_COL.values())
TSV_COLUMNS = ["index"] + DATA_COLUMNS

# Named HTML entities that may appear on the pages (numeric ones are decoded generically)
NAMED_ENTITIES = {
    "amp": "&",
    "lt": "<",
    "gt": ">",
    "quot": '"',
    "apos": "'",
    "nbsp": " ",
    "times": "×",
    "deg": "°",
    "ndash": "–",
    "mdash": "—",
    "hellip": "…",
}

# <p class="text-sm text-gray-500" ...>LABEL</p> <p class="font-medium" ...>VALUE</p>
# Label is [^<]* so the lazy match can never swallow other elements
PAIR_RE = re.compile(
    r'<p class="text-sm text-gray-500"[^>]*>([^<]*)</p>\s*'
    r'<p class="font-medium"[^>]*>(.*?)</p>',
    re.S,
)
TITLE_RE = re.compile(r'<span id="satellite-title"[^>]*>(.*?)</span>', re.S)
SUMMARY_RE = re.compile(r'<div id="satellite-summary"[^>]*>(.*?)</div>', re.S)
CANONICAL_RE = re.compile(r'<link rel="canonical" href="([^"]+)"')
TAG_RE = re.compile(r"<[^>]+>")
ENTITY_RE = re.compile(r"&(#[xX][0-9a-fA-F]+|#\d+|[a-zA-Z]+);")
MOJIBAKE_RE = re.compile("[ÂÃâ]")
WS_RE = re.compile(r"\s+")
NUM_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")

def read_lines(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        # splitlines() removes \n, line.strip() filters out empty or whitespace-only lines
        lines = [line for line in f.read().splitlines() if line.strip()]
    return lines

def append_line(file_path, line):
    with open(file_path, 'a', encoding='utf-8', buffering=1) as f:
        f.write(line + '\n')

def read_parsed_indexes(output_tsv):
    # Index is the first column of every row; skip the header line
    lines = read_lines(output_tsv)
    return {line.split('\t', 1)[0] for line in lines[1:]}

def find_html_files(input_dir):
    # Map index (file name without .html) -> full path
    html_files = {}
    for filename in os.listdir(input_dir):
        if filename.endswith('.html'):
            index = filename[:-len('.html')]
            html_files[index] = os.path.join(input_dir, filename)
    return html_files

def decode_entity(match):
    entity = match.group(1)
    try:
        if entity[:2] in ("#x", "#X"):
            return chr(int(entity[2:], 16))
        if entity[0] == "#":
            return chr(int(entity[1:]))
    except (ValueError, OverflowError):
        return match.group(0)
    # Unknown named entities are left untouched
    return NAMED_ENTITIES.get(entity, match.group(0))

def unescape_entities(text):
    return ENTITY_RE.sub(decode_entity, text)

def fix_mojibake(text):
    # Repair UTF-8 text that was decoded as latin-1/cp1252 (e.g. 'Â°' -> '°')
    if not MOJIBAKE_RE.search(text):
        return text
    for encoding in ("latin-1", "cp1252"):
        try:
            return text.encode(encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return text

def clean_value(raw):
    text = TAG_RE.sub(" ", raw)       # Drop inner tags (<span class="truncate">...)
    text = unescape_entities(text)    # &lt; &#39; &amp; ...
    text = fix_mojibake(text)
    text = WS_RE.sub(" ", text).strip() # Also removes \n, \t, \r -> safe for TSV
    return text or None

def parse_number(value):
    if value is None:
        return None
    match = NUM_RE.search(value)
    return match.group(0).replace(",", "") if match else value

def extract_first(pattern, html_content):
    match = pattern.search(html_content)
    return clean_value(match.group(1)) if match else None

def extract_labeled_fields(html_content):
    fields = {}
    for label, value in PAIR_RE.findall(html_content):
        col = LABEL_TO_COL.get(clean_value(label) or "")
        # Skip unknown labels and keep only the first occurrence of each label
        if col is None or col in fields:
            continue
        cleaned = clean_value(value)
        fields[col] = parse_number(cleaned) if col in NUMERIC_COLS else cleaned
    return fields

def extract_satellite(html_content):
    # Return a dict of column -> value, or None for non-satellite pages
    name = extract_first(TITLE_RE, html_content)
    if name is None:
        return None

    data = dict.fromkeys(DATA_COLUMNS)
    data["name"] = name
    data["url"] = extract_first(CANONICAL_RE, html_content)
    data["summary"] = extract_first(SUMMARY_RE, html_content)
    data.update(extract_labeled_fields(html_content))
    return data

def to_tsv_line(index, data):
    # None becomes an empty field
    values = [index] + ["" if data[col] is None else data[col] for col in DATA_COLUMNS]
    return "\t".join(values)

def process_file(index, file_path, output_tsv):
    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        html_content = f.read()

    data = extract_satellite(html_content)
    if data is None:
        print(f"[!] Not a satellite page, skipping: {file_path}")
        return False

    append_line(output_tsv, to_tsv_line(index, data))
    print(f"[+] Extracted {index} ({data['name']})")
    return True

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input_dir', type=str, required=True, help='Directory with scraped .html files')
    parser.add_argument('--output_tsv', type=str, required=False, default="./satellites.tsv", help='Output TSV file with extracted data')

    args = parser.parse_args()

    # Check if the input directory exists
    if not os.path.isdir(args.input_dir):
        print(f"[!] The specified input_dir does not exist: {args.input_dir}")
        return

    # Check if the output TSV exists and is not empty, if not create it with a header
    if not os.path.exists(args.output_tsv) or os.path.getsize(args.output_tsv) == 0:
        with open(args.output_tsv, 'w', encoding='utf-8') as f:
            f.write("\t".join(TSV_COLUMNS) + '\n')

    # Create a list of files to parse, excluding those that have already been parsed
    parsed_indexes = read_parsed_indexes(args.output_tsv)
    html_files = find_html_files(args.input_dir)
    awaiting_indexes = sorted(set(html_files) - parsed_indexes)

    print(f"Total files to parse: {len(awaiting_indexes)} (already parsed: {len(parsed_indexes)})")

    extracted_count = 0
    for index in awaiting_indexes:
        try:
            if process_file(index, html_files[index], args.output_tsv):
                extracted_count += 1
        except Exception as e:
            print(f"[!] Failed to parse {html_files[index]}: {e}")

    print(f"Done. Extracted {extracted_count} of {len(awaiting_indexes)} files.")

if __name__ == "__main__":
    main()
