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

代码组织: 一个字段一个 `@checks` 函数, 函数内**一遍取值、顺序走完该字段的全部谓词**——
同字段的 `names()`/`affils()`/`_fold()` 只算一次, 不再每条规则重扫一遍。
码(code)仍逐条独立、严重度走 `SEV` 一张表, 所以诊断粒度、抑制粒度、报告口径都不变。

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

    def __init__(self, doc, out_root=OUT, meta=None, corpus=None, ner=None, xch=None):
        self.doc = doc
        self.dd = os.path.join(out_root, doc) if (doc and out_root) else ""
        self._m = meta
        self._corpus = corpus
        self._ner = ner
        self._xch = xch
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
    def xch(self):
        """三渠道**原始**读法 {渠道: 全文}。

        与 corpus 的区别是本判据存在的全部理由: `unified/` 是"每区域挑一家"的共识层,
        挑错了就无从发现——13563530 的题名取 dots 的错读, unified 里就只有错读那一份。
        原文只有一份却读了三遍, 三份读法互为校验, 这是唯一能抓到"单渠道读错"的账本。
        """
        if self._xch is None:
            d = {}
            for _p, ch, t in (channel_lines(self.dd) if self.dd else []):
                d[ch] = (d.get(ch, "") + "\n" + t) if ch in d else t
            self._xch = d
        return self._xch

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
# 登记: 码 → 严重度一张表 + 一个字段一个判据函数
# ---------------------------------------------------------------------------

SEV = {
    # 标题
    "V-TITLE-SENT": "bad", "V-TITLE-LONG": "warn",
    "V-TITLE-IS-BODY": "bad", "V-TITLE-HEADER": "warn",
    # 作者
    "V-AUTH-SYM": "bad", "V-AUTH-SECNO": "bad", "V-AUTH-STOP": "bad",
    "V-AUTH-INST": "bad", "V-AUTH-PHRASE": "bad", "V-AUTH-PUNCT": "warn",
    "V-AUTH-DUP": "warn", "V-AUTH-ROMA-IN-TITLE": "bad",
    "V-AUTH-IN-TITLE": "warn", "V-AUTH-SHARE-AFFIL": "note",
    # 机构
    "V-AFF-NOTAFF": "bad", "V-AFF-PRESS": "bad", "V-AFF-SENT": "bad",
    "V-AFF-LONG": "warn", "V-AFF-IN-TITLE": "bad", "V-AFF-CONTAIN": "note",
    "V-AFF-NER-DISJOINT": "note", "V-AFF-COUNT-VS-AUTH": "warn",
    # 数值 / 区段
    "V-YEAR-RANGE": "bad", "V-VOL-CTX": "warn", "V-ISS-CTX": "warn",
    "V-PAGES-FMT": "warn", "V-PAGES-SPAN": "warn",
    "V-KW-COUNT": "warn", "V-KW-SENT": "warn",
    "V-ABS-PREFIX": "warn", "V-ABS-SHORT": "note",
    # 跨渠道互校
    "V-XCH-TYPO": "bad",
}

FIELD_CHECKS = []       # [(field, fn)]


def checks(field):
    """登记一个字段的判据函数。函数内一遍取值, 顺序走完该字段的全部谓词。"""
    def deco(fn):
        FIELD_CHECKS.append((field, fn))
        return fn
    return deco


def _f(code, value, msg, evidence=""):
    # 严重度随码走, 不再由登记处携带 —— 改级只动上面那张表
    return {"code": code, "value": value, "msg": msg, "evidence": evidence,
            "sev": SEV[code]}


# ============================ ① 题名 =======================================

@checks("标题")
def _title(c):
    """题名: 不结句 / 不超长 / 不是页眉 / 不整段来自正文块。"""
    t = _txt(c.val("title")).strip()
    if not t:
        return []
    out = []

    # 不结句 —— 1536751 实证: 整栏正文段被当题名(197 字, 以 '…취지이다.' 收尾)
    if _SENT.search(t) or re.findall(u"[다요음임]\\.\\s", t):
        out.append(_f("V-TITLE-SENT", t[:60],
                      "题名以句末终结词收尾/含句内句号, 更像正文段落",
                      "1536751: 真题名 '통합문법: 주제·초점과 시점' 被正文段压过"))

    # 长度离群 —— 语料内韩文题名普遍 15–60 折合字; 超 120 基本是段落
    f = _fold(t)
    if len(f) > 120:
        out.append(_f("V-TITLE-LONG", "%d 字" % len(f),
                      "题名折合长度 %d > 120, 超常识区间" % len(f)))

    # 是 running 页眉(刊名+卷期+日期)
    if re.search(u"(?:제?\\s*\\d+\\s*권|Vol\\.?\\s*\\d+|No\\.?\\s*\\d+|통권|\\|)", t) \
            and re.search(u"(?:19|20)\\d{2}", t):
        out.append(_f("V-TITLE-HEADER", t[:70], "题名含卷期+年份且带竖线, 像页眉而非题名"))

    # 整段出现在某个正文块里(≥60 字重叠) —— 取到了正文而非题名
    if len(f) >= 60:
        probe = f[:80]
        for j in c.pages[:2]:
            for u in j.get("unified") or []:
                if u.get("type") not in ("Text", "List", "Table"):
                    continue
                body = _fold(u.get("main_text") or "")
                if len(body) >= 60 and (probe[:40] in body or body[:40] in f):
                    out.append(_f("V-TITLE-IS-BODY", probe[:50],
                                  "题名与正文块 %s 高度重叠(取到了正文)" % u.get("uid"),
                                  "1536751 实证"))
                    return out
    return out


# ============================ ② 作者 =======================================

@checks("作者")
def _authors(c):
    """作者: 一遍扫署名, 走完形态/来源/重复, 再走 roman 与机构绑定两条跨槽判据。"""
    names, aus = c.names(), c.authors()
    t = _fold(_txt(c.val("title")))
    t_en = _fold(_txt(c.val("title_en")))
    out = []

    for n in names:
        # 前导破折号/尾随冒号 —— 2632491 实证: '- 권여선'(副标题行)被当署名
        if re.match(u"^[\\-–—•·*+]+\\s*", n) or re.search(u"[:;,]\\s*$", n):
            out.append(_f("V-AUTH-SYM", n, "作者名带前导/尾随符号",
                          "2632491: 副标题 '- 권여선, 은희경…' 被当署名"))
        # 章节编号/目录条目 —— 13275609 实证: 'Ⅰ. 서론'
        if _SECNO.match(n):
            out.append(_f("V-AUTH-SECNO", n, "作者名是章节编号/目录条目",
                          "13275609: 'Ⅰ. 서론'"))
        # 区段词 —— 1536751 实证: ['초점','주제','초점'] 来自正文 '주제·초점'
        if _STOPWORD.match(n):
            out.append(_f("V-AUTH-STOP", n, "作者名是区段头/目录词",
                          "1536751: 초점/주제"))
        # 机构行被当署名
        if _INST_TAIL.search(n) or _INST_BAD.search(n):
            out.append(_f("V-AUTH-INST", n, "作者名含机构/出版社词, 机构行被当署名"))
        # 是题名的子串 —— 13563535 实证: 유씨삼대록/유숙기
        # 只查 ≥5 折合字: 3 音节韩文名与题名撞词是正常现象(如作者 이상 + 题名 '이상 문학')
        if len(t) >= 10 and len(_fold(n)) >= 5 and _fold(n) in t:
            out.append(_f("V-AUTH-IN-TITLE", n, "作者名是题名的子串",
                          "13563535: '유씨삼대록' 是题名首词"))
        # 短语/长串, 非人名形态; 한글이름(漢字) 是体例, 两条都放行
        if not _NAME_HANJA.match(n):
            f = _fold(n)
            if re.search(u"[가-힣]\\s+[가-힣]", n) or len(f) > 20 \
                    or (_hangul_ratio(n) < 0.2 and len(n.split()) > 5):
                out.append(_f("V-AUTH-PHRASE", n[:50], "作者名是短语/长串, 非人名形态"))
            if _NAME_BAD_PUNCT.search(n):
                out.append(_f("V-AUTH-PUNCT", n[:50], "作者名含标点, 像从长行里切坏的片段"))

    # 同一作者重复 —— 切分把同一行的词切了两遍
    cnt = collections.Counter(_fold(n) for n in names if n)
    for k, v in cnt.items():
        if v >= 2:
            out.append(_f("V-AUTH-DUP", k, "同一作者出现 %d 次" % v))

    # roman 槽里放的是"英文题名"而不是人名 ——
    # 13563535 实证: roman='Korean Old Novel Yussi-Samdaerok'(英题) 被当 이윤석 的罗马拼音。
    # 人名罗马化在折叠后 ≤26 字符且 ≤3 词; 题名切片远长于此, 或带英文虚词, 或直接是 title_en。
    for a in aus:
        raw = (a.get("roman") or "").strip()
        r = _fold(raw)
        if len(r) < 8:
            continue
        why = ""
        if t_en and r in t_en:
            why = "roman 就是 title_en 的一部分"
        elif len(r) > 26 or len(raw.split()) > 3:
            why = "roman 折叠 %d 字/%d 词, 超出人名罗马化形态" % (len(r), len(raw.split()))
        elif re.search(r"\b(?:and|of|the|for|with|from)\b", raw.lower()):
            why = "roman 含英文虚词"
        if why:
            out.append(_f("V-AUTH-ROMA-IN-TITLE", raw, why,
                          "13563535: 'Korean Old Novel Yussi-Samdaerok'"))

    # 多个作者共享同一 affiliation 且完全一致 —— 常是"全部绑到第一条"的痕迹
    aff = [str(a.get("affiliation") or "").strip() for a in aus]
    aff = [x for x in aff if x]
    if len(aff) >= 3 and len(set(aff)) == 1:
        out.append(_f("V-AUTH-SHARE-AFFIL", aff[0],
                      "%d 位作者全部绑同一机构, 疑似序位兜底硬配" % len(aff)))
    return out


# ============================ ③ 机构 =======================================

@checks("机构")
def _affiliations(c):
    """机构: 一遍扫每条机构, 再走包含关系、NER 互证、作者数对不上三条整体判据。"""
    affs = c.affils()
    t = _fold(_txt(c.val("title")))
    out = []

    for a in affs:
        f = _fold(a)
        # 认不出机构尾词 —— 24 条眼检实证: 论文题名/摘要句被当机构
        if not _INST_TAIL.search(a):
            out.append(_f("V-AFF-NOTAFF", a[:60], "机构里认不出机构尾词"))
        # 是出版社/项目名 —— 13563530 实证: 'Oxford University Press, 2012'
        if _INST_BAD.search(a):
            out.append(_f("V-AFF-PRESS", a[:60], "机构是出版社/项目名, 来自引文条目",
                          "13563530: 'Oxford University Press, 2012'"))
        # 是句子 —— 13563531/13563533 实证: 致谢句 '…일환으로' 被当机构
        if _SENT.search(a):
            out.append(_f("V-AFF-SENT", a[:60], "机构是完整句子(摘要/致谢句)",
                          "13563531/13563533: 致谢句"))
        # 长度离群 —— 机构署名普遍 6–60 字
        if len(f) > 80:
            out.append(_f("V-AFF-LONG", "%d 字" % len(f), "机构折合长度 > 80, 超常识区间"))
        # 是题名的一部分 —— 13563526 实证: '관무불가침 세계관…'
        if len(f) >= 8 and f in t:
            out.append(_f("V-AFF-IN-TITLE", a[:50], "机构是题名的一部分",
                          "13563526: '관무불가침 세계관…'"))

    # 一条包含另一条 —— 冗余, 且通常是"长的那条被硬切"的痕迹
    for i, a in enumerate(affs):
        for j, b in enumerate(affs):
            if i != j and len(_fold(a)) > len(_fold(b)) and _fold(b) in _fold(a):
                out.append(_f("V-AFF-CONTAIN", b, "被 %r 完全包含(冗余)" % a[:30]))

    # 与 NER 的 org 实体零交集 —— 两个独立源都不认, 值得看一眼(非错, 只是提示)
    orgs = [o for o in c.ner_orgs if len(_fold(o)) >= 4]
    if orgs and affs:
        blob = _fold(" ".join(affs))
        hit = [o for o in orgs if any(w in blob for w in _fold(o).split()) and len(_fold(o)) >= 6]
        if not hit:
            out.append(_f("V-AFF-NER-DISJOINT", " / ".join(affs)[:50],
                          "NER 认出 org 但与本篇机构无交集(两源互不印证)"))

    # 作者数 > 机构数 且 无上标标记 —— 机构配对的序位兜底可能错
    aus = c.authors()
    if len(aus) >= 3 and len(affs) == 1 and not any(a.get("markers") for a in aus):
        out.append(_f("V-AFF-COUNT-VS-AUTH", "%d 作者 / 1 机构" % len(aus),
                      "多作者只配到一个机构且无上标标记, 疑似全部绑第一条"))
    return out


# ============================ ④ 数值/区段 ==================================

@checks("出版年")
def _year(c):
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


@checks("卷")
def _volume(c):
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


@checks("期")
def _issue(c):
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


@checks("页码")
def _pages(c):
    """页码: 形态 + 跨度与 PDF 实际页数是否矛盾(跨页上限按每页 2 栏放宽)。"""
    v = _txt(c.val("pages"))
    if not v:
        return []
    out = []
    if not _PAGES_OK.match(v):
        out.append(_f("V-PAGES-FMT", v, "页码不是 'n' 或 'n-m' 形"))
    m = _PAGES_RANGE.match(v)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        npage = len(c.pages)
        if a > b:
            out.append(_f("V-PAGES-SPAN", v, "起始页 %d > 结束页 %d" % (a, b)))
        elif npage and (b - a + 1) > npage * 2 + 2:
            out.append(_f("V-PAGES-SPAN", v,
                          "跨度 %d 页 > 文档实际 %d 页的 2 倍" % (b - a + 1, npage)))
    return out


@checks("关键词")
def _keywords(c):
    """关键词: 数量离群 + 混进摘要切片。"""
    kw = _s(c.val("keywords"))
    if not isinstance(kw, list) or not kw:
        return []
    out = []
    if len(kw) > 12:
        out.append(_f("V-KW-COUNT", "%d 个" % len(kw),
                      "关键词数 %d > 12, 可能把正文或摘要切碎收进来了" % len(kw)))
    for k in kw:
        s = str(k or "").strip()
        if len(_fold(s)) > 40 or re.search(u"(?:다|요)\\.\\s*$", s) \
                or re.search(u"[。！？!?]", s):
            out.append(_f("V-KW-SENT", s[:40], "关键词过长/带句子标点, 像摘要切片"))
    return out


@checks("摘要")
def _abstract(c):
    """摘要: 不以区段头残留开头 + 长度不像残句。
    实测本库 21 篇摘要折合长度 min=350 / 中位=551, 低于 200 基本是残句。"""
    a = _txt(c.val("abstract")).strip()
    if not a:
        return []
    out = []
    head = a[:24]
    if re.match(u"^[\\*\\s]*(?:국문\\s*초록|국문\\s*요약|영문\\s*초록|영문\\s*요약|초\\s*록|"
                u"Abstract|ABSTRACT|요\\s*약)\\s*[*:：]?", head) or head.startswith("**"):
        out.append(_f("V-ABS-PREFIX", head, "摘要以区段头残留开头"))
    n = len(_fold(a))
    if n < 200:
        out.append(_f("V-ABS-SHORT", "%d 字" % n,
                      "摘要折合长度 %d < 200, 像残句而非摘要" % n))
    return out


# ============================ ⑤ 跨渠道互校 =================================

# 只清真正的 HTML 标签。**不能**用通配 `<[^>]+>`——题名里的 <케이팝 데몬 헌터스>
# 是书名号, 通配会把书名整体删掉, 于是"题名里的词在三渠道都找不到"这条判据失效
# (2026-11-13 实测: 通配版全库 0 命中, 漏掉了唯一的真错字)。
_XCH_TAG = re.compile(r"</?(?:sup|sub|i|b|em|strong|span|br|p|div|table|tr|td|th|u|small)\b[^>]*>",
                      re.I)
_XCH_SPLIT = re.compile(u"[\\s,;:·()\\[\\]{}<>\"'“”‘’「」『』《》〈〉、，。.!?/\\\\|~\\-–—]+")


def _xch_tokens(s):
    """短字段切实词。长度 <3 的不查。

    阈值取 3 而不是 4: 韩文实词多数只有 2–3 个音节(고전/서사/케이팝), 取 4 会把
    `케이팝` 本身滤掉, 判据就永远看不见它该抓的那条(2026-11-13 实测: len≥4 全库 0 命中)。
    """
    s = _XCH_TAG.sub(" ", s or "")
    out = []
    for t in _XCH_SPLIT.split(s):
        if len(t) < 3 or not re.search(u"[가-힣A-Za-z]", t):
            continue
        out.append(t)
    return out


def _xch_near(tok, text, maxn=3):
    """在 text 里找与 tok 等长、仅差 1 个字符的词——OCR 单字符混淆的形态。

    纯大小写差异不算: `Evaporation`/`evaporation` 是两个渠道对同一词的大小写习惯,
    不是错字(895567 实证: 报出来的是噪声)。
    """
    out = []
    L = len(tok)
    if L < 3:
        return out
    # 必须用与取 token 相同的分隔符。`\S+` 会把 `<케이팝` 连尖括号切成 4 个字,
    # 长度对不上, 判据就永远看不见 13563530 那条(2026-11-13 实测)。
    for w in _XCH_SPLIT.split(text or ""):
        if len(w) != L or w == tok:
            continue
        d = [i for i in range(L) if w[i] != tok[i]]
        if len(d) == 1 and w[d[0]].lower() != tok[d[0]].lower():
            out.append(w)
            if len(out) >= maxn:
                break
    return out


def _xch_window(text, anchor, back=80, fwd=220):
    """以 anchor 为锚裁一段窗口。

    **必须按位置比，不能全篇搜**——2026-11-13 实证: `역사상 춘천의 군사적 거점과 가치`
    这个题名，全篇搜会在参考文献书名 `한국역사와 문화` 里找到 `역사와`(差 1 字符),
    于是把一个正确的题名报成错字。裁窗后只在同一处比，误配消失。
    """
    i = (text or "").find(anchor)
    if i < 0:
        return ""
    return text[max(0, i - back): i + len(anchor) + fwd]


# 韩文单字助词。末字只在这几个之间替换 = 语法变化(만들기의/만들기는), 不是错字。
_HAN_TAIL = set(u"이가을를은는의에와과로도만")


def _han_particle_only(a, b):
    """两词仅末字不同、且两末字都是助词 → 判为助词替换, 不是错字。"""
    if len(a) != len(b) or a == b:
        return False
    d = [i for i in range(len(a)) if a[i] != b[i]]
    if len(d) != 1 or d[0] != len(a) - 1:
        return False
    return a[d[0]] in _HAN_TAIL and b[d[0]] in _HAN_TAIL


@checks("跨渠道")
def _xchannel(c):
    """短字段的实词若只有一家这么读、另两家在同一位置读成仅差 1 字符的词 = 该家 OCR 读错。

    2026-11-13 实证: 13563530 题名 `<케이팝 데몬 헌터스>` 的 케이팝 被 dots 读成 케이팔
    (dots 12 次全错), 而 mineru 36 次、paddle 30 次全对; 字段值取的正是 dots 的错读。
    该词 3 个韩文音节、全在字符集内、长度正常 —— 形态/来源/离群三类判据一条都报不出来,
    只有"另外两家在同一位置怎么读"能发现它。

    三道闸, 每道都是被误报打回来的:
      ① 按锚点裁窗 —— 全篇搜会跨位置误配(역사상 撞上参考文献书名里的 역사와)
      ② 另两家必须读成同一个词 —— 单家偶然近邻不算
      ③ 末字助词替换不算 —— 만들기의/만들기는 是语法变化
    摘要不走这条: 长文本空格丢失会让几乎每个词都只落单渠道(全库 58 例全是空格问题)。
    """
    xc = c.xch
    if len(xc) < 3:                       # 三家全在才有"另两家"可依
        return []
    folded = dict((ch, _fold(t)) for ch, t in xc.items())
    vals = [(u"题名", _txt(c.val("title"))), (u"题名(英)", _txt(c.val("title_en")))]
    vals += [(u"作者", n) for n in c.names()]
    vals += [(u"机构", a) for a in c.affils()]
    for k, lab in (("journal", u"刊名"), ("publisher", u"出版者"),
                   ("volume", u"卷"), ("issue", u"期"), ("pages", u"页码"), ("year", u"年")):
        vals.append((lab, _txt(c.val(k))))

    out, seen = [], set()
    for lab, val in vals:
        ts = _xch_tokens(val)
        if not ts:
            continue
        sup = {}
        for t in ts:
            ft = _fold(t)
            sup[t] = [ch for ch, s in folded.items() if ft in s] if ft else []
        # 锚点 = 第一个三家都有的词, 用来在三个渠道里对齐同一位置
        anchor = next((t for t in ts if len(sup[t]) >= 3), None) \
            or next((t for t in ts if len(sup[t]) >= 2), None)
        if not anchor:
            continue
        win = dict((ch, _xch_window(s, anchor)) for ch, s in xc.items())

        for t in ts:
            ft = _fold(t)
            if not ft or (lab, ft) in seen or len(sup[t]) >= 2:
                continue
            seen.add((lab, ft))
            cands = [set(_xch_near(t, win[ch])) for ch in xc if ch not in sup[t] and win[ch]]
            if len(cands) < 2:
                continue
            common = set.intersection(*cands)
            common = set(w for w in common if not _han_particle_only(t, w))
            if not common:
                continue
            best = sorted(common, key=lambda w: -sum(1 for x in cands if w in x))[:2]
            out.append(_f("V-XCH-TYPO", u"%s: %s" % (lab, t),
                          u"%s 的「%s」只有 %s 这么读, 另两家在同一位置读作「%s」—— 单渠道 OCR 错字"
                          % (lab, t, "/".join(sup[t]) or "无渠道", best[0]),
                          u"13563530: dots 读 케이팔 / mineru·paddle 读 케이팝"))
    return out


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------

SEV_ORDER = {"bad": 0, "warn": 1, "note": 2}


def audit_doc(doc, out_root=OUT, min_sev="warn", ctx=None):
    """对单篇跑全部字段判据。返回 {doc, findings, level}。"""
    c = ctx or Ctx(doc, out_root)
    if not c.m:
        return {"doc": doc, "findings": [], "level": "skip", "label": "无 metadata"}
    lim = SEV_ORDER.get(min_sev, 1)
    out = []
    for field, fn in FIELD_CHECKS:
        try:
            got = fn(c) or []
        except Exception as e:                      # 一个字段崩了不能拖垮其余字段
            got = [{"code": "V-FIELD-ERR", "sev": "warn", "value": "", "evidence": "",
                    "msg": "%s判据异常: %s" % (field, e)}]
        for g in got:
            if SEV_ORDER.get(g["sev"], 2) > lim:    # 严重度随码走, 收集后再筛
                continue
            # 键序固定: 报告/JSON 的字段顺序不随登记方式变
            out.append({"code": g["code"], "value": g["value"], "msg": g["msg"],
                        "evidence": g.get("evidence", ""),
                        "field": g.get("field") or field,
                        "sev": g["sev"], "doc": doc})
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

    # ⑧ 三渠道互校: 13563530 原错值(dots 把 케이팝 读成 케이팔, 另两家全对)
    ttl = u"디지털 시대의 고전 서사 교육과 <%s 데몬 헌터스>"
    tail = u" 정보라미 부산대학교 점필재연구소"
    chk("三渠道-单家错字", True,
        Ctx("x1", None, meta={"title": {"value": ttl % u"케이팔"},
                              "authors": [{"name": u"정보라미"}],
                              "affiliations": [u"부산대학교 점필재연구소"]}, corpus="",
            xch={"dots": ttl % u"케이팔" + tail, "mineru": ttl % u"케이팝" + tail,
                 "paddle": ttl % u"케이팝" + tail}),
        ["V-XCH-TYPO"])
    # 三家一致 → 沉默(精度夹逼)
    chk("三渠道-一致", False,
        Ctx("x2", None, meta={"title": {"value": ttl % u"케이팝"},
                              "authors": [{"name": u"정보라미"}],
                              "affiliations": [u"부산대학교 점필재연구소"]}, corpus="",
            xch={"dots": ttl % u"케이팝" + tail, "mineru": ttl % u"케이팝" + tail,
                 "paddle": ttl % u"케이팝" + tail}), [])
    # 末字助词替换(만들기의/만들기는)是语法变化, 不是错字
    tp = u"간이 온도계 %s 개선 방안 연구"
    chk("三渠道-助词替换", False,
        Ctx("x3", None, meta={"title": {"value": tp % u"만들기의"}}, corpus="",
            xch={"mineru": tp % u"만들기의", "dots": tp % u"만들기는",
                 "paddle": tp % u"만들기로"}), [])
    # 跨位置不得误配: 역사상 只在 minera, 而 dots 的 역사와 在参考文献书名里(不同位置)
    chk("三渠道-跨位置", False,
        Ctx("x4", None, meta={"title": {"value": u"역사상 춘천의 군사적 거점과 가치"}}, corpus="",
            xch={"mineru": u"역사상 춘천의 군사적 거점과 가치 유재춘",
                 "dots": u"춘천의 군사적 거점과 가치 <한국역사와 문화> 유재춘",
                 "paddle": u"춘천의 군사적 거점과 가치 유재춘"}), [])

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
