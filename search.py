import argparse
import re

from indexer import ALL_FIELD, FIELDS, TEXT_FIELDS, avg_lengths
from normalize import read_tsv, to_float
from tokenizer import tokenize

# BM25 parameters: k1 = how fast repeating a term stops helping, b = how much document length matters
BM25_K1 = 1.5
BM25_B = 0.75

# short filter names: date=*.*.1995
FILTER_ALIASES = {"date": "launch_date"}

# columns printed next to each result
SHOW_COLUMNS = ["object_type", "status", "orbit_class", "launch_date"]


# 'orbit_class=GEO launch_vehicle:"falcon 9" broadband' -> filters, keywords
# filters: [(field, op, value)], keywords: [(field, term)], plain words go to the 'all' field
def parse_query(query, doc_columns):
    filters, keywords = [], []
    # split on spaces, but keep quoted parts together: launch_vehicle:"falcon 9" -> launch_vehicle:falcon 9
    for part in re.findall(r'(?:[^\s"]|"[^"]*")+', query):
        part = part.replace('"', '')
        # filter: field, operator, value with no spaces around the operator (inclination_deg>90)
        m = re.match(r"^([a-z_]+)(>=|<=|!=|=|>|<)(.+)$", part)
        if m:
            field, op, value = m.groups()
            field = FILTER_ALIASES.get(field, field)
            if field not in doc_columns:
                raise ValueError(f"unknown filter field '{field}', use one of: {', '.join(doc_columns)}")
            filters.append((field, op, value))
            continue

        # keyword in one field: field:words (manufacturer:hughes, launch_vehicle:"falcon 9")
        m = re.match(r"^([a-z_]+):(.+)$", part)
        if m:
            field, text = m.groups()
            if field not in TEXT_FIELDS:
                raise ValueError(f"unknown text field '{field}', use one of: {', '.join(TEXT_FIELDS)}")
        else:
            field, text = ALL_FIELD, part

        # same tokenizer as the indexer, repeated terms are dropped
        for term in tokenize(text):
            if (field, term) not in keywords:
                keywords.append((field, term))
    return filters, keywords


# '24.05.2014' -> '20140524' so dates sort by year, month, day; other text -> lowercase
def sort_key(s):
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", s)
    if m:
        day, month, year = m.groups()
        return year + month.zfill(2) + day.zfill(2)
    return s.lower()


# 'STARLINK*' -> regex 'STARLINK.*', * = any text, ? = one character
def matches(value, pattern):
    regex = re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
    return re.fullmatch(regex, value, re.IGNORECASE) is not None


# one filter on one value; numbers are compared as numbers, dates as dates, everything else as lowercase text
# '=' and '!=' accept a list and * / ? wildcards: orbit_class=LEO,MEO  date=*.*.1995
def passes(value, op, target):
    # a missing value only passes '!='
    if value == "":
        return op == "!="

    v, t = to_float(value), to_float(target)
    if op in ("=", "!="):
        same = any((v is not None and to_float(o) == v) or matches(value, o) for o in target.split(","))
        return same if op == "=" else not same

    if v is None or t is None:
        v, t = sort_key(value), sort_key(target)
    if op == ">":
        return v > t
    if op == "<":
        return v < t
    if op == ">=":
        return v >= t
    return v <= t


# reads only the postings lines of the query terms, the rest of the file is skipped
# returns {(field, term): (tfidf, bm25, {norad_id: tf})}
def read_postings(file_path, keywords):
    found = {}
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


# intersection of two postings lists sorted by docID, in linear time (lecture: INTERSECT(p1, p2))
def intersect(p1, p2):
    answer = []
    i = j = 0
    while i < len(p1) and j < len(p2):
        if p1[i] == p2[j]:
            answer.append(p1[i])
            i += 1
            j += 1
        elif p1[i] < p2[j]:
            i += 1
        else:
            j += 1
    return answer


# docs that contain every keyword: the postings lists are intersected from the shortest (lowest df) up,
# so the intermediate result stays as small as possible
def intersect_all(keywords, postings):
    if len(postings) < len(keywords):
        return []  # a keyword is in no document
    lists = sorted((sorted(int(doc) for doc in docs) for _, _, docs in postings.values()), key=len)
    result = lists[0]
    for p in lists[1:]:
        result = intersect(result, p)
    return [str(doc) for doc in result]


# method 1: tf-idf, how often the term is in the document times how rare it is, document length is ignored
def score_tfidf(tf, idf):
    return tf * idf


# method 2: BM25, tf is saturated by k1 and normalized by the field length
def score_bm25(tf, idf, doc_len, avg_len):
    # a doc with a term in the field has doc_len > 0, so avg_len > 0
    norm = 1 - BM25_B + BM25_B * doc_len / avg_len
    return idf * tf * (BM25_K1 + 1) / (tf + BM25_K1 * norm)


# candidates + keywords -> [(norad_id, score)] best first
# match 'all': every keyword must be in the document (AND), 'any': at least one (OR)
def rank(candidates, keywords, postings, lengths, avg, method, match):
    if not keywords:
        return [(doc, None) for doc in sorted(candidates, key=int)]
    if match == "all":
        candidates = candidates & set(intersect_all(keywords, postings))

    scores = {}
    for (field, _), (idf_t, idf_b, docs) in postings.items():
        for doc, tf in docs.items():
            if doc in candidates:
                if method == "tfidf":
                    score = score_tfidf(tf, idf_t)
                else:
                    score = score_bm25(tf, idf_b, lengths[doc][field], avg[field])
                scores[doc] = scores.get(doc, 0) + score

    # higher score first, equal scores (boilerplate) by NORAD ID
    return sorted(scores.items(), key=lambda kv: (-kv[1], int(kv[0])))


def print_results(results, docs, filters, method, top):
    columns = SHOW_COLUMNS + [f for f, _, _ in filters if f not in SHOW_COLUMNS]
    print(f"\n[{method}] {len(results)} results" + (f", showing top {top}" if len(results) > top else ""))
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

    print(f"filters:  {' '.join(f + op + v for f, op, v in filters) or '-'}")
    print(f"keywords: {' '.join(t if f == ALL_FIELD else f'{f}:{t}' for f, t in keywords) or '-'}")

    _, length_rows = read_tsv(f"{args.index_dir}/doc_lengths.tsv")
    lengths = {r["norad_id"]: {field: int(r[field]) for field in FIELDS} for r in length_rows}
    avg = avg_lengths(lengths)

    candidates = {doc for doc, d in docs.items() if all(passes(d[field], op, value) for field, op, value in filters)}
    postings = read_postings(f"{args.index_dir}/postings.tsv", keywords) if keywords else {}
    for method in (["tfidf", "bm25"] if args.method == "both" else [args.method]):
        results = rank(candidates, keywords, postings, lengths, avg, method, args.match)
        print_results(results, docs, filters, method, args.top)


if __name__ == "__main__":
    main()
