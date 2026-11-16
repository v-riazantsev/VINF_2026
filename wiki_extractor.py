import argparse
import bz2
import re

from normalize import normalize_wiki_row
from tsv_io import write_tsv

URL_PREFIX = "https://en.wikipedia.org/wiki/"

# infobox field (as written in the infobox) -> TSV column; dates, masses and power are normalized in normalize.py
INFOBOX_FIELDS = {
    "name": "wiki_name",
    "SATCAT": "wiki_satcat",
    "mission_type": "wiki_mission_type",
    "operator": "wiki_operator",
    "manufacturer": "wiki_manufacturer",
    "spacecraft_bus": "wiki_bus",
    "launch_mass": "wiki_launch_mass_kg",
    "dry_mass": "wiki_dry_mass_kg",
    "power": "wiki_power_w",
    "launch_date": "wiki_launch_date",
    "launch_rocket": "wiki_launch_rocket",
    "launch_site": "wiki_launch_site",
    "orbit_regime": "wiki_orbit_regime",
    "decay_date": "wiki_decay_date",
    "mission_duration": "wiki_mission_duration",
}
COLUMNS = ["wiki_url"] + list(INFOBOX_FIELDS.values()) + ["wiki_text"]

ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "nbsp": " ", "ndash": "–", "mdash": "—", "minus": "−"}

# wiki markup -> plain text on one line
def clean_wiki(text):
    # <!-- comment -->, <ref name="a" />, <ref>...</ref>, <gallery>...</gallery> -> ''
    text = re.sub(r"<!--.*?-->|<ref[^>]*/>|<ref.*?</ref>|<gallery.*?</gallery>", " ", text, flags=re.S)
    # {{cite web|url=...}}, {{sfn|Orloff|2000}} -> ''
    text = re.sub(r"\{\{(cite|sfn)[^}]*\}\}", " ", text)
    # {{convert|2.4|m|ft}} -> '2.4 m'
    text = re.sub(r"\{\{(convert|cvt)\|([^|}]*)\|([^|}]*)[^}]*\}\}", r" \2 \3 ", text)
    # [[File:x.jpg|thumb|caption]], [[Category:Satellites]] -> ''
    text = re.sub(r"\[\[(File|Image|Category):.*?\]\]", " ", text)
    # {{Start date|1990|04|24}} -> '|1990|04|24}}'   (template name removed, the rest is cleaned below)
    text = re.sub(r"\{\{[^|}]*", " ", text)
    # <br />, </small> -> ''
    text = re.sub(r"</?\w[^>]*>", " ", text)
    # '''bold''', ''italic'' -> 'bold', 'italic'
    text = re.sub(r"'{2,}", "", text)
    # == Heading == -> 'Heading'
    text = re.sub(r"={2,}", " ", text)
    # every leftover bracket and pipe: [[NASA|nasa]] -> 'NASA nasa', '|1990|04|24}}' -> '1990 04 24'
    text = re.sub(r"[\[\]{}|]", " ", text)
    # &nbsp; -> ' ', &mdash; -> '—'
    for name, char in ENTITIES.items():
        text = text.replace(f"&{name};", char)
    # 'a \n  b' -> 'a b'
    return re.sub(r"\s+", " ", text).strip()


# one <page> of the dump -> TSV row, None when it is not a satellite article with an ID
def process_page(page):
    # only articles (namespace 0) with a SATCAT field
    if "<ns>0</ns>" not in page or "SATCAT" not in page:
        return None
    # the dump stores '<' as '&lt;', without this the <ref>, <!-- --> and <br> regexes would not match
    page = page.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    title, text = re.search(r"<title>(.*?)</title>.*<text.*?>(.*)</text>", page, re.S).groups()

    # the infobox ends with a '}}' line that is not followed by another field ('|')
    infobox = re.search(r"\{\{Infobox(.*?)\n\}\}(?!\s*\|)", text, re.S | re.I)
    if not infobox:
        return None
    # every field starts on a new line: '| SATCAT = 20580'
    # lines without '=' ('| 4371' inside {{ubl ...}}) are list items of the field above
    # setdefault keeps the first value, so a nested template's '| name =' does not overwrite the real one
    fields = {}
    for part in re.split(r"\n\|(?=[\w ]+=)", infobox.group(1)):
        key, _, value = part.partition("=")
        fields.setdefault(key.strip(), value)

    row = {"wiki_url": URL_PREFIX + title.replace(" ", "_")}
    for field, column in INFOBOX_FIELDS.items():
        row[column] = clean_wiki(fields.get(field, ""))
    # SATCAT = NORAD ID, a comma-separated list because one article can describe several satellites
    row["wiki_satcat"] = ",".join(re.findall(r"\d+", row["wiki_satcat"]))
    if not row["wiki_satcat"]:
        return None  # can not be joined with keeptrack

    # article text without the infobox
    row["wiki_text"] = clean_wiki(text[infobox.end():])
    return normalize_wiki_row(row)


# local mode: the dump is read line by line, a page ends with '</page>'
def run_local(dump_path):
    rows, lines = [], []
    with bz2.open(dump_path, "rt", encoding="utf-8") as f:
        for line in f:
            lines.append(line)
            if "</page>" in line:
                rows.append(process_page("".join(lines)))
                lines = []
    return [row for row in rows if row]


# Spark mode: the .bz2 is split among all cores and cut at '</page>' instead of at newlines, one record = one page
def run_spark(dump_path):
    from pyspark.sql import SparkSession

    # Spark's default 1g is too small for the whole dump
    spark = SparkSession.builder.appName("wiki_extractor").config("spark.driver.memory", "4g").getOrCreate()
    pages = spark.read.text(dump_path, lineSep="</page>")
    # Only pages with 'SATCAT' are sent to Python
    pages = pages.filter("value LIKE '%SATCAT%'")
    rows = pages.rdd.map(lambda row: process_page(row.value)).filter(lambda row: row).collect()
    spark.stop()
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=str, required=True, help="Wikipedia dump, e.g. enwiki-latest-pages-articles.xml.bz2")
    parser.add_argument("--output_tsv", type=str, required=False, default="./wiki_sat.tsv", help="Output TSV with satellite articles")
    parser.add_argument("--mode", type=str, required=False, default="local", choices=["local", "spark"], help="local = one process, spark = distributed")
    args = parser.parse_args()

    rows = run_local(args.input) if args.mode == "local" else run_spark(args.input)
    rows.sort(key=lambda row: row["wiki_url"])  # both modes write the same file
    write_tsv(args.output_tsv, COLUMNS, rows)
    print(f"Done. Wrote {len(rows)} satellite articles to {args.output_tsv}")


if __name__ == "__main__":
    main()