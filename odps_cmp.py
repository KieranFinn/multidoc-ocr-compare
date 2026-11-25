# -*- coding: utf-8 -*-
"""odps_cmp.py — 工作台抽样 vs KCI 官方(ODPS) 字段比对(2026-11-24)。

连接键: source_file = md5(a_url), a_url = https://www.kci.go.kr/...artiId=<ART号>。
ART 号无法从工作台数字 doc id 推出, 所以走"整表导出 + 本地关联":
  主键 = md5(a_url);  兜底 = (折叠题名, 年份) —— 命中后必须再验首作者才采信。

判定四档(ODPS 是参照不是标准答案):
  same   归一后一致
  fmt    仅格式差(大小写/空白/页码形态/姓名序), 归一后一致
  diff   值真不同(两边都非空) —— 需人工定责: 我们错 or 官方可疑
  miss   单边空(官方为空居多; 官方空 ≠ 我们错)

用法:
  python odps_cmp.py <导出.csv> --ws KCI11_17 --sample 30   # 随机抽 30 篇
  python odps_cmp.py <导出.csv> --ws KCI11_17 13578215 ...  # 指定篇目
  python odps_cmp.py <导出.csv> --ws KCI11_17 --all         # 全库
  python odps_cmp.py <导出.csv> --only title pages authors  # 只看这几列
导出: DataWorks 临时查询结果区右键导出 CSV(utf-8-sig, 带全部行)。
"""
import argparse
import csv
import io
import json
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

URL = ("https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci"
       "?sereArticleSearchBean.artiId=%s")

FIELDS = ("title", "title_en", "year", "volume", "issue", "pages", "doi",
          "journal", "keywords", "authors", "references", "lang")

_LANG_MAP = {u"韩文": "kr", u"中文": "zh", u"英文": "en", u"俄语": "ru",
             u"葡萄牙语": "pt", u"法语": "fr"}


# ---------- 归一器(两边各自归一到同一口径, 再比) ----------

def fold(s):
    return re.sub(r"\s+", "", str(s or "")).lower()


def _strip_punct(s):
    return re.sub(r"[\s\-–—·•․,，.。:：;；!?'\"“”‘’()()〈〉《》\[\]<>]", "", fold(s))


def _int(v):
    m = re.search(r"\d+", str(v or ""))
    return int(m.group(0)) if m else None


def _pages(v):
    """'315-328' / '315–328' / '315' / 315 → (首, 尾) 或 (n, n)。"""
    if v is None:
        return None
    s = str(v).strip()
    m = re.match(r"^(\d{1,4})\s*[-–—~～]\s*(\d{1,4})$", s)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    n = _int(s)
    return (n, n) if n is not None else None


def _kw_list(cell):
    """ODPS a_keywords 是 JSON 数组字符串(可能含 \r\n / 转义)。"""
    if not cell:
        return []
    try:
        v = json.loads(cell)
        return [str(x).strip() for x in v if str(x).strip()]
    except Exception:
        return [x.strip() for x in re.split(r"[,;]", str(cell)) if x.strip()]


def _kw_split_koen(kws):
    ko, en = [], []
    for k in kws:
        if re.search(u"[가-힣]", k):
            ko.append(_strip_punct(k))
        else:
            en.append(_strip_punct(k))
    return ko, en


def _authors_odps(cell):
    """authors_raw JSON → [(order, ko, en, [affils])]。"""
    if not cell:
        return []
    try:
        v = json.loads(cell)
    except Exception:
        return []
    out = []
    for a in v if isinstance(v, list) else []:
        if not isinstance(a, dict):
            continue
        try:
            order = int(a.get("author_order") or 99)
        except ValueError:
            order = 99
        out.append((order, str(a.get("author_name") or "").strip(),
                    str(a.get("author_name_en") or "").strip(),
                    [str(x).strip() for x in (a.get("affiliations") or [])]))
    out.sort(key=lambda x: x[0])
    return out


# ---------- 工作台侧取值 ----------

def bench_fields(meta):
    def val(k):
        v = meta.get(k)
        return v.get("value") if isinstance(v, dict) else v

    kws = [str(x) for x in (meta.get("keywords") or [])]
    kw_en = [str(x) for x in (meta.get("keywords_en") or [])]
    aus = []
    for a in (meta.get("authors") or []):
        if isinstance(a, dict):
            aus.append(a)
    refs = meta.get("references") or []
    return {
        "title": fold(val("title")),
        "title_en": _strip_punct(val("title_en")),
        "year": _int(val("year")),
        "volume": _int(val("volume")),
        "issue": _int(val("issue")),
        "pages": _pages(val("pages")),
        "doi": fold(str(val("doi") or "").replace("https://doi.org/", "")),
        "journal": fold(val("journal")),
        "keywords": sorted(_strip_punct(x) for x in kws),
        "keywords_en": sorted(_strip_punct(x) for x in kw_en),
        "authors": [{"name": a.get("name") or "", "roman": a.get("roman") or "",
                     "affil": a.get("affiliation") or ""} for a in aus],
        "references": len(refs),
        "lang": _LANG_MAP.get(meta.get("language") or "", fold(meta.get("language"))),
    }


# ---------- ODPS 侧取值 ----------

def odps_fields(row):
    aus = _authors_odps(row.get("authors_raw"))
    kws = _kw_list(row.get("a_keywords"))
    ko, en = _kw_split_koen(kws)
    rng = _pages(row.get("a_page_range"))
    if rng is None:
        f, l = _int(row.get("a_first_page")), _int(row.get("a_last_page"))
        rng = (f, l) if f is not None and l is not None else None
    return {
        "title": fold(row.get("a_title")),
        "title_en": _strip_punct(row.get("a_title_en")),
        "year": _int(row.get("a_year")),
        "volume": _int(row.get("a_volume")),
        "issue": _int(row.get("a_issue")),
        "pages": rng,
        "doi": fold(str(row.get("a_doi") or "").replace("https://doi.org/", "")),
        "journal": fold(row.get("j_title")),
        "keywords": sorted(ko),
        "keywords_en": sorted(en),
        "authors": [{"name": a[1], "roman": a[2], "affil": " | ".join(a[3])}
                    for a in aus],
        "references": len(_kw_list(row.get("references_raw")) or []) or None,
        "lang": fold(row.get("a_lang")),
        "_raw_kw_count": len(kws),
    }


# ---------- 逐字段比对 ----------

def cmp_field(name, b, o):
    """→ (verdict, 说明)。verdict ∈ same/fmt/diff/miss/skip"""
    if name == "authors":
        return cmp_authors(b, o)
    if name == "references":
        return cmp_refs(b, o)
    if name in ("keywords", "keywords_en"):
        if not b and not o:
            return ("same", "")
        if not o:
            return ("miss", "官方空/无此类词")
        if not b:
            return ("diff", "我们缺; 官方 %d 个" % len(o))
        bs, os_ = set(b), set(o)
        if bs == os_:
            return ("same", "%d 个" % len(bs))
        return ("fmt" if (bs & os_) else "diff",
                "交集 %d; 仅我方 %s; 仅官方 %s" % (
                    len(bs & os_), "/".join(sorted(bs - os_)[:3]) or "无",
                    "/".join(sorted(os_ - bs)[:3]) or "无"))
    # 标量字段
    if b in (None, "", []) and o in (None, "", []):
        return ("same", "")
    if o in (None, "", []):
        return ("miss", "官方为空")
    if b in (None, "", []):
        return ("diff", "我们为空")
    if name in ("title", "title_en", "journal"):
        if b == o:
            return ("same", "")
        if _strip_punct(b) == _strip_punct(o):
            return ("fmt", "标点/空白差")
        return ("diff", "值不同")
    if b == o:
        return ("same", "")
    return ("diff", "bench=%r odps=%r" % (b, o))


def cmp_authors(b, o):
    if not b and not o:
        return ("same", "")
    if not o:
        return ("miss", "官方无作者")
    if not b:
        return ("diff", "我们缺作者")
    notes = []
    if len(b) != len(o):
        notes.append("人数 %d≠%d" % (len(b), len(o)))
    ko_b = [fold(a["name"]) for a in b]
    ko_o = [fold(a["name"]) for a in o]
    if ko_b == ko_o[:len(ko_b)]:
        pass
    elif set(ko_b) == set(ko_o):
        notes.append("顺序不同")
    else:
        only_b = [a["name"] for a, f in zip(b, ko_b) if f not in set(ko_o)]
        only_o = [a["name"] for a, f in zip(o, ko_o) if f not in set(ko_b)]
        notes.append("人名不一致: 仅我方 %s / 仅官方 %s" % (
            ",".join(only_b[:3]) or "无", ",".join(only_o[:3]) or "无"))
    if not notes:
        return ("same", "%d 人" % len(b))
    return ("diff" if any("不一致" in n for n in notes) else "fmt", "; ".join(notes))


def cmp_refs(b, o):
    if not o:
        return ("miss", "官方无参考文献列")
    if not b:
        return ("diff", "官方 %d 条, 我们 0 条" % o)
    if b == o:
        return ("same", "%d 条" % b)
    return ("fmt" if abs(b - o) <= max(2, 0.1 * max(b, o)) else "diff",
            "我们 %d 条 / 官方 %d 条" % (b, o))


# ---------- 加载与关联 ----------

def load_odps(fp):
    rows = []
    # DataWorks 结果区"复制选中"出来是 TSV; 导出的文件是 CSV。按首行 sniff。
    with io.open(fp, encoding="utf-8-sig", newline="") as fh:
        head = fh.read(4096)
        fh.seek(0)
        delim = "\t" if ("\t" in head.splitlines()[0] if head.splitlines() else False) else ","
        for row in csv.DictReader(fh, delimiter=delim):
            if row.get("article_kci_id") or row.get("a_url"):
                rows.append(row)
    return rows


def build_index(rows):
    by_url = {}
    by_title = {}
    by_doc = {}
    for r in rows:
        if r.get("doc"):
            by_doc[r["doc"]] = r
        url = (r.get("a_url") or "").strip()
        if url:
            import hashlib
            by_url[hashlib.md5(url.encode()).hexdigest()] = r
        key = (fold(r.get("a_title")), str(_int(r.get("a_year")) or ""))
        if key[0]:
            by_title.setdefault(key, []).append(r)
    return by_url, by_title, by_doc


def find_row(bf, by_url, by_title, by_doc=None, doc=None):
    if by_doc and doc and doc in by_doc:
        return by_doc[doc], "doc-join"
    import hashlib
    art = re.search(r"artiId=(\w+)", bf.get("_url_hint", "") or "")
    if art:
        h = hashlib.md5((URL % art.group(1)).encode()).hexdigest()
        if h in by_url:
            return by_url[h], "url"
    cands = by_title.get((bf["title"], str(bf["year"] or "")), [])
    if len(cands) == 1:
        return cands[0], "title"
    return None, None


# ---------- 主流程 ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_file")
    ap.add_argument("docs", nargs="*", help="doc id; 空则配 --sample/--all")
    ap.add_argument("--ws", default=None)
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--workers", type=int, default=1)
    a = ap.parse_args()

    import workspaces
    root, ws_name, src = workspaces.resolve_ws_root(explicit=a.ws, here=HERE)
    out_dir = os.path.join(root, "out") if root else os.path.join(HERE, "out")
    print("[工作区] %s (%s) · 来源 %s" % (ws_name, root or HERE, src))

    rows = load_odps(a.csv_file)
    by_url, by_title, by_doc = build_index(rows)
    print("[odps] %d 行已索引(doc键 %d / url键 %d / 题名键 %d)" % (
        len(rows), len(by_doc), len(by_url), len(by_title)))

    docs = a.docs
    if not docs:
        pool = sorted(d for d in os.listdir(out_dir)
                      if os.path.isfile(os.path.join(out_dir, d, "metadata.json")))
        if a.all:
            docs = pool
        elif a.sample:
            random.seed(20261124)
            docs = random.sample(pool, min(a.sample, len(pool)))
        else:
            raise SystemExit("请给 doc 列表, 或 --sample N, 或 --all")
    fields = a.only or list(FIELDS)

    stat = {f: {"same": 0, "fmt": 0, "diff": 0, "miss": 0} for f in fields}
    n_hit = n_miss = 0
    diff_rows = []
    for doc in docs:
        mfp = os.path.join(out_dir, doc, "metadata.json")
        try:
            meta = json.load(io.open(mfp, encoding="utf-8"))
        except Exception:
            continue
        bf = bench_fields(meta)
        # language 不在 metadata.json 里(qc 现算), 补一次; 与 GUI 语言列同口径
        try:
            import qc
            bf["lang"] = _LANG_MAP.get(qc.language_of(doc, os.path.dirname(mfp)) or "", "")
        except Exception:
            pass
        row, how = find_row(bf, by_url, by_title, by_doc, doc)
        if row is None or (not (row.get("a_url") or "").strip()
                           and not (row.get("a_title") or "").strip()):
            n_miss += 1
            print("? %-14s 未命中(表未覆盖或题名对不上)" % doc)
            continue
        n_hit += 1
        of = odps_fields(row)
        line = []
        for f in fields:
            v, note = cmp_field(f, bf.get(f), of.get(f))
            stat[f][v] += 1
            mark = {"same": "=", "fmt": "~", "diff": "≠", "miss": "∅"}[v]
            line.append("%s%s" % (f, mark))
            if v in ("diff",):
                diff_rows.append((doc, f, note))
            if v in ("diff", "fmt") and note:
                print("  %-14s %-10s %s %s" % (doc, f, mark, note[:90]))
        print("%-14s [%s] via %s" % (doc, " ".join(line), how))

    print("\n===== 汇总 (命中 %d / 未命中 %d) =====" % (n_hit, n_miss))
    print("%-10s %6s %6s %6s %6s" % ("字段", "一致", "格式", "真差", "单边"))
    for f in fields:
        s = stat[f]
        tot = max(1, sum(s.values()))
        print("%-10s %6d %6d %6d %6d  (一致率 %.0f%%)" % (
            f, s["same"], s["fmt"], s["diff"], s["miss"], 100.0 * s["same"] / tot))
    if diff_rows:
        print("\n真差清单(需人工定责, 我们错 or 官方可疑):")
        for doc, f, note in diff_rows[:40]:
            print("  %-14s %-10s %s" % (doc, f, note[:100]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
