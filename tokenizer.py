import argparse
import re
import unicodedata

# shared by the indexer and the search, both must use it or terms will not match
# changing the rules means the index has to be rebuilt

# small English stopword list, common words that carry no search meaning
STOPWORDS = set("""
a an and are as at be been but by for from has have in into is it its of on or
that the their this to was were which with also
""".split())


# 'Société' -> 'societe' like in the lecture example (šťastlivo -> stastny), non-latin letters (e.g. CJK) stay untouched
def strip_accents(s):
    decomposed = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


# light plural stemmer: satellites -> satellite, batteries -> battery, classes -> class
def stem(token):
    # numbers and short words stay as they are
    if len(token) <= 3 or token.isdigit():
        return token
    if token.endswith("sses"):
        return token[:-2]
    if token.endswith("ies"):
        return token[:-3] + "y"
    # 'debris', 'status', 'class' are not plurals
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


# text -> list of terms, order and duplicates are kept (tf needs them)
def tokenize(text):
    terms = []
    # letters/digits in any script, underscore excluded
    for t in re.findall(r"[^\W_]+", strip_accents(text).lower()):
        # one-letter words are noise ('h' in 'H-IIA'), a lone digit is not ('9' in 'Falcon 9')
        if len(t) == 1 and t.isascii() and t.isalpha():
            continue
        if t not in STOPWORDS:
            terms.append(stem(t))
    return terms


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", type=str, required=True, help="Text to tokenize")
    args = parser.parse_args()

    print(tokenize(args.text))


if __name__ == "__main__":
    main()
