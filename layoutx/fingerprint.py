import argparse
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PIPE = os.path.dirname(HERE)
OUT = os.path.join(PIPE, "out")
ANCHORS = ["13569913", "13567494", "13568651", "13569417", "13569428", "13564500", "13564501", "13569720"]


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def pages(doc):
    folder = os.path.join(OUT, doc, "unified")
    names = [n for n in os.listdir(folder) if re.fullmatch(r"page_\d+\.json", n)]
    return [load(os.path.join(folder, n)) for n in sorted(names, key=lambda n: int(n[5:-5]))]


def clean(s):
    s = re.sub(r"</?(?:sup|sub|b|i|em|strong)>", "", s or "", flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


def is_abstract(s):
    return bool(re.match(r"^\s*(?:\*\*)?(?:요약|국문\s*요약|초록|abstract|summary)\b", clean(s), re.I))


def is_keywords(s):
    return bool(re.match(r"^\s*(?:\*\*)?(?:주제어|키워드|핵심어|keywords?|key\s+words?)\s*[:：]", clean(s), re.I))


def margin(u):
    return u.get("type") in ("Page-header", "Page-footer", "Page-number")


def title_candidate(p):
    w, h = p["w"], p["h"]
    cand = []
    for order, u in enumerate(p["unified"]):
        text = clean(u.get("main_text"))
        x1, y1, x2, y2 = u["box"]
        if margin(u) or u.get("type") not in ("Text", "Section-header") or not text or y1 < .055 * h or y1 > .53 * h:
            continue
        if is_abstract(text) or is_keywords(text) or re.match(r"^(?:ISSN|DOI|https?://|www\.)", text, re.I):
            continue
        if len(text) > (1200 if u.get("type") == "Section-header" else 400) or ("@" in text and u.get("type") != "Section-header"):
            continue
        score = (3 if u.get("type") == "Section-header" else 0)
        score += 4 if len(re.findall(r"[가-힣]", text)) >= 4 else 0
        score += 1.5 if .1 * w < x1 and x2 < .94 * w else 0
        score += 1.0 if len(text) >= 8 else -3
        score += 1.0 if (x2 - x1) > .28 * w else 0
        score -= y1 / h
        score -= .1 * order
        cand.append((score, order, u))
    if not cand:
        return None
    best = max(cand, key=lambda z: (z[0], -z[1]))[2]
    if best.get("type") != "Section-header":
        return best
    adjacent = []
    for u in p["unified"]:
        text = clean(u.get("main_text"))
        if u.get("type") != "Section-header" or not re.search(r"[가-힣]", text) or not (6 <= len(text) <= 180):
            continue
        if is_abstract(text) or is_keywords(text) or re.search(r"[|@]", text):
            continue
        if abs(u["box"][1] - best["box"][1]) < .065 * h or (best["box"][1] <= u["box"][1] <= best["box"][3] + .04 * h):
            adjacent.append(u)
    if best not in adjacent:
        adjacent.append(best)
    adjacent.sort(key=lambda u: u["box"][1])
    if len(adjacent) < 2:
        return best
    return dict(best, main_text="\n".join(u.get("main_text") or "" for u in adjacent),
                box=[min(u["box"][0] for u in adjacent), min(u["box"][1] for u in adjacent),
                     max(u["box"][2] for u in adjacent), max(u["box"][3] for u in adjacent)])


def byline_candidates(p, title):
    if not title:
        return []
    w, h = p["w"], p["h"]
    out = []
    for u in p["unified"]:
        if margin(u) or u.get("type") not in ("Text", "Section-header"):
            continue
        text = clean(u.get("main_text"))
        x1, y1, x2, y2 = u["box"]
        if y1 < title["box"][3] - 3 or y1 > .67 * h or len(text) > 260:
            continue
        if is_abstract(text) or is_keywords(text) or "@" in text or "http" in text:
            continue
        plain = re.sub(r"[\s*†‡¹²³⁴⁵⁶⁷⁸⁹<>/sup,()（）\-]", "", text)
        hangul = len(re.findall(r"[가-힣]", plain))
        latin = len(re.findall(r"[A-Za-z]", plain))
        marks = bool(re.search(r"[*†‡<]|\([^)]*(?:대학|연구|학과|교수)[^)]*\)", text))
        short_ko = 2 <= len(re.sub(r"\s", "", text)) <= 35 and hangul >= 2 and len(text.split()) <= 10
        roman = bool(re.match(r"^[A-Z][A-Za-z' .,&-]{2,100}$", text))
        if short_ko or roman or marks:
            out.append(u)
    return out


def fingerprint(doc):
    ps = pages(doc)
    p0, tail = ps[0], ps[-1]
    title = title_candidate(p0)
    bys = byline_candidates(p0, title)
    all_blocks = [u for p in ps for u in p["unified"]]
    abstract_first = any(is_abstract(u.get("main_text")) for u in p0["unified"])
    abstract_tail = any(is_abstract(u.get("main_text")) for u in tail["unified"])
    abstract_any = any(is_abstract(u.get("main_text")) for u in all_blocks)
    kw_first = any(is_keywords(u.get("main_text")) for u in p0["unified"])
    kw_tail = any(is_keywords(u.get("main_text")) for u in tail["unified"])
    issn = any(re.search(r"\bISSN\b", u.get("main_text") or "", re.I) for u in p0["unified"])
    grid = len(bys) >= 3 and sum((u["box"][2] - u["box"][0]) < .42 * p0["w"] for u in bys) >= 3
    shape = "grid" if grid else "none" if not bys else "single" if len(bys) == 1 else "multi"
    body = [u for u in p0["unified"] if not margin(u) and u["box"][1] > .52 * p0["h"] and not is_abstract(u.get("main_text")) and not is_keywords(u.get("main_text"))]
    widths = [(u["box"][2] - u["box"][0]) / p0["w"] for u in body]
    cols = 2 if len(widths) >= 2 and sum(x < .53 for x in widths) >= 2 else 1
    spaced = any(re.search(r"[가-힣]\s+[가-힣]\s+[가-힣]", u.get("main_text") or "") for u in bys)
    roman = any(re.search(r"[A-Za-z]{3,}", u.get("main_text") or "") for u in bys)
    key = (
        "header" if title and title.get("type") == "Section-header" else "text" if title else "none",
        int((title["box"][1] / p0["h"]) * 10) if title else -1,
        shape,
        "roman" if roman else "plain",
        "spaced" if spaced else "normal",
        "first" if abstract_first else "tail" if abstract_tail else "other" if abstract_any else "none",
        "first" if kw_first else "tail" if kw_tail else "other" if any(is_keywords(u.get("main_text")) for u in all_blocks) else "none",
        "issn" if issn else "noissn",
        "%dcol" % cols,
    )
    return {"doc": doc, "cluster_key": list(key), "title": title, "bylines": bys,
            "features": {"pages": len(ps), "abstract": key[5], "keywords": key[6], "issn": issn,
                         "columns": cols, "author_shape": shape, "roman": roman, "spaced": spaced}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "fingerprints.jsonl"))
    args = ap.parse_args()
    docs = sorted(n for n in os.listdir(OUT) if n.isdigit() and os.path.isdir(os.path.join(OUT, n)))
    records = []
    for i, doc in enumerate(docs, 1):
        try:
            records.append(fingerprint(doc))
        except Exception as e:
            records.append({"doc": doc, "error": "%s: %s" % (type(e).__name__, e), "cluster_key": ["error"]})
        if i % 100 == 0:
            print("fingerprinted", i, file=sys.stderr)
    with open(args.out, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, default=lambda x: None) + "\n")
    groups = collections.defaultdict(list)
    for r in records:
        groups[tuple(r["cluster_key"])].append(r["doc"])
    ordered = sorted(groups.items(), key=lambda z: (-len(z[1]), z[0]))
    clusters = [{"cluster_id": "C%02d" % (i + 1), "key": list(k), "count": len(v), "docs": v}
                for i, (k, v) in enumerate(ordered)]
    summary = {"total": len(records), "clusters": len(clusters), "cluster_list": clusters,
               "anchors": {d: next((c["cluster_id"] for c in clusters if d in c["docs"]), None) for d in ANCHORS}}
    with open(os.path.join(HERE, "clusters.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps({"total": len(records), "clusters": len(clusters), "head": [(c["cluster_id"], c["count"], c["key"]) for c in clusters[:12]], "anchors": summary["anchors"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
