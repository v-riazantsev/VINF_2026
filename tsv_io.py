# returns (header, rows as dicts)
def read_tsv(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        # strip only the newline, strip() would eat trailing empty columns
        header = f.readline().rstrip("\n").split("\t")
        rows = [dict(zip(header, line.rstrip("\n").split("\t"))) for line in f]
    return header, rows


def write_tsv(file_path, header, rows):
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\t".join(header) + "\n")
        for row in rows:
            f.write("\t".join(row[c] for c in header) + "\n")


# one row (dict) -> one TSV line without the newline
def to_line(row, header):
    return "\t".join(row[c] for c in header)
