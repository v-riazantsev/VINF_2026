import argparse

from tsv_io import read_tsv, write_tsv


# both modes return (satellite, article) pairs, article is None when the satellite has no wiki article

# local mode: dict SATCAT -> article, then one lookup per satellite
def run_local(satellites, wiki):
    # one article can list several SATCAT numbers ('4371,4372')
    # with two articles for one satellite the first one wins (wiki_sat.tsv is sorted by URL)
    by_satcat = {}
    for article in wiki:
        for satcat in article["wiki_satcat"].split(","):
            by_satcat.setdefault(satcat, article)

    pairs = []
    for sat in satellites:
        pairs.append((sat, by_satcat.get(sat["norad_id"])))
    return pairs


# Spark mode: (norad_id, satellite) left outer join (satcat, article)
def run_spark(satellites, wiki):
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.appName("join").getOrCreate()
    sc = spark.sparkContext
    # one (satcat, article) pair for every SATCAT number of every article
    articles = sc.parallelize(wiki).flatMap(lambda article: [(satcat, article) for satcat in article["wiki_satcat"].split(",")])
    # two articles for one satellite: keep the first URL, like the local mode
    articles = articles.reduceByKey(lambda a, b: a if a["wiki_url"] < b["wiki_url"] else b)
    sats = sc.parallelize(satellites).map(lambda sat: (sat["norad_id"], sat))
    # leftOuterJoin keeps every satellite: (norad_id, (satellite, article or None))
    pairs = sats.leftOuterJoin(articles).values().collect()
    spark.stop()
    return pairs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--satellites_tsv", type=str, required=False, default="./data_clean.tsv", help="TSV produced by extractor.py")
    parser.add_argument("--wiki_tsv", type=str, required=False, default="./wiki_sat.tsv", help="TSV produced by wiki_extractor.py")
    parser.add_argument("--output_tsv", type=str, required=False, default="./data_joined.tsv", help="Joined TSV")
    parser.add_argument("--mode", type=str, required=False, default="local", choices=["local", "spark"], help="local = one process, spark = distributed")
    args = parser.parse_args()

    sat_columns, satellites = read_tsv(args.satellites_tsv)
    wiki_columns, wiki = read_tsv(args.wiki_tsv)

    pairs = run_local(satellites, wiki) if args.mode == "local" else run_spark(satellites, wiki)
    # Spark returns the pairs in random order, sorting makes both modes write the same file
    pairs.sort(key=lambda pair: int(pair[0]["norad_id"]))

    # every satellite stays, satellites without an article get empty wiki columns
    no_article = dict.fromkeys(wiki_columns, "")
    rows = []
    joined = 0
    used_articles = set()
    for sat, article in pairs:
        if article:
            joined += 1
            used_articles.add(article["wiki_url"])
        else:
            article = no_article
        rows.append({**sat, **article})
    write_tsv(args.output_tsv, sat_columns + wiki_columns, rows)

    print(f"satellites: {len(rows)}, joined with a wiki article: {joined}, "
          f"unique wiki articles used: {len(used_articles)} of {len(wiki)}")


if __name__ == "__main__":
    main()