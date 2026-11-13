# -*- coding: utf-8 -*-
"""不变量对账(audit_invariant): 不审"值", 审原文自己留下的账本。

字段值后验(audit_value)问的是"这个值立不立得住", 判据靠形态/来源/离群——都要先认字。
可是很多缺陷根本不用认字: 原文自己就写着一份可校验的账, 你只要去对账。

  ① 脚注编号   论著脚注印着 1..N。缺号即漏抽, 且前后相邻号的页码能把缺口夹逼到页。
  ② 文献编号   [1][2]… / 1. 2. … 同口径。
  ③ 印刷页码   页边行印着 110,111,112…。跳变即丢页 —— 不依赖任何正文内容。
  ④ 卷期年份   页眉印着 `제67집 2020. 9.`, 与 metadata 的 volume/issue/year 直接对账。
  ⑤ 上标标记   authors[].markers 的最大号不能超过机构数。
  ⑥ 引文年份   参考文献年份不得晚于本文出版年(容 +1, 预印/网络版)。

这一类判据的共同点: 不需要知道"值应该是什么", 只要求"这些值之间的关系必须成立"。
所以误报率接近零、不需要标定阈值、不需要负例夹具, 也不需要大模型。

三类形态 → 三种归因(这是它最实用的地方, 不只报错还告诉你改哪层):
  缺 0        内容没漏, 问题在归属/展示层(实证: 1004818 脚注 40/40 齐, 缺陷是 p2 显示 0 条)
  缺连续段    那一整块在同一页被丢弃, 页码夹逼可直接定位(实证: 2632491 缺 9-12, 原文在 p3)
  缺首号      首页首条, 典型是基金/致谢注被当非引文过滤

用法:
    python -m metapy.audit_invariant            # 全库
    python -m metapy.audit_invariant <doc>      # 单篇
    python -m metapy.audit_invariant --json
    python -m metapy.audit_invariant --selftest
"""
import os, io, re, json, glob, sys, collections

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")

_YEAR = re.compile(r"^(?:19|20)\d{2}$")
_NUMTOK = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")

SEV = {
    "N-FN-GAP": "bad",        # 脚注缺号
    "N-FN-DUP": "warn",       # 脚注重号
    "N-FN-ORDER": "warn",     # 脚注号与页码不单调
    "N-REF-GAP": "bad",       # 文献缺号
    "N-REF-DUP": "warn",      # 文献重号
    "N-PG-GAP": "bad",        # 印刷页码跳变(丢页)
    "N-PG-RANGE": "bad",      # pages 区间与页边行印的页码不符
    "N-VOL-MISMATCH": "bad",  # 卷号与页眉印的不符
    "N-ISS-MISMATCH": "bad",  # 期号与页眉印的不符
    "N-YEAR-MISMATCH": "warn",  # 出版年与页眉年份不符
    "N-MARK-OVER": "warn",    # 上标号超过机构数
    "N-CITE-FUTURE": "warn",  # 引文年份晚于出版年
}


def _f(code, value, msg):
    return {"code": code, "sev": SEV[code], "value": value, "msg": msg}


def _int(v):
    m = _NUMTOK.search(str(v if v is not None else ""))
    return int(m.group(1)) if m else None


class Ctx(object):
    """一篇的上下文。meta/pages 可注入(自检用), 也可从磁盘读。"""

    def __init__(self, doc, out_root=OUT, meta=None, pages=None):
        self.doc = doc
        self.dd = os.path.join(out_root, doc) if (doc and out_root) else ""
        self._m = meta
        self._p = pages

    @property
    def m(self):
        if self._m is None:
            fp = os.path.join(self.dd, "metadata.json")
            self._m = json.load(io.open(fp, encoding="utf-8")) if os.path.isfile(fp) else {}
        return self._m

    @property
    def pages(self):
        if self._p is None:
            self._p = []
            for fp in sorted(glob.glob(os.path.join(self.dd, "unified", "page_*.json")),
                             key=lambda x: _int(os.path.basename(x)) or 0):
                try:
                    self._p.append(json.load(io.open(fp, encoding="utf-8")))
                except Exception:
                    continue
        return self._p

    def val(self, k):
        v = self.m.get(k)
        if isinstance(v, dict):
            v = v.get("value")
        return v


# ---------------------------------------------------------------------------
# ① 脚注编号
# ---------------------------------------------------------------------------

def _seq_check(items, code_gap, code_dup, label, pairs):
    """pairs = [(号, 页)]。缺号用相邻号页码夹逼出落页。

    编号回退且新组从 1 起 → 多套表(국문/영문 双语文献表、正文+附录两套注), 各组分别对账;
    回退但新组不从 1 起 → 真的重复(同一批条目被抽了两遍), 照报。"""
    groups = _split(pairs)
    if len(groups) > 1 and all(g and g[0][0] == 1 for g in groups[1:]):
        out = []
        for i, g in enumerate(groups):
            out += _gaps(g, code_gap, "%s(第%d表)" % (label, i + 1))
        return out
    out = _gaps(pairs, code_gap, label)
    got = [n for n, _ in pairs if n]
    cnt = collections.Counter(got)
    dup = sorted(k for k, v in cnt.items() if v > 1)
    if dup:
        out.append(_f(code_dup, "%s %s" % (label, ",".join(map(str, dup))),
                      "%s编号重复: %s(同一条被切成两半或编号解析错)" % (label, ",".join(map(str, dup)))))
    return out


def _split(pairs):
    """按编号回退切段。"""
    gs, cur = [], []
    for it in pairs:
        if cur and it[0] is not None and cur[-1][0] is not None and it[0] <= cur[-1][0]:
            gs.append(cur)
            cur = []
        cur.append(it)
    if cur:
        gs.append(cur)
    return gs


def _gaps(pairs, code_gap, label):
    out = []
    got = [n for n, _ in pairs if n]
    if not got:
        return out
    lo, hi = min(got), max(got)
    miss = sorted(set(range(lo, hi + 1)) - set(got))

    def where(n):
        low = [p for k, p in pairs if k and p and k < n]
        high = [p for k, p in pairs if k and p and k > n]
        a = max(low) if low else None
        b = min(high) if high else None
        if a is not None and a == b:
            return "p%d" % b
        if a is None:
            return "首号前(PDF 前 2 页)"
        if b is None:
            return "p%d 之后" % a
        return "p%d–p%d 之间" % (a, b)

    for grp in _runs(miss):
        loc = where(grp[0])
        if len(grp) == 1:
            out.append(_f(code_gap, "%s %d" % (label, grp[0]),
                          "%s缺第 %d 号, 落在 %s" % (label, grp[0], loc)))
        else:
            out.append(_f(code_gap, "%s %d-%d" % (label, grp[0], grp[-1]),
                          "%s缺 %d-%d 共 %d 条(连续), 落在 %s" % (
                              label, grp[0], grp[-1], len(grp), loc)))
    return out


def _runs(nums):
    """把有序数字切成连续段: [9,10,11,12,15] → [[9,10,11,12],[15]]"""
    out, cur = [], []
    for n in nums:
        if cur and n == cur[-1] + 1:
            cur.append(n)
        else:
            if cur:
                out.append(cur)
            cur = [n]
    if cur:
        out.append(cur)
    return out


def chk_fnote(c):
    fr = c.m.get("footnote_refs") or []
    if not fr:
        return []
    pairs, nonum = [], 0
    for r in fr:
        n = _int(r.get("num"))
        if n is None:
            nonum += 1
        else:
            pairs.append((n, r.get("page")))
    if not pairs or nonum > len(fr) * 0.3:
        return []                       # 无编号体例(用 * † 或直接接排), 交给版面判据
    out = _seq_check(fr, "N-FN-GAP", "N-FN-DUP", "脚注", pairs)
    # 号增页减 → 归属页错
    seq = [(n, p) for n, p in pairs if p]
    bad = [(seq[i - 1], seq[i]) for i in range(1, len(seq))
           if seq[i][1] < seq[i - 1][1]]
    if bad:
        a, b = bad[0]
        out.append(_f("N-FN-ORDER", "num %d→%d: p%s→p%s" % (a[0], b[0], a[1], b[1]),
                      "脚注号递增而页码回退, 归属页错了 %d 处" % len(bad)))
    return out


# ---------------------------------------------------------------------------
# ② 参考文献编号
# ---------------------------------------------------------------------------

def chk_refs(c):
    rf = c.m.get("references") or []
    if not rf:
        return []
    # 'footnote' 体例的文献号就是脚注号: 说明性脚注不进文献表, 缺口天然存在 → 不做序列对账
    if any((r.get("style") or "") == "footnote" for r in rf):
        return []
    pairs, nonum = [], 0
    for r in rf:
        n = _int(r.get("num"))
        if n is None:
            nonum += 1
        else:
            pairs.append((n, r.get("page")))
    # 只在"编号体例"里查: 有号的比例要够高, 否则是 authoryear
    if not pairs or len(pairs) < max(3, len(rf) * 0.8):
        return []
    # 双语文献表(국문/영문 各一套, 编号都从 1 起) → 每个号恰好出现两次, 是体例不是错
    cnt = collections.Counter(n for n, _ in pairs)
    if cnt and all(v == 2 for v in cnt.values()):
        return []
    return _seq_check(rf, "N-REF-GAP", "N-REF-DUP", "文献", pairs)


# ---------------------------------------------------------------------------
# ③ 印刷页码序列
# ---------------------------------------------------------------------------

def _edge_nums(j, lo=0, hi=9999):
    """一页页边行里的候选印刷页码。宁漏不错:
    - Page-number 类型直接取整段数字(去破折号/方括号);
    - Page-header/Footer 只在形态像页码时取(开头数字+刊名 / 结尾数字), 且必须落在 pages 窗口内
      —— 这两类块里混着 DOI、卷期号、正文句里的 "1학년", 不设窗口会大量误报。"""
    out = []
    for u in (j.get("unified") or []):
        t = u.get("type")
        if t not in ("Page-number", "Page-header", "Page-footer"):
            continue
        s = (u.get("main_text") or "").strip()
        if not s:
            continue
        if t == "Page-number":
            d = re.sub(r"\D", "", s)
            if 1 <= len(d) <= 4:
                out.append(int(d))
            continue
        m = re.match(u"^(\\d{1,4})\\s+\\S", s) or re.search(u"(?<!\\d)(\\d{1,4})$", s)
        if not m:
            continue
        tok = m.group(1)
        if len(tok) == 4 and _YEAR.match(tok):
            continue                                  # 年份不是页码
        n = int(tok)
        if lo <= n <= hi:                             # 可疑来源必须有 pages 先验
            out.append(n)
    return out


def _page_window(c):
    pg = str(c.val("pages") or "")
    m = re.match(r"^\s*(\d{1,4})", pg)
    if not m:
        return 0, 9999
    a = int(m.group(1))
    m2 = re.search(u"[-–~～]\\s*(\\d{1,4})", pg)
    b = int(m2.group(1)) if m2 else a + 80
    return a - 5, b + 5


def _main_seq(pages, lo=0, hi=9999):
    """找"印刷页码 = d × PDF页 + off"的主序列。返回 (d, off, first) 或 None。
    d=1 单页版式; d=2 双页扫描(一张 PDF 横跨两个印刷页)。
    每页只取最小候选号参与拟合, 避免双页版的 off 分裂成两个。"""
    nums = {}
    for j in pages:
        p, ns = j.get("page") or 0, _edge_nums(j, lo, hi)
        if p and ns:
            nums[p] = sorted(set(ns))
    if len(nums) < 4:
        return None
    best = None
    for d in (1, 2):
        cnt = collections.Counter(n - d * p for p, ns in nums.items() for n in ns)
        off = cnt.most_common(1)[0][0]
        first = dict((p, min(n for n in ns if abs(n - d * p - off) <= 1))
                     for p, ns in nums.items()
                     if any(abs(n - d * p - off) <= 1 for n in ns))
        rate = len(first) / float(len(nums))
        if len(first) >= 4 and rate >= 0.7 and (best is None or rate > best[0]):
            best = (rate, (d, off, first))
    return best[1] if best else None


def chk_page_seq(c):
    lo, hi = _page_window(c)
    seq = _main_seq(c.pages, lo, hi)
    if not seq:
        return []
    d, off, first = seq
    ps = sorted(first)
    out = []
    for i in range(1, len(ps)):
        gap = first[ps[i]] - first[ps[i - 1]]
        exp = d * (ps[i] - ps[i - 1])
        # 双页扫描版的首页常只印一个页码(另半页是刊头), 首间隔允许少 1
        if i == 1 and gap == exp - 1:
            continue
        if gap != exp:
            out.append(_f("N-PG-GAP", "p%d→p%d: %d→%d" % (ps[i - 1], ps[i],
                                                          first[ps[i - 1]], first[ps[i]]),
                          "印刷页码跨了 %d, 按每 PDF 页 %d 个印刷页应为 %d(丢页或乱序)" % (
                              gap, d, exp)))
    # 印刷页码起始 vs pages 起始。只比起始: 扫描件常把下一篇文章的开头也扫进来, 末端不可靠
    imp_lo = min(first.values()) - d * (ps[0] - 1)
    pg = str(c.val("pages") or "")
    m = re.match(r"^\s*(\d{1,4})", pg)
    if m and abs(int(m.group(1)) - imp_lo) > 1:
        out.append(_f("N-PG-RANGE", "pages=%s / 印刷起 %d" % (pg, imp_lo),
                      "pages 起始页写的是 %s, 页边行实际从 %d 起" % (m.group(1), imp_lo)))
    return out


# ---------------------------------------------------------------------------
# ④ 卷期年份对账(页眉印的 vs metadata)
# ---------------------------------------------------------------------------

def _edge_text(c):
    parts = []
    for j in c.pages[:4]:
        for u in (j.get("unified") or []):
            if u.get("type") in ("Page-number", "Page-header", "Page-footer"):
                parts.append(u.get("main_text") or "")
    return "\n".join(parts)


def chk_numbering(c):
    t = _edge_text(c)
    if not t:
        return []
    out = []
    vol = _int(c.val("volume"))
    iss = _int(c.val("issue"))
    year = _int(c.val("year"))

    # 卷: 제N권 / Vol. N / Vol N
    vs = set(int(x) for x in re.findall(u"제\\s*(\\d{1,3})\\s*권", t))
    vs |= set(int(x) for x in re.findall(r"Vol\.?\s*(\d{1,3})(?!\d)", t, re.I))
    # 期: 제N집 / 제N호 / No. N / Issue N
    iss_s = set(int(x) for x in re.findall(u"제\\s*(\\d{1,3})\\s*[집호]", t))
    iss_s |= set(int(x) for x in re.findall(r"(?:No|Issue)\.?\s*(\d{1,3})(?!\d)", t, re.I))

    if vs and vol is not None and vol not in vs:
        out.append(_f("N-VOL-MISMATCH", "卷=%s / 页眉=%s" % (vol, ",".join(map(str, sorted(vs)))),
                      "metadata 卷号 %s 与页眉印的 %s 不符" % (vol, ",".join(map(str, sorted(vs))))))
    # 韩文 '제N집' 是辑(可当卷也可当期); 只有当它既不是卷也不是期时才报
    if iss_s and iss is not None and iss not in iss_s and iss != vol:
        out.append(_f("N-ISS-MISMATCH", "期=%s / 页眉=%s" % (iss, ",".join(map(str, sorted(iss_s)))),
                      "metadata 期号 %s 与页眉印的 %s 不符" % (iss, ",".join(map(str, sorted(iss_s))))))

    ys = set(int(x) for x in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", t))
    if ys and year is not None and year not in ys:
        # 页眉里常有多个年份(收稿/受理/刊行), 只要有一个对上就算一致
        out.append(_f("N-YEAR-MISMATCH", "年=%s / 页眉=%s" % (year, ",".join(map(str, sorted(ys)))),
                      "metadata 出版年 %s 未出现在页眉年份 %s 里" % (
                          year, ",".join(map(str, sorted(ys))))))
    return out


# ---------------------------------------------------------------------------
# ⑤ 上标标记 vs 机构数
# ---------------------------------------------------------------------------

def chk_markers(c):
    aus = [a for a in (c.m.get("authors") or []) if isinstance(a, dict)]
    aff = c.m.get("affiliations") or []
    marks = set()
    for a in aus:
        for m in (a.get("markers") or []):
            n = _int(m)
            if n:
                marks.add(n)
    if not marks or not aff:
        return []
    out = []
    if max(marks) > len(aff):
        out.append(_f("N-MARK-OVER", "上标 max=%d / 机构 %d" % (max(marks), len(aff)),
                      "作者上标最大号 %d 超过机构数 %d, 配对错位" % (max(marks), len(aff))))
    elif len(marks) > 1 and len(aff) not in (1, len(marks)):
        out.append(_f("N-MARK-OVER", "上标 %d 种 / 机构 %d" % (len(marks), len(aff)),
                      "作者用了 %d 种上标但机构 %d 条, 数量对不上" % (len(marks), len(aff))))
    return out


# ---------------------------------------------------------------------------
# ⑥ 引文年份 ≤ 出版年
# ---------------------------------------------------------------------------

def chk_cite_year(c):
    pub = _int(c.val("year"))
    if not pub:
        return []
    out = []
    for key, label in (("references", "文献"), ("footnote_refs", "脚注")):
        fut = []
        for r in (c.m.get(key) or []):
            y = _int(r.get("year"))
            if y and y > pub + 1:
                fut.append((y, str(r.get("raw") or "")[:30]))
        if fut:
            fut.sort()
            out.append(_f("N-CITE-FUTURE", "%s 年=%d" % (label, fut[-1][0]),
                          "%s里有 %d 条年份晚于出版年 %d(最早 %d): %s" % (
                              label, len(fut), pub, fut[0][0], fut[0][1])))
    return out


CHECKS = [chk_fnote, chk_refs, chk_page_seq, chk_numbering, chk_markers, chk_cite_year]


def audit_doc(doc, out_root=OUT, ctx=None):
    c = ctx or Ctx(doc, out_root)
    if not c.m:
        return {"doc": doc, "findings": [], "level": "skip"}
    out = []
    for fn in CHECKS:
        try:
            got = fn(c) or []
        except Exception as e:
            got = [{"code": "N-ERR", "sev": "warn", "value": "",
                    "msg": "%s 判据异常: %s" % (fn.__name__, e)}]
        for g in got:
            g["doc"] = doc
            out.append(g)
    out.sort(key=lambda x: ({"bad": 0, "warn": 1}.get(x["sev"], 2), x["code"]))
    level = "ok"
    if any(x["sev"] == "bad" for x in out):
        level = "bad"
    elif out:
        level = "warn"
    return {"doc": doc, "findings": out, "level": level}


def audit_all(out_root=OUT):
    rows = []
    for d in sorted(os.listdir(out_root)):
        if os.path.isfile(os.path.join(out_root, d, "metadata.json")):
            rows.append(audit_doc(d, out_root))
    return rows


# ---------------------------------------------------------------------------
# 回归: 每类判据一个真缺陷 + 一个合法夹逼
# ---------------------------------------------------------------------------

def _selftest():
    ok = fail = 0

    def chk(name, meta, want, pages=None):
        nonlocal ok, fail
        r = audit_doc(None, None, Ctx("s", None, meta=meta, pages=pages or []))
        codes = set(f["code"] for f in r["findings"])
        good = (bool(codes & set(want)) if want else not codes)
        if good:
            ok += 1
        else:
            fail += 1
            print("FAIL [%s] 命中=%s 期望=%s" % (name, sorted(codes), want))

    base = {"pages": {"value": "110-124"}, "year": {"value": "2020"},
            "volume": {"value": "67"}, "issue": {"value": "67"},
            "authors": [{"name": "이신조"}], "affiliations": ["한양여자대학교"]}

    # ① 脚注缺连续段(2632491 实证: 9-12 落在 p3)
    m = dict(base, footnote_refs=[{"num": n, "page": p, "raw": "x"} for n, p in
                                  [(1, 2), (2, 2), (8, 2), (13, 4), (14, 4)]])
    chk("脚注缺连续段", m, ["N-FN-GAP"])

    # ② 脚注齐(合法, 1004818 实证 40/40)
    m = dict(base, footnote_refs=[{"num": n, "page": 1 + n // 4, "raw": "x"} for n in range(1, 41)])
    chk("脚注齐", m, [])

    # ③ 文献缺号(numbered 体例)
    m = dict(base, references=[{"num": n, "raw": "x"} for n in (1, 2, 4, 5)])
    chk("文献缺号", m, ["N-REF-GAP"])

    # ④ 文献 authoryear(无号, 不能报)
    m = dict(base, references=[{"num": None, "raw": "x"} for _ in range(6)])
    chk("authoryear 不报", m, [])

    # ⑤ 印刷页码跳变(丢页): 单页版式, p3→p4 跳了 3
    pages = [{"page": p, "unified": [{"type": "Page-number", "main_text": str(n)}]}
             for p, n in [(1, 110), (2, 111), (3, 112), (4, 115), (5, 116), (6, 117)]]
    m = dict(base, pages={"value": "110-117"})
    chk("印刷页码跳变", m, ["N-PG-GAP"], pages)

    # ⑥ 双页扫描版 d=2 连续(合法)
    pages = [{"page": p, "unified": [{"type": "Page-header", "main_text": str(n) + " 어문학연구 제67집"},
                                     {"type": "Page-header", "main_text": str(n + 1) + " 어문학연구 제67집"}]}
             for p, n in [(1, 110), (2, 112), (3, 114), (4, 116), (5, 118)]]
    m = dict(base, pages={"value": "110-119"})
    chk("双页扫描版连续", m, [], pages)

    # ⑦ 卷号与页眉不符
    pages = [{"page": p, "unified": [{"type": "Page-header", "main_text": "제12권 제3호 2019"}]}
             for p in (1, 2, 3, 4)]
    m = dict(base, year={"value": "2020"}, volume={"value": "67"}, issue={"value": "67"})
    chk("卷期不符", m, ["N-VOL-MISMATCH"], pages)

    # ⑧ 上标超过机构数
    m = dict(base, authors=[{"name": "a", "markers": ["1", "3"]}], affiliations=["x"])
    chk("上标超机构数", m, ["N-MARK-OVER"])

    # ⑨ 引文年份晚于出版年
    m = dict(base, references=[{"num": None, "year": "2023", "raw": "x"}])
    chk("引文未来年", m, ["N-CITE-FUTURE"])

    print("audit_invariant selftest: %d/%d PASS" % (ok, ok + fail))
    return 0 if fail == 0 else 1


def _main(argv):
    if "--selftest" in argv:
        return _selftest()
    docs = [a for a in argv if not a.startswith("-")]
    rows = ([audit_doc(d, OUT) for d in docs] if docs else audit_all(OUT))
    if "--json" in argv:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        for r in rows:
            if not r["findings"]:
                continue
            print("%-26s %s" % (r["doc"], r["level"]))
            for f in r["findings"]:
                print("   [%s] %-16s %-26s %s" % (f["sev"], f["code"], f["value"], f["msg"]))
        dist = collections.Counter(r["level"] for r in rows)
        by = collections.Counter(f["code"] for r in rows for f in r["findings"])
        print("\n汇总: %d 篇  %s" % (len(rows), dict(dist)))
        if by:
            print("按判据: " + "  ".join("%s=%d" % kv for kv in
                                     sorted(by.items(), key=lambda x: -x[1])))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
