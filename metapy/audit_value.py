# -*- coding: utf-8 -*-
"""字段值后验(post-hoc value audit): 查"抽到了、但可能抽错"的字段。

质检 `gui.qc_of()` 只问一件事——必抽项**在不在**。所以在的字段是错值, 质检永远是绿的。
本模块问的是另一半: **在的那个值, 立不立得住**。两者互补, 不互相替代。

判据分四类, 全部来自可复现实证, 不是想象出来的规则:

  ① 形态先验   值本身的长度/字符集/前后缀是否像这个字段该有的样子
               (机构 24 条眼检 → 抓出出版社/论文题名/致谢句 6 条)
  ② 来源先验   值的出处是否与其语义相符 —— 题名不能结句、机构必须有机构尾
               (1536751 的题名是 197 字正文段, 就是"来源错"而非"形态错")
  ③ 跨字段一致 一个字段的错会以另一个字段的错为症状, 可以机械反查
               (作者全错 → 反查 `_pick_title` → 发现题名错了)
  ④ 语料离群   同一字段在全集上的分布, 单个值超出常识区间即为可疑
               (题名长度 P95、作者人数、机构数)

每条规则带 `evidence` 字段写明它是被哪篇文档逼出来的。规则宁少勿滥:
一条高精度规则的价值是"报了就得改", 低精度规则只会让人关掉这个检查。

用法:
    python -m metapy.audit_value                # 全库, 只报 bad/warn
    python -m metapy.audit_value 13563526       # 单篇
    python -m metapy.audit_value --all          # 连 note 一起报
    python -m metapy.audit_value --json         # 机器可读
    python -m metapy.audit_value --selftest     # 规则回归(含 4 例真实错值)
"""
import os, io, re, json, glob, sys, datetime, collections

from .common import _fold, _hangul_ratio, channel_lines

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")
NOW_YEAR = datetime.date.today().year

# ---------------------------------------------------------------------------
# 独立判据(刻意不复用抽取器的守卫: 抽取器自己的守卫是"自证", 后验要的是"他证")
# ---------------------------------------------------------------------------

# 机构尾词表: 与 front._AFF_STRICT 刻意不同序不同集, 靠"两套词表都认不出"提高精度。
_INST_TAIL = re.compile(
    u"(?:대학교|대학원|대학|학과|학부|학부대학|연구소|연구원|연구단|연구재단|연구회|"
    u"교육원|학술원|어학원|개발원|진흥원|관리원|지원센터|센터|박물관|도서관|병원|의료원|"
    u"재단|학회|학교|공단|공사|협회|조합|기관|위원회|"
    r"University|Universit[\u00e4\u00e9]|College|Institute|Laborator|School|Department|"
    r"Faculty|Center|Centre|Academy|Hospital|Campus|Association|Society|Foundation|"
    r"Council|Ministry|Agency|Bureau|Corporation|Company|Ltd|Inc\b)", re.I)

# 非机构: 出版社/期刊/项目名 —— 出现在机构行里必是误检(6 条实证里有 2 条属此类)
_INST_BAD = re.compile(
    u"(?:Press\\b|Publishing\\b|Publications\\b|출판|출판사|"
    u"\\bProject\\b|\\bProgram(?:me)?\\b|\\bInitiative\\b|학회\\s*발표|\\b발표\\b|"
    u"\\bSeries\\b|\\bWorking\\s+Paper)", re.I)

# 句子特征: 机构署名不是句子; 摘要/致谢被当机构时几乎必命中
_SENT = re.compile(
    u"(?:본고는|본\\s*논문은|이\\s*논문은|이\\s*글은|밝힙니다|일환으로|지원사업|"
    u"사의를|감사를|수정[·ㆍ]?\\s*보완|발표한\\s*글|논평|토론을\\s*통해|연구\\s*과제|"
    u"보고서를|것임을|것이다|하고자\\s*한다|살펴보고자)|(?:다|요|음|임|함|됨)\\.\\s*$")

# 区段头/目录词: 这些永远不是人名
_STOPWORD = re.compile(
    u"^(?:서\\s*론|서\\s*언|결\\s*론|머\\s*리\\s*말|맺\\s*음\\s*말|목\\s*차|차\\s*례|"
    u"초\\s*록|요\\s*약|국문\\s*초록|국문\\s*요약|영문\\s*초록|영문\\s*요약|"
    u"참\\s*고\\s*문\\s*헌|인\\s*용\\s*문\\s*헌|Abstract|ABSTRACT|Keywords?|"
    u"주제\\s*어|중심\\s*어|핵심\\s*어|색\\s*인\\s*어|표\\s*목\\s*차|그\\s*림\\s*목\\s*차)$", re.I)

# 章节编号: 'Ⅰ. 서론' / 'II. Method' / '1. 序論'
_SECNO = re.compile(
    u"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+|I{1,3}|IV|V|VI{0,3}|\\d{1,2})\\s*[.)．]\\s*\\S")

# 合法人名形态: 한글이름(漢字) —— 这是体例, 不是错
_NAME_HANJA = re.compile(u"^[가-힣]{2,5}\\s*[\\(（][\u4e00-\u9fff]{2,5}[\\)）]$")

# 人名里不该出现的标点(括号已由 _NAME_HANJA 放行)
_NAME_BAD_PUNCT = re.compile(u"[,;:／/<>〈〉《》「」『』\\[\\]]|\\.\\.|--")

_PAGES_OK = re.compile(u"^\\s*\\d{1,4}\\s*(?:[-–~～]\\s*\\d{1,4})?\\s*$")
_PAGES_RANGE = re.compile(u"^(\\d{1,4})\\s*[-–~～]\\s*(\\d{1,4})$")


def _s(v):
    """单值字段统一取值: dict 形如 {'value':...} 与裸值都吃。"""
    if isinstance(v, dict):
        v = v.get("value")
    if isinstance(v, list):
        return v
    return v if v not in (None, "", [], {}) else None


def _txt(v):
    v = _s(v)
    return "" if v is None else str(v)


# ---------------------------------------------------------------------------
# 上下文
# ---------------------------------------------------------------------------

class Ctx(object):
    """一篇文档的判据上下文。meta/corpus 可直接注入(自检用), 也可从磁盘加载(实跑用)。"""

    def __init__(self, doc, out_root=OUT, meta=None, corpus=None, ner=None):
        self.doc = doc
        self.dd = os.path.join(out_root, doc) if (doc and out_root) else ""
        self._m = meta
        self._corpus = corpus
        self._ner = ner
        self._pages = None

    # -- 惰性资源 ---------------------------------------------------------
    @property
    def m(self):
        if self._m is None:
            fp = os.path.join(self.dd, "metadata.json")
            if os.path.isfile(fp):
                self._m = json.load(io.open(fp, encoding="utf-8")) or {}
            else:
                self._m = {}
        return self._m

    @property
    def pages(self):
        if self._pages is None:
            self._pages = []
            for fp in sorted(glob.glob(os.path.join(self.dd, "unified", "page_*.json"))):
                try:
                    j = json.load(io.open(fp, encoding="utf-8"))
                except Exception:
                    continue
                self._pages.append(j)
        return self._pages

    @property
    def corpus(self):
        """全篇统一层正文(含页眉页脚), 用于"值能否在原文里站住"的检索。"""
        if self._corpus is None:
            parts = []
            for j in self.pages:
                for u in j.get("unified") or []:
                    parts.append(u.get("main_text") or "")
            self._corpus = "\n".join(parts)
        return self._corpus

    @property
    def ner_orgs(self):
        if self._ner is None:
            self._ner = []
            fp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "out_llm_trial", "fds_extract", "ner_lite", self.doc or "",
                              "ner_lite.json")
            if os.path.isfile(fp):
                try:
                    recs = json.load(io.open(fp, encoding="utf-8")).get("records") or []
                    self._ner = [str(r.get("name") or "").strip()
                                 for r in recs if isinstance(r, dict) and r.get("kind") == "org"]
                except Exception:
                    self._ner = []
        return self._ner

    # -- 取值捷径 ---------------------------------------------------------
    def val(self, k):
        return _s(self.m.get(k))

    def authors(self):
        out = []
        for a in (self.m.get("authors") or []):
            if isinstance(a, dict):
                out.append(a)
            else:
                out.append({"name": a})
        return out

    def names(self):
        return [(a.get("name") or "").strip() for a in self.authors()]

    def affils(self):
        return [str(x).strip() for x in (self.m.get("affiliations") or []) if str(x or "").strip()]


# ---------------------------------------------------------------------------
# 规则注册
# ---------------------------------------------------------------------------

RULES = []          # (code, field, sev, fn)


def rule(code, field, sev):
    def deco(fn):
        RULES.append((code, field, sev, fn))
        return fn
    return deco


def _f(code, value, msg, evidence=""):
    return {"code": code, "value": value, "msg": msg, "evidence": evidence}


# ============================ ① 题名 =======================================

@rule("V-TITLE-SENT", "标题", "bad")
def _title_sent(c):
    """题名不结句。1536751 实证: 整栏正文段被当题名(197 字, 以 '…취지이다.' 收尾)。"""
    t = _txt(c.val("title")).strip()
    if not t:
        return []
    if _SENT.search(t) or len(re.findall(u"[다요음임]\\.\\s", t)) >= 1:
        return [_f("V-TITLE-SENT", t[:60],
                   "题名以句末终结词收尾/含句内句号, 更像正文段落",
                   "1536751: 真题名 '통합문법: 주제·초점과 시점' 被正文段压过")]
    return []


@rule("V-TITLE-LONG", "标题", "warn")
def _title_long(c):
    """题名长度离群。语料内韩文题名普遍 15–60 折合字; 超 120 基本是段落。"""
    t = _txt(c.val("title")).strip()
    n = len(_fold(t))
    if n > 120:
        return [_f("V-TITLE-LONG", "%d 字" % n, "题名折合长度 %d > 120, 超常识区间" % n)]
    return []


@rule("V-TITLE-IS-BODY", "标题", "bad")
def _title_is_body(c):
    """题名整段出现在某个正文块里(≥60 字重叠) —— 说明取的是正文而非题名。"""
    t = _fold(_txt(c.val("title")))
    if len(t) < 60:
        return []
    probe = t[:80]
    for j in c.pages[:2]:
        for u in j.get("unified") or []:
            if u.get("type") not in ("Text", "List", "Table"):
                continue
            body = _fold(u.get("main_text") or "")
            if len(body) >= 60 and (probe[:40] in body or body[:40] in t):
                return [_f("V-TITLE-IS-BODY", probe[:50],
                           "题名与正文块 %s 高度重叠(取到了正文)" % u.get("uid"),
                           "1536751 实证")]
    return []


@rule("V-TITLE-HEADER", "标题", "warn")
def _title_header(c):
    """题名是 running 页眉(刊名+卷期+日期), 不是文章题名。"""
    t = _txt(c.val("title")).strip()
    if not t:
        return []
    if re.search(u"(?:제?\\s*\\d+\\s*권|Vol\\.?\\s*\\d+|No\\.?\\s*\\d+|통권|\\|)", t) \
            and re.search(u"(?:19|20)\\d{2}", t):
        return [_f("V-TITLE-HEADER", t[:70], "题名含卷期+年份且带竖线, 像页眉而非题名")]
    return []


# ============================ ② 作者 =======================================

@rule("V-AUTH-SYM", "作者", "bad")
def _auth_sym(c):
    """作者名带前导符号。2632491 实证: '- 권여선'(副标题行首破折号被当人名)。"""
    out = []
    for n in c.names():
        if re.match(u"^[\\-–—•·*+]+\\s*", n) or re.search(u"[:;,]\\s*$", n):
            out.append(_f("V-AUTH-SYM", n, "作者名带前导/尾随符号",
                          "2632491: 副标题 '- 권여선, 은희경…' 被当署名"))
    return out


@rule("V-AUTH-SECNO", "作者", "bad")
def _auth_secno(c):
    """作者名是章节编号。13275609 实证: 目录条目 'Ⅰ. 서론' 混进作者。"""
    return [_f("V-AUTH-SECNO", n, "作者名是章节编号/目录条目", "13275609: 'Ⅰ. 서론'")
            for n in c.names() if _SECNO.match(n)]


@rule("V-AUTH-STOP", "作者", "bad")
def _auth_stop(c):
    """作者名是区段词。1536751 实证: ['초점','주제','초점'] 来自正文 '주제·초점'。"""
    return [_f("V-AUTH-STOP", n, "作者名是区段头/目录词", "1536751: 초점/주제")
            for n in c.names() if _STOPWORD.match(n)]


@rule("V-AUTH-INST", "作者", "bad")
def _auth_inst(c):
    """作者名里含机构词 —— 署名行与机构行混淆。"""
    out = []
    for n in c.names():
        if _INST_TAIL.search(n) or _INST_BAD.search(n):
            out.append(_f("V-AUTH-INST", n, "作者名含机构/出版社词, 机构行被当署名"))
    return out


@rule("V-AUTH-PHRASE", "作者", "bad")
def _auth_phrase(c):
    """作者名是短语而非人名: 韩文名内含空格, 或折合长度 > 20, 或西文名 > 5 词。"""
    out = []
    for n in c.names():
        if _NAME_HANJA.match(n):
            continue
        f = _fold(n)
        if re.search(u"[가-힣]\\s+[가-힣]", n) or len(f) > 20 \
                or (_hangul_ratio(n) < 0.2 and len(n.split()) > 5):
            out.append(_f("V-AUTH-PHRASE", n[:50], "作者名是短语/长串, 非人名形态"))
    return out


@rule("V-AUTH-PUNCT", "作者", "warn")
def _auth_punct(c):
    """作者名含人名不会带的标点(한글이름(漢字) 体例除外)。"""
    out = []
    for n in c.names():
        if _NAME_HANJA.match(n):
            continue
        if _NAME_BAD_PUNCT.search(n):
            out.append(_f("V-AUTH-PUNCT", n[:50], "作者名含标点, 像从长行里切坏的片段"))
    return out


@rule("V-AUTH-DUP", "作者", "warn")
def _auth_dup(c):
    """同一作者重复出现 —— 说明切分把同一行的词切了两遍。"""
    cnt = collections.Counter(_fold(n) for n in c.names() if n)
    return [_f("V-AUTH-DUP", k, "同一作者出现 %d 次" % v) for k, v in cnt.items() if v >= 2]


@rule("V-AUTH-ROMA-IN-TITLE", "作者", "bad")
def _roma_in_title(c):
    """roman 槽里放的是"英文题名"而不是人名。
    13563535 实证: roman='Korean Old Novel Yussi-Samdaerok'(英题) 被当 이윤석 的罗马拼音。
    人名罗马化在折叠后 ≤26 字符且 ≤3 词; 题名切片远长于此, 或带英文虚词, 或直接是 title_en。"""
    t_en = _fold(_txt(c.val("title_en")))
    out = []
    for a in c.authors():
        raw = (a.get("roman") or "").strip()
        r = _fold(raw)
        if len(r) < 8:
            continue
        why = ""
        if t_en and len(r) >= 8 and r in t_en:
            why = "roman 就是 title_en 的一部分"
        elif len(r) > 26 or len(raw.split()) > 3:
            why = "roman 折叠 %d 字/%d 词, 超出人名罗马化形态" % (len(r), len(raw.split()))
        elif re.search(r"\b(?:and|of|the|for|with|from)\b", raw.lower()):
            why = "roman 含英文虚词"
        if why:
            out.append(_f("V-AUTH-ROMA-IN-TITLE", raw, why,
                          "13563535: 'Korean Old Novel Yussi-Samdaerok'"))
    return out


@rule("V-AUTH-IN-TITLE", "作者", "warn")
def _auth_in_title(c):
    """作者名出现在题名里 —— 多半是从题名切下来的词。13563535 实证: 유씨삼대록/유숙기。
    只查 ≥5 折合字的名字: 3 音节的韩文名与题名撞词是正常现象(如作者 이상 + 题名 '이상 문학')。"""
    t = _fold(_txt(c.val("title")))
    if len(t) < 10:
        return []
    out = []
    for n in c.names():
        f = _fold(n)
        if len(f) >= 5 and f in t:
            out.append(_f("V-AUTH-IN-TITLE", n, "作者名是题名的子串",
                          "13563535: '유씨삼대록' 是题名首词"))
    return out


@rule("V-AUTH-SHARE-AFFIL", "作者", "note")
def _auth_share_affil(c):
    """多个作者共享同一 affiliation 且与 affiliations[] 完全一致 —— 常是"全部绑到第一条"的痕迹。"""
    aff = [str(a.get("affiliation") or "").strip() for a in c.authors()]
    aff = [x for x in aff if x]
    if len(aff) >= 3 and len(set(aff)) == 1:
        return [_f("V-AUTH-SHARE-AFFIL", aff[0], "%d 位作者全部绑同一机构, 疑似序位兜底硬配" % len(aff))]
    return []


# ============================ ③ 机构 =======================================

@rule("V-AFF-NOTAFF", "机构", "bad")
def _aff_notaff(c):
    """机构不含机构尾词。24 条眼检实证: 论文题名/摘要句被当机构。"""
    out = []
    for a in c.affils():
        if not _INST_TAIL.search(a):
            out.append(_f("V-AFF-NOTAFF", a[:60], "机构里认不出机构尾词"))
    return out


@rule("V-AFF-PRESS", "机构", "bad")
def _aff_press(c):
    """机构是出版社/项目名。13563530 实证: 'Oxford University Press, 2012'。"""
    return [_f("V-AFF-PRESS", a[:60], "机构是出版社/项目名, 来自引文条目")
            for a in c.affils() if _INST_BAD.search(a)]


@rule("V-AFF-SENT", "机构", "bad")
def _aff_sent(c):
    """机构是句子。13563531/13563533 实证: 致谢句 '…일환으로' 被当机构。"""
    return [_f("V-AFF-SENT", a[:60], "机构是完整句子(摘要/致谢句)")
            for a in c.affils() if _SENT.search(a)]


@rule("V-AFF-LONG", "机构", "warn")
def _aff_long(c):
    """机构长度离群。机构署名普遍 6–60 字。"""
    return [_f("V-AFF-LONG", "%d 字" % len(_fold(a)), "机构折合长度 > 80, 超常识区间")
            for a in c.affils() if len(_fold(a)) > 80]


@rule("V-AFF-IN-TITLE", "机构", "bad")
def _aff_in_title(c):
    """机构出现在题名里 —— 机构行抄的是题名。13563526 实证: '관무불가침 세계관…'。"""
    t = _fold(_txt(c.val("title")))
    return [_f("V-AFF-IN-TITLE", a[:50], "机构是题名的一部分")
            for a in c.affils() if len(_fold(a)) >= 8 and _fold(a) in t]


@rule("V-AFF-CONTAIN", "机构", "note")
def _aff_contain(c):
    """一条机构包含另一条 —— 冗余, 且通常是"长的那条被硬切"的痕迹。"""
    aff = c.affils()
    out = []
    for i, a in enumerate(aff):
        for j, b in enumerate(aff):
            if i != j and len(_fold(a)) > len(_fold(b)) and _fold(b) in _fold(a):
                out.append(_f("V-AFF-CONTAIN", b, "被 %r 完全包含(冗余)" % a[:30]))
    return out


@rule("V-AFF-NER-DISJOINT", "机构", "note")
def _aff_ner_disjoint(c):
    """与 NER 的 org 实体零交集 —— 两个独立源都不认, 值得看一眼(非错, 只是提示)。"""
    orgs = [o for o in c.ner_orgs if len(_fold(o)) >= 4]
    if not orgs or not c.affils():
        return []
    blob = _fold(" ".join(c.affils()))
    hit = [o for o in orgs if any(w in blob for w in _fold(o).split()) and len(_fold(o)) >= 6]
    if not hit:
        return [_f("V-AFF-NER-DISJOINT", " / ".join(c.affils())[:50],
                   "NER 认出 org 但与本篇机构无交集(两源互不印证)")]
    return []


# ============================ ④ 数值/跨字段 ================================

@rule("V-YEAR-RANGE", "出版年", "bad")
def _year_range(c):
    v = _txt(c.val("year"))
    if not v:
        return []
    m = re.search(r"(19|20)\d{2}", v)
    if not m:
        return [_f("V-YEAR-RANGE", v, "出版年不含四位数年份")]
    y = int(m.group(0))
    if not (1900 <= y <= NOW_YEAR + 1):
        return [_f("V-YEAR-RANGE", y, "出版年 %d 超出合理区间" % y)]
    return []


def _num_in_margin(c, n):
    """数字是否出现在页眉/页脚/页码块里。
    卷期不一定带 '제N권' 标记: 《글로컬 어문학 문화 연구》的页眉是 '… | 20 | 202510',
    BMC 是 '(2019) 43:44'。这两种体例的卷期只在页边行里, 所以页边命中同样算站得住。"""
    pat = re.compile(r"(?<!\d)" + re.escape(n) + r"(?!\d)")
    for j in c.pages[:3]:
        for u in j.get("unified") or []:
            if u.get("type") in ("Page-header", "Page-footer", "Page-number") \
                    and pat.search(u.get("main_text") or ""):
                return True
    return False


@rule("V-VOL-CTX", "卷", "warn")
def _vol_ctx(c):
    """卷号必须在原文里站得住: 紧邻卷标记, 或出现在页眉页脚里。
    1004818 实证: 脚注引书 'vol. 3 of …' 会被当卷号 —— 那种情况页边无命中, 仍报。"""
    v = _txt(c.val("volume"))
    mm = re.search(r"\d+", v)
    if not mm:
        return []
    n = mm.group(0)
    pat = re.compile(u"(?:제\\s*%s\\s*[권집]|Vol\\.?\\s*%s(?!\\d)|Volume\\s*%s(?!\\d)|%s\\s*[권집])" % (n, n, n, n), re.I)
    if pat.search(c.corpus) or _num_in_margin(c, n):
        return []
    return [_f("V-VOL-CTX", v, "卷号 %s 既未紧邻卷标记, 也不在任何页眉页脚里" % n)]


@rule("V-ISS-CTX", "期", "warn")
def _iss_ctx(c):
    """期号同卷号口径。
    1536751 实证: 全篇唯一 '203호' 是作者住址「3동 203호」; 1004818 的 'No. 715579' 是档案编号。"""
    v = _txt(c.val("issue"))
    mm = re.search(r"\d+", v)
    if not mm:
        return []
    n = mm.group(0)
    pat = re.compile(u"(?:제\\s*%s\\s*[호집]|No\\.?\\s*%s(?!\\d)|Issue\\s*%s(?!\\d)|%s\\s*[호집])" % (n, n, n, n), re.I)
    if pat.search(c.corpus) or _num_in_margin(c, n):
        return []
    return [_f("V-ISS-CTX", v, "期号 %s 既未紧邻期标记, 也不在任何页眉页脚里" % n)]


@rule("V-PAGES-FMT", "页码", "warn")
def _pages_fmt(c):
    v = _txt(c.val("pages"))
    if v and not _PAGES_OK.match(v):
        return [_f("V-PAGES-FMT", v, "页码不是 'n' 或 'n-m' 形")]
    return []


@rule("V-PAGES-SPAN", "页码", "warn")
def _pages_span(c):
    """页码跨度与 PDF 实际页数矛盾(跨页上限按每页 2 栏放宽)。"""
    v = _txt(c.val("pages"))
    m = _PAGES_RANGE.match(v or "")
    if not m:
        return []
    a, b = int(m.group(1)), int(m.group(2))
    if a > b:
        return [_f("V-PAGES-SPAN", v, "起始页 %d > 结束页 %d" % (a, b))]
    npage = len(c.pages)
    if npage and (b - a + 1) > npage * 2 + 2:
        return [_f("V-PAGES-SPAN", v, "跨度 %d 页 > 文档实际 %d 页的 2 倍" % (b - a + 1, npage))]
    return []


@rule("V-KW-COUNT", "关键词", "warn")
def _kw_count(c):
    kw = _s(c.val("keywords"))
    if not isinstance(kw, list):
        return []
    if len(kw) == 0:
        return []
    if len(kw) > 12:
        return [_f("V-KW-COUNT", "%d 个" % len(kw), "关键词数 %d > 12, 可能把正文或摘要切碎收进来了" % len(kw))]
    return []


@rule("V-KW-SENT", "关键词", "warn")
def _kw_sent(c):
    kw = _s(c.val("keywords"))
    if not isinstance(kw, list):
        return []
    out = []
    for k in kw:
        s = str(k or "").strip()
        if len(_fold(s)) > 40 or re.search(u"(?:다|요)\\.\\s*$", s) \
                or re.search(u"[。！？!?]", s):
            out.append(_f("V-KW-SENT", s[:40], "关键词过长/带句子标点, 像摘要切片"))
    return out


@rule("V-ABS-PREFIX", "摘要", "warn")
def _abs_prefix(c):
    """摘要值里残留区段头 —— 说明摘要区起点判错, 头被一起吃进去了。"""
    a = _txt(c.val("abstract")).strip()
    if not a:
        return []
    head = a[:24]
    if re.match(u"^[\\*\\s]*(?:국문\\s*초록|국문\\s*요약|영문\\s*초록|영문\\s*요약|초\\s*록|"
                u"Abstract|ABSTRACT|요\\s*약)\\s*[*:：]?", head) or head.startswith("**"):
        return [_f("V-ABS-PREFIX", head, "摘要以区段头残留开头")]
    return []


@rule("V-ABS-SHORT", "摘要", "note")
def _abs_short(c):
    """摘要过短。实测本库 21 篇摘要折合长度 min=350 / 中位=551, 低于 200 基本是残句。"""
    a = _txt(c.val("abstract")).strip()
    n = len(_fold(a))
    if a and n < 200:
        return [_f("V-ABS-SHORT", "%d 字" % n, "摘要折合长度 %d < 200, 像残句而非摘要" % n)]
    return []


@rule("V-AFF-COUNT-VS-AUTH", "机构", "warn")
def _aff_count(c):
    """作者数 > 机构数 且 无上标标记 —— 机构配对的序位兜底可能错。"""
    aus = c.authors()
    aff = c.affils()
    if len(aus) >= 3 and len(aff) == 1:
        marked = sum(1 for a in aus if a.get("markers"))
        if marked == 0:
            return [_f("V-AFF-COUNT-VS-AUTH", "%d 作者 / 1 机构" % len(aus),
                       "多作者只配到一个机构且无上标标记, 疑似全部绑第一条")]
    return []


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------

SEV_ORDER = {"bad": 0, "warn": 1, "note": 2}


def audit_doc(doc, out_root=OUT, min_sev="warn", ctx=None):
    """对单篇跑全部规则。返回 {doc, findings, level}。"""
    c = ctx or Ctx(doc, out_root)
    if not c.m:
        return {"doc": doc, "findings": [], "level": "skip", "label": "无 metadata"}
    lim = SEV_ORDER.get(min_sev, 1)
    out = []
    for code, field, sev, fn in RULES:
        if SEV_ORDER[sev] > lim:
            continue
        try:
            got = fn(c) or []
        except Exception as e:                      # 一条规则崩了不能拖垮整篇
            got = [{"code": code, "value": "", "msg": "规则异常: %s" % e, "evidence": ""}]
        for g in got:
            g.update({"field": field, "sev": sev, "doc": doc})
            out.append(g)
    out.sort(key=lambda x: (SEV_ORDER[x["sev"]], x["code"]))
    level = "ok"
    if any(x["sev"] == "bad" for x in out):
        level = "bad"
    elif any(x["sev"] == "warn" for x in out):
        level = "warn"
    elif out:
        level = "note"
    return {"doc": doc, "findings": out, "level": level}


def audit_all(out_root=OUT, min_sev="warn"):
    rows = []
    for d in sorted(os.listdir(out_root)):
        if os.path.isdir(os.path.join(out_root, d)) and os.path.isfile(
                os.path.join(out_root, d, "metadata.json")):
            rows.append(audit_doc(d, out_root, min_sev))
    return rows


def summarize(rows):
    dist = collections.Counter(r["level"] for r in rows)
    bycode = collections.Counter(f["code"] for r in rows for f in r["findings"])
    return {"docs": len(rows), "dist": dict(dist), "by_code": dict(bycode)}


def write_html(rows, fp):
    sev_col = {"bad": "#c0392b", "warn": "#b7791f", "note": "#2b6cb0"}
    body = []
    for r in rows:
        if not r["findings"]:
            continue
        body.append("<h3>%s <span style='font-size:12px;color:#666'>%s</span></h3>"
                    % (r["doc"], r["level"]))
        body.append("<table><tr><th>级别</th><th>规则</th><th>字段</th>"
                    "<th>值</th><th>判据</th><th>实证</th></tr>")
        for f in r["findings"]:
            body.append("<tr><td style='color:%s'>%s</td><td><code>%s</code></td><td>%s</td>"
                        "<td>%s</td><td>%s</td><td style='color:#666'>%s</td></tr>"
                        % (sev_col.get(f["sev"], "#333"), f["sev"], f["code"], f["field"],
                           _esc(f["value"]), _esc(f["msg"]), _esc(f["evidence"])))
        body.append("</table>")
    if not body:
        body.append("<p style='color:#2f855a'>无发现: 所有字段值通过形态/来源/跨字段/离群判据。</p>")
    html = ("<!doctype html><meta charset='utf-8'><title>字段值后验</title>"
            "<style>body{font:14px/1.6 -apple-system,'Segoe UI',sans-serif;margin:28px;"
            "max-width:1100px;color:#1a202c}h3{margin:22px 0 6px}"
            "table{border-collapse:collapse;width:100%%;margin-bottom:8px}"
            "th,td{border-bottom:1px solid #e2e8f0;padding:6px 8px;text-align:left;"
            "vertical-align:top}th{background:#f7fafc;font-weight:600}"
            "code{background:#f1f5f9;padding:1px 5px;border-radius:3px}"
            "</style><h2>字段值后验报告</h2><p style='color:#666'>质检判'在不在', "
            "本报告判'值对不对'。生成于 %s</p>%s"
            % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "\n".join(body)))
    io.open(fp, "w", encoding="utf-8").write(html)


def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


# ---------------------------------------------------------------------------
# 规则回归: 用 4 例真实错值 + 4 例合法值夹逼, 保证"报了就该改、不该报的别报"
# ---------------------------------------------------------------------------

def _selftest():
    ok = fail = 0

    def chk(name, hit, ctx, want_codes, min_sev="warn"):
        nonlocal ok, fail
        r = audit_doc(None, out_root=None, min_sev=min_sev, ctx=ctx)
        codes = {f["code"] for f in r["findings"]}
        good = bool(codes & set(want_codes)) if want_codes else not codes
        if good:
            ok += 1
        else:
            fail += 1
            print("FAIL [%s] 命中=%s 期望=%s" % (name, sorted(codes), want_codes))

    # ① 13563535 原错值: 摘要块进作者区 + 英题当 roman
    c = Ctx("s1", None, meta={
        "title": {"value": "유씨삼대록, 겸산 유숙기, 그리고 완산 이씨"},
        "authors": [{"name": "이윤석", "roman": "Korean Old Novel Yussi-Samdaerok"},
                    {"name": "유씨삼대록", "roman": "Yu Suk-gi"}],
        "affiliations": ["연세대학교 국어국문학과"]}, corpus="")
    chk("13563535-作者", True, c, ["V-AUTH-ROMA-IN-TITLE"])

    # ② 13275609 原错值: 目录条目进作者
    c = Ctx("s2", None, meta={
        "title": {"value": "국제법상 영역권원으로서 실효적 점유와 독도에 관한 연구"},
        "authors": [{"name": "민영훈"}, {"name": "Ⅰ. 서론"}],
        "affiliations": ["한국해양수산개발원"]}, corpus="")
    chk("13275609-作者", True, c, ["V-AUTH-SECNO"])

    # ③ 2632491 原错值: 副标题当署名
    c = Ctx("s3", None, meta={
        "title": {"value": "한국소설에 나타난 음식 모티프와 가족 관계의 의미 양상"},
        "authors": [{"name": "- 권여선"}, {"name": "은희경"}, {"name": "이신조"}],
        "affiliations": ["한양여자대학교 문예창작과"]}, corpus="")
    chk("2632491-作者", True, c, ["V-AUTH-SYM"])

    # ④ 1536751 原错值: 正文当题名 + 초점/주제 当作者
    c = Ctx("s4", None, meta={
        "title": {"value": "통합문법의 틀에서 담화의 기능의 일부인 주제·초점 실현과 지시대상물에 "
                            "대한 화자의 시점을 기술하여 화용과 운율의 상호 접촉을 밝히는 것이 "
                            "이 글의 취지이다."},
        "authors": [{"name": "초점"}, {"name": "주제"}, {"name": "초점"}],
        "affiliations": ["서울대학교"]}, corpus="")
    chk("1536751-题名", True, c, ["V-TITLE-SENT"])
    chk("1536751-作者", True, c, ["V-AUTH-STOP", "V-AUTH-DUP"])

    # ⑤ 6 条垃圾机构(2026-11-12 二轮实证)
    for i, bad in enumerate([u"Oxford University Press, 2012",
                             u"관무불가침 세계관과 동아시아 질서",
                             u"본고는 한국어교육의 일환으로 진행되었다",
                             u"교육과학기술부"]):
        c = Ctx("s5-%d" % i, None, meta={
            "title": {"value": "한국어 교육 과정 연구"},
            "authors": [{"name": "홍길동"}],
            "affiliations": [bad]}, corpus="")
        chk("垃圾机构-%d" % i, True, c,
            ["V-AFF-PRESS", "V-AFF-NOTAFF", "V-AFF-SENT"])

    # ⑥ 合法值不能报(精度夹逼): 体例正确的 4 篇
    legit = [
        ("l1", {"title": {"value": "통합문법: 주제·초점과 시점"},
                "authors": [{"name": "장석진", "affiliation": "서울대학교"}],
                "affiliations": ["서울대학교"], "year": {"value": "2000"},
                "volume": {"value": "12"}, "issue": {"value": "2"},
                "pages": {"value": "145-175"},
                "abstract": {"value": "이 글은 통합문법의 틀에서 담화 기능의 일부인 주제와 초점, "
                                       "그리고 지시대상물에 대한 화자의 시점을 기술하는 것을 목적으로 "
                                       "한다. 아울러 화용과 운율, 화용과 어휘의 상호 제약을 논한다."},
                "keywords": ["통합문법", "주제", "초점", "시점"]},
         "12권 2호 Vol. 12 No. 2"),
        ("l2", {"title": {"value": "한국소설에 나타난 음식 모티프와 가족 관계의 의미 양상"},
                "authors": [{"name": "이신조", "affiliation": "한양여자대학교 문예창작과"}],
                "affiliations": ["한양여자대학교 문예창작과"], "year": {"value": "2020"},
                "volume": {"value": "67"}, "issue": {"value": "67"},
                "pages": {"value": "109-140"},
                "abstract": {"value": "최근 음식에 대한 담론이 활발히 진행되는 가운데 음식은 생존의 "
                                       "절대적 수단일 뿐만 아니라 가족 서사를 구성하는 매개로 기능한다. "
                                       "이 글은 세 편의 단편소설을 통해 그 의미를 탐색한다."},
                "keywords": ["음식 모티프", "가족", "모성"]},
         "제67집 67호"),
        ("l3", {"title": {"value": "Characteristics of vegetation succession on Pinus thunbergii"},
                "authors": [{"name": "Yongsik Hong"}, {"name": "Euijoo Kim"}],
                "affiliations": ["Korea Environmental Preservation Association"],
                "year": {"value": "2019"}, "volume": {"value": "43"},
                "pages": {"value": "44"},
                "abstract": {"value": "We investigated vegetation succession on Pinus thunbergii "
                                       "forests in warm temperate regions of Jeju Island over a "
                                       "period of several decades and report the main trends."},
                "keywords": ["vegetation", "succession", "Pinus thunbergii"]},
         "Vol. 43"),
        ("l4", {"title": {"value": "국제법상 영역권원으로서 실효적 점유와 독도에 관한 연구"},
                "authors": [{"name": "민영훈", "affiliation": "한국해양수산개발원"}],
                "affiliations": ["한국해양수산개발원"], "year": {"value": "2025"},
                "volume": {"value": "9"}, "issue": {"value": "1"},
                "pages": {"value": "1-30"},
                "abstract": {"value": "일본이 독도에 대한 자국 영유권 주장을 강화하는 가운데 이에 대한 "
                                       "우리나라의 대응 정책이 적정한가에 관한 의문이 있다. 이 글은 "
                                       "국제법상 실효적 점유의 요건을 검토한다."},
                "keywords": ["독도", "실효적 점유", "영역권원"]},
         "제9권 제1호"),
    ]
    for doc, meta, corpus in legit:
        c = Ctx(doc, None, meta=meta, corpus=corpus)
        chk("合法-%s" % doc, False, c, [])

    # ⑦ note 级规则也要有夹逼: 残句摘要必须报出来(本库摘要最短 350, 阈值 200)
    c = Ctx("n1", None, meta={
        "title": {"value": "한국어 교육 과정 연구"},
        "authors": [{"name": "홍길동"}],
        "affiliations": ["서울대학교"],
        "abstract": {"value": "요약."},
        "keywords": ["교육"]}, corpus="")
    chk("残句摘要", True, c, ["V-ABS-SHORT"], min_sev="note")

    print("audit_value selftest: %d/%d PASS" % (ok, ok + fail))
    return 0 if fail == 0 else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv):
    if "--selftest" in argv:
        return _selftest()
    min_sev = "note" if "--all" in argv else "warn"
    # --html 后面那个参数是输出路径, 不是文档名(否则它会被当成一篇待审文档)
    _drop = set()
    if "--html" in argv:
        _i = argv.index("--html")
        if _i + 1 < len(argv) and not argv[_i + 1].startswith("-"):
            _drop.add(argv[_i + 1])
    docs = [a for a in argv if not a.startswith("-") and a not in _drop]
    rows = ([audit_doc(d, OUT, min_sev) for d in docs] if docs
            else audit_all(OUT, min_sev))
    if "--json" in argv:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        for r in rows:
            if not r["findings"]:
                continue
            print("%-26s %s" % (r["doc"], r["level"]))
            for f in r["findings"]:
                print("   [%s] %-20s %-6s %s" % (f["sev"], f["code"], f["field"], f["msg"]))
                print("        值: %s" % str(f["value"])[:70])
        s = summarize(rows)
        print("\n汇总: %d 篇  %s" % (s["docs"], s["dist"]))
        if s["by_code"]:
            print("按规则: " + "  ".join("%s=%d" % kv for kv in
                                     sorted(s["by_code"].items(), key=lambda x: -x[1])))
    if "--html" in argv:
        fp = argv[argv.index("--html") + 1] if len(argv) > argv.index("--html") + 1 \
            else os.path.join(OUT, "_value_audit.html")
        write_html(rows, fp)
        print("报告: %s" % fp)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
