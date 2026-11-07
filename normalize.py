import re
from datetime import datetime

# columns where only the number is kept (unit is in the column name)
NUMERIC_COLS = {
    "apogee_km", "perigee_km", "inclination_deg", "raan_deg", "eccentricity",
    "arg_perigee_deg", "period_min", "mean_motion_rev_day",
    "length_m", "diameter_m", "span_m", "dry_mass_kg", "launch_mass_kg",
    "radar_cross_section",
}

# columns where 0 really means "unknown" (e.g. debris with no known size/mass)
ZERO_IS_UNKNOWN = ["length_m", "diameter_m", "span_m", "dry_mass_kg", "launch_mass_kg"]

# orbit class thresholds, altitude in km
LEO_MAX_APOGEE = 2000
GEO_ALT = 35786
GEO_TOLERANCE = 500

DERIVED_COLUMNS = ["orbit_class", "status", "age_years", "object_type"]


# float or None when the value is not a number
def to_float(s):
    try:
        return float(s)
    except ValueError:
        return None


# '1,410.5 kg' -> '1410.5', value without a number is kept as it is
def parse_number(value):
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", value)
    return match.group(0).replace(",", "") if match else value


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


# clean one extracted row and fill all derived columns, age is counted up to today
def normalize_row(r, today):
    if r["intl_designator"].upper() == "UNKNOWN":
        r["intl_designator"] = ""

    for c in NUMERIC_COLS:
        r[c] = parse_number(r[c])
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
    return r