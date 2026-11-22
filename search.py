import argparse
import operator
import re

from indexer import ALL_FIELD, FIELDS, TEXT_FIELDS, avg_lengths
from normalize import to_float
from tokenizer import tokenize
from tsv_io import read_tsv

# BM25 parameters: k1 = how fast repeating a term stops helping, b = how much document length matters
BM25_K1 = 1.5
BM25_B = 0.75

# short filter names: date=*.*.1995
FILTER_ALIASES = {"date": "launch_date"}

# columns printed next to each result
SHOW_COLUMNS = ["object_type", "status", "orbit_class", "launch_date", "wiki_name"]

COMPARE = {">": operator.gt, "<": operator.lt, ">=": operator.ge, "<=": operator.le}


# 'orbit_class=GEO launch_vehicle:"falcon 9" broadband'
#   -> filters [("orbit_class", "=", "GEO")], keywords [("launch_vehicle", "falcon"), ("launch_vehicle", "9"), ("all", "broadband")]
# raw=True (Lucene): keywords are kept as written, [("launch_vehicle", '"falcon 9"'), ("all", "broadband")],
# so Lucene's query parser can read phrases ("..."), fuzzy (hubel~) and wildcards (star*)
def parse_query(query, doc_columns, raw=False):
    filters, keywords = [], []
    # split on spaces, quoted parts stay together
    for part in re.findall(r'(?:[^\s"]|"[^"]*")+', query):
        # filter: field, operator, value (inclination_deg>90), quotes only group the value
        m = re.match(r"^([a-z_]+)(>=|<=|!=|=|>|<)(.+)$", part.replace('"', ''))
        if m:
            field, op, value = m.groups()
            field = FILTER_ALIASES.get(field, field)
            if field not in doc_columns:
                raise ValueError(f"unknown filter field '{field}', use one of: {', '.join(doc_columns)}")
            filters.append((field, op, value))
            continue

        # keyword in one field (manufacturer:hughes) or in all fields
        m = re.match(r"^([a-z_]+):(.+)$", part)
        if m:
            field, text = m.groups()
            if field not in TEXT_FIELDS:
                raise ValueError(f"unknown text field '{field}', use one of: {', '.join(TEXT_FIELDS)}")
        else:
            field, text = ALL_FIELD, part
        if raw:
            keywords.append((field, text))
            continue
        for term in tokenize(text.replace('"', '')):
            if (field, term) not in keywords:
                keywords.append((field, term))
    return filters, keywords


# '24.05.2014' -> '20140524' so dates compare by year, month, day; other text -> lowercase
def sort_key(s):
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", s)
    if m:
        day, month, year = m.groups()
        return year + month.zfill(2) + day.zfill(2)
    return s.lower()


# value equals one pattern: same number, or text with * / ? wildcards ('STARLINK*')
def equals(value, pattern):
    v = to_float(value)
    if v is not None and v == to_float(pattern):
        return True
    regex = re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
    return re.fullmatch(regex, value, re.IGNORECASE) is not None


# one filter on one value; '=' and '!=' accept a list: orbit_class=LEO,MEO
def passes(value, op, target):
    if value == "":
        return op == "!="  # a missing value only passes '!='
    if op in ("=", "!="):
        same = any(equals(value, t) for t in target.split(","))
        return same if op == "=" else not same
    # numbers as numbers, dates as dates, everything else as lowercase text
    v, t = to_float(value), to_float(target)
    if v is None or t is None:
        v, t = sort_key(value), sort_key(target)
    return COMPARE[op](v, t)


# reads only the lines of the query terms and stops when all are found
# -> {(field, term): (tfidf, bm25, {norad_id: tf})}
def read_postings(file_path, keywords):
    found = {}
    if not keywords:
        return found
    with open(file_path, "r", encoding="utf-8") as f:
        f.readline()  # header
        for line in f:
            field, term, _, idf_t, idf_b, plist = line.rstrip("\n").split("\t")
            if (field, term) in keywords:
                docs = {doc: int(tf) for doc, tf in (item.split(":") for item in plist.split(","))}
                found[(field, term)] = (float(idf_t), float(idf_b), docs)
                if len(found) == len(keywords):
                    break
    return found


# method 2: BM25, tf is saturated by k1 and normalized by the field length
def score_bm25(tf, idf, doc_len, avg_len):
    norm = 1 - BM25_B + BM25_B * doc_len / avg_len
    return idf * tf * (BM25_K1 + 1) / (tf + BM25_K1 * norm)


# -> [(norad_id, score)] best first; match 'all' = every keyword (AND), 'any' = at least one (OR)
def rank(candidates, keywords, postings, lengths, avg, method, match):
    if not keywords:
        return [(doc, None) for doc in sorted(candidates, key=int)]
    if match == "all":
        if len(postings) < len(keywords):
            return []  # a keyword is in no document
        # documents that contain every keyword
        for _, _, docs in postings.values():
            candidates = candidates & docs.keys()

    scores = {}
    for (field, _), (idf_t, idf_b, docs) in postings.items():
        for doc, tf in docs.items():
            if doc in candidates:
                if method == "tfidf":
                    score = tf * idf_t  # method 1: tf-idf, document length is ignored
                else:
                    score = score_bm25(tf, idf_b, lengths[doc][field], avg[field])
                scores[doc] = scores.get(doc, 0) + score

    # higher score first, equal scores by NORAD ID
    return sorted(scores.items(), key=lambda kv: (-kv[1], int(kv[0])))


def print_query(filters, keywords):
    print(f"filters:  {' '.join(f + op + v for f, op, v in filters) or '-'}")
    print(f"keywords: {' '.join(t if f == ALL_FIELD else f'{f}:{t}' for f, t in keywords) or '-'}")


# total: number of all results when results holds only the top ones
def print_results(results, docs, filters, method, top, total=None):
    total = len(results) if total is None else total
    columns = SHOW_COLUMNS + [f for f, _, _ in filters if f not in SHOW_COLUMNS]
    print(f"\n[{method}] {total} results" + (f", showing top {top}" if total > top else ""))
    for i, (doc, score) in enumerate(results[:top], 1):
        d = docs[doc]
        score_str = f"{score:8.4f}" if score is not None else "       -"
        info = " ".join(f"{c}={d[c]}" for c in columns)
        print(f"{i:4}. {doc:>8} {score_str}  {d['name'][:40]:40}  {info}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index_dir", type=str, required=False, default="./index", help="Directory produced by indexer.py")
    parser.add_argument("--query", type=str, required=True, help="Query: filters, field:keywords and plain keywords")
    parser.add_argument("--method", type=str, required=False, default="bm25", choices=["tfidf", "bm25", "both"], help="Ranking method")
    parser.add_argument("--match", type=str, required=False, default="all", choices=["all", "any"], help="Keywords: all must match, or any")
    parser.add_argument("--top", type=int, required=False, default=20, help="How many results to print")
    args = parser.parse_args()

    doc_columns, doc_rows = read_tsv(f"{args.index_dir}/docs.tsv")
    docs = {d["norad_id"]: d for d in doc_rows}
    try:
        filters, keywords = parse_query(args.query, doc_columns)
    except ValueError as e:
        print(f"[!] {e}")
        return

    print_query(filters, keywords)

    _, length_rows = read_tsv(f"{args.index_dir}/doc_lengths.tsv")
    lengths = {r["norad_id"]: {field: int(r[field]) for field in FIELDS} for r in length_rows}
    avg = avg_lengths(lengths)

    candidates = {doc for doc, d in docs.items() if all(passes(d[field], op, value) for field, op, value in filters)}
    postings = read_postings(f"{args.index_dir}/postings.tsv", keywords)
    for method in (["tfidf", "bm25"] if args.method == "both" else [args.method]):
        results = rank(candidates, keywords, postings, lengths, avg, method, args.match)
        print_results(results, docs, filters, method, args.top)


if __name__ == "__main__":
    main()