# -*- coding: utf-8 -*-
r"""本篇期刊/出版年/卷/期/页/关键词 (--stage issue): 页眉页脚+首页顶区 来源行 投票正则, 不用 LLM.
规格(全部经 13 篇语料核实):
① 来源行: 每页 struct 型块(页眉/页脚/页码)逐行 + 首页顶区(y1<0.12H)任意类型逐行; 折叠>130 字符的
  长行(引擎把正文粘进 footer 的误标, 如 2983265 p10/p15/p19)不进。
② 期刊名: 首页行 > 全语料投票。三种形态: 『名』书名号; 卷期/页码标记前取前缀(剥前导页码/年份/
  'Name et al.'/季节词); '|' 分隔行的首个非数字段(ko)与含 Journal 类关键词的拉丁段(en)。
  韩英同词行按拉丁段拆双字段(journal + journal_en)。
  守卫: 折叠长 2~50 / 不含 ':' / 不等于本篇标题或作者名(running 头=标题+作者, 如 1536751/1892171
  的 '통합문법: … 155'/'146 장석진' 不出刊名)。
③ 年/卷/期/页: 全语料投票(≥2 页 high, 单页 mid, 无则 null+low)。年须行内有刊头信号
  (권/호/집/Vol/No/pp/ISSN/DOI/书名号/季节词) 且不被数字或连字符夹住(ISSN 2384-2008 的 2008 出局);
  卷=제N권 > Vol.N > N집 优先级组内投票, 통권(总卷)只记 audit 不进卷; 期=제N호/No.N(통권 旁路);
  页=pp. 强型 > 行尾裸区间(>999 或逆序出局, ISSN 区间自然被拒) > 页码推断
  (每页取 Page-number 行首数字, 无则行尾, 'Page N of M' 取 N, struct 行补齐, offset=号-页序号 众数回填首页)。
④ DOI 行进 audit  corroboration: \.(YYYY)\.(\d{1,3})\. → 年+卷; (\d{1,3})\.(\d{1,3})\.(YYYY)\d{0,2}\. → 卷+期+年。
⑤ 关键词: KW_LINE 组(同块续行; 有冒号取冒号后, 无冒号剥头词尾部/空白; 逗号/分号切分; 冒号前残留的
  头词尾字如 '■ 중심어 :' 的 '어' 随冒号切掉; 剥 '(Key Words)' 标签残留)。韩/英双字段(2026-11-09
  用户拍板: 有韩英双板的都提取): 页序各取首个非空组, 韩文头词(키워드/중심어/핵심어/주요어/주제어,
  含 ■/◆ 前缀)入 keywords[], 英文头词(Key words/Keywords)入 keywords_en[]; 与 abstract 双字段
  对齐, 字段语言不再由版式先后决定; 原始行进 audit。
产出: metadata.json 新字段 journal/journal_en/year/volume/issue/pages + keywords[] + keywords_en[];
      置信明细(候选/票数/依据)进 metadata_audit.json issue 段(§38 分层)。
"""
import os, re, io, json, datetime, collections

from .common import *

_Y_GUARD = re.compile(r"권|호|집|Vol|No|pp|ISSN|DOI|doi|Journal|학회지|학술지|『|』|AUTUMN|SPRING|SUMMER|WINTER|FALL")
_YEAR = re.compile(r"(?<![\d\-])((?:19|20)\d{2})(?![\d\-])")
_JBOOK = re.compile(r"『([^』]{2,40})』")
_KWAN = re.compile(r"제\s*(\d{1,4})\s*권")
_JIP = re.compile(r"(?<!\d)(\d{1,4})\s*집")
_HO = re.compile(r"제?\s*(\d{1,4})\s*호")
_TONGKWAN = re.compile(r"통권\s*제?\s*(\d{1,4})\s*호")
_VOLEN = re.compile(r"(?<![A-Za-z])Vol\.?\s*(\d{1,4})", re.I)
_NOEN = re.compile(r"(?<![A-Za-z])No\.?\s*(\d{1,4})", re.I)
_PP = re.compile(r"pp?\.?\s*(\d{1,4})\s*[-–~～]\s*(\d{1,4})")
_BARE_RANGE = re.compile(r"(?:^|[\s,(（])(\d{1,4})\s*[-–~～]\s*(\d{1,4})(?=[.,)\s쪽]|$)")
_BMC = re.compile(r"\(\s*((?:19|20)\d{2})\s*\)\s*(\d{1,3}):(\d{1,4})")
_PAGE_OF = re.compile(r"Page\s+(\d{1,4})\s+of\s+\d{1,4}", re.I)
_DOI = re.compile(r"10\.\d{4,}/[^\s\"”’]+", re.I)
_DOI_YV = re.compile(r"\.((?:19|20)\d{2})\.(\d{1,3})\.")
_DOI_VIY = re.compile(r"(?<!\d)(\d{1,3})\.(\d{1,3})\.((?:19|20)\d{2})\d{0,2}\.")
# 刊名前缀截断: 最早出现的卷期/页码/括号年/BMC 卷:文号/季节词之前
_BOUNDARY = re.compile(
    r"제\s*\d+\s*[권호]|제?\s*\d{1,4}\s*집|(?<![A-Za-z])Vol\.?\s*\d|(?<![A-Za-z])No\.?\s*\d|"
    r"pp?\.|\(\s*(?:19|20)\d{2}\s*\)|\(\s*(?:19|20)\d{2}\s*\)\s*\d{1,3}:|"
    r"AUTUMN|SPRING|SUMMER|WINTER|FALL", re.I)
_ENKEY = re.compile(r"Journal|Review|Quarterly|Bulletin|Annals|Proceedings|Studies|Research|Letters")
_FUNC = {"of", "and", "the", "for", "in", "on", "de", "des", "la", "le", "y", "di", "del", "&", "et"}
_KW_SPLIT = re.compile(u"[,，;；]")


def _pageno_of(text):
    """单行的印刷页码: 'Page N of M' 取 N; 行首数字优先(防 '34 4' 尾号干扰), 无则行尾数字。"""
    s = (text or "").strip()
    m = _PAGE_OF.search(s)
    if m:
        return int(m.group(1))
    m = re.match(r"^(\d{1,4})(?![\d.])", s)
    if m:
        return int(m.group(1))
    m = re.search(r"(?<![\d.])(\d{1,4})\s*$", s)
    if m:
        return int(m.group(1))
    return None


def _clean_jname(c):
    """刊名清洗: 剥前导页码/年份(循环两次), 前后分隔符, 'Name et al.', 尾部孤立年份, 括号残边。"""
    c = (c or "").strip()
    for _ in range(2):
        c = re.sub(r"^(?:(?:19|20)\d{2}|\d{1,4})\s*[/·.,\s]+", "", c).strip()
    c = c.strip(u" \t/·.,-–—|〔【('（")
    m = re.match(r"^.+?\bet\s+al\.?\s+", c, re.I)
    if m and len(c) - m.end() >= 8:
        c = c[m.end():]
    c = re.sub(r"[\s,.]*[,.\s]*(?:19|20)\d{2}\b[\s,.'」』)]*$", "", c).strip()
    c = c.strip(u" \t/·.,-–—|〔【('（")
    return c


def _split_ko_en(c):
    """韩英同词行拆双字段: 含拉丁段且其中有 Journal 类关键词 → 以关键词为锚向左收 ≤4 个
    大写/功能词、向右收大写词与功能词, en=该词段(含前导 The/Korean 等), ko=余部;
    无拉丁关键词段 → 仅 ko; 无谚文且拉丁≥4 且无数字 → 仅 en(全词)。"""
    if not re.search(u"[가-힣]", c):
        if re.search(r"[A-Za-z]{4,}", c) and not re.search(r"\d", c):
            return None, c
        return c, None
    words = c.split()
    kw_i = None
    for i, w in enumerate(words):
        if _ENKEY.search(w):
            kw_i = i
            break
    if kw_i is None:
        return c, None

    def _en_tok(w):
        return bool(re.match(r"^[A-Z]", w)) or w.lower() in _FUNC

    l = kw_i
    while l - 1 >= 0 and kw_i - (l - 1) <= 4 and _en_tok(words[l - 1]):
        l -= 1
    r = kw_i + 1
    while r < len(words) and _en_tok(words[r]):
        r += 1
    en = " ".join(words[l:r]).strip(u" ,·|/")
    ko = " ".join(words[:l] + words[r:]).strip(u" ,·|/")
    return ko or None, en or None


def _jname_from_line(s):
    """单行刊名候选(已验证守卫外的基本形态); 返回 (ko, en) 或 None。"""
    s = (s or "").strip()
    if not s:
        return None
    m = _JBOOK.search(s)
    if m:
        return _split_ko_en(m.group(1).strip())
    mb = _BOUNDARY.search(s)
    cand = None
    if mb:
        cand = _clean_jname(s[:mb.start()])
    elif "|" in s:
        parts = [p.strip() for p in s.split("|")]
        parts = [p for p in parts if p and not re.search(r"\d", p)]
        if parts:
            cand = _clean_jname(parts[0])
            _, en = _split_ko_en(parts[0])
            if en is None:
                for p in parts[1:]:
                    ko2, en2 = _split_ko_en(_clean_jname(p))
                    if en2:
                        en = en2
                        break
            if cand:
                return (cand, en)
    if not cand:
        # 独立英文刊名行(多行页眉的英名单独成行, 如 3275609 'The Korean Journal of the Law
        # of the Sea'): 无 boundary/管道/数字/谚文, 词全为大写或功能词, 含刊名词 → 仅 en
        if _ENKEY.search(s) and not re.search(u"[가-힣]", s) and not re.search(r"\d", s):
            words = s.split()
            if len(_fold(s)) >= 8 and all(re.match(r"^[A-Z]", w) or w.lower() in _FUNC
                                          for w in words):
                return None, s.strip()
        return None
    if not (2 <= len(_fold(cand)) <= 50):
        return None
    if ":" in cand or u"：" in cand:
        return None   # 标题型 running 头(含副题冒号)不出刊名
    if re.match(u"^(?:19|20)\\d{2}\\s*년", cand):
        return None   # 日期行残段('2025년 6월, pp. 1~30' 的 '2025년 6월')不出刊名
    ko, en = _split_ko_en(cand)
    if en is None and "|" in s:
        # 带 boundary 的 '|' 行: 刊名截断了英名段(英名在卷期标记之后), 整行扫管道段补 en
        for part in s.split("|"):
            _, en2 = _split_ko_en(_clean_jname(part))
            if en2 and len(en2) >= 8:
                en = en2
                break
    return ko, en


def _vote(cand_pages, first_page0):
    """众数: (winner, support_pages_sorted)。并列取先出现页小者。"""
    if not cand_pages:
        return None, []
    best = max(sorted(cand_pages), key=lambda v: (len(cand_pages[v]), -min(cand_pages[v])[0]))
    return best, sorted(p for p, _ in cand_pages[best])


def _add(cand_pages, value, page, uid=None):
    if value is None:
        return
    cand_pages.setdefault(value, [])
    if page not in [p for p, _ in cand_pages[value]]:
        cand_pages[value].append((page, uid))


def extract(source, n_pages, excluded_folds):
    """核心抽取(纯函数, selftest 直接喂构造行)。
    source: [(page, uid, line)]; excluded_folds: 本篇标题/作者的折叠集合(守卫);
    n_pages: 物理页数。返回结果 dict(含 audit 段)。"""
    journal, journal_en = {}, {}
    year, vol_kwan, vol_vol, vol_jip, issue, pages_pp, pages_bare = {}, {}, {}, {}, {}, {}, {}
    tongkwan, bmc_art = [], []
    for page, uid, s in source:
        if len(_fold(s)) > 130:
            continue   # 正文粘入的误标长行
        got = _jname_from_line(s)
        if got:
            ko, en = got
            if ko and _fold(ko) not in excluded_folds and 2 <= len(_fold(ko)) <= 50:
                _add(journal, ko, page, uid)
                if en:
                    _add(journal_en, en, page, uid)
            elif en and _fold(en) not in excluded_folds and 2 <= len(_fold(en)) <= 80:
                _add(journal_en, en, page, uid)
        # 年(刊头信号守卫 + 数字/连字符夹住排除)
        if _Y_GUARD.search(s):
            for m in _YEAR.finditer(s):
                y = int(m.group(1))
                if y in YEAR_OK:
                    _add(year, y, page, uid)
        # 卷(三组)/期(통권 旁路)
        for m in _KWAN.finditer(s):
            _add(vol_kwan, int(m.group(1)), page, uid)
        for m in _JIP.finditer(s):
            _add(vol_jip, int(m.group(1)), page, uid)
        for m in _VOLEN.finditer(s):
            _add(vol_vol, int(m.group(1)), page, uid)
        for m in _HO.finditer(s):
            if "통권" in s[max(0, m.start() - 3):m.start()]:
                continue
            v = int(m.group(1))
            if 1 <= v <= 999:
                _add(issue, v, page, uid)
        for m in _NOEN.finditer(s):
            _add(issue, int(m.group(1)), page, uid)
        for m in _TONGKWAN.finditer(s):
            tongkwan.append({"value": int(m.group(1)), "page": page})
        # BMC (연도) 권:문号
        for m in _BMC.finditer(s):
            if int(m.group(2)) < 999:
                _add(vol_vol, int(m.group(2)), page, uid)
            _add(year, int(m.group(1)), page, uid)
            bmc_art.append({"value": int(m.group(3)), "page": page})
        # 页
        for m in _PP.finditer(s):
            a, b = int(m.group(1)), int(m.group(2))
            if a < b and b < 9999:
                _add(pages_pp, (a, b), page, uid)
        for m in _BARE_RANGE.finditer(s):
            a, b = int(m.group(1)), int(m.group(2))
            if a < b and a < 999 and b < 999:
                _add(pages_bare, (a, b), page, uid)
        # DOI corroboration
        if _DOI.search(s):
            for m in _DOI_YV.finditer(s):
                _add(year, int(m.group(1)), page, uid)
                _add(vol_vol, int(m.group(2)), page, uid)
            for m in _DOI_VIY.finditer(s):
                _add(vol_vol, int(m.group(1)), page, uid)
                _add(issue, int(m.group(2)), page, uid)
                _add(year, int(m.group(3)), page, uid)
    # —— 定 winners ——
    # 刊名: 首页行(阅读序首个) > 全语料投票; ko/en 各自独立定(纯英文刊名 BMC 型无 ko 字段)
    p0list = [v for v, ps in journal.items() if any(p == 0 for p, _ in ps)]
    if p0list:
        j_w = p0list[0]
        j_sup = [0]
    else:
        j_w, j_sup = _vote(journal, 0)
    p0en = [v for v, ps in journal_en.items() if any(p == 0 for p, _ in ps)]
    if p0en:
        j_en_w = p0en[0]
        j_en_sup = [0]
    else:
        j_en_w, j_en_sup = _vote(journal_en, 0)

    def _lvl(sup):
        return "high" if len(sup) >= 2 else ("mid" if sup else "low")

    def _uid0(cand, winner):
        """胜出值首个命中行的 unified uid(来源行锚): viewer 来源行点击跳转原文块用。
        页码推断兜底(pages_derived)无来源行, uid=None。"""
        if not cand or winner is None or winner not in cand:
            return None
        first = cand[winner][0]
        return first[1] if isinstance(first, tuple) else None

    y_w, y_sup = _vote(year, 0)
    vol_w, vol_sup = None, []
    vol_grp = None
    for grp in (vol_kwan, vol_vol, vol_jip):   # 권 > Vol > 집
        if grp:
            vol_w, vol_sup = _vote(grp, 0)
            vol_grp = grp
            break
    i_w, i_sup = _vote(issue, 0)
    pg_w, pg_sup = None, []
    pg_grp = None
    if pages_pp:
        pg_w, pg_sup = _vote(pages_pp, 0)
        pg_grp = pages_pp
    elif pages_bare:
        pg_w, pg_sup = _vote(pages_bare, 0)
        pg_grp = pages_bare
    res = {
        "journal": {"value": j_w, "uid": _uid0(journal, j_w),
                    "confidence": {"level": _lvl(j_sup), "source": "regex"}},
        "journal_en": {"value": j_en_w, "uid": _uid0(journal_en, j_en_w),
                       "confidence": {"level": _lvl(j_en_sup), "source": "regex"}},
        "year": {"value": y_w, "uid": _uid0(year, y_w),
                 "confidence": {"level": _lvl(y_sup), "source": "regex"}},
        "volume": {"value": vol_w, "uid": _uid0(vol_grp, vol_w),
                   "confidence": {"level": _lvl(vol_sup), "source": "regex"}},
        "issue": {"value": i_w, "uid": _uid0(issue, i_w),
                  "confidence": {"level": _lvl(i_sup), "source": "regex"}},
        "pages": {"value": ("%d-%d" % pg_w) if pg_w else None,
                  "uid": _uid0(pg_grp, pg_w),
                  "confidence": {"level": _lvl(pg_sup), "source": "regex"}},
    }
    audit = {"journal_candidates": {k: sorted(p for p, _ in v) for k, v in journal.items()},
             "journal_en_candidates": {k: sorted(p for p, _ in v) for k, v in journal_en.items()},
             "year_candidates": {str(k): sorted(p for p, _ in v) for k, v in year.items()},
             "volume_candidates": dict(
                 [("kwan:%d" % k, sorted(p for p, _ in v)) for k, v in vol_kwan.items()]
                 + [("vol:%d" % k, sorted(p for p, _ in v)) for k, v in vol_vol.items()]
                 + [("jip:%d" % k, sorted(p for p, _ in v)) for k, v in vol_jip.items()]),
             "issue_candidates": {str(k): sorted(p for p, _ in v) for k, v in issue.items()},
             "pages_candidates": {"%d-%d" % k: sorted(p for p, _ in v) for k, v in
                                  dict(list(pages_pp.items()) + list(pages_bare.items())).items()},
             "tongkwan": tongkwan, "bmc_article_no": bmc_art}
    if pg_w is None:
        res["pages"]["confidence"]["level"] = "low"
    res["_audit"] = audit
    res["_internal"] = {"pages_pp": pages_pp, "pages_bare": pages_bare}
    return res


def _derive_pages(page_values):
    """页码推断: offset=印刷号-物理页序号 众数回填; 返回 (first, last, support, offset) 或 None。"""
    offs = collections.Counter()
    for p, v in page_values.items():
        if v is not None:
            offs[v - p] += 1
    if not offs:
        return None
    offset, support = offs.most_common(1)[0]
    last_p = max(page_values)
    return offset, offset + last_p, support, offset


def _kw_parse(s, lines, i):
    """单条 KW_LINE 行 → (en, items, raw) 或 None。en=头词含 ASCII 字母(英文组);
    有冒号取冒号后, 无冒号剥头词尾部/空白; 剥 '(Key Words)' 式标签残留(B3);
    同块续行(行尾逗/分号/· 未完结, ≤4 行, 遇头词/摘要头/목차行停); 逗号/分号切分。
    items 长度>1、≤15; raw=命中行+续行原文(审计留痕)。"""
    m = KW_LINE.match(s)
    if not m:
        return None
    en = bool(re.search(r"[A-Za-z]", m.group(1) or ""))
    rest = s[m.end():]
    if ":" in rest or u"：" in rest:
        rest = re.split(r"[:：]", rest, maxsplit=1)[1]
    rest = re.sub(r"^\s*[(（]\s*Key\s*words?\s*[)）]\s*", "", rest, flags=re.I)
    acc = rest.strip()
    jj = i + 1
    while (not acc or acc.endswith((",", u"，", ";", u"；", u"·"))) and \
            jj < len(lines) and jj <= i + 4:
        nxt = lines[jj].strip()
        if KW_LINE.match(nxt) or ABS_HEAD_KO.match(nxt) or ABS_HEAD_EN.match(nxt) \
                or re.match(r"^(요\s*약|서\s*론|Ⅰ\.|Abstract$)", nxt):
            break
        acc += " " + nxt
        jj += 1
    raw = [s] + [lines[k].strip() for k in range(i + 1, jj) if lines[k].strip()]
    items = [x.strip().strip(".").strip() for x in _KW_SPLIT.split(acc)]
    items = [x for x in items if x and len(x) > 1][:15]
    return en, items, raw


def issue_doc(doc, out_root):
    """单篇: 来源行收集 → extract() → 页码推断兜底 → metadata.json 新字段 + audit.issue。"""
    doc_dir = os.path.join(out_root, doc)
    udir = os.path.join(doc_dir, "unified")
    meta_fp = os.path.join(doc_dir, "metadata.json")
    if not os.path.isdir(udir) or not os.path.exists(meta_fp):
        print("跳过 issue %s: 缺 unified/metadata" % doc)
        return None
    meta = json.load(io.open(meta_fp, encoding="utf-8"))
    pages = sorted(int(re.match(r"page_(\d+)\.json$", f).group(1))
                   for f in os.listdir(udir) if re.match(r"page_\d+\.json$", f))
    pdata = {p: json.load(io.open(os.path.join(udir, "page_%d.json" % p), encoding="utf-8"))
             for p in pages}
    source = []
    page_values = {}
    for p in pages:
        j = pdata[p]
        got = None
        for u in j["unified"]:
            if u["type"] == "Page-number":
                for ln in (u["main_text"] or "").split("\n"):
                    v = _pageno_of(ln)
                    if v is not None:
                        got = v
                        break
                if got is not None:
                    break
        if got is None:
            for u in j["unified"]:
                if u["type"] not in STRUCT_TYPES:
                    continue
                for ln in (u["main_text"] or "").split("\n"):
                    v = _pageno_of(ln)
                    if v is not None:
                        got = v
                        break
                if got is not None:
                    break
        page_values[p] = got
        for u in j["unified"]:
            t = u["main_text"] or ""
            if u["type"] in STRUCT_TYPES or (p == 0 and u["box"][1] < 0.12 * j["h"]):
                for ln in t.split("\n"):
                    if ln.strip():
                        source.append((p, u["uid"], ln.strip()))
    excluded = set()
    for k in ("title", "title_en"):
        v = (meta.get(k) or {}).get("value")
        if v:
            excluded.add(_fold(v))
    for a in meta.get("authors") or []:
        if a.get("name"):
            excluded.add(_fold(a["name"]))
    res = extract(source, len(pages), excluded)
    audit = res.pop("_audit")
    res.pop("_internal")
    # —— 页: pp/裸区间无 → 页码推断 ——
    if res["pages"]["value"] is None:
        d = _derive_pages(page_values)
        if d:
            first, last, support, offset = d
            res["pages"]["value"] = "%d-%d" % (first, last)
            res["pages"]["confidence"] = {"level": "mid" if support >= 3 else "low",
                                          "source": "regex"}
            audit["pages_derived"] = {"first": first, "last": last, "offset": offset,
                                      "support": support,
                                      "per_page": {str(k): v for k, v in page_values.items()}}
    # —— 关键词: 韩/英双字段(2026-11-09 用户拍板: 有韩英双板的都提取)——
    # 页序各取首个 KW_LINE 组: 韩文头词(키워드/중심어/핵심어/주요어/주제어)→keywords[],
    # 英文头词(Key words/Keywords)→keywords_en[]; 与 abstract/abstract_en 双字段对齐,
    # 字段语言不再由版式先后决定(895567 英先页1→旧口径只收英; 13563525 韩先→只收韩)。
    kws, kws_en, kw_raw, kw_raw_en = [], [], [], []
    kw_loc = kw_loc_en = None
    for p in pages:
        if kws and kws_en:
            break
        j = pdata[p]
        blocks = [u for u in j["unified"] if (u["main_text"] or "").strip()]
        blocks.sort(key=lambda u: (u["box"][1], u["box"][0]))
        for u in blocks:
            if kws and kws_en:
                break
            lines = (u["main_text"] or "").split("\n")
            for i, ln in enumerate(lines):
                got = _kw_parse(ln.strip(), lines, i)
                if not got:
                    continue
                en, items, raw = got
                if not items:
                    continue
                if en:
                    if kws_en:
                        continue
                    kws_en, kw_raw_en, kw_loc_en = items, raw, {"page": p + 1, "uid": u["uid"]}
                else:
                    if kws:
                        continue
                    kws, kw_raw, kw_loc = items, raw, {"page": p + 1, "uid": u["uid"]}
    # —— 落盘 ——
    meta.update({
        "doc": doc,
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "journal": res["journal"], "journal_en": res["journal_en"], "year": res["year"],
        "volume": res["volume"], "issue": res["issue"], "pages": res["pages"],
        "keywords": kws, "keywords_en": kws_en,
    })
    io.open(meta_fp, "w", encoding="utf-8").write(json.dumps(meta, ensure_ascii=False, indent=1))
    audit["keywords"] = {"items": kws, "items_en": kws_en, "raw_lines": kw_raw,
                         "raw_lines_en": kw_raw_en, "source": kw_loc, "source_en": kw_loc_en}
    a = load_audit(doc_dir)
    a["issue"] = audit
    save_audit(doc_dir, a)
    print("== issue %s == 期刊=%s(%s) 年=%s 卷=%s 期=%s 页=%s KW=%d" % (
        doc, res["journal"]["value"], res["journal_en"]["value"] or "-",
        res["year"]["value"], res["volume"]["value"], res["issue"]["value"],
        res["pages"]["value"], len(kws)))
    return meta


# —— 冻结自检(python -m metapy.issue) ——
def _selftest():
    n_ok = 0
    ex_title = {_fold(u"통합문법: 주제·초점과 시점")}
    ex_author = {_fold(u"장석진")}

    # 1) 『』书名号 + pp + 집
    r = extract([(0, "u0", u"『현대유럽철학연구』 제70집, pp.33~63.")], 1, set())
    ok1 = r["journal"]["value"] == u"현대유럽철학연구" and r["volume"]["value"] == 70 \
        and r["pages"]["value"] == "33-63"
    # 2) 前导页码两式
    r = extract([(1, "u1", u"192 군사연구 제130집"),
                 (2, "u2", u"군사연구 제130집 205")], 3, set())
    ok2 = r["journal"]["value"] == u"군사연구" and r["volume"]["value"] == 130
    # 3) 首页权威: 학회지(1x,p0) 压过 학회(7x,pp2-8); 韩英拆段; 尾部孤立年剥除; 裸区间页
    src = [(0, "u0", u"한국음악치료학회지 Korean Journal of Music Therapy 2007, Vol. 9, No. 2, 1-16")] + \
          [(p, "u%d" % p, u"한국음악치료학회(제 9권 2호, 2007)") for p in range(2, 9)]
    r = extract(src, 9, set())
    ok3 = r["journal"]["value"] == u"한국음악치료학회지" \
        and r["journal_en"]["value"] == "Korean Journal of Music Therapy" \
        and r["year"]["value"] == 2007 and r["volume"]["value"] == 9 \
        and r["issue"]["value"] == 2 and r["pages"]["value"] == "1-16" \
        and r["journal"]["uid"] == "u0" and r["year"]["uid"] == "u0" \
        and r["volume"]["uid"] == "u2" and r["pages"]["uid"] == "u0"   # 来源行 uid 回填(권>Vol,锚=胜出行)
    # 4) et al. 剥离 + BMC 卷:文号(期不出)
    r = extract([(0, "u0", "Hong et al. Journal of Ecology and Environment (2019) 43:44")] * 2, 2, set())
    ok4 = r["journal_en"]["value"] == "Journal of Ecology and Environment" \
        and r["year"]["value"] == 2019 and r["volume"]["value"] == 43 \
        and r["issue"]["value"] is None
    # 5) 无제 的 NN집
    r = extract([(1, "u1", u"中央史論 46집")], 2, set())
    ok5 = r["journal"]["value"] == u"中央史論" and r["volume"]["value"] == 46
    # 6) 守卫: ISSN 年份夹住不出; 本篇标题行(2015…방안) 无刊名无年
    r = extract([(0, "u0", "ISSN 2384-2008 (Print) / 2733-8681 (Online)")], 1, set())
    r2 = extract([(1, "u1", u"2015 교육과정에 따른 검정교과서의 창의융합활동에 제시된간이 온도계 만들기의 개선 방안")],
                 2, ex_title)
    ok6 = r["year"]["value"] is None and r2["journal"]["value"] is None \
        and r2["year"]["value"] is None
    # 7) '|' 行: ko/en 段拆分; '20'/'202510' 不出卷期
    r = extract([(0, "u0", "글로컬 어문학 문화 연구 | International Journal of Glocal Language "
                          "and Literary Studies | 20 | 202510")], 1, set())
    ok7 = r["journal"]["value"] == u"글로컬 어문학 문화 연구" \
        and r["journal_en"]["value"] == "International Journal of Glocal Language and Literary Studies" \
        and r["volume"]["value"] is None and r["issue"]["value"] is None
    # 8) DOI  corroboration 两式(dasein=年+卷, jcic=卷+期+年)
    r = extract([(0, "u0", "DOI 10.20974/dasein.2023.70.33")], 1, set())
    r2 = extract([(0, "u0", "http://www.doi.org/10.32823/jcic.9.4.202311.347")], 1, set())
    ok8 = r["year"]["value"] == 2023 and r["volume"]["value"] == 70 \
        and r2["year"]["value"] == 2023 and r2["volume"]["value"] == 9 and r2["issue"]["value"] == 4
    # 9) 통권 旁路: 期=1 非 16, audit 记 16
    r = extract([(1, "u1", u"2 / 국제해양법연구 제9권 제1호 · 통권 제16호")], 2, set())
    ok9 = r["issue"]["value"] == 1 and r["volume"]["value"] == 9 \
        and r["_audit"]["tongkwan"][0]["value"] == 16
    # 10) 关键词解析三式 + 韩英分组(含 '■ 중심어 :' 头词尾字随冒号切掉; ◆ 주제어(B3);
    #     '(Key Words)' 标签残留剥除; 头词按是否含 ASCII 字母分英/韩)
    from .common import KW_LINE as _KL
    def _kw(s):
        got = _kw_parse(s.strip(), [s], 0)
        return got[1] if got else None
    ok10 = _kw(u"핵심어\t 이성당, 군산, 글로컬라이제이션") == [u"이성당", u"군산", u"글로컬라이제이션"] \
        and _kw(u"■ 중심어 : 창의융합활동, 간이 온도계") == [u"창의융합활동", u"간이 온도계"] \
        and _kw(u"◆ 주제어(Key Words) : 경찰공무원, 외상 후 스트레스장애") == [u"경찰공무원", u"외상 후 스트레스장애"] \
        and _kw(u"주제어 : 성곽, 우수주") == [u"성곽", u"우수주"] \
        and _kw("Key Words: alpha, beta") == ["alpha", "beta"] \
        and _kw_parse("Keywords Lee Sung Dang, Gunsan", ["Keywords Lee Sung Dang, Gunsan"], 0)[0] is True \
        and _kw_parse(u"핵심어 이성당, 군산", [u"핵심어 이성당, 군산"], 0)[0] is False \
        and not _KL.match(u"◆ 관련법령은 다음과 같다")
    # 11) 页码推断: 'Page N of M' 取 N, 行首优先('34 4'→34), offset 众数回填首页
    ok11 = _pageno_of("Page 2 of 16") == 2 and _pageno_of(u"통합문법: 주제·초점과 시점 155") == 155 \
        and _pageno_of("34 4") == 34
    pv = {0: None, 1: 146, 2: 147, 3: 148, 30: 175}
    d = _derive_pages(pv)
    ok11 = ok11 and d[0] == 145 and d[1] == 175 and d[2] >= 3
    # 12) 标题型 running 头(':')与作者行不出刊名
    r = extract([(1, "u1", u"통합문법: 주제·초점과 시점 155"), (2, "u2", u"146 장석진")], 3,
                ex_title | ex_author)
    ok12 = r["journal"]["value"] is None and r["pages"]["value"] is None
    # 13) 日期残段('2025년 6월, pp. 1~30')不出刊名; 首页首个候选优先于后续行
    src = [(0, "u0", u"국제해양법연구 | 제9권 제1호·통권 제16호"),
           (0, "u1", u"2025년 6월, pp. 1~30"),
           (0, "u2", u"The Korean Journal of the Law of the Sea | Vol.9, No.1, June, 2025")]
    r = extract(src, 1, set())
    ok13 = r["journal"]["value"] == u"국제해양법연구" and r["pages"]["value"] == "1-30" \
        and r["year"]["value"] == 2025
    # 14) 带 boundary 的 '|' 行: 英名段在卷期标记之后也能配对(journal_en)
    r = extract([(0, "u0", u"국제해양법연구 | 제9권 제1호·통권 제16호 | 2025년 6월, pp. 1~30 | "
                          u"The Korean Journal of the Law of the Sea | Vol.9, No.1, June, 2025")], 1, set())
    ok14 = r["journal"]["value"] == u"국제해양법연구" \
        and r["journal_en"]["value"] == "The Korean Journal of the Law of the Sea" \
        and r["volume"]["value"] == 9 and r["issue"]["value"] == 1 and r["pages"]["value"] == "1-30"
    # 15) 多行页眉逐行形态: 英名单独成行(仅 en 规则), ko 名靠次页 running 头
    src = [(0, "u%d" % i, s) for i, s in enumerate([
        u"국제해양법연구", u"제9권 제1호·통권 제16호", u"2025년 6월, pp. 1~30",
        "The Korean Journal of the Law of the Sea", "Vol.9, No.1, June, 2025"])] + \
        [(1, "u5", u"2 / 국제해양법연구 제9권 제1호 · 통권 제16호")]
    r = extract(src, 2, set())
    ok15 = r["journal"]["value"] == u"국제해양법연구" \
        and r["journal_en"]["value"] == "The Korean Journal of the Law of the Sea" \
        and r["volume"]["value"] == 9 and r["issue"]["value"] == 1 and r["pages"]["value"] == "1-30"
    # 16) issue_doc 级: 韩英双板同篇(英在页1/韩在页2)双字段各收——字段语言不再由版序决定
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        dd = os.path.join(tmp, "kwbi")
        udir = os.path.join(dd, "unified")
        os.makedirs(udir)
        io.open(os.path.join(dd, "metadata.json"), "w", encoding="utf-8").write("{}")
        def _pu(pno, uid, text):
            return {"uid": uid, "page": pno, "type": "Text", "main_text": text,
                    "box": [10, 100, 500, 200], "cross_page": {"spans": False},
                    "readings": [], "confidence": {"level": "high"}, "review": False,
                    "members": {}}
        io.open(os.path.join(udir, "page_0.json"), "w", encoding="utf-8").write(json.dumps(
            {"doc": "kwbi", "page": 1, "w": 1000, "h": 1500, "in_ref": False,
             "unified": [_pu(1, "u0_0", "Key words : Mine Waters, Evaporite")]}, ensure_ascii=False))
        io.open(os.path.join(udir, "page_1.json"), "w", encoding="utf-8").write(json.dumps(
            {"doc": "kwbi", "page": 2, "w": 1000, "h": 1500, "in_ref": False,
             "unified": [_pu(2, "u1_0", u"주요어 : 광산배수, 증발잔류광물")]}, ensure_ascii=False))
        got = issue_doc("kwbi", tmp)
        ok16 = bool(got) and got.get("keywords") == [u"광산배수", u"증발잔류광물"] \
            and got.get("keywords_en") == ["Mine Waters", "Evaporite"]
    for i, ok in enumerate((ok1, ok2, ok3, ok4, ok5, ok6, ok7, ok8, ok9, ok10, ok11, ok12, ok13,
                            ok14, ok15, ok16), 1):
        if ok:
            n_ok += 1
        else:
            print("FAIL [issue case %d]" % i)
    print("issue selftest: %d/16 PASS" % n_ok)
    return n_ok == 16


if __name__ == "__main__":
    raise SystemExit(0 if _selftest() else 1)
