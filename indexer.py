import argparse
import math
import os

from normalize import read_tsv, write_tsv
from tokenizer import tokenize

# text columns that get their own inverted index, so a query can search one field (manufacturer:hughes)
TEXT_FIELDS = ["name", "summary", "payload", "bus", "manufacturer", "launch_vehicle", "launch_site",
               "owner", "country", "constellation", "purpose", "mission", "shape", "motor",
               "equipment", "power_system", "adcs", "life_expectancy", "transmitter_frequency",
               "related_satellites", "major_events"]

# extra field with all text fields together, used for keywords without a field
ALL_FIELD = "all"
FIELDS = TEXT_FIELDS + [ALL_FIELD]

# columns copied to docs.tsv, the search uses them for filters and for printing results
DOC_COLUMNS = ["norad_id", "name", "url", "intl_designator", "object_type", "status", "orbit_class",
               "launch_date", "age_years",
               "apogee_km", "perigee_km", "inclination_deg", "eccentricity", "period_min",
               "launch_site", "launch_vehicle", "manufacturer", "owner", "country",
               "dry_mass_kg", "launch_mass_kg"]

# how many most common terms the report shows
TOP_TERMS = 15


# idf method 1, classic: log(N / df), a term in every document gets 0
def tfidf(n_docs, df):
    return math.log(n_docs / df)


# idf method 2, BM25 (probabilistic): smoothed with +0.5 and +1 so it never goes negative
def bm25(n_docs, df):
    return math.log((n_docs - df + 0.5) / (df + 0.5) + 1)


# rows -> postings {field: {term: {norad_id: tf}}},
# lengths {norad_id: {field: number of terms}} for BM25
def build_index(rows):
    postings = {field: {} for field in FIELDS}
    lengths = {}
    for r in rows:
        doc = r["norad_id"]
        texts = {field: r[field] for field in TEXT_FIELDS}
        # the 'all' field is just every text field joined, so its terms are counted again
        texts[ALL_FIELD] = " ".join(texts.values())

        lengths[doc] = {}
        for field, text in texts.items():
            # term -> how many times it is in this field (tf)
            terms = {}
            for term in tokenize(text):
                terms[term] = terms.get(term, 0) + 1
            # field length = number of terms with repeats, an empty field gets 0
            lengths[doc][field] = sum(terms.values())
            # postings keep the row order, the search sorts them by docID before intersecting
            for term, tf in terms.items():
                # setdefault creates the {norad_id: tf} dict the first time a term is seen
                postings[field].setdefault(term, {})[doc] = tf
    return postings, lengths


# BM25 average field length (avgdl) over all documents; the search computes it the same way from doc_lengths.tsv
def avg_lengths(lengths):
    return {field: sum(doc_lengths[field] for doc_lengths in lengths.values()) / len(lengths) for field in FIELDS}


# one line per (field, term): df, both idf values and the postings as norad:tf,norad:tf
# terms are only letters/digits (tokenizer), so tab, ':' and ',' can not appear in them
def write_postings(file_path, postings, n_docs):
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("field\tterm\tdf\ttfidf\tbm25\tpostings\n")
        for field in FIELDS:
            # terms sorted alphabetically inside each field, like a dictionary
            for term in sorted(postings[field]):
                docs = postings[field][term]
                # document frequency = in how many documents the term is
                df = len(docs)
                plist = ",".join(f"{doc}:{tf}" for doc, tf in docs.items())
                # idf is stored once here, so the search does not have to know N
                f.write(f"{field}\t{term}\t{df}\t{tfidf(n_docs, df):.6f}\t"
                        f"{bm25(n_docs, df):.6f}\t{plist}\n")


# one row per document, one column per field: number of terms (BM25)
def write_lengths(file_path, lengths):
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\t".join(["norad_id"] + FIELDS) + "\n")
        for doc, doc_lengths in lengths.items():
            f.write("\t".join([doc] + [str(doc_lengths[field]) for field in FIELDS]) + "\n")


# short report: per-field sizes and the most common terms with both idf values
def print_report(n_docs, postings, avg):
    print(f"documents: {n_docs}")
    print(f"{'field':24}{'avg_len':>10}{'terms':>10}{'postings':>12}")
    for field in FIELDS:
        # total postings = sum of df over all terms of the field
        n_postings = sum(len(docs) for docs in postings[field].values())
        print(f"{field:24}{avg[field]:>10.1f}{len(postings[field]):>10}{n_postings:>12}")

    print(f"\ntop {TOP_TERMS} terms by df in '{ALL_FIELD}' (boilerplate shows up here):")
    print(f"{'term':20}{'df':>8}{'tfidf':>12}{'bm25':>12}")
    # highest df first, these terms are in almost every page and get idf close to 0
    common = sorted(postings[ALL_FIELD].items(), key=lambda kv: len(kv[1]), reverse=True)
    for term, docs in common[:TOP_TERMS]:
        df = len(docs)
        print(f"{term:20}{df:>8}{tfidf(n_docs, df):>12.4f}{bm25(n_docs, df):>12.4f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_tsv", type=str, required=False, default="./data_clean.tsv", help="TSV produced by normalize.py")
    parser.add_argument("--index_dir", type=str, required=False, default="./index", help="Output directory for the index files")
    args = parser.parse_args()

    os.makedirs(args.index_dir, exist_ok=True)

    # the whole collection fits in memory, so the index is built in one pass
    _, rows = read_tsv(args.input_tsv)
    postings, lengths = build_index(rows)

    # inverted index, BM25 field lengths, and the columns for filters and output
    write_postings(os.path.join(args.index_dir, "postings.tsv"), postings, len(rows))
    write_lengths(os.path.join(args.index_dir, "doc_lengths.tsv"), lengths)
    write_tsv(os.path.join(args.index_dir, "docs.tsv"), DOC_COLUMNS, rows)

    print_report(len(rows), postings, avg_lengths(lengths))


if __name__ == "__main__":
    main()
