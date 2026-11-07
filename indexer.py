import argparse
import math
import os
from collections import Counter

from tsv_io import read_tsv, write_tsv
from tokenizer import tokenize

# text columns with their own index, so a query can search one field (manufacturer:hughes)
TEXT_FIELDS = ["name", "summary", "payload", "bus", "manufacturer", "launch_vehicle", "launch_site",
               "owner", "country", "constellation", "purpose", "mission", "shape", "motor",
               "equipment", "power_system", "adcs", "life_expectancy", "transmitter_frequency",
               "related_satellites", "major_events"]

# all text fields together, for keywords without a field
ALL_FIELD = "all"
FIELDS = TEXT_FIELDS + [ALL_FIELD]

# columns copied to docs.tsv, for filters and printing results
DOC_COLUMNS = ["norad_id", "name", "url", "intl_designator", "object_type", "status", "orbit_class",
               "launch_date", "age_years",
               "apogee_km", "perigee_km", "inclination_deg", "eccentricity", "period_min",
               "launch_site", "launch_vehicle", "manufacturer", "owner", "country",
               "dry_mass_kg", "launch_mass_kg"]

POSTINGS_COLUMNS = ["field", "term", "df", "tfidf", "bm25", "postings"]
LENGTHS_COLUMNS = ["norad_id"] + FIELDS

TOP_TERMS = 15


# idf method 1, classic: log(N / df)
def tfidf(n_docs, df):
    return math.log(n_docs / df)


# idf method 2, BM25: smoothed, never negative
def bm25(n_docs, df):
    return math.log((n_docs - df + 0.5) / (df + 0.5) + 1)


# {"norad_id": "25544", "name": "ISS (ZARYA)", ...}
#   -> ("25544", {"norad_id": "25544", "name": "2", ...}, {"name": {"iss": 1, "zarya": 1}, ...})
def index_document(row):
    norad_id = row["norad_id"]
    texts = {field: row[field] for field in TEXT_FIELDS}
    texts[ALL_FIELD] = " ".join(texts.values())

    lengths_row = {"norad_id": norad_id}
    tf = {}
    for field, text in texts.items():
        terms = tokenize(text)
        lengths_row[field] = str(len(terms))
        tf[field] = Counter(terms)
    return norad_id, lengths_row, tf


# ("name", "iss"), [("49044", 1), ("25544", 1)], 70946
#   -> {"field": "name", "term": "iss", "df": "2", "tfidf": "10.476...", "bm25": "10.25...", "postings": "25544:1,49044:1"}
def postings_row(term_key, docs, n_docs):
    field, term = term_key
    docs = sorted(docs, key=lambda d: int(d[0]))
    df = len(docs)
    return {"field": field, "term": term, "df": str(df),
            "tfidf": f"{tfidf(n_docs, df):.6f}", "bm25": f"{bm25(n_docs, df):.6f}",
            "postings": ",".join(f"{norad_id}:{count}" for norad_id, count in docs)}


# both modes return (lengths_rows, postings_rows)
def run_local(rows):
    lengths_rows = []
    by_term = {}  # (field, term) -> [(norad_id, tf), ...]
    for norad_id, lengths_row, tf in map(index_document, rows):
        lengths_rows.append(lengths_row)
        for field, counts in tf.items():
            for term, count in counts.items():
                if (field, term) not in by_term:
                    by_term[(field, term)] = []
                by_term[(field, term)].append((norad_id, count))

    postings = []
    for term_key, docs in by_term.items():
        postings.append(postings_row(term_key, docs, len(rows)))
    return lengths_rows, postings


# ("25544", {...}, {"name": {"iss": 1, "zarya": 1}, ...})
#   -> [(("name", "iss"), ("25544", 1)), (("name", "zarya"), ("25544", 1)), ...]
def document_items(doc):
    norad_id, _, tf = doc
    items = []
    for field, counts in tf.items():
        for term, count in counts.items():
            items.append(((field, term), (norad_id, count)))
    return items


def run_spark(rows):
    # imported here, so the local mode works without Spark
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.appName("satellite_indexer").config("spark.driver.memory", "4g").getOrCreate()
    sc = spark.sparkContext
    # workers need tokenizer.py
    sc.addPyFile(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tokenizer.py"))
    n_docs = len(rows)

    # a few tasks per core; cached because it is used twice
    docs = sc.parallelize(rows, sc.defaultParallelism).map(index_document).cache()
    lengths_rows = docs.map(lambda doc: doc[1]).collect()

    # same as by_term in run_local, then one row per term
    items = docs.flatMap(document_items)
    by_term = items.groupByKey()
    postings = by_term.map(lambda term: postings_row(term[0], term[1], n_docs)).collect()
    spark.stop()
    return lengths_rows, postings


# BM25 average field length, used by search.py
def avg_lengths(lengths):
    return {field: sum(doc_lengths[field] for doc_lengths in lengths.values()) / len(lengths) for field in FIELDS}


def print_report(n_docs, postings):
    print(f"documents: {n_docs}")
    terms_per_field = Counter(p["field"] for p in postings)
    for field, count in sorted(terms_per_field.items()):
        print(f"{field:24}{count:>8} terms")

    print(f"\ntop {TOP_TERMS} terms by df in '{ALL_FIELD}':")
    print(f"{'term':20}{'df':>8}{'tfidf':>12}{'bm25':>12}")
    all_rows = [p for p in postings if p["field"] == ALL_FIELD]
    all_rows.sort(key=lambda p: -int(p["df"]))
    for p in all_rows[:TOP_TERMS]:
        print(f"{p['term']:20}{p['df']:>8}{p['tfidf']:>12}{p['bm25']:>12}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_tsv", type=str, required=False, default="./data_clean.tsv", help="TSV produced by extractor.py")
    parser.add_argument("--index_dir", type=str, required=False, default="./index", help="Output directory for the index files")
    parser.add_argument("--mode", type=str, required=False, default="local", choices=["local", "spark"], help="local = one process, spark = distributed")
    args = parser.parse_args()

    os.makedirs(args.index_dir, exist_ok=True)

    _, rows = read_tsv(args.input_tsv)
    rows.sort(key=lambda row: int(row["norad_id"]))
    if args.mode == "local":
        lengths_rows, postings = run_local(rows)
    else:
        lengths_rows, postings = run_spark(rows)
    # Spark returns terms in random order, sorting makes both modes write the same file
    postings.sort(key=lambda row: (row["field"], row["term"]))

    write_tsv(os.path.join(args.index_dir, "postings.tsv"), POSTINGS_COLUMNS, postings)
    write_tsv(os.path.join(args.index_dir, "doc_lengths.tsv"), LENGTHS_COLUMNS, lengths_rows)
    write_tsv(os.path.join(args.index_dir, "docs.tsv"), DOC_COLUMNS, rows)

    print_report(len(rows), postings)


if __name__ == "__main__":
    main()