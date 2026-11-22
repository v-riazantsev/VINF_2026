# pyright: reportMissingImports=false
import argparse
from collections import defaultdict

import lucene
from java.lang import Double, Integer
from java.nio.file import Paths
from java.text import NumberFormat
from java.util import HashMap, Locale
from org.apache.lucene.analysis.en import EnglishAnalyzer
from org.apache.lucene.index import DirectoryReader
from org.apache.lucene.queryparser.flexible.standard import StandardQueryParser
from org.apache.lucene.queryparser.flexible.standard.config import PointsConfig, StandardQueryConfigHandler
from org.apache.lucene.search import IndexSearcher
from org.apache.lucene.search.similarities import BM25Similarity, ClassicSimilarity
from org.apache.lucene.store import FSDirectory

from indexer import ALL_FIELD
from lucene_indexer import DATE_FIELDS, NUMBER_FIELDS
from search import BM25_B, BM25_K1, print_results


# Lucene query text -> Lucene query
# the parser picks the query type from the text:
#   hubble                        -> TermQuery
#   "space telescope"             -> PhraseQuery
#   hubbel~                       -> FuzzyQuery
#   tele*                         -> PrefixQuery
#   hubble -status:decayed        -> BooleanQuery
#   apogee_km:[2000 TO *]         -> DoublePoint range (number field)
#   launch_date:[19950101 TO 19951231] -> IntPoint range (date field)
def build_query(text):
    parser = StandardQueryParser(EnglishAnalyzer())
    parser.setDefaultOperator(StandardQueryConfigHandler.Operator.AND)

    number_format = NumberFormat.getInstance(Locale.ROOT)  # reads '2000' and '0.5' as numbers
    points = HashMap()
    for field in NUMBER_FIELDS:
        points.put(field, PointsConfig(number_format, Double.class_))  # indexed as DoublePoint
    for field in DATE_FIELDS:
        points.put(field, PointsConfig(number_format, Integer.class_))  # indexed as IntPoint (yyyymmdd)
    parser.setPointsConfigMap(points)

    return parser.parse(text, ALL_FIELD)  # words without a field search 'all'


# Lucene document -> dict of its stored values, a missing field gives ''
def stored_values(doc):
    values = defaultdict(str)
    for field in doc.getFields():
        values[field.name()] = field.stringValue()
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index_dir", type=str, required=False, default="./lucene_index", help="Directory produced by lucene_indexer.py")
    parser.add_argument("--query", type=str, required=True, help="Lucene query syntax")
    parser.add_argument("--method", type=str, required=False, default="bm25", choices=["tfidf", "bm25", "both"], help="Ranking method")
    parser.add_argument("--top", type=int, required=False, default=20, help="How many results to print")
    args = parser.parse_args()

    # starts Java, Lucene works only after this
    lucene.initVM()

    try:
        query = build_query(args.query)
    except lucene.JavaError as e:  # text Lucene can not parse, or a wrong number
        print(f"[!] {e}")
        return
    print(f"lucene:  {query}")  # the final query as Lucene sees it, after the analyzer

    # number / date fields used in the query, print_results shows them as extra columns
    filters = [(field, "", "") for field in NUMBER_FIELDS + DATE_FIELDS if field + ":" in args.query]

    searcher = IndexSearcher(DirectoryReader.open(FSDirectory.open(Paths.get(args.index_dir))))
    similarities = {"tfidf": ClassicSimilarity(), "bm25": BM25Similarity(BM25_K1, BM25_B)}
    methods = ["tfidf", "bm25"] if args.method == "both" else [args.method]

    for method in methods:
        searcher.setSimilarity(similarities[method])
        results = []  # [(norad_id, score)], like rank() in search.py
        docs = {}  # norad_id -> stored values, for print_results
        for hit in searcher.search(query, args.top).scoreDocs:
            values = stored_values(searcher.storedFields().document(hit.doc))
            docs[values["norad_id"]] = values
            results.append((values["norad_id"], hit.score))
        print_results(results, docs, filters, method, args.top, total=searcher.count(query))


if __name__ == "__main__":
    main()