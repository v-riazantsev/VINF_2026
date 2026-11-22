# pyright: reportMissingImports=false
import argparse
import os

import lucene
from java.nio.file import Paths
from org.apache.lucene.analysis.en import EnglishAnalyzer
from org.apache.lucene.document import Document, DoublePoint, Field, IntPoint, StoredField, TextField
from org.apache.lucene.index import IndexWriter, IndexWriterConfig
from org.apache.lucene.store import FSDirectory

from indexer import ALL_FIELD, TEXT_FIELDS
from tsv_io import read_tsv

# short values (GEO, payload, in_orbit)
EXACT_FIELDS = ["norad_id", "intl_designator", "object_type", "status", "orbit_class"]

# numbers: DoublePoint for range queries (inclination_deg:[90 TO *]) + StoredField to print the value
NUMBER_FIELDS = ["apogee_km", "perigee_km", "inclination_deg", "eccentricity", "period_min",
                 "dry_mass_kg", "launch_mass_kg", "age_years",
                 "wiki_launch_mass_kg", "wiki_dry_mass_kg", "wiki_power_w"]

# dates 'dd.mm.yyyy': IntPoint with yyyymmdd for range queries (24.04.1990 -> 19900424)
DATE_FIELDS = ["launch_date", "wiki_launch_date", "wiki_decay_date"]

# only printed, never searched
LINK_FIELDS = ["url", "wiki_url"]


# '24.04.1990' -> 19900424, so dates compare as numbers
def date_number(value):
    day, month, year = value.split(".")
    return int(year + month + day)


# one row of data_joined.tsv -> Lucene document, empty values are left out
def make_document(row):
    doc = Document()
    for field in EXACT_FIELDS + TEXT_FIELDS:
        if row[field]:
            store = Field.Store.NO if field == "wiki_text" else Field.Store.YES
            doc.add(TextField(field, row[field], store))
    # all text fields together
    doc.add(TextField(ALL_FIELD, " ".join(row[field] for field in TEXT_FIELDS), Field.Store.NO))
    for field in NUMBER_FIELDS:
        if row[field]:
            doc.add(DoublePoint(field, float(row[field])))
            doc.add(StoredField(field, row[field]))
    for field in DATE_FIELDS:
        if row[field]:
            doc.add(IntPoint(field, date_number(row[field])))
            doc.add(StoredField(field, row[field]))
    for field in LINK_FIELDS:
        if row[field]:
            doc.add(StoredField(field, row[field]))
    return doc


# size of all files in the index folder
def folder_size(path):
    return sum(os.path.getsize(os.path.join(path, name)) for name in os.listdir(path))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_tsv", type=str, required=False, default="./data_joined.tsv", help="TSV produced by join.py")
    parser.add_argument("--index_dir", type=str, required=False, default="./lucene_index", help="Output directory for the Lucene index")
    args = parser.parse_args()

    lucene.initVM()

    _, rows = read_tsv(args.input_tsv)
    directory = FSDirectory.open(Paths.get(args.index_dir))
    config = IndexWriterConfig(EnglishAnalyzer())
    config.setOpenMode(IndexWriterConfig.OpenMode.CREATE)
    writer = IndexWriter(directory, config)

    for row in rows:
        writer.addDocument(make_document(row))
    writer.close()

    print(f"documents: {len(rows)}, "
          f"index size: {folder_size(args.index_dir) / 1024 / 1024:.1f} MB -> {args.index_dir}")


if __name__ == "__main__":
    main()