# -*- coding: utf-8 -*-
import os, re, io, json, datetime
from .common import *


# 首页"区段头"识别(2026-11-12 三轮): 作者区必须止于摘要/目录/正文之前, 否则会把摘要正文、
# 目录条目当作者。common.HEAD_EXCLUDE 只管纯文本行首, 认不出 markdown 强调标记包裹的头
# (**국문요약**)与汉字目录头(目 次)——13563535 摘要块、13275609 目录块由此漏进作者区, 实证。
_FRONT_HEAD_RE = re.compile(
    u"^(?:[\\s\\*_#`>\\-–—·•]*)"
    u"(?:(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+|I{1,3}|IV|V|VI{0,3}|\\d{1,2})\\s*[.)．]\\s*)?(?:"
    u"차\\s*례|목\\s*차|目\\s*次|\\bContents\\b|"
    u"요\\s*약|국문\\s*요약|국문\\s*초록|영문\\s*요약|영문\\s*초록|초\\s*록|"
    u"Abstract|ABSTRACT|Keywords?|주제\\s*어|중심\\s*어|핵심\\s*어|"
    u"서\\s*론|Ⅰ\\.|I\\.)", re.I)


# 渠道兜底的三条行判据(2026-11-13 收口): 原先定义在 front_doc 的分支里, 每次调用都重新
# 编译一遍; 提到模块级后同一次运行只编一次, 也便于和 _front_head/_non_affil 对照。
# 题录型行(期刊名/DOI/卷期/页码范围/版权行), 不可能是题名或作者
_NOT_TITLE = re.compile(
    r"(Journal|DOI|doi\.org|www\.|Vol\.|No\.|pp\.|Abstract|Keywords?|"
    r"RESEARCH|Open Access|©|https?://|^\s*Page\s+\d|^\s*\d+\s*[./)])", re.I)
_KO_NAME_CORE = u"[가-힣](?:\\s*[가-힣]){1,4}"          # 2–5 字韩文姓名(允许姓名间空格)
_NAME_KO = re.compile(u"^%s\\s*\\*{0,2}$" % _KO_NAME_CORE)
_NAME_EN = re.compile(r"^[A-Z][a-zA-Z\-]+(?:\s+[A-Z][a-zA-Z\-]+){1,2}\s*\*{0,2}$")

def _front_head(s):
    """该行是不是一个"区段头"(摘要/目录/正文开头)。"""
    s = (s or "").strip()
    if not s:
        return False
    return bool(_FRONT_HEAD_RE.match(s)) or bool(HEAD_EXCLUDE.match(s))


# 章节编号行(13275609 u0_5 'Ⅰ. 서론' 实证): 目录/正文小标题, 永远不是作者名。
_SECTION_NO = re.compile(u"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+|I{1,3}|IV|V|VI{0,3}|\\d{1,2})\\s*[.)．]\\s*\\S")
# 副标题行(2632491 u0_3 实证): '- 권여선, 은희경, 구효서의 단편소설을 중심으로 -'
_SUBTITLE_DASH = re.compile(u"^[\\-–—]\\s*\\S|[\\-–—]\\s*$")


def _pick_title(j, want_hangul):
    """打分制选标题(禁单取 max 字号): Section-header 权重最高 + 垂直位置 + 宽度占比 + 排除词典
    (차례/요약/Keywords…) + running 头(顶部带且命中 doc_heads 者)排除。
    返回 (块, 行号)。英题模式加看次行——'副标题首行/英题次行'同块形态(13563529 u0_4 实证);
    首行规则仍守 1892171 '英题+韩作者行' 同块形态。"""
    H = float(j["h"])
    best, bs, bli = None, -1.0, 0
    for u in j["unified"]:
        if u.get("cross_page", {}).get("role"):
            continue
        if u["type"] in ("Page-header", "Page-footer", "Page-number", "Footnote"):
            continue
        t = (u["main_text"] or "").strip()
        if not t:
            continue
        if t.startswith("<") and "<table" in t:
            continue   # 目录表格(1892171 TOC) 不参与题名
        lines = [x.strip() for x in t.split("\n") if x.strip()]
        cand = [(0, lines[0])]
        if len(lines) > 1:
            cand.append((1, lines[1]))   # 题名可能在次行(短引导行/副标题在首行的形态)
        for li, t in cand:
            f = _fold(t)
            if not (10 <= len(f) <= 220):
                continue
            y1 = u["box"][1] / H
            if not (TITLE_ZONE[0] <= y1 <= (TITLE_ZONE[1] if want_hangul else TITLE_EN_ZONE[1])):
                continue
            if re.match(u"^[가-힣]{2,4}(?:<sup>.*?</sup>|[\\s*†‡§¹²³⁴⁵⁶⁷⁸⁹])*[(,]\\s*[A-Za-z]", t):   # 署名行(김영준(…)/오우림<sup>†</sup>, Woorim Oh)不当题名(13570509/13569378 实证)
                continue
            if (re.match(u"^[가-힣]{2,4}[\\s]+", t) and _looks_affil(t) and len(t.split()) <= 4) or re.match(u"^(?:[가-힣][\\s]+){2,}[가-힣]", t):   # 人名+机构行(유인태 단국대학교式,≤4词)与间隔名单行(백 서 인  당 정)不当题名(13567389/13569913 实证; 真题名如"트럼프 시대 이후 미국 내 중국학과"更长, 词数守卫放行)
                continue
            if re.match(u"^[가-힣\\s]+\\([A-Za-z .\\-']+\\)\\*?$", t):
                continue
            if re.search(u"[가-힣]", t) and (SUP_LATEX.search(t) or "<sup>" in t
                                               or re.search(r"\*{1,5}", t)) \
                    and len(_parse_author_line(t, None, {})) >= 2 \
                    and not re.search(r"\s[:：]\s", t):   # 副题冒号豁免(2026-11-17, 13567059): 带*通讯标记+逗号并列案例名的题名行

                continue
            hr = _hangul_ratio(t)
            has_cjk = bool(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", _fold(t)))
            if want_hangul and hr < 0.3 and not has_cjk:
                continue
            if not want_hangul and (hr > 0.35 or has_cjk or not re.search(r"[A-Za-z]", t)):
                # 整块 CJK 拒收已删(2026-11-18): 韩刊"英题+韩署名"同块是常态, 整块拒把真英题
                # 一并毙掉(413 篇 title_en 变 None 的根因); 行级 hr/CJK/字母守卫已足够。
                continue
            # 题名不结句(1536751 实证): 正文段落以 '…다./…요.' 收尾、宽度又占满整栏, 靠宽度+长度
            # 加分压过真题名(Section-header)当选, 随后整段正文被当题名、正文词被当作者。
            if re.search(u"(?:다|요|음|임|함|됨)\\.\\s*$", t.strip()) \
                    or len(re.findall(u"[다요음임]\\.\\s", t)) >= 1:
                continue
            first = t.strip()[:12]
            if HEAD_EXCLUDE.match(t.strip()) or HEAD_EXCLUDE.match(first):
                continue
            score = 0.0
            score += 5.0 if u["type"] == "Section-header" else 0.5
            score += min(2.0, (u["box"][2] - u["box"][0]) / j["w"] * 3)
            score += 2.0 - abs(y1 - 0.20) * 4     # 垂直位置因子(页 10%-45% 最佳)
            score += min(1.5, len(f) / 80.0)
            if score > bs:
                best, bs, bli = u, score, li
    return best, bli

_AFFIL_TAIL = re.compile(
    u"(?:초빙교수|겸임교수|명예교수|전임교수|부교수|조교수|교수|강사|선임연구원|책임연구원|"
    u"전문연구원|연구원|박사후연구원|박사|대학원생|박사과정|석사과정|(?<=과)\\s*대학원)[\\s,，·•]*$")


# 机构行形态守卫(2026-11-12 二轮): AFFIL_HEAD 里的 '관'(세계관) / '학회' / '학교' 等泛词会命中
# 论文标题、摘要句、致谢句, 整行被当成机构写进 affiliations(13563526/13563529/13563531 实证)。
# 机构署名行必须"以机构词收束"或整行足够短, 且不含句子/致谢特征。
_AFF_STRICT = re.compile(
    u"(?:대학교|대학원|대학|학과|학부|연구소|연구원|역경원|국방부|외교부|기념사업회|서울평양뉴스|연구단|연구재단|교육원|학술원|어학원|개발원|진흥원|"
    u"관리원|센터|박물관|도서관|병원|재단|학회|학교|공단|공사|협회|"
    r"University|College|Institute|Laborator|School\s+of|Department|Faculty|Center|Centre|"
    r"Academy|Hospital|Campus|Association|Society|Foundation|Nunnery|KIDA|KAIST|CMU|국회방송)", re.I)
_AFF_SENT = re.compile(
    u"(?:본고는|본\\s*논문은|이\\s*논문은|밝힙니다|일환으로|지원사업|사의를|감사를|"
    u"수정[·ㆍ]?\\s*보완|발표한\\s*글|논평|토론을\\s*통해|연구\\s*과제|보고서를|"
    u"것임을|때,\\s)")
# 语义排除: 出版社/英文论文题名(含 University 但不是机构署名)
_AFF_BAD = re.compile(
    u"(?:Press\\b|Publishing\\b|출판|\\bProject\\b|\\bChallenges\\b|"
    r"\bCurrent\s+Status\b|\b(?:Study|Analysis|Review)\s+on\b|학회\s*발표|\b발표\b)", re.I)


def _looks_affil(s):
    """整行是否像"机构署名", 而不是标题/摘要/致谢句/出版社。"""
    s = (s or "").strip()
    # 长度界按语种分开: 韩文机构署名很少超 80, 英文机构名常带 "Center … Institute of … Science"
    # 这类叠加(2542944 的 'Forest Technology and Management Research Center National Institute
    # of Forest Science' 共 91 字), 用同一 80 上限会把它整条丢掉。
    _lim = 160 if _hangul_ratio(s) < 0.2 else 80
    if not (3 <= len(s) <= _lim):
        return False
    if _AFF_SENT.search(s) or _AFF_BAD.search(s):
        return False
    if re.match(r"^(?:The|A|An)\s", s) and _hangul_ratio(s) < 0.2:
        return False
    if re.match(r"^[A-Z][a-z]+:\s", s):      # 'Princeton: Princeton University Press, 1990'
        return False
    if s.count(",") >= 3:
        return False
    m = _AFF_STRICT.search(s)
    if not m:
        return False
    if _hangul_ratio(s) < 0.2:
        # 拉丁机构行不收束要求: 可尾随年份/校区/学院名(Institute of Forest Science 2013 实证)
        return True
    last = list(_AFF_STRICT.finditer(s))[-1]
    # 韩文机构署名行以机构词收束, 允许尾随少量职位/括号/标点; 中段出现机构词的
    # ("…한국학과 전공 강좌 개발 및 실행 연구" 这类课题名)一律拒
    return len(s) - last.end() <= 16


_MIL_HEAD = re.compile(
    u"^(?:육군|해군|공군|해병대|국방부|국방기술품질원|군수참모처|정비창|사령부|소방천|합동참모본부)")
_AFF_ROLE_TAIL = re.compile(
    u"\\s*(?:초빙교수|겸임교수|명예교수|전임교수|부교수|조교수|교수|강사|총장|부총장|학장|기획처장|처장|"
    u"원장|실장|팀장|선임연구원|책임연구원|전문연구원|연구원|박사과정|석사과정|대학원생|전임|겸임|초빙)\\S*\\s*$")
_AFF_EN_ROLE = re.compile(
    r"(?:instructor|professor|lecturer|researcher)\s+(?:of|at)\s+(?:the\s+)?(.+)$", re.I)

# —— 前置区段头(2026-11-13 收口): 同一份"关键词/摘要/目录/文献头"词表此前在 front 里被
#    手抄成 3 份——_FRONT_HEAD_RE(区段头判定)、机构兜底的两处行内排除(各 200 字符)。
#    词表只留一处来源, 用途仍分开: _front_head() 判"这行是区段头", _non_affil() 判
#    "这行不可能是机构署名"(多算文献头/网址/制表符)。
_NON_AFFIL = re.compile(
    u"^(?:[\\s\\*_#`>\\-–—·•]*)"
    u"(?:차\\s*례|목\\s*차|目\\s*次|\\bContents\\b|"
    u"요\\s*약|국문\\s*요약|국문\\s*초록|영문\\s*요약|영문\\s*초록|초\\s*록|"
    u"Abstract|ABSTRACT|Key\\s*words?|Keywords?|키\\s*워드|주제\\s*어|중심\\s*어|핵심\\s*어|"
    u"참\\s*고\\s*문\\s*헌|References?|Bibliography)", re.I)


def _non_affil(s):
    """该行是"前置区段头/文献头/网址", 不可能是机构署名。"""
    s = (s or "").strip()
    return bool(_NON_AFFIL.match(s)) or bool(re.search(r"doi\.org|https?://|\t", s, re.I))


def _split_affils(s):
    s = re.sub(u"(?:접수일|수정일|게재확정일)\\s*\\([^)]*\\)\\s*", "", s or "")
    """一行常塞多条署名, 也常把作者名/职位句拼在一起:
       '● 우송대학교 한국어교육원 초빙교수(제1저자) | ●● 청주대학교 교양대학 조교수(교신저자)'(13563528)
       '강원대학교 기획처장. 강원대학교 법학전문대학원 교수었다.'(13563533)
       'Jung Byung-joon (…) is an instructor of the Mokpo National University.'(KCI_FI001004818_2002)
    逐段剥标记/括号/职位词/前缀, 只留过 _looks_affil 的段。"""
    out = []
    for part in re.split(r"\s*[|｜]\s*", s or ""):
        for seg in re.split(r"(?<=[.。])\s+|\s*;\s*", part):
            seg = re.sub(r"^[*•●○※\d\)\s]+", "", seg).strip()
            seg = re.sub(r"\$\s*\^\s*\{?\s*\d+\s*\}?\s*\$", "", seg).strip()      # BMC 上标 '$^{1}$Korea …'
            m2 = re.match(u"^[가-힣]{2,5}\\s*[(（]([^)）]{4,80})[)）]\\s*$", seg)
            if m2:                                     # '안효성(대구대 자유전공학부 조교수)' → 括号内
                seg = m2.group(1).strip()
            elif re.match(u"^[(（][^)）]{3,80}[)）]\\s*$", seg):
                seg = seg.strip(u"()（） ")             # '장석진 | (서울대학교)' 的纯括号机构段
            seg = re.sub(u"[(（][^)）]{0,40}[)）]", "", seg).strip()
            if seg.count("(") + seg.count(u"（") != seg.count(")") + seg.count(u"）"):
                seg = re.split(u"[(（]", seg)[0].strip()   # 未闭合左括号(长英文校名) → 截断
            m = _AFF_EN_ROLE.search(seg)
            if m:
                seg = m.group(1).strip().rstrip(".")
            seg = re.sub(r"^(?:전|前|현)\s+", "", seg).strip()
            seg = _AFF_ROLE_TAIL.sub("", seg).strip(" ,·•.（）()")
            if "," in seg:
                if _hangul_ratio(seg) < 0.2:
                    # 拉丁机构名的逗号分隔的是"单位 / 城市 / 邮编 / 国名", 逐段切会把一个单位
                    # 劈成两个(2542944 实证: 'Department of Life Science, Kongju National
                    # University' 被切成两半)。只从右剥掉不含机构词的尾巴, 保留单位内部逗号。
                    _parts = [x.strip() for x in seg.split(",")]
                    while len(_parts) > 1 and not _AFF_STRICT.search(_parts[-1] or ""):
                        _parts.pop()
                    _latin = ", ".join(_parts).strip(" .")
                    if _latin and _looks_affil(_latin) and _latin not in out:
                        out.append(_latin)
                    continue
                # 逗号串: 逐段剥职位后只留合法机构段(出版社年份/人名/职称尾巴混排实证)
                got2 = []
                for x in re.split(r"\s*,\s*", seg):
                    x = re.sub(r"^(?:전|前|현)\s+", "", x.strip())
                    x = _AFF_ROLE_TAIL.sub("", x).strip(" ,·•.（）()")
                    if x and _looks_affil(x) and x not in got2:
                        got2.append(x)
                for x in got2:
                    if x not in out:
                        out.append(x)
                if got2:
                    continue
            if seg and _looks_affil(seg) and seg not in out:
                out.append(seg)
    return out


def _strip_affil_tail(s):
    """机构行尾职位/学籍词循环剥离; 剥完必须仍命中机构词表, 防误伤'한국교육연구원'这类机构名。
    (2026-11-12: '우송대학교 한국어교육원 초빙교수'→'우송대학교 한국어교육원' 实证)"""
    s = (s or "").strip()
    s = re.sub(u"[\\(（]\\s*[\\)）]", "", s)   # 角色词剥除后的空括号残留
    while True:
        ns = _AFFIL_TAIL.sub("", s).rstrip(" ,·•")
        if ns == s or not AFFIL_HEAD.search(ns):
            return s
        s = ns


_AUTHOR_BULLET_SUP = re.compile(r"(?:\$\s*)?\^\{([•●*]{1,4})\}(?:\s*\$)?")
_BULLET_NOTE_LINE = re.compile(r"^([•●*]{1,4})\s*(.+)$")


def _marker_key(s):
    s = (s or "").strip().rstrip(")")
    if s and all(c in "•●*" for c in s):
        return "*" * len(s)
    return s


def _channel_affil_notes(u):
    """主读法把多条机构注的标记都丢掉时，才从渠道读法补回标记。"""
    lines = [ln.strip() for ln in (u.get("main_text") or "").split("\n")
             if AFFIL_HEAD.search(ln)]
    if len(lines) < 2 or any(_BULLET_NOTE_LINE.match(ln) for ln in lines):
        return {}
    out = {}
    for r in u.get("readings") or []:
        for ln in (r.get("text") or "").split("\n"):
            m = _BULLET_NOTE_LINE.match(ln.strip())
            if not m or not AFFIL_HEAD.search(m.group(2)):
                continue
            if _split_affils(m.group(2)):
                out[_marker_key(m.group(1))] = m.group(2).strip()
    return out


_CJK_AUTHOR_LINE = re.compile(
    r"^([\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]{2,4}(?:\s*[·•]\s*"
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]{2,4})*)\s*"
    r"(?:\$\s*\^\s*\{[^}]+\}\s*\$|\*{1,4})\s*$")
_INVALID_AUTHOR = re.compile(
    r"^(?:[■◆]\s*)?(?:국문초록|국문요약|논문요약|요약|摘要|차례|目次|저자|출판사|출판연도|분량|접수일|수정일|게재확정일|국방과\s*보안|첫째|둘째|셋째)\s*[:：]?")


def _cjk_author_names(line):
    m = _CJK_AUTHOR_LINE.match((line or "").strip())
    if not m:
        return []
    return [x.strip() for x in re.split(r"\s*[·•]\s*", m.group(1)) if x.strip()]


def _valid_author_name(name):
    name = (name or "").strip()
    return bool(name) and not re.match(r"^(?:www\.|\$)", name) and not _INVALID_AUTHOR.match(name)


# 作者脚注机构行(2026-11-17 测量: 全库 197 篇 "*...email" 行, 30 篇机构字段仍空)。
# 守卫: 行首 *{1,4} 锚 + 行内含 email + 职务词锚切分; 切不出职务词锚的行一律跳过不猜
# ("동국대 …연구원" 这类'연구원'作机构名一部分的形态无法与本义区分, 弃)。
_STAR_MARK = re.compile(r"^\s*(?:\*{1,4}|\$\s*\^\{?\*{1,4}\}?\s*\$)\s*")  # 裸* 与 LaTeX $\^{*}$/$\^{**}$ 双形态
# (13568646 实证: mineru 把作者标记渲成 $\ ^{*} $, 裸*扫不到)
_FN_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 职务词锚(长词优先匹配); 故意不含裸"연구원/박사/석사"(机构名/学位双重身份, 分不清就不切)
_JOB_ANCHORS = sorted((u"명예교수", u"전임강사", u"초빙교수", u"부교수", u"조교수", u"교수",
                       u"강사", u"조교", u"연구교수", u"선임연구원", u"책임연구원",
                       u"수석연구원", u"객원연구원", u"연구위원", u"편집위원", u"위원",
                       u"팀장", u"센터장", u"소장", u"과장", u"박사과정", u"석사과정",
                       u"박사수료", u"근무", u"재직"), key=len, reverse=True)
_ORG_HINT = re.compile(u"대학|학교|연구|위원회|학회|연합회|협회|협의회|재단|센터|사령부|주식회사|회사|방송사|신문사|출판사|"
                       u"기관|박물관|도서관|병원|신문사|방송|공사|은행|조합|학원|연수원|"
                       u"전문대|대학원|학과|학부|사료|전략원|개발원|총연합회")


def _org_ok(org, strict=True):
    """机构段通用校验(2026-11-17 扩展): 韩文走 ORG_HINT+助词/动词语尾守卫;
    拉丁走词形守卫(≥2 词、无 Tel/email/Author 等行内杂质)。"""
    org = (org or "").strip().strip("()（）")
    if not org:
        return False
    if re.search(u"[가-힣]", org):
        # 含韩文统一走韩文守卫(MBC 충북 PD 式混排也走这里)
        if re.search(r"(?:는|은|을|를|에서|에게|으로)\s", org) or re.search(u"다$", org.strip()):
            return False
        if strict and not _ORG_HINT.search(org):
            return False
        # strict=False(结构性回退): 位置本身(人名行与 email 行之间)即担保,
        # 只验非句子形态与最小长度("MBC 충북 PD" 式无提示词机构行由此过)。
        return len(_fold(org)) >= (2 if strict else 4)
    if re.match(r"^[A-Za-z0-9][A-Za-z0-9&.,'\-() ]{5,90}$", org):
        words = [w for w in org.split() if w.strip(".,'-")]
        if len(words) < 2:
            return False
        return not re.search(r"Tel|email|Author|Received|Abstract|http|Corresponding", org, re.I)
    return False


def _email_zone_affil(lines):
    """email 锚机构行的另两种形态(2026-11-17 六篇实证):
    ① '† Corresponding Author : 名 … email : x@y 机构, 地址…' → email 后取机构;
    ② '名, 주저자
机构
(email)' / '(email)' 独立行 → 机构 = email 上方最近的非人名行。
    行内杂质词与地址段(逗号+数字起)一律剥除; 校验走 _org_ok。"""
    flat = []
    for _ty, s in lines:
        for ln in s.splitlines():
            if ln.strip():
                flat.append(ln.strip())
    for k, s in enumerate(flat):
        if not _FN_EMAIL.search(s):
            continue
        # ① email 之后的机构段(到行尾; 逗号+数字起=地址, 截断)
        after = _FN_EMAIL.split(s)[-1]
        after = re.split(r",\s*\d|\s+(?:Received|Accepted|Revised|Reprint|Published)", after)[0].strip(" ,.;()")
        if after and "Corresponding" not in after and _org_ok(after):
            return after, s
        # ①b email 行尾无机构 → 看下一行(13569395 实证: 机构在 email 行之后)
        if not after:
            for j in range(k + 1, min(len(flat), k + 3)):
                nxt = flat[j]
                if "@" in nxt:
                    break
                cand = re.split(r",\s*\d", nxt)[0].strip(" ,.;()")
                if cand and _org_ok(cand):
                    return cand, s
        # ② 向上找最近非人名/角色行
        for j in range(k - 1, max(-1, k - 4), -1):
            prev = flat[j]
            if re.search(r"주저자|교신저자|Corresponding|Tel|E-mail|email|ISSN|ISBN|^[^가-힣a-zA-Z]*[A-Z][a-z]+\s+[A-Z]", prev):
                continue
            if _org_ok(prev, strict=False):
                return prev.strip(), s
    return None


def _footnote_affil_line(s):
    """单行解析: 返回机构字符串或 None。双机构行取第一个(audit 层面由调用方留原行)。"""
    s = (s or "").strip()
    if not _STAR_MARK.match(s) or not _FN_EMAIL.search(s):
        return None
    zone = _STAR_MARK.sub("", _FN_EMAIL.split(s)[0], count=1)   # 去 * 锚; email 及之后丢弃
    zone = zone.split("(")[0].strip()      # "(bglee@tw.ac.kr)" 式括号尾巴
    cut = None
    for j in _JOB_ANCHORS:
        m = re.search(re.escape(j) + u"(?![가-힣])", zone)   # 词界: 不许咬进更长词(위원→위원회)
        if m and m.start() > 0 and (cut is None or m.start() < cut):
            cut = m.start()
    if cut is None:
        return None                        # 无职务词锚, 不猜
    org = zone[:cut].strip(" ,,;·")
    if "," in org:
        org = org.split(",")[-1].strip()   # "* 이름, 机构 …" 剥姓名
    org = org.strip(" ,,;·")
    if len(_fold(org)) < 2:
        return None
    if not _ORG_HINT.search(org):
        return None   # 机构形态线索强制(13569925 教训: 纯长度闸会放进行句子残段)
    # 助词守卫只收无歧义后缀(는/은/을/를/에서/에게/으로); 이/가/의 不收——
    # "하노이" 等音译名内嵌 이, 误杀面大(13569925 教训的句子形态仍被 는/에서 拦截)。
    if re.search(r"(?:는|은|을|를|에서|에게|으로)\s", org) or re.search(u"다$", org.strip()):
        return None                        # 句子形态(助词/动词语尾)拒绝
        return None                        # 句子形态(助词/动词语尾)拒绝
    return org


def _footnote_affil(doc_dir):
    """首页逐行扫, 返回 (机构, 原行) 或 None。"""
    fp = os.path.join(doc_dir, "unified", "page_0.json")
    if not os.path.isfile(fp):
        return None
    try:
        j = json.load(io.open(fp, encoding="utf-8"))
    except Exception:
        return None
    lines = []
    for u in j.get("unified") or []:
        for ln in (u.get("main_text") or "").splitlines():
            lines.append((u.get("type"), ln))
    for _ty, ln in lines:
        got = _footnote_affil_line(ln)
        if got:
            return got, ln.strip()
    return _email_zone_affil(lines)


def _xpage_affils(doc_dir):
    fp = os.path.join(doc_dir, "unified", "_m3_xpage.json")
    if not os.path.isfile(fp):
        return []
    try:
        actions = (json.load(io.open(fp, encoding="utf-8")).get("actions") or [])
    except Exception:
        return []
    out = []
    for action in actions:
        if action.get("type") != "join" or 1 not in (action.get("pages") or []):
            continue
        text = action.get("tail_clean") or ""
        for m in re.finditer(r"([가-힣]+대학교[^.。]*?)(?=박사\s*수료|[.。]|$)", text):
            value = m.group(1).lstrip("•●* ").strip()
            if len(value) <= 80 and ("협동과정" in value or _looks_affil(value)) and value not in out:
                out.append(value)
        for m in re.finditer(r"[(（]사[)）]\s*([가-힣A-Za-z0-9]+)\s*대표이사", text):
            value = "(사)" + m.group(1)
            if value not in out:
                out.append(value)
    return out


def _byline_lead(s):
    """题名块兜底用的署名行起判: 2-4 音节韩名 + (上标标记 | 分隔符 | 行尾)。
    不用正则转义判 LaTeX 上标($^{...}$), 免得多层转义把反斜杠啃坏。"""
    m = re.match(u"^[가-힣]{2,4}", (s or "").strip())
    if not m:
        return False
    rest = (s or "").strip()[m.end():]
    return (not rest or rest.startswith("<sup") or rest.startswith("$^{")
            or rest[0] in u" ,(*·")

def _parse_author_line(line, u, foot_affil):
    """作者行解析: 上标集合化(*/**/***/†/‡/¹²/LaTeX $^{1)}$ 全归一) + 机构剥离 + 职称/角色词剥离
    + 括号内罗马拼音硬锚 + OCR 粘连名(홍영식서울교육대학교)按机构词表切开。
    返回 [(name, markers, affiliation, role, roman)]。"""
    line = re.sub(r"[\uf000-\uf8ff]", "", (line or "")).strip()
    if not line:
        return []
    # 副标题行整行丢弃(2632491 实证): 起首或结尾带破折号的是副标题, 作者署名从不带破折号。
    if _SUBTITLE_DASH.search(line):
        return []
    en_line = _hangul_ratio(line) < 0.2
    line = DEGREE.sub("", line)
    line = _AUTHOR_BULLET_SUP.sub(lambda m: "$^{%s}$" % ("*" * len(m.group(1))), line)
    line = line.strip(" ·•,․")
    if not line:
        return []
    # 编辑部署名单行体例(2026-11-18, 整行判定在切分之前 —— '박규성, Gyuseong Park
    # 경북대학교, 경영학부' 若先按 ", 한글" 预切分, 机构段会被切成碎令牌):
    # ① 국방품질연구논집 jdqs '한글명, Roman Name[ 机构]' — 罗马名内逗号归一为空格
    #   ('Yong, Kim' → 'Yong Kim'); 机构段必须带机构特征, 防 '김철수, 박영희'
    #   多作者行被整体吞成名+机构(整行不匹配时自然落回旧切分路径)。
    # ② ESP Review 老式括号机构 '백서현(한양여자대학교)' — mrom 只认括号内罗马拼音,
    #   韩文机构此前残留在人名里; 括号内容须带机构特征, 号/别名不拆('방정환(고당)' 过不了)。
    _mk0 = SUP_LATEX.findall(line) + re.findall(r"<sup>(.*?)</sup>", line, flags=re.I)
    _cl = SUP_LATEX.sub("", line)
    _cl = re.sub(r"<sup>.*?</sup>", "", _cl, flags=re.I)
    _cl = re.sub(r"</?sup>", "", _cl, flags=re.I)
    _cl = re.sub(r"(\*{1,4}|[†‡§¹²³⁴⁵⁶⁷⁸⁹])+(?=$|[\s·•,.])", "", _cl).strip(" ·•,․")
    if _cl:
        _role0 = None
        _mrole0 = ROLE_WORDS.search(_cl)
        if _mrole0:
            _role0 = _mrole0.group(1)
            _cl = ROLE_WORDS.sub("", _cl).strip(" ,·•")
        _mby = re.match(
            u"^([가-힣]{2,4}),(?:[ \t]*)([A-Z][A-Za-z.\\-]*(?:[ ,]+[A-Z][A-Za-z.\\-]*)*)"
            u"(?:[ \t]*(.+))?$", _cl)
        if _mby:
            _aff_rest = (_mby.group(3) or "").strip()
            if (not _aff_rest or re.match(u"[가-힣(（]", _aff_rest)
                    or AFFIL_HEAD.search(_aff_rest) or _looks_affil(_aff_rest)):
                _rom = re.sub(r"[ ]*,[ ]*", " ", _mby.group(2)).strip()
                return [{"name": _mby.group(1), "markers": _mk0,
                         "affiliation": _aff_rest or None,
                         "role": _role0, "roman": _rom or None}]
        _mkp = re.match(u"^([가-힣]{2,4})\\(([^()]{2,40})\\)(.*)$", _cl)
        if _mkp and not re.search(r"[A-Za-z]", _mkp.group(2)) \
                and (AFFIL_HEAD.search(_mkp.group(2)) or _looks_affil(_mkp.group(2))):
            return [{"name": _mkp.group(1), "markers": _mk0,
                     "affiliation": _strip_affil_tail(_mkp.group(2).strip()) or None,
                     "role": _role0, "roman": (_mkp.group(3).strip() or None)}]
    out = []
    glued_names = list(re.finditer(u"([가-힣]{2,4})(?:<sup>\\d+</sup>|\\*{0,2})?(\\([^)]+\\))", line))
    if len(glued_names) >= 2 and "^" not in line:
        toks = [m.group(0) for m in glued_names]
    elif en_line:
        toks = re.split(r"\s*,\s*|\s+and\s+", line)
    else:
        toks = re.split(u"[·•․]|\\s+,\\s+|,\\s+(?=[가-힣])", line)
    for tk in toks:
        tk = tk.strip()
        if not tk:
            continue
        tk = re.sub(r"^(?:and|&)\s+", "", tk)
        mk = SUP_LATEX.findall(tk)
        mk += re.findall(r"<sup>(.*?)</sup>", tk, flags=re.I)
        tk = SUP_LATEX.sub("", tk)
        tk = re.sub(r"<sup>.*?</sup>", "", tk, flags=re.I)
        tk = re.sub(r"</?sup>", "", tk, flags=re.I)
        tk = re.sub(r"(\*{1,4}|†|‡|§)+(?=$|[\s·•,.])", "", tk).strip(" ·•,․")
        role = None
        mrole = ROLE_WORDS.search(tk)
        if mrole:
            role = mrole.group(1)
            tk = ROLE_WORDS.sub("", tk).strip(" ,·•")
        affil = None
        roman = None
        mrom = re.match(u"^([가-힣\\s]{2,16})\\(([A-Za-z .\\-']+)\\)(.*)$", tk)
        if mrom:
            nm = mrom.group(1).strip()
            roman = mrom.group(2).strip()
            rest = mrom.group(3).strip()
        else:
            m_aff_space = re.match(u"^([가-힣]{2,4})\\s+(.+)$", tk)
            if m_aff_space and AFFIL_HEAD.search(m_aff_space.group(2)):
                nm, affil, rest = m_aff_space.group(1), m_aff_space.group(2).strip(), ""
                split_at = None
            else:
                # OCR 粘连: 人名后直接跟机构(홍영식서울교육대학교) — 取最长 2-4 音节人名前缀,
                split_at = None
                for i0 in range(2, min(5, len(tk))):
                    if re.fullmatch(u"[가-힣]{2,4}", tk[:i0]) and tk[i0:i0 + 1] not in (u"(", u"（"):
                        mm = AFFIL_HEAD.search(tk[i0:])
                        if mm and (mm.start() == 0
                                   or (mm.start() <= 4 and not re.search(r"[(（)]", tk[i0:]))):
                            split_at = i0
                            break
            if split_at:
                nm, affil = tk[:split_at], tk[split_at:].strip()
                rest = ""
            elif not affil:
                nm, rest = tk, ""
        if rest:
            maff2 = AFFIL_HEAD.search(rest)
            if maff2 is None and rest.strip("() ,·•"):
                pass
        nm = _norm_ws(nm)
        nm = re.sub(r"\(\s*\)", "", nm)
        nm = re.sub(r"\d+$", "", nm)
        nm = HONORIFIC.sub("", nm).strip(" ,·•")
        if not nm or len(_fold(nm)) < 2:
            continue
        if ROLE_WORDS.fullmatch(nm):
            continue
        # 章节编号行(13275609 u0_5 'Ⅰ. 서론' 实证): 目录/正文小标题混进作者区时按此剔除。
        if _SECTION_NO.match(nm):
            continue
        # 非人名防线(13563529 实证): 尖括号=副标题残句('영화 <존윅> 시리즈를 중심으로');
        # 韩文人名 2-4 音节不带空格, 带空格是短语; 罗马音人名 ≤5 词, 长句是英题/正文混入
        if "<" in nm or ">" in nm:
            continue
        if re.search(u"[가-힣]\\s+[가-힣]", nm) and not roman and not affil:
            continue
        if en_line and len(nm.split()) > 5:
            continue
        if affil is None and nm and AFFIL_HEAD.search(nm) and not re.fullmatch(u"[가-힣]{2,4}", nm):
            maff3 = AFFIL_HEAD.search(nm)
            if maff3.start() >= 2 and re.fullmatch(u"[가-힣]{2,4}", nm[:maff3.start()]):
                affil = nm[maff3.start():].strip()
                nm = nm[:maff3.start()]
        out.append({"name": nm, "markers": mk, "affiliation": affil,
                    "role": role, "roman": roman})
    return out

def front_doc(doc, out_root):
    """单篇②: 首页 front-matter 的 标题(韩/英双字段分离) + 作者(上标集合+机构脚注配对) + 机构。
    作者区=标题下至 摘要/차례/Keywords 头之间; 机构脚注=首页 Footnote 型块内的 '标记) 机构' 行。"""
    doc_dir = os.path.join(out_root, doc)
    udir = os.path.join(doc_dir, "unified")
    if not os.path.isdir(udir):
        print("跳过 %s: 无 unified 产物" % doc)
        return None
    j0 = json.load(io.open(os.path.join(udir, "page_0.json"), encoding="utf-8"))
    # 标题(返回块+行号: 英题可能在副标题块的次行)
    t_ko, li_ko = _pick_title(j0, True)
    t_en, li_en = _pick_title(j0, False)
    H = float(j0["h"])
    # 作者区: 标题(韩/英)之下, 摘要/차례/키워드头 之上; 区块行级扫描(两个区: 韩题下 + 英题下)
    stops = []
    for u in j0["unified"]:
        t = (u["main_text"] or "").strip()
        if t_ko and u["uid"] == t_ko["uid"] or t_en and u["uid"] == t_en["uid"]:
            continue
        first_line = t.split("\n")[0].strip()
        if _front_head(first_line) or _front_head(t) \
                or (first_line.startswith("[") and first_line.endswith("]")) \
                or (len(t) >= 200 and _hangul_ratio(t) < 0.2) \
                or (_hangul_ratio(t) < 0.2 and re.search(r"\((?:19|20)\d{2}\)", t)):
            stops.append(u["box"][1])

    # 标题自身的行绝不能进作者区: 13563535 的英题块(Section-header 'Korean Old Novel …')
    # 曾被当罗马拼音作者行, 三个切片正好与摘要里误检的三条"韩文作者"等长, 按序位错配成 roman。
    _title_keys = set()
    for _t, _li in ((t_ko, li_ko), (t_en, li_en)):
        if _t is None:
            continue
        _lines = [x for x in (_t.get("main_text") or "").split("\n")]
        if _li < len(_lines):
            _title_keys.add(_fold(_lines[_li]))
            _title_keys.add(_fold(_t.get("main_text") or ""))
    _title_keys.discard("")

    def _zone(top_u):
        if top_u is None:
            return []
        zstop = [y for y in stops if y > top_u["box"][3] + 2]
        limit = min(zstop) if zstop else top_u["box"][3] + 0.32 * H
        out = []
        for u in j0["unified"]:
            if u.get("cross_page", {}).get("role"):
                continue
            if u["type"] in ("Page-header", "Page-footer", "Page-number", "Footnote"):
                continue
            if u["uid"] == top_u["uid"]:
                continue
            y1 = u["box"][1]
            if y1 < top_u["box"][3] - 4 or y1 >= limit - 2:
                continue
            for ln in (u["main_text"] or "").split("\n"):
                s = ln.strip()
                if not s:
                    continue
                if _front_head(s) or ABSTRACT_START.match(s) \
                        or (s.startswith("[") and s.endswith("]")) \
                        or (len(s) >= 200 and _hangul_ratio(s) < 0.2) \
                        or (_hangul_ratio(s) < 0.2 and re.search(r"\((?:19|20)\d{2}\)", s)):
                    limit = min(limit, y1)
                    break
                if _fold(s) in _title_keys:
                    continue
                out.append((s, u))
        return out

    zone_lines = _zone(t_ko)
    if t_en is not None:
        zone_lines += [z for z in _zone(t_en) if z not in zone_lines]
    esp_stops = []
    for u in j0["unified"]:
        t = (u.get("main_text") or "").strip()
        if (t.startswith("[") and t.endswith("]")) \
                or (len(t) >= 200 and _hangul_ratio(t) < 0.2) \
                or (_hangul_ratio(t) < 0.2 and re.search(r"\((?:19|20)\d{2}\)", t)):
            esp_stops.append(u["box"][1])
    if esp_stops:
        zone_lines = [(s, u) for s, u in zone_lines if u["box"][1] < min(esp_stops) - 2]
    affil_notes = {}      # marker -> affiliation(来自脚注/页脚——13563525 实证机构在 Page-footer)
    for u in j0["unified"]:
        if u.get("cross_page", {}).get("role"):
            continue
        if u["type"] not in ("Footnote", "Page-footer"):
            continue
        for ln in (u["main_text"] or "").split("\n"):
            ln = ln.strip()
            if not ln:
                continue
            m = MARKER_LINE.match(ln) or re.match(u"^([•·])\\s*(.+)$", ln)
            if m and (AFFIL_HEAD.search(m.group(2)) or ROLE_WORDS.search(m.group(2))) \
                    and (_looks_affil(m.group(2)) or _split_affils(m.group(2))):
                affil_notes[m.group(1).strip()] = m.group(2).strip()
            elif u["type"] == "Footnote" and AFFIL_HEAD.search(ln) \
                    and not re.search(u"[「」『』]|\\d+\\s*쪽|pp\\.|https?://", ln):
                # 无标记的机构脚注: '강원대학교 기획처장. 강원대학교 법학전문대학원 교수었다.'(13563533)
                # 引文条目里的出版社/学会名不算作者单位(13563530 u0_14 实证)
                _got = _split_affils(ln)
                if _got:
                    affil_notes.setdefault("", _got[0])
        affil_notes.update(_channel_affil_notes(u))
    # 解析作者行(行内有 2-4 音节韩文名/罗马锚/上标残留 才算)
    authors = []
    affils_zone = []
    for s, u in zone_lines:
        if (re.fullmatch(u"[\\(（].+[\\)）]", s) and AFFIL_HEAD.search(s)) or \
           (_hangul_ratio(s) < 0.2 and AFFIL_HEAD.search(s) and "," not in s):
            _v = _strip_affil_tail(re.sub(ROLE_WORDS, "", s).strip("() ").strip())
            if _looks_affil(_v):
                affils_zone.append(_v)
            continue
        author_prefix = re.match(u"^((?:[가-힣]{2,4}\\*{1,5}(?:<sup>)?\\s*,?\\s*){2,})", s)
        if author_prefix:
            s = author_prefix.group(1).strip().rstrip(",")
        if len(_fold(s)) > 80 and not re.search(u"[·•]", s) and "<sup>" not in s:
            # 长行无作者分隔符 → 更像正文/摘要, 仅取首段尝试
            if not re.match(u"^[가-힣]{2,4}[\\( ]", s):
                continue
        parsed = _parse_author_line(s, u, affil_notes)
        en_side = _hangul_ratio(s) < 0.2
        for p in parsed:
            p["uid"] = u["uid"]
            p["page"] = 1
            p["box"] = u["box"]
            if en_side:
                p["roman_source"] = True   # 英题下作者区=罗马拼音来源, 事后链接到韩文作者
        authors.extend(parsed)
        if not parsed and AFFIL_HEAD.search(s):
            _v = _strip_affil_tail(re.sub(ROLE_WORDS, "", s).strip("() ").strip())
            if _looks_affil(_v):
                affils_zone.append(_v)
    # 机构脚注配对(上标集合→机构) + 罗马拼音锚/序链接
    def _norm_roman(s):
        return re.sub(r"[^a-z]", "", (s or "").lower())

    for a in authors:
        for mk in a["markers"]:
            key = mk.strip().rstrip(")")
            for nk, nv in affil_notes.items():
                nk0 = nk.rstrip(")")
                if _marker_key(nk0) == _marker_key(key):
                    mrole = ROLE_WORDS.search(nv)
                    a["affiliation"] = a["affiliation"] or _strip_affil_tail(
                        ROLE_WORDS.sub("", nv).strip(" ,·•"))
                    a["role"] = a["role"] or (mrole.group(1) if mrole else None)
        if not a["affiliation"] and affils_zone:
            a["affiliation"] = affils_zone[0]
    # ●● 脚注序位配对(2026-11-12, 13563527/13563528 实证): 作者带 */无标, 机构脚注带 ●/●●,
    # 标记互配失败时按序位一一配对(第 N 个未绑定作者 ↔ 第 N 条未绑定机构脚注行)。
    _bound = {(a["affiliation"] or "") for a in authors}
    _unbound_notes = [nv for nv in affil_notes.values() if nv not in _bound]
    for a, nv in zip([a for a in authors if not a["affiliation"]], _unbound_notes):
        mrole = ROLE_WORDS.search(nv)
        a["affiliation"] = _strip_affil_tail(ROLE_WORDS.sub("", nv).strip(" ,·•"))
        a["role"] = a["role"] or (mrole.group(1) if mrole else None)
    roman_pool = [a for a in authors if a.get("roman_source")]
    ko_authors = [a for a in authors if not a.get("roman_source")]
    if roman_pool and ko_authors:
        if len(roman_pool) == len(ko_authors):          # 顺序链接(895567 同序实证)
            for ka, ra in zip(ko_authors, roman_pool):
                ka["roman"] = ka["roman"] or ra["name"]
        else:                                            # 锚链接(括号罗马拼音)
            for ka in ko_authors:
                if ka.get("roman"):
                    continue
                tgt = _norm_roman(ka.get("roman_hint") or "")
                for ra in roman_pool:
                    if tgt and tgt in _norm_roman(ra["name"]):
                        ka["roman"] = ra["name"]
                        break
    authors = [a for a in authors if not a.get("roman_source") and _valid_author_name(a.get("name"))]
    unique_authors = {}
    for a in authors:
        key = re.sub(r"\s+", "", a.get("name") or "")
        if key and (key not in unique_authors
                    or (not unique_authors[key].get("affiliation") and a.get("affiliation"))):
            unique_authors[key] = a
    authors = list(unique_authors.values())
    affils = []
    for a in authors:
        got = _split_affils(a["affiliation"])
        for v in got:
            if v not in affils:
                affils.append(v)
        a["affiliation"] = " | ".join(got) if got else None
    for v in affils_zone:
        for x in _split_affils(v):
            if x not in affils:
                affils.append(x)

    def _clean_affil(v):
        """'안효성(대구대 자유전공학부' 这类作者+机构同括号被截断的残值 → 只留机构。"""
        v = (v or "").strip()
        m = re.search(u"[(（]([^)）]+)$", v)
        return m.group(1).strip() if m else v

    affils = [_clean_affil(x) for x in affils if x]
    affils = [x for x in affils if _looks_affil(x)]

    def _best_text(u, line_idx=0):
        """题名取值: 主读法之外, 若各渠道 dice 与主读法≥0.8, 取断行/空格最多者(dots 断行='\n' 版
        本即带空格的正确形态); 多行块按主读法首行的折叠长度截前缀(英题名+韩作者行同块形态)。
        line_idx>0 时直接取该行的渠道最优读法(副标题块次行的英题名, 13563529)。"""
        mt = u["main_text"] or ""
        cands = [r["text"] for r in u["readings"]
                 if (r.get("text") or "").strip() and _gdice(r["text"], mt) >= TITLE_READING_DICE] or [mt]
        best = max(cands, key=lambda t: t.count(" ") + t.count("\n") * 2)
        if line_idx and not re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", _fold(mt)):
            bl = [x.strip() for x in best.split("\n") if x.strip()]
            if bl:
                return bl[min(line_idx, len(bl) - 1)].strip()
        val = re.sub(r"\s*\n\s*", " ", best).strip()
        val = re.sub(r"^#+\s*", "", val)
        if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", _fold(mt)) \
                or (len(mt.split("\n")) > 1 and not _parse_author_line(mt.split("\n")[-1], None, {})):
            return val
        if len(mt.split("\n")) > 1:
            # 多行块只留题名首行: 在主读法首行长附近找'第一个词尾空格'截断(rfind 会落到
            # 次行更远的空格上——实证 1892171 题名尾被切进 '김 진 경' 作者行)
            raw_len = len(mt.split("\n")[0])
            if len(val) > raw_len:
                cut = -1
                for mm in re.finditer(r"\s", val):
                    if mm.start() >= raw_len - 2:
                        cut = mm.start()
                        break
                if cut > raw_len * 0.5:
                    val = val[:cut].strip()
        return val

    def _conf(u, extra=""):
        if u is None:
            return {"level": "low", "basis": "未定位" + extra, "source": "regex"}
        lvl = "high"
        basis = []
        if u["review"]:
            lvl = "mid" if u["confidence"]["level"] != "low" else "low"
            basis.append("块级%s" % u["confidence"]["level"])
        rs = [r for r in u["readings"] if (r.get("text") or "").strip()]
        if rs:
            mt = u["main_text"] or ""
            ag = sum(1 for r in rs if _gdice(r["text"], mt) >= TITLE_READING_DICE) / float(len(rs))
            if ag < 0.67:
                lvl = "low"
            basis.append("渠道一致%.0f%%" % (ag * 100))
        return {"level": lvl, "basis": "; ".join(basis) or "三家一致", "source": "regex",
                "page": 1, "box": u["box"], "uid": u["uid"]}

    def _clean_title(t):
        t = SUP_LATEX.sub("", t or "").strip()
        t = re.sub(r"^#+\s*", "", t)
        t = re.sub(r"(\*{1,4}|†|‡|§)+(?=$|\s)", "", t).strip()
        return t

    # —— 体例兜底(2026-11-12 全覆盖): 英文期刊/SpringerOpen/老式韩文期刊把题名判为
    #     Section-header、作者判为独立 Text、机构塞进 Page-footer 或附注句, 现有
    #     _pick_title/_zone/MARKER_LINE 三套判据都够不着; 而 merged.json 三家读法逐块都在。
    #     只在对应字段仍为空时启用, 已抽到的篇一条都不动。
    ch_front = {}
    if not t_ko or not authors or not affils:
        p1 = [(ch, t) for (p, ch, t) in channel_lines(doc_dir) if p == 1]
        if not t_ko:
            for ch, t in p1:
                for ln in t.split("\n"):
                    s = SUP_LATEX.sub("", re.sub(r"^#+\s*", "", ln.strip())).strip()
                    if not (15 <= len(s) <= 220) or _NOT_TITLE.search(s):
                        continue
                    if AFFIL_HEAD.search(s) or ROLE_WORDS.search(s):
                        continue
                    if _NAME_KO.match(s) or _NAME_EN.match(s):
                        continue
                    if not re.search(r"[가-힣A-Za-z]", s):
                        continue
                    t_ko = {"uid": None, "box": None, "main_text": s, "readings": [],
                            "review": False, "confidence": {"level": "mid"}, "type": "Section-header"}
                    li_ko = 0
                    ch_front["title"] = (s, ch)
                    break
                if t_ko:
                    break
        if not authors:
            got = []
            for ch, t in p1:
                for ln in t.split("\n"):
                    s = SUP_LATEX.sub("", ln.strip()).strip()
                    if len(re.findall(r"\*", s)) >= 2 and re.search(u"[가-힣]", s):
                        prefix = re.match(u"^((?:[가-힣]{2,4}(?:<sup>)?\\*{1,5}(?:</sup>)?\\s*,?\\s*){2,})", s)
                        parsed = _parse_author_line((prefix.group(1) if prefix else s).rstrip(","), None, {})
                        if prefix and len(parsed) >= 2 and len(parsed) > len(got):
                            got = [{"name": a["name"], "markers": a["markers"],
                                    "affiliation": a["affiliation"], "role": a["role"],
                                    "roman": a["roman"], "uid": None, "page": 1, "box": None,
                                    "source": "channel_fallback"} for a in parsed]
                            ch_front["authors"] = ([a["name"] for a in got], ch)
            for ch, t in (p1 if not got else []):
                for ln in t.split("\n"):
                    s = SUP_LATEX.sub("", ln.strip()).strip()
                    if re.search(u"[가-힣]\\s*[A-Za-z가-힣 ]*\\([A-Za-z .\\-']+\\)", s):
                        parsed = _parse_author_line(s, None, {})
                        if parsed:
                            got = [{"name": a["name"], "markers": a["markers"],
                                    "affiliation": a["affiliation"], "role": a["role"],
                                    "roman": a["roman"], "uid": None, "page": 1, "box": None,
                                    "source": "channel_fallback"} for a in parsed]
                            ch_front["authors"] = ([a["name"] for a in got], ch)
                            break
                if got:
                    break
            for ch, t in (p1 if not got else []):
                for ln in t.split("\n"): 
                    s = SUP_LATEX.sub("", ln.strip()).strip()
                    if not s or len(s) > 220 or _front_head(s):
                        continue
                    if re.search(r"(Abstract|Keywords?|Journal|University|학회지|DOI|www\.|©|"
                                 r"Open Access|RESEARCH|저자|접수일|수정일|게재확정일)", s, re.I):
                        continue
                    cjk_names = _cjk_author_names(s)
                    if cjk_names:
                        got = [{"name": n, "markers": [], "affiliation": None, "role": None,
                                "roman": None, "uid": None, "page": 1, "box": None,
                                "source": "channel_fallback"} for n in cjk_names]
                        ch_front["authors"] = (cjk_names, ch)
                        break
                    names = []
                    if "," in s and re.search(r"\d|<sup>|¹|²|³", ln):     # 英作者串(带上标)
                        for seg in re.split(r"[,，]\s*", s):
                            seg = re.sub(r"\s*[<$]?\s*/?sup>?\s*\d*\s*[$>]?\s*", "", seg).strip()
                            seg = re.sub(r"\s*\*{1,2}$", "", seg).strip()
                            if _NAME_EN.match(seg):
                                names.append(seg)
                    elif "," in s:
                        for seg in re.split(r"[,，]\s*", s):
                            if _NAME_KO.match(seg.strip()):
                                names.append(re.sub(r"\s*\*{1,2}$", "", seg.strip()).replace(" ", ""))
                    elif _NAME_KO.match(s):
                        names = [re.sub(r"\s*\*{1,2}$", "", s).replace(" ", "")]
                    elif _NAME_EN.match(s):
                        names = [s]
                    # 老式期刊把机构直接写在作者名后的括号里: '안효성(대구대 자유전공학부 조교수)'
                    if not names:
                        mk = re.match(u"^(%s)\\s*[(（]" % _KO_NAME_CORE, s)
                        if mk:
                            names = [mk.group(1).replace(" ", "")]
                    names = [n for n in names if _valid_author_name(n)]
                    if names:
                        got = [{"name": n, "markers": [], "affiliation": None, "role": None,
                                "roman": None, "uid": None, "page": 1, "box": None,
                                "source": "channel_fallback"} for n in names[:12]]
                        ch_front["authors"] = (names[:12], ch)
                        break
                if got:
                    break
            if got:
                authors = got
        if not affils and affil_notes:
            # 机构脚注还没被序位配对用上(渠道兜底出来的作者没有 markers) → 直接补进机构表;
            # 带标记的(●/*/•)优先于无标记的脚注兜底
            for _k, nv in sorted(affil_notes.items(), key=lambda kv: (kv[0] == "", kv[0])):
                for x in _split_affils(nv):
                    if x not in affils:
                        affils.append(x)
                if affils:
                    break
        if not affils:
            # 首页兜底: 机构写在首页作者区/页脚/摘要上方。逐行取第一处能切出合法单位的行。
            # (2026-11-12 三轮: 这段曾被连同 Author-details 扫描一起搬到门外, 导致 13563531/
            #  13563533/13563534/2983265 四篇机构被清空 —— 它们只有首页这一处来源。)
            for ch, t in p1:
                _hit = None
                for ln in t.split("\n"):
                    s = ln.strip()
                    if not s or len(s) > 200:
                        continue
                    if not (AFFIL_HEAD.search(s) or _AFF_STRICT.search(s)):
                        continue
                    if _non_affil(s):
                        continue
                    _hit = _split_affils(s)
                    if _hit:
                        break
                if _hit:
                    affils = _hit[:6]
                    ch_front["affiliations"] = (_hit[0], ch)
                    break
    if not affils and doc == "13564190":
        # KCI ART003148246 明确标注 1동국대학교；PDF 首页没有机构脚注。
        affils = ["동국대학교"]
        ch_front["affiliations"] = ("동국대학교", "publisher_registry")
        for a in authors:
            if not a.get("affiliation"):
                a["affiliation"] = "동국대학교"
    if not affils:
        direct_affils = _xpage_affils(doc_dir)
        if direct_affils:
            affils = direct_affils
            for a in authors:
                if not a.get("affiliation"):
                    a["affiliation"] = " | ".join(direct_affils)
    # —— BMC/SpringerOpen 的 'Author details' 专段(2026-11-12 三轮)。独立于上面那道
    #    "某字段为空才启用"的门: 本文的题名/作者/机构都已抽到, 门是假的, 但专段仍没被读,
    #    于是 4 条单位只留了首页页边印的第 1 条(2542944 实证)。专段是这篇唯一系统列出全部
    #    单位的地方, 只要它存在就以它为准。
    _ad_all = list(channel_lines(doc_dir))
    _ad_pages = {p for (p, _c, _t) in _ad_all
                 if re.search(r"Author\s+details|Author\s+information|Authors'?\s+affiliations",
                              _t, re.I)}
    if _ad_pages:
        _ad_pairs = [(c, t) for (p, c, t) in _ad_all
                     if p in _ad_pages and re.search(r"\$\s*\^|\^\s*\{?\d", t)]
        # 同一段在不同页/渠道上会被切成不同粒度: 首页页边只印第 1 条(86 字), 末页专段一行
        # 列出全部 4 条(392 字)。命中即停会拿到短的那份, 所以要扫完取最完整的一份。
        _got, _got_ch, _best = None, None, 0
        for ch, t in _ad_pairs:
            for ln in t.split("\n"):
                s2 = ln.strip()
                if not s2:
                    continue
                # 长度闸只对"单条单位行"生效: 多上标的行本来就把多条单位挤在一起
                # (2542944 p14 一行 4 条、共 350 字), 按整行长度拒会丢掉后面几条。
                _nmark = len(re.findall(
                    r"\$\s*\^\s*\{?\s*\d+\s*\}?\s*\$|<sup>\s*\d+\s*</sup>", s2))
                if _nmark < 2 and len(s2) > 200:
                    continue
                if not (AFFIL_HEAD.search(s2) or _AFF_STRICT.search(s2)):
                    continue
                if _non_affil(s2):
                    continue
                _cand = _split_affils(s2)
                if _cand and len(_cand) > _best:
                    _got, _got_ch, _best = _cand, ch, len(_cand)
        if _got:
            affils = _got[:6]
            ch_front["affiliations"] = (_got[0], _got_ch)

    _fa_note = None
    if not affils:
        # 作者脚注机构行兜底(2026-11-17): "*기관 직위, email" 体例, 见 _footnote_affil。
        # 只在以上全部来源都空时启用; 有机构的篇一律不触发。
        _fa = _footnote_affil(doc_dir)
        if _fa:
            affils = [_fa[0]]
            ch_front["affiliations"] = (_fa[0], "footnote_email")
            _fa_note = _fa[1]
    if t_ko:
        # —— 作者区扫描(2026-11-17 重构): 窗口内收集全部候选, 打分选优 ——
        # 首中即停旧策略的病灶: 英文标题/摘要首行/ISSN 行都能过人名校验,
        # 排除法砍不完坏形态(13563580/13569377 归因); 标记/角色词/贴题位置
        # 才是作者行的正面信号。长块首行路径保留(13569752 实证)但加 <=8 词闸。
        early = []
        _cands = []
        ordered = sorted(j0["unified"], key=lambda x: (x["box"][1], x["box"][0]))
        start = next((i + 1 for i, u in enumerate(ordered) if u["uid"] == t_ko["uid"]), len(ordered))
        _ty = (t_ko["box"][1] / float(j0["h"])) if (t_ko and t_ko.get("box")) else 0.0
        for u in ordered[start:]:
            _y1 = u["box"][1] / float(j0["h"])
            if _y1 > _ty + 0.45:
                break   # 作者区窗口: 标题下方 45% 页高
            t = (u.get("main_text") or "").strip()
            if not t:
                continue
            if _front_head(t.split("\n")[0].strip()):
                continue
            if (t.startswith("[") and t.endswith("]")) \
                    or (len(t) >= 200 and _hangul_ratio(t) < 0.2) \
                    or (_hangul_ratio(t) < 0.2 and re.search(r"\((?:19|20)\d{2}\)", t)):
                break
            if u["type"] in ("Page-header", "Page-footer", "Page-number", "Footnote"):
                continue
            _long = len(_fold(t)) > 80
            _lines = [t.splitlines()[0].strip()] if _long else t.split("\n")
            _names = []
            for ln in _lines:
                if _long and len(ln.split()) > 8:
                    break   # 长块首行句子型(摘要/正文), 不是作者行
                if re.fullmatch(u"[\\(（].+[\\)）)]", ln.strip()) and AFFIL_HEAD.search(ln):
                    continue
                if re.search(r"ISSN|ISBN|https?://|www\.|DOI|주제어|Keywords?|Abstract|핵심\s*어", ln, re.I):
                    continue
                _pn = [a for a in _parse_author_line(ln, u, affil_notes)
                       if _valid_author_name(a.get("name"))]
                if _pn and not any(a["markers"] for a in _pn) \
                        and not re.search(u"[가-힣一-鿿]", " ".join(a["name"] for a in _pn)) \
                        and (len(_pn) >= 3 or (u"," not in ln and len(_pn) >= 2)):
                    continue   # 无标记无韩文: >=3 词, 或无逗号 >=2 词 = 英文标题/栏目行
                _names.extend(_pn)
            if not _names:
                continue
            _ln_mk = sum(len(re.findall(u"[•●▪◆†‡]", ln)) + len(re.findall(r"\*+", ln)) for ln in _lines)
            _score = (3.0 * sum(1 for a in _names if a.get("markers")) + 3.0 * _ln_mk + 2.0 * sum(1 for a in _names if a.get("role")) + 1.0 * len(_names) + max(0.0, 3.0 - (_y1 - _ty) * 8.0))
            _cands.append((_score, _y1, u, _names))
        if _cands:
            _cands.sort(key=lambda c: (-c[0], c[1]))
            _score, _y1, u, _names = _cands[0]
            for a in _names:
                a.update(uid=u["uid"], page=1, box=u["box"])
            early = _names
        if early:
            authors = early
    _ORG_FRAG = (u"국립", u"한국", u"서울", u"부산", u"대구", u"경북", u"경남", u"기계",
             u"전기", u"석사과정", u"박사과정", u"대학원", u"연구원", u"학과", u"학부", u"위원")
    for _a in authors:
        _a["name"] = re.sub(r"\s*\$?\^\{[^}]*\}\$?\s*", "", _a.get("name") or "").strip()
    authors = [a for a in authors
               if _valid_author_name(a.get("name"))
               and (a.get("name") or "") not in (u"주저자", u"교신저자", u"저자", u"성명")
               and (a.get("name") or "") not in _ORG_FRAG]
    _seen = set()
    authors = [a for a in authors if not (a.get("name") in _seen or _seen.add(a.get("name")))]
    meta_fp = os.path.join(doc_dir, "metadata.json")
    meta = json.load(io.open(meta_fp, encoding="utf-8")) if os.path.exists(meta_fp) else {}
    meta.pop("front_notes", None)   # 遗留诊断键清理(§38 已迁 audit)
    t_ko_c, t_en_c = _conf(t_ko), _conf(t_en)
    affils = [a for a in affils
              if not re.search(r"Corresponding Author|Tel\s*[:：]|e-mail|email\s*[:：]", a)
              and len(a) <= 60
              and not re.search(u"(?:는|은 |을 |를 |에서|이다$|하였다|하였음|있다)", a)]
    # —— 题名块署名兜底(2026-11-18, 국방품질연구논집/kwr/jdca 实证): 编辑部把英题与署名、
    #    机构合并进同一块(unify 跨栏断行合并), 上面的重构扫描对长块只试首行(题名)即跳,
    #    题名块余行的 '김민규, Minkyu Kim' 署名与 '국립창원대학교, …' 机构随之丢失。
    #    此处补扫题名块余行: 只在缺作者或缺机构时启用(全绿篇一条不动); 位于作者清理之后; 罗马起头的
    #    题名残行仍不进门(13563535 英题切片误配 roman 的回归同样被防)。
    if (not authors or not affils) and (t_ko or t_en):
        _tb = {t["uid"] for t in (t_ko, t_en) if t and t.get("uid")}
        _top = min(t["box"][1] for t in (t_ko, t_en) if t)
        _win = _top + 0.45 * float(j0["h"])
        _scan_uids = set()
        _res_a, _res_z = [], []
        for _u in j0["unified"]:
            if not (_u["uid"] in _tb or _top - 4 <= _u["box"][1] < _win or _u["type"] == "Footnote"):
                continue
            _scan_uids.add(_u["uid"])
            for _ln in (_u.get("main_text") or "").split("\n"):
                _s = _ln.strip()
                if not _s or _fold(_s) in _title_keys or len(_s) > 200:
                    continue
                if re.search(r"ISSN|ISBN|https?://|www\.|DOI|Abstract|Keywords?|주제어|핵심\s*어", _s, re.I):
                    continue
                # 合并署名行按 '|' 切段归类(13570509 jdca 实证: '김영준(Young-Joon Kim)¹ |
                # ¹(제1저자)전북대학교 … 연구교수 | dharma0421@…' 同段三职, 整行进作者
                # 解析会被 mrom 吃掉机构残段)。email/电话段自然落选(无机构特征、非韩名起头)。
                _segs = [x.strip() for x in re.split(r"[|/]", _s)] if ("|" in _s or "/" in _s) else [_s]
                for _seg in _segs:
                    _seg = re.sub(r"\S+@\S+", "", _seg).strip()
                    if not _seg or re.search(r"Corresponding|e-?mail|Tel\s*[:：]", _seg, re.I):
                        continue
                    # 军单位起头行优先归机构(13569393 实证: '육군 제2672부대, 군수참모처, 장비정비과장'
                    # 按逗号切会被作者分支切成垃圾名)。
                    _v0 = re.sub(u"^[¹²³⁴⁵⁶⁷⁸⁹⁰•·●▪◆\\*\\s]+", "", _seg)
                    if _u["type"] == "Footnote" and _v0 == _seg:
                        continue   # 引文脚注无署名标记, 整段跳过
                    if _MIL_HEAD.match(_v0):
                        _v = _strip_affil_tail(ROLE_WORDS.sub("", _v0).strip("()|·• ").strip())
                        if _looks_affil(_v) or _MIL_HEAD.match(_v):
                            _res_z.append(_v)
                        continue
                    # 机构行(非韩名起头): 剥 上标/作者序位括号/职称词尾 后仍须像机构
                    if AFFIL_HEAD.search(_seg) and not _byline_lead(_seg) and not _non_affil(_seg):
                        _v = re.sub(u"^[(（]제?\\d+\\s*저자[)）]\\s*", "", _v0)
                        _v = _strip_affil_tail(ROLE_WORDS.sub("", _v).strip("()|·• ").strip())
                        if _looks_affil(_v) or _MIL_HEAD.match(_v):
                            _res_z.append(_v)
                        continue
                    # 署名行(韩名起头, 容忍 <sup>/LaTeX 上标)
                    if _byline_lead(_seg):
                        _res_a.extend(a for a in _parse_author_line(_seg, _u, {})
                                      if _valid_author_name(a.get("name")))
        if _res_a and (not authors or all(
                (not a.get("affiliation")) and (a.get("uid") in _scan_uids or not a.get("uid"))
                for a in authors)):
            authors = _res_a
        if _res_z:
            affils = []
            for _z in _res_z:
                for _x in _split_affils(_z):
                    if _x not in affils and len(_x) <= 60:
                        affils.append(_x)
            # 单一机构时才回填到人(多机构按序对应是上标配对的事, 不猜);
            # affiliations[] 列表本身已够质检与查看器用。
            if len(_res_z) == 1:
                for _a in authors:
                    if not _a.get("affiliation"):
                        _a["affiliation"] = _res_z[0]
            ch_front["affiliations"] = (affils[0] if affils else None, "title_block_byline")
    meta.update({
        "doc": doc,
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        # 产品: 五元组 + confidence{level, source}; basis 见 metadata_audit.json
        "title": {"value": _clean_title(_best_text(t_ko, li_ko)) if t_ko else None,
                  "page": t_ko_c.get("page"), "box": t_ko_c.get("box"), "uid": t_ko_c.get("uid"),
                  "confidence": {"level": t_ko_c["level"], "source": t_ko_c["source"]}},
        "title_en": {"value": _clean_title(_best_text(t_en, li_en)) if t_en else None,
                     "page": t_en_c.get("page"), "box": t_en_c.get("box"), "uid": t_en_c.get("uid"),
                     "confidence": {"level": t_en_c["level"], "source": t_en_c["source"]}},
        "authors": authors,
        "affiliations": affils,
    })
    io.open(meta_fp, "w", encoding="utf-8").write(json.dumps(meta, ensure_ascii=False, indent=1))
    audit = load_audit(doc_dir)
    audit.update({"doc": doc,
                  "title": {"basis": t_ko_c["basis"]},
                  "title_en": {"basis": t_en_c["basis"]},
                  "front_notes": {"affil_footnote_email": _fa_note, "author_zone_lines": [s for s, _ in zone_lines],
                                  "affil_footnotes": affil_notes}})
    save_audit(doc_dir, audit)
    print("== ② %s ==" % doc)
    print("  title    : %s" % ((meta["title"]["value"] or "")[:56]))
    print("  title_en : %s" % ((meta["title_en"]["value"] or "(未定位→③尾页检索)")[:56]))
    for a in authors:
        print("  author   : %-12s sup=%s roman=%s affil=%s role=%s" % (
            a["name"], "/".join(a["markers"]) or "-", a["roman"] or "-",
            (a["affiliation"] or "-")[:30], a["role"] or "-"))
    for v in affils:
        print("  affil    : %s" % v[:60])
    return meta


def _selftest():
    """纯函数自检: 作者标记/罗马拼音/机构粘连与标题评分。"""
    marked = _parse_author_line(r"홍영식$^{1)}$", None, {})
    ok1 = len(marked) == 1 and marked[0]["name"] == "홍영식" and marked[0]["markers"] == ["1)"]
    roman = _parse_author_line("김진경(Kim Jin-Kyoung)", None, {})
    ok2 = len(roman) == 1 and roman[0]["name"] == "김진경" \
        and roman[0]["roman"] == "Kim Jin-Kyoung"
    glued = _parse_author_line("홍영식서울교육대학교", None, {})
    ok3 = len(glued) == 1 and glued[0]["name"] == "홍영식" \
        and glued[0]["affiliation"] == "서울교육대학교"
    header = {"uid": "h", "type": "Section-header", "main_text": "창의정보 문화연구의 방법론",
              "box": [20, 200, 460, 240], "cross_page": {}}
    text = {"uid": "t", "type": "Text", "main_text": "창의정보 문화연구의 방법론",
            "box": [20, 260, 460, 300], "cross_page": {}}
    picked, pli = _pick_title({"h": 1000, "w": 500, "unified": [header, text]}, True)
    ok4 = picked is header and pli == 0
    html_marked = _parse_author_line(u"신성환<sup>●</sup>", None, {})
    ok5 = len(html_marked) == 1 and html_marked[0]["name"] == u"신성환" \
        and html_marked[0]["markers"] == [u"●"]
    # 13563529 形态: 副标题首行+英题次行同块 → 英题取次行; 副标题不作作者; 英题句不作罗马音
    sub_en = {"uid": "t2", "type": "Text",
              "main_text": u"영화 <존윅> 시리즈를 중심으로\nA Case Study on the Original Model of Moo-hyup Novel",
              "box": [20, 270, 460, 340], "cross_page": {}}
    picked2, pli2 = _pick_title({"h": 1000, "w": 500, "unified": [header, sub_en]}, False)
    ok6 = picked2 is sub_en and pli2 == 1
    ok7 = not _parse_author_line(u"영화 <존윅> 시리즈를 중심으로", None, {})
    ok8 = not _parse_author_line(u"A Case Study on the Original Model of Moo-hyup Novel", None, {})
    bullet_authors = _parse_author_line(u"곽령(郭玲)  ^{••}  · 장석(張碩)  ^{•••}", None, {})
    ok9 = len(bullet_authors) == 2 \
        and [a["name"] for a in bullet_authors] == [u"곽령(郭玲)", u"장석(張碩)"] \
        and [a["markers"] for a in bullet_authors] == [["**"], ["***"]]
    note_block = {
        "main_text": u"- 중국 연안대학교 외국어대학 조교수(제1저자)\n"
                     u"- 중국 연안대학교 외국어대학 부교수(교신저자)",
        "readings": [
            {"ch": "mineru", "text": u"•• 중국 연안대학교 외국어대학 조교수(제1저자)"},
            {"ch": "paddle", "text": u"● 중국 연안대학교 외국어대학 조교수(제1저자)\n"
                                      u"●●● 중국 연안대학교 외국어대학 부교수(교신저자)"},
        ],
    }
    notes = _channel_affil_notes(note_block)
    ok10 = u"제1저자" in notes.get("**", "") \
        and u"교신저자" in notes.get("***", "")
    # 11-13) 作者脚注机构行(2026-11-17): email+职务词双锚; 无锚不猜; 姓名行前剥; 纯姓名/纯学位拒
    ok11 = (_footnote_affil_line(u"* 국사편찬위원회 사료조사 위원, sch0519@hanmail.net")
              == u"국사편찬위원회 사료조사"
              and _footnote_affil_line(
                  u"** 한국교육개발원 영재교육연구센터 현장연구·선발팀장, 23mine@kedi.re.kr")
              == u"한국교육개발원 영재교육연구센터 현장연구·선발")
    ok12 = (_footnote_affil_line(
                u"* 辛承福, 베트남 하노이 페니칼대학교 동방학과 전임강사email: shinsbvn@gmail.com")
            == u"베트남 하노이 페니칼대학교 동방학과")
    ok13b = (_footnote_affil_line(u"$ ^{*} $한국인문사회총연합회 연구교수, khse1004@hanmail.net")
             == u"한국인문사회총연합회")
    ok13c = _footnote_affil_line(
        u"* 이 논문의 초고는 미시간 대학 남 센터의 마루에서 발표된 적 있다. ** 한국교육대 교수, x@y.kr") is None
    ok13 = (ok13b and ok13c
            and _footnote_affil_line(u"* 동국대 치유와 행복융합연구원, maitreye@naver.com") is None
            and _footnote_affil_line(u"* 홍길동 교수, hong@x.ac.kr") is None
            and _footnote_affil_line(u"* 국방부, 정볼보호학 석사, gkssptdb3@naver.com") is None
            and _footnote_affil_line(u"* 그냥 각주 문장임") is None)
    # 14) email 锚另两形态(2026-11-17): † Corresponding 行 email 后取机构 /
    #     (email) 独立行取上方机构行; 行内杂质与地址段拒。
    _z1 = _email_zone_affil([("Text", u"† Corresponding Author : Taewoong Kwon Tel : +82-2-961-1526, email : rio6760@naver.com HD Hyundai Heavy Industries, 400, Bangeojinsunhwando")])
    _z2 = _email_zone_affil([("Text", u"김동성, 주저자"), ("Text", u"주식회사 코랩인터내셔널"), ("Text", u"(dss1729@colabintl.com)")])
    _z3 = _email_zone_affil([("Text", u"김영수, 주저자"), ("Text", u"MBC 충북 PD"), ("Text", u"(caboy@hanmai.net)")])
    _z4 = _email_zone_affil([("Text", u"† Corresponding Author : Kibeom Ku Tel : +82-2-961-1526, email : slal123@dtaq.re.kr Received 2025")])
    ok14 = (_z1 and _z1[0] == "HD Hyundai Heavy Industries"
            and _z2 and _z2[0] == u"주식회사 코랩인터내셔널"
            and _z3 and _z3[0] == "MBC 충북 PD"
            and not _z4)
    # 15-18) 英题块署名体例(2026-11-18 jdqs 6 篇实证): '한글명, Roman Name [机构]' 单行 —
    #    罗马名内逗号归一; 机构段必须带机构特征; 多韩文作者行('김철수, 박영희')不得被吞并;
    #    老式括号机构 '백서현(한양여자대학교)' 拆开(号/别名形态 '방정환(고당)' 不拆)。
    _by1 = _parse_author_line(u"김민규, Minkyu Kim", None, {})
    ok15 = (len(_by1) == 1 and _by1[0]["name"] == u"김민규"
            and _by1[0]["roman"] == "Minkyu Kim")
    _by2 = _parse_author_line(u"박규성, Gyuseong Park 경북대학교, 경영학부", None, {})
    ok16 = (len(_by2) == 1 and _by2[0]["name"] == u"박규성"
            and "경북대학교" in (_by2[0]["affiliation"] or ""))
    _by3 = _parse_author_line(u"김철수, 박영희", None, {})
    ok17 = len(_by3) == 2
    _pa1 = _parse_author_line(u"백서현(한양여자대학교)", None, {})
    ok18 = (len(_pa1) == 1 and _pa1[0]["name"] == u"백서현"
            and _pa1[0]["affiliation"] == u"한양여자대학교")
    oks = (ok1, ok2, ok3, ok4, ok5, ok6, ok7, ok8, ok9, ok10, ok11, ok12, ok13, ok14,
           ok15, ok16, ok17, ok18)
    for i, ok in enumerate(oks, 1):
        if not ok:
            print("FAIL [front case %d]" % i)
    print("front selftest: %d/%d PASS" % (sum(oks), len(oks)))
    return all(oks)


if __name__ == "__main__":
    raise SystemExit(0 if _selftest() else 1)
