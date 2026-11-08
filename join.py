import argparse

from tsv_io import read_tsv, write_tsv


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--satellites_tsv", type=str, required=False, default="./data_clean.tsv", help="TSV produced by extractor.py")
    parser.add_argument("--wiki_tsv", type=str, required=False, default="./wiki_sat.tsv", help="TSV produced by wiki_extractor.py")
    parser.add_argument("--output_tsv", type=str, required=False, default="./data_joined.tsv", help="Joined TSV")
    args = parser.parse_args()

    sat_columns, satellites = read_tsv(args.satellites_tsv)
    wiki_columns, wiki = read_tsv(args.wiki_tsv)

    # SATCAT -> article; one article can list several SATCAT numbers ('4371,4372')
    by_satcat = {}
    for article in wiki:
        for satcat in article["wiki_satcat"].split(","):
            by_satcat.setdefault(satcat, article)

    # every satellite stays, satellites without an article get empty wiki columns
    no_article = dict.fromkeys(wiki_columns, "")
    rows = []
    joined = 0
    used_articles = set()
    for sat in satellites:
        article = by_satcat.get(sat["norad_id"])
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