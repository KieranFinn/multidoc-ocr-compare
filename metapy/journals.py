# -*- coding: utf-8 -*-
"""刊例注册表(2026-11-18): 已知期刊版式族的路由与事实登记。

动机: 题录抽取的残差失败几乎全呈刊例结构 —— 通用守卫按行形态启发式判断, 新版式一来
就漏; 而同一刊例的修复若散在通用代码里, 改一处容易误伤别刊(拆东墙补西墙)。
本模块把每族的"版式签名 / 由哪个抽取路径负责 / 已知缺口"登记成显式数据:
  - failures.py 按族聚类红单(轮次收敛的"学习集");
  - 新规则评审时按族查"这条路归谁管、误报边界是什么";
  - 新批次入库时按 DOI 前缀/刊名路由到已知族, 预期其版式, 红单直接归因。

只登记**事实**, 不在这里执行抽取 —— 抽取逻辑仍归 metapy/front.py 与 metapy/issue.py,
那里每条守卫各带实证注释。新增一族时: 加一条 FAMILIES + 在 front/issue 的守卫注释里
互指(两处都要写, 防漂移)。
"""
import io
import json
import os
import re

# key: 族 id(短名); names: 刊名(可能多语言); doi_prefix: 路由主键(小写, 无尾点);
# layout: 版式签名(什么人眼可辨的特征); extractor: 负责的抽取路径(函数名可搜);
# gaps: 已知缺口(篇目/字段, 多为"原文证据缺失"型, 评审时先查这里别再挖一遍)。
FAMILIES = [
    {
        "key": "kgs", "names": ["국방과 보안"], "doi_prefix": None,
        "layout": "免卷期刊(国防部杂志型, 通卷编号): 不印年卷期, 首页投稿历程行 "
                  "'접수일(YYYY년…), 수정일(…), 게재확정일(YYYY년…)' 供推年; 正文 [n] 括号引用",
        "extractor": "issue._pubdate_fn_year; issue._apply_doi_series('defsec')",
        "gaps": "13564438 唯一无投稿历程行(年无证据, 备注); 13564462 正文零标注"
                "(文末 15 条参考文献无文内标记, 备注)。2026-11-18 修正: 原文不印卷期但 "
                "KCI 官方著录有(2023→5-1, 2024→6-1, 2025→7-x, 2026→8-1)——全库 67 篇已按著录"
                "批量对齐(journal_toc_fill.py), 该刊不再视为免卷; 新批次此刊缺卷期直接跑 "
                "journal_toc_fill 或 webfill, 别再备注豁免",
    },
    {
        "key": "bjh", "names": [], "doi_prefix": "10.33957/bjh",
        "layout": "KCI 分发 PDF 不印刊名: 首页 DOI(年.卷.期) + 每页眉 '页码 N호'; "
                  "部分篇末页有 '투고일(…), 심사일(…), 게재확정일(…)'",
        "extractor": "issue._apply_doi_series('BJH') 填年/卷/期; 刊名恒缺走备注",
        "gaps": "全库 44 篇均缺刊名(原文不印, 非缺陷); 刊名缩写 BJH 未确证全称",
    },
    {
        "key": "jdqs", "names": ["국방품질연구논집"], "doi_prefix": "10.23199/jdqs",
        "layout": "英题/韩题/署名/机构被 unify 合并进同一块: '한글명, Roman Name[ 机构]' 单行署名 "
                  "+ 机构行 + Corresponding 脚注; ISSN 行夹在题间",
        "extractor": "front._parse_author_line 的 byline 分支(整行判定, 占有量词); "
                     "front 题名块/作者窗口兜底(_byline_lead)",
        "gaps": "无",
    },
    {
        "key": "kwr", "names": ["한국과 세계"], "doi_prefix": "10.22743/kwr",
        "layout": "方括号关键词行(可并进英文摘要块末行, 无头词); 作者行 '손은경 경북대학교' 或 "
                  "'김영준(Young-Joon Kim)¹ | ¹(제1저자)机构 职称 | email' 合并行",
        "extractor": "issue 关键词行级括号兜底(韩英双板守卫); front paren/m_aff_space 分支; "
                     "题名块兜底按 '|' 切段归类",
        "gaps": "13567244 双行题名现取次行(题值部分)",
    },
    {
        "key": "jdca", "names": ["디지털문화아카이브지"], "doi_prefix": "10.23089/jdca",
        "layout": "页眉印 'Vol.N No.M, 页码-页码, 年月' + DOI; 作者机构合并行按 '|' 分三段",
        "extractor": "front 题名块兜底('|' 切段); issue 页眉正则",
        "gaps": "无",
    },
    {
        "key": "kjcss", "names": ["중국사회과학논총", "The Korea Journal of Chinese Social Science"],
        "doi_prefix": "10.23192/kjcss",
        "layout": "刊名页脚双印(韩 'N 중국사회과학논총' + 英 'The Korea Journal of…'); "
                  "末页 '원고접수일: YYYY년…/심사완료일/게재확정일' 冒号体例, 可能分两快; "
                  "多数篇有 DOI(年.卷.期.文号), 少数无",
        "extractor": "issue._pubdate_fn_year(页级双词守卫, 取 게재확정일 年); "
                     "issue 页脚刊名剥页码(_apply_running_journal)",
        "gaps": "8 篇无 DOI(38/40/43/45/69/71/73/74): 卷期原文无证据(备注); "
                "期以月代(是否入免卷名单待用户拍板)",
    },
    {
        "key": "syeon", "names": ["산업연구"], "doi_prefix": None,
        "layout": "页脚刊例 'N 산업연구 | YYYY년 제V권 제N호'(可迟到第 29 页); 文献条目为 ·/• "
                  "项目符号体例且 unify 常合并成整块(30+ 条); 正文混韩英引用",
        "extractor": "issue 页脚刊例守卫(issue.py: 页脚放行/clean_jname); refs._blocks_to_entries "
                     "行首点号剥除(2026-11-24, 13578215 实证 15 条)",
        "gaps": "无",
    },
    {
        "key": "jnah", "names": ["유라시아문화"], "doi_prefix": "10.47527/jnah",
        "layout": "통권 '제 N 집' 编号(页眉/页脚), DOI 四段为 年.期.집.文号(riks 式, 期∈{2,8})",
        "extractor": "issue._YIV_RIKS 白名单(jnah, 2026-11-18 增补)",
        "gaps": "无",
    },
    {
        "key": "mses", "names": [u"다문화사회와 교육연구"], "doi_prefix": "10.22957/mses",
        "layout": "页脚/页眉印 'N 다문화사회와 교육연구 N집'(老刊按집编号); DOI 为 卷..年月.文号",
        "extractor": "issue._DOI_VYY 年+卷; journal_from_doi 注册表填空",
        "gaps": "无",
    },
    {
        "key": "jams", "names": ["Journal of Advances in Military Studies", "JAMS"],
        "doi_prefix": "10.37944/jams",
        "layout": "多行英文刊头: 'JAMS / JOURNAL OF ADVANCES IN MILITARY STUDIES' + "
                  "'YYYY, Vol. N, No. M, pp.-pp.' + DOI; OCR 常把刊头粘连成 "
                  "'J A JAMS M S JOURNAL OFADVANCES…'",
        "extractor": "issue._latin_jname 的 JAMS 归一分支 + _YEAR_VOL 年卷期行; "
                     "front en_line 分支 '姓, 名' **保持印刷形**(2026-11-24 用户拍板, "
                     "原'名 姓'归一已撤——存值须与原文/KCI著录/Crossref 一致, 实证 13571795 "
                     "Crossref family=Mun; 同族 58 篇)",
        "gaps": "无",
    },
    {
        "key": "espkor", "names": ["ESP Review"], "doi_prefix": "10.23191/espkor",
        "layout": "页眉印 'ESP Review Vol. N, No. M, Season YYYY'; 作者行 '백서현(한양여자대학교)' "
                  "括号机构; 方括号关键词",
        "extractor": "front._parse_author_line 的 paren 分支(括号内容须带机构特征)",
        "gaps": "无",
    },
]

_FOLD = lambda s: re.sub(r"\s+", "", (s or "")).lower()

# ---- DOI 前缀 → 刊名注册表(2026-11-18, 白名单思路替代负向守卫) ----
# 刊名是封闭值域: 与其认识每一种垃圾形态, 不如只认注册表。两个来源:
#   manual: 人手/网络定名一次, 永驻(BJH=방정환연구 由 doi.org CSL + KCI 著录页双证实);
#   corpus: 从全库语料投票推导(DOI 前缀 → 该前缀下出现最多的刊名, ≥2 篇才收录)。
# 维护: python journals.py --build 重扫语料刷新 corpus 行(manual 行不被覆盖);
#       failures.py --round 每轮自动调用一次。
REGISTRY_FP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_doi_registry.json")
_MANUAL = {
    "10.33957/bjh": {"journal": u"방정환연구", "journal_en": "RESEARCH ON BANGJUNGHWAN",
                     "source": "manual",
                     "note": u"doi.org CSL + KCI 著录页双证实 2026-11-18; 발행기관 사단법인 방정환연구소, "
                             u"ISSN 2671-4051。注意: KCI 卷=자체권(2025→7), DOI 第二段=통권(13/14)"},
    "10.23073/defsec": {"journal": u"국방과 보안", "journal_en": None, "source": "manual",
                        "note": u"国防部杂志型免卷刊物(2026-11-17 用户拍板)"},
    "10.37944/jams": {"journal": "Journal of Advances in Military Studies",
                      "journal_en": "JAMS", "source": "manual",
                      "note": u"2026-11-24 KCI11_17 刊头/DOI 双实证; OCR 粘连变体由 issue._latin_jname 归一"},
    "10.22957/mses": {"journal": u"다문화사회와 교육연구", "journal_en": None, "source": "manual",
                      "note": u"2026-11-24 13573745 DOI 前缀+页脚 '다문화사회와 교육연구 10집' 双实证"},
}
_REGISTRY_CACHE = None


def load_registry():
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is not None:
        return _REGISTRY_CACHE
    reg = dict(_MANUAL)
    if os.path.isfile(REGISTRY_FP):
        try:
            for k, v in (json.load(io.open(REGISTRY_FP, encoding="utf-8")) or {}).items():
                reg.setdefault(k, v)          # manual 优先, corpus 行补缺
        except Exception:
            pass
    _REGISTRY_CACHE = reg
    return reg


def invalidate_registry():
    global _REGISTRY_CACHE
    _REGISTRY_CACHE = None


def _doi_prefixes(text):
    return ["10.%s/%s" % (m.group(1), m.group(2).lower())
            for m in re.finditer(r"10\.(\d{4,9})/([A-Za-z0-9]+)", text or "")]


def journal_from_doi(text):
    """DOI 文本 → (刊名, 刊名英) 或 (None, None)。最长前缀优先。"""
    reg = load_registry()
    hits = sorted((p for p in _doi_prefixes(text) if p in reg), key=len, reverse=True)
    if hits:
        row = reg[hits[0]]
        return row.get("journal"), row.get("journal_en")
    return None, None


def build_registry(out_root="out", write=True):
    """从全库语料投票推导 corpus 行: 同前缀下刊名众数, ≥2 篇才收录。返回合并后注册表。"""
    from collections import Counter
    votes = {}
    for doc in sorted(os.listdir(out_root)):
        mp = os.path.join(out_root, doc, "metadata.json")
        if not os.path.isfile(mp):
            continue
        try:
            m = json.load(io.open(mp, encoding="utf-8"))
        except Exception:
            continue
        jv = (m.get("journal") or {}).get("value")
        if not jv:
            continue
        t = family_page0(doc, out_root)
        for p in _doi_prefixes(t):
            votes.setdefault(p, Counter())[jv] += 1
    corpus = {p: {"journal": c.most_common(1)[0][0], "journal_en": None, "source": "corpus",
                  "note": u"语料投票 %d 篇" % sum(c.values())}
              for p, c in votes.items() if c.most_common(1)[0][1] >= 2}
    reg = dict(corpus)
    for k, v in _MANUAL.items():
        reg.setdefault(k, dict(v))
    if write:
        io.open(REGISTRY_FP, "w", encoding="utf-8").write(
            json.dumps(corpus, ensure_ascii=False, indent=1, sort_keys=True))
    invalidate_registry()
    return reg


# ---- 同刊卷期账本 + sibling 推断(2026-11-18) ----
# 语料自带的冗余: 某刊带 DOI 的篇构成 (年,卷,期,页码区间) 账本。同刊某篇缺卷/期时,
# 其印刷页码完整落入某卷期跨度且不撞该卷期已知文章区间 → 唯一解才继承(只填空)。
_ILL_CACHE = {"sig": None, "ledger": {}}


def _ledger_signature(out_root):
    n, mt = 0, 0
    for d in os.listdir(out_root):
        mp = os.path.join(out_root, d, "metadata.json")
        if os.path.isfile(mp):
            n += 1
            mt = max(mt, os.path.getmtime(mp))
    return "%d:%d" % (n, int(mt))


def issue_ledger(out_root="out"):
    """{刊名fold: {'issues': {(年,卷,期): [篇号区间...]}, 'span': {(年,卷,期): (最小页,最大页)}}}"""
    sig = _ledger_signature(out_root)
    if _ILL_CACHE["sig"] == sig:
        return _ILL_CACHE["ledger"]
    ledger = {}
    for doc in sorted(os.listdir(out_root)):
        mp = os.path.join(out_root, doc, "metadata.json")
        if not os.path.isfile(mp):
            continue
        try:
            m = json.load(io.open(mp, encoding="utf-8"))
        except Exception:
            continue
        jv = (m.get("journal") or {}).get("value")
        vol = (m.get("volume") or {}).get("value")
        iss = (m.get("issue") or {}).get("value")
        yr = (m.get("year") or {}).get("value")
        pg = (m.get("pages") or {}).get("value") or ""
        mm = re.match(r"^(\d+)\s*-\s*(\d+)$", str(pg))
        if not (jv and vol is not None and iss is not None and mm):
            continue
        key = _FOLD(jv)
        ent = ledger.setdefault(key, {"issues": {}, "span": {}})
        ik = (yr, vol, iss)
        f, l = int(mm.group(1)), int(mm.group(2))
        ent["issues"].setdefault(ik, []).append((f, l))
        sf, sl = ent["span"].get(ik, (f, l))
        ent["span"][ik] = (min(sf, f), max(sl, l))
    _ILL_CACHE["sig"] = sig
    _ILL_CACHE["ledger"] = ledger
    return ledger


def fit_issue(journal, year, pages, ledger):
    """同刊卷期推断: 页码区间完整落入某卷期跨度且不与已知文章重叠 → 唯一解返回
    (年,卷,期); 无候选/多候选/页码冲突 → None(调用方如实不填)。"""
    ent = ledger.get(_FOLD(journal))
    mm = re.match(r"^(\d+)\s*-\s*(\d+)$", str(pages or ""))
    if not ent or not mm:
        return None
    f, l = int(mm.group(1)), int(mm.group(2))
    fits = []
    for ik, (sf, sl) in ent["span"].items():
        if year is not None and ik[0] is not None and ik[0] != year:
            continue
        if not (sf <= f and l <= sl):
            continue
        if any(not (l < a or b < f) for a, b in ent["issues"].get(ik, [])):
            continue
        fits.append(ik)
    return fits[0] if len(fits) == 1 else None



def family_of(meta=None, page0_text=None):
    """按 DOI 前缀 -> 刊名 -> 版式签名的顺序定族; 返回族 dict 或 None。

    meta: metadata.json dict(取 journal/journal_en); page0_text: 首页统一文本(取 DOI)。
    宽松匹配(折叠大小写/空白); 识别不出属正常 —— 新族正是这么被发现的。"""
    text = _FOLD(page0_text or "")
    doi = ""
    m = re.search(r"10\.\d{4,9}/([a-z0-9]+)", text)
    if m:
        doi = m.group(1).lower()
    names = set()
    for k in ("journal", "journal_en"):
        v = (meta or {}).get(k) or {}
        v = v.get("value") if isinstance(v, dict) else v
        if v:
            names.add(_FOLD(str(v)))
    best = None
    for fam in FAMILIES:
        p = fam.get("doi_prefix")
        if doi and p and doi == p.split("/")[-1]:
            return fam
    # 刊名匹配: 折叠后相等, 或族名是刊名前缀且余量 ≤4 字(防 '한국과세계' 子串吞长刊名)
    for fam in FAMILIES:
        for n in fam.get("names") or []:
            nf = _FOLD(n)
            if names and any(x == nf or (nf in x and len(x) - len(nf) <= 4)
                             for x in names if len(x) >= 4):
                return fam
    return None


def family_page0(doc, out_root="out"):
    """读首页统一文本供 family_of 用(无 unified 时返回空串)。"""
    fp = os.path.join(out_root, doc, "unified", "page_0.json")
    if not os.path.isfile(fp):
        return ""
    try:
        j = json.load(io.open(fp, encoding="utf-8"))
    except Exception:
        return ""
    return "\n".join((u.get("main_text") or "") for u in j.get("unified") or [])


if __name__ == "__main__":
    covered = {f["key"]: [] for f in FAMILIES}
    unknown = []
    for doc in sorted(os.listdir("out")):
        mp = os.path.join("out", doc, "metadata.json")
        if not os.path.isfile(mp):
            continue
        meta = json.load(io.open(mp, encoding="utf-8"))
        fam = family_of(meta, family_page0(doc))
        (covered[fam["key"]] if fam else unknown).append(doc)
    print("== 刊例族覆盖 ==")
    for f in FAMILIES:
        print("  %-8s %3d 篇  %s" % (f["key"], len(covered[f["key"]]),
                                      (f["names"] or [f.get("doi_prefix")])[0]))
    print("  %-8s %3d 篇" % ("(未识别)", len(unknown)))
    if unknown:
        print("  未识别样例:", unknown[:8])
