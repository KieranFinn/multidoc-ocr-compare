# -*- coding: utf-8 -*-
"""行内引文抽取(--stage cspan): 正文 Text 块 → 切句 → 三种形态 → 与 references[] 对号。
规格(用户九条+第十条替换版, 全部经代码核实):
① 收集: type=="Text" 且页不在 ref_pages(含 §44 回退, 本文件重跑该 8 行逻辑——与 refs.py
   refs_doc 同口径, 改一处必须同步另一处); Footnote 块排除——行内引文本就在 Text 块, 且
   Footnote 块行首的 [\\d] 是脚注编号(FOOTNOTE_CITE 形态, m3 共享不许动), 排除同时防这层误报。
② 三形态: 叙述式=通用候选+本篇 references 同年作者键反向定位(精确优先, 长键允许 1 次 OCR 编辑);
   括注式=平衡括号扫描+顶层分号拆分, 多引逐单元生成独立原文 offset; 数字式仍=严格单数字+≤max num。
③ 数字式总开关(文档级, 由正文分布决定, 与文献表格式解耦): N_num(宽松内腔 [\\d,\\s\\-]+ 采样)
   >=3 且 > N_fn(脚注块 FOOTNOTE_CITE 计数) 才开; N_num==0 关; 其余关+audit 记 num_mode_ambiguous。
④ 对号: (作者,年份) 与 references[].authors_raw/_foldv 比对; 完整年份后缀优先于前四位,
   多候选按页距升序取最近, n_candidates 落 audit; 把握三档 exact(双命中)/partial(单命中)/unlinked。
⑤ 产出: citation_spans[]{page,uid,sentence,raw,citation_text,offset,kind,authors,year,
   linked_refs,confidence}; 跨页块 page=块首页, audit 记 cross_page(页不精确, 先不追求完美)。
不用 LLM(封闭语法+机器可对号); 不碰 unify/m3/FOOTNOTE_CITE; 脚注引文归 frefs 不重复计。
"""
import os, re, io, json, datetime, collections
import regress
from .common import *


def _sentences(t):
    """切句(一套规则): 句末标点(.。．!?)后随空白/行尾才切; 小数 \\d.\\d 与常见缩写
    (Fig. et al. p. pp. vol. no. Dr. Prof. Mr. Ms. St. vs. etc.) 不切; 失败兜底整块。"""
    t = (t or "").strip()
    if not t:
        return []
    prot = re.sub(r"\d\.\d", lambda m: m.group(0).replace(".", u"\x00"), t)
    prot = re.sub(r"(?i)\b(fig|et al|p|pp|vol|no|dr|prof|mr|ms|st|vs|etc)\.",
                  lambda m: m.group(0).replace(".", u"\x00"), prot)
    parts = re.split(r"(?<=[.。．!?])\s+", prot)
    return [p.replace(u"\x00", ".").strip() for p in parts if p.strip()]


NARR = re.compile(r"([0-9A-Za-z가-힣一-鿿·.'\-]{2,40}?)[ ]?(\((?:19|20)\d{2}[a-z]?\))")
NUM_LOOSE = re.compile(r"\[\s*\d{1,3}(?:[,\-\s]\d{1,3})*\s*\]")
NUM_STRICT = re.compile(r"(?<![\[\d])\[\s*(\d{1,3})\s*\]")

_SENT_CITE = (
    (u"narrative", NARR),
    (u"numeric", NUM_STRICT),
)


def _year_ok(y):
    return y[:4].isdigit() and int(y[:4]) in YEAR_OK


def _paren_cite(inner):
    """括注式判定: 括号内必须同时有(作者段)与(四位年份段)——只含年份的括号(如
    '(2015 개정교육과정)')在此自然出局, 回链比对兼任误报过滤器。返回 (author_token, year) 或 None。"""
    year = None
    segs = [s.strip() for s in inner.split(",") if s.strip()]
    for s in segs:
        m = re.search(r"(?:19|20)\d{2}[a-z]?", s)
        if m and _year_ok(m.group(0)):
            year = m.group(0)
    if year is None:
        return None
    for s in segs:
        if re.search(r"(?:19|20)\d{2}", s):
            continue   # 年份段不当作者
        if re.search(u"[A-Za-z가-힣一-鿿]{2}", s):
            return s, year
    return None


def _narr_name_ok(name):
    """叙述式名字段: 至少含 2 个字母/韩文/汉字, 排除纯句尾碎片('는'/'과' 等)。"""
    core = re.sub(u"[^0-9A-Za-z가-힣一-鿿]", "", name or "")
    return len(core) >= 2


def _author_fold(s):
    value = re.sub(r"\*", "", s or "").strip()
    value = re.sub(r"(?:\bet\s+al\.?|\betc\.?|외)\s*$", "", value, flags=re.I).strip()
    return _foldv(value)


def _edit_distance_le1(a, b):
    """有界 OCR 容错：只允许一次替换或单字符插入/删除。"""
    if a == b:
        return True
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    if len(a) > len(b):
        a, b = b, a
    if len(b) != len(a) + 1:
        return False
    i = j = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
        else:
            j += 1
            if j - i > 1:
                return False
    return True


def _author_matches(value, row):
    key = _author_fold(value)
    if key and key in row["af"]:
        return True
    if len(key) < 5:
        return False
    return any(_edit_distance_le1(key, _author_fold(k)) for k in row["akeys"])


def _fold_map(text):
    """返回折叠文本及每个折叠字符对应的原文下标，匹配后仍可精确切原文。"""
    folded, positions = [], []
    for i, ch in enumerate(text or ""):
        for out in _foldv(ch):
            folded.append(out)
            positions.append(i)
    return "".join(folded), positions


def _author_keys(r):
    """由本篇 references 生成作者定位键；机构只留全名，人名补常见姓氏形态。"""
    authors = [a for a in (r.get("authors") or []) if a]
    raw = (r.get("authors_raw") or "").strip()
    vals = list(authors)
    if raw and len(authors) <= 1:
        vals.append(raw)
    keys = set()
    for value in vals:
        value = re.sub(r"(?:\bet\s+al\.?|\betc\.?|외)\s*$", "", value.strip(), flags=re.I)
        value = value.strip(" ,.;·*")
        if len(_author_fold(value)) >= 2:
            keys.add(value)
        if not AFFIL_HEAD.search(value):
            tokens = [x for x in re.findall(r"[A-Za-z]+", value) if len(x) >= 2]
            for token in {tokens[0] if tokens else "", tokens[-1] if tokens else ""}:
                if len(token) >= 2:
                    keys.add(token)
    return sorted(keys, key=lambda x: (-len(_author_fold(x)), x))


def _narrative_reverse(sent, m, ridx):
    """用本篇同年作者键向左锚定叙述式边界，返回 (原文起点, 完整作者段)。"""
    year = m.group(2).strip("()")[:5]
    if not _year_ok(year):
        return None
    left = sent[:m.start(2)]
    folded, positions = _fold_map(left)
    best = None
    for row in ridx.rows:
        if row["y4"] != year[:4]:
            continue
        for key in row["akeys"]:
            k = _author_fold(key)
            if len(k) < 2:
                continue
            idx = folded.rfind(k)
            while idx >= 0:
                between = folded[idx + len(k):]
                if re.fullmatch(r"(?:etal\.?|etc\.?|외|[·*])*", between, re.I):
                    start = positions[idx]
                    name = left[start:].strip()
                    score = (row["yfull"] == year, len(k), idx)
                    if best is None or score > best[0]:
                        best = (score, start, name)
                    break
                idx = folded.rfind(k, 0, idx)
    if best is None:
        for token in re.finditer(r"[A-Za-z가-힣一-鿿]{5,}", left):
            key = _author_fold(token.group(0))
            between = _foldv(left[token.end():])
            if not re.fullmatch(r"(?:etal\.?|etc\.?|외|[·*])*", between, re.I):
                continue
            for row in ridx.rows:
                if row["y4"] != year[:4] or not _author_matches(key, row):
                    continue
                score = (row["yfull"] == year, len(key), token.start())
                if best is None or score > best[0]:
                    best = (score, token.start(), left[token.start():].strip())
    return (best[1], best[2]) if best else None


def _split_top_level(text, separators=";；"):
    """按非嵌套分号切分，返回 (文本, 起点, 终点)。"""
    out, start, depth = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch in separators and depth == 0:
            out.append((text[start:i], start, i))
            start = i + 1
    out.append((text[start:], start, len(text)))
    return out


def _paren_hits(sent):
    """平衡括号扫描；多引括注逐单元返回不重叠的 (start,end,kind,(author,year))。"""
    hits, stack = [], []
    for i, ch in enumerate(sent):
        if ch == "(":
            stack.append(i)
        elif ch == ")" and stack:
            start = stack.pop()
            if stack:
                continue
            inner = sent[start + 1:i]
            units = _split_top_level(inner)
            parsed = []
            for text, a, b in units:
                got = _paren_cite(text)
                if got:
                    parsed.append((a, b, text, got))
            if not parsed:
                continue
            if len(units) == 1 and len(parsed) == 1:
                hits.append((start, i + 1, "parenthetical", parsed[0][3]))
                continue
            for a, b, text, got in parsed:
                lead = len(text) - len(text.lstrip())
                trail = len(text) - len(text.rstrip())
                hits.append((start + 1 + a + lead, start + 1 + b - trail,
                             "parenthetical", got))
    return hits


class _RefIndex(object):
    """references[] 对号索引: 作者键、原文折叠文本与完整年份。"""

    def __init__(self, refs):
        self.refs = refs or []
        self.rows = []
        for i, r in enumerate(self.refs):
            self.rows.append({
                "index": i, "num": r.get("num"), "uid": r.get("uid"),
                "page": (r.get("page") or 1),
                "af": _author_fold(r.get("authors_raw") or "; ".join(r.get("authors") or [])),
                "akeys": _author_keys(r),
                "yfull": r.get("year") or "",
                "y4": (r.get("year") or "")[:4],
            })
        self.max_num = max([r["num"] for r in self.rows if isinstance(r["num"], int)] or [0])

    def link_ay(self, author_tok, year, page):
        """作者+年份对号：完整年份后缀优先，再退四年前四位；多候选仍按页距取最近。
        返回 (best_row, n_candidates, matched_author, matched_year)。"""
        key = _author_fold(author_tok)
        y4 = year[:4]
        a_hits = [r for r in self.rows if key and key in r["af"]]
        if not a_hits:
            a_hits = [r for r in self.rows if r["y4"] == y4 and _author_matches(key, r)]
        full = [r for r in a_hits if r["yfull"] == year]
        exact = [r for r in a_hits if r["y4"] == y4]
        pool = full or exact or a_hits or [r for r in self.rows if r["y4"] == y4]
        if not pool:
            return None, 0, False, False
        best = min(pool, key=lambda r: abs((r["page"] or 1) - (page or 1)))
        return best, len(pool), bool(a_hits), any(r["y4"] == y4 for r in self.rows)

    def link_num(self, num, page):
        """数字式对号: 按 num 过滤(1632041 两段重复 1..13/1..14 由此消歧), 页距升序取最近。"""
        pool = [r for r in self.rows if r["num"] == num]
        if not pool:
            return None, 0
        return min(pool, key=lambda r: abs((r["page"] or 1) - (page or 1))), len(pool)


def _ref_pages(pdata, pages):
    """文献区页集合(含 §44 回退)——与 refs.py refs_doc 同口径重跑(那边是局部变量拿不到;
    改一处必须同步另一处)。"""
    ref_pages = [p for p in pages if pdata[p].get("in_ref")]
    if not ref_pages:
        return []
    p0 = min(ref_pages) - 1
    if p0 >= 0 and any(u["type"] == "Section-header"
                       and regress.REF_HEAD.search((u["main_text"] or "").split("\n")[0] or "")
                       for u in pdata[p0]["unified"]):
        ref_pages = sorted(set(ref_pages) | set(range(p0, max(ref_pages) + 1)))
    return ref_pages


def cspan_doc(doc, out_root):
    """单篇: 收集正文 Text 块 → 切句 → 三形态 → 对号 → 产物 citation_spans[] + audit.cspan。"""
    doc_dir = os.path.join(out_root, doc)
    meta_fp = os.path.join(doc_dir, "metadata.json")
    udir = os.path.join(doc_dir, "unified")
    if not os.path.exists(meta_fp) or not os.path.isdir(udir):
        print("跳过 cspan %s: 缺 metadata/unified" % doc)
        return None
    meta = json.load(io.open(meta_fp, encoding="utf-8"))
    refs = meta.get("references") or []
    if not refs:
        print("== cspan %s == 无 references[], 跳过(对号无目标)" % doc)
        return None
    pages = sorted(int(re.match(r"page_(\d+)\.json$", f).group(1))
                   for f in os.listdir(udir) if re.match(r"page_\d+\.json$", f))
    pdata = {p: json.load(io.open(os.path.join(udir, "page_%d.json" % p), encoding="utf-8"))
             for p in pages}
    ref_pages = set(_ref_pages(pdata, pages))
    ridx = _RefIndex(refs)
    # —— 数字式总开关: 正文分布决定, 与文献表格式解耦 ——
    n_num, n_fn, blocks = 0, 0, []
    for p in pages:
        if p in ref_pages:
            continue
        for u in pdata[p]["unified"]:
            if u.get("cross_page", {}).get("role") == "head":
                continue   # 与 refs_doc 同口径: 跨页块只收 tail 侧
            if u["type"] == "Footnote":
                n_fn += len(FOOTNOTE_CITE.findall((u["main_text"] or "")))
                continue   # 脚注归 frefs; 行内引文在 Text 块
            if u["type"] != "Text":
                continue
            t = (u["main_text"] or "").strip()
            if not t:
                continue
            n_num += len(NUM_LOOSE.findall(t))
            blocks.append((p, u, t))
    numeric_on = n_num >= NUMERIC_CITE_MIN and n_num > n_fn
    ambiguous = (not numeric_on) and n_num > 0
    spans = []
    for p, u, t in blocks:
        for sent in _sentences(t):
            hits = []
            for kind, rx in _SENT_CITE:
                if kind == "numeric" and not numeric_on:
                    continue   # 总开关关: 正文上标数字多为脚注引用, 不出
                for m in rx.finditer(sent):
                    hits.append((m.start(), m.end(), kind, m))
            hits.extend(_paren_hits(sent))
            hits.sort(key=lambda x: x[0])
            last_end = -1
            for s0, e0, kind, m in hits:
                previous_end = last_end
                if s0 < last_end:
                    continue   # 重叠去重(取先出现者)
                last_end = e0
                authors, year, best, ncand = None, None, None, 0
                if kind == "narrative":
                    name = m.group(1)
                    if not _narr_name_ok(name):
                        continue
                    year = m.group(2).strip(u"()")[:5]
                    if not _year_ok(year):
                        continue
                    reverse = _narrative_reverse(sent, m, ridx)
                    if reverse:
                        s0, name = reverse
                        if s0 < previous_end:
                            continue
                    authors = [name]
                    best, ncand, ma, my = ridx.link_ay(name, year, p + 1)
                    conf = "exact" if (best and ma and my and best in
                                       [r for r in ridx.rows if _author_matches(name, r) and r["y4"] == year[:4]]) \
                        else ("partial" if (best and (ma or my)) else "unlinked")
                elif kind == "parenthetical":
                    author_tok, year = m
                    authors = [author_tok]
                    best, ncand, ma, my = ridx.link_ay(author_tok, year, p + 1)
                    conf = "exact" if (best and ma and my and best["y4"] == year[:4]
                                       and _author_matches(author_tok, best)) \
                        else ("partial" if (best and (ma or my)) else "unlinked")
                else:
                    n0 = int(m.group(1))
                    if ridx.max_num and n0 > ridx.max_num:
                        continue   # 超文献表编号上限
                    year, authors = None, None
                    best, ncand = ridx.link_num(n0, p + 1)
                    conf = "exact" if best else "unlinked"
                spans.append({
                    "page": p + 1, "uid": u["uid"], "kind": kind,
                    "sentence": sent, "raw": sent,
                    "citation_text": sent[s0:e0], "offset": [s0, e0],
                    "authors": authors, "year": year,
                    "linked_refs": ([{"index": best["index"], "num": best["num"],
                                      "uid": best["uid"]}] if best else []),
                    "n_candidates": ncand, "confidence": conf,
                })
    meta["citation_spans"] = spans
    meta["generated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    io.open(meta_fp, "w", encoding="utf-8").write(json.dumps(meta, ensure_ascii=False, indent=1))
    cval = {
        "entry_count": len(spans),
        "num_mode": {"on": numeric_on, "n_num": n_num, "n_fn": n_fn,
                     "ambiguous": ambiguous},
        "kind_dist": dict(collections.Counter(s["kind"] for s in spans)),
        "conf_dist": dict(collections.Counter(s["confidence"] for s in spans)),
        "cross_page_blocks": sum(1 for _, u, _ in blocks
                                 if (u.get("cross_page") or {}).get("spans")),
    }
    audit = load_audit(doc_dir)
    audit["cspan"] = cval
    save_audit(doc_dir, audit)
    print("== cspan %s == 引文句=%d 数字式=%s 置信=%s" % (
        doc, len(spans), "开" if numeric_on else "关(%d/%d)" % (n_num, n_fn),
        dict(collections.Counter(s["confidence"] for s in spans))))
    return meta


# —— 冻结自检(python -m metapy.cspan) ——
def _selftest():
    refs = [
        {"num": 1, "uid": "uA", "page": 30, "authors_raw": u"김교헌", "year": "2000",
         "authors": [u"김교헌"], "title": u"T1"},
        {"num": 2, "uid": "uB", "page": 30, "authors_raw": u"전현숙·손정락", "year": "2011",
         "authors": [u"전현숙", u"손정락"], "title": u"T2"},
        {"num": 1, "uid": "uC", "page": 36, "authors_raw": u"유재춘", "year": "1999",
         "authors": [u"유재춘"], "title": u"T3"},
    ]
    ridx = _RefIndex(refs)
    n_ok = 0
    # 1) 切句: 小数/缩写不切, 句末切
    ss = _sentences(u"결과는 3.5배였다. Kim et al. argued that stress. 다음 단계를 본다")
    ok1 = len(ss) == 3 and u"3.5" in ss[0] and u"et al. argued" in ss[1]
    # 2) 叙述式: 紧贴(년) 命中; 过远名字不命中为 parenthetical 或漏
    m = NARR.search(u"김교헌(2000)은 주장했다")
    ok2 = bool(m) and m.group(1) == u"김교헌"
    # 3) 括注式: 双要素过; 只年份出局
    ok3 = _paren_cite(u"김교헌, 2000") == (u"김교헌", u"2000") and _paren_cite(u"2015 개정교육과정") is None
    # 4) 对号: exact + 1632041 型重复 num 页距消歧
    best, nc, ma, my = ridx.link_ay(u"김교헌", "2000", 29)
    ok4 = best["uid"] == "uA" and ma and my
    best2, nc2 = ridx.link_num(1, 36)
    ok5 = best2["uid"] == "uC" and nc2 == 2   # num=1 两条, 按页距取 36 页的 uC
    # 6) 数字式严格形态: 纯数字过, 列表/紧贴违规不过
    ok6 = bool(NUM_STRICT.search(u"결과[12]로")) and not NUM_STRICT.search(u"[12, 15]") \
        and not NUM_STRICT.search(u"[[12]]")
    # 7) 名字碎片: '는' 之类不成立
    ok7 = not _narr_name_ok(u"는") and _narr_name_ok(u"김교헌")
    hard_refs = [
        {"uid": "uM", "num": 1, "page": 12, "authors_raw": "McCarty et al.",
         "authors": ["McCarty"], "year": "1998"},
        {"uid": "uC", "num": 2, "page": 12, "authors_raw": "Childs et al.",
         "authors": ["Childs"], "year": "1982"},
        {"uid": "uL", "num": 3, "page": 12, "authors_raw": "Lazaroff et al.",
         "authors": ["Lazaroff"], "year": "1982"},
        {"uid": "uB", "num": 4, "page": 12, "authors_raw": "Bigham et al.",
         "authors": ["Bigham"], "year": "1990"},
    ]
    hard = _RefIndex(hard_refs)
    # 8) 叙述式反向定位: 原 NARR 在此形态只抓 al.，词典恢复完整作者段和起点
    text8 = "McCarty et al. (1998)은 주장했다"
    m8 = NARR.search(text8)
    ok8 = _narrative_reverse(text8, m8, hard) == (0, "McCarty et al.")
    # 9) 多引括注: 三个独立原文 span，各自对号，不再首作者配末年份
    text9 = "(Childs et al., 1982; Lazaroff et al., 1982; Bigham *et al.*, 1990)"
    hits9 = _paren_hits(text9)
    linked9 = []
    for a, b, kind, got in hits9:
        author, year = got
        best9, _, _, _ = hard.link_ay(author, year, 1)
        linked9.append((text9[a:b], best9["uid"] if best9 else None))
    ok9 = len(hits9) == 3 and linked9 == [
        ("Childs et al., 1982", "uC"), ("Lazaroff et al., 1982", "uL"),
        ("Bigham *et al.*, 1990", "uB")]
    suffix_refs = [
        {"uid": "uNA", "authors_raw": "Nordstrom", "authors": ["Nordstrom"], "year": "1982a"},
        {"uid": "uNB", "authors_raw": "Nordstrom", "authors": ["Nordstrom"], "year": "1982b"},
    ]
    best10, _, _, _ = _RefIndex(suffix_refs).link_ay("Nordstrom", "1982b", 1)
    ok10 = best10["uid"] == "uNB"
    fuzzy_refs = [{"uid": "uF", "num": 14, "page": 13,
                   "authors_raw": "Filipek, L. H., Nordstrom, D. K. and Ficklin, W. H",
                   "authors": ["Filipek L. H", "Nordstrom D. K", "Ficklin W. H"],
                   "year": "1987"}]
    fuzzy = _RefIndex(fuzzy_refs)
    text11 = "Fillipek et al. (1987)은 보고했다"
    reverse11 = _narrative_reverse(text11, NARR.search(text11), fuzzy)
    best11, _, ma11, my11 = fuzzy.link_ay("Fillipek et al.", "1987", 12)
    ok11 = (reverse11 == (0, "Fillipek et al.") and best11["uid"] == "uF"
            and ma11 and my11 and not _author_matches("Kim", fuzzy.rows[0]))
    oks = (ok1, ok2, ok3, ok4, ok5, ok6, ok7, ok8, ok9, ok10, ok11)
    for i, ok in enumerate(oks, 1):
        if ok:
            n_ok += 1
        else:
            print("FAIL [cspan case %d]" % i)
    print("cspan selftest: %d/%d PASS" % (n_ok, len(oks)))
    return n_ok == len(oks)

if __name__ == "__main__":
    raise SystemExit(0 if _selftest() else 1)
