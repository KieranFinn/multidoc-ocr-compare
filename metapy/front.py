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
        if u["type"] in ("Page-header", "Page-footer", "Page-number"):
            continue
        t = (u["main_text"] or "").strip()
        if not t:
            continue
        if t.startswith("<") and "<table" in t:
            continue   # 目录表格(1892171 TOC) 不参与题名
        lines = [x.strip() for x in t.split("\n") if x.strip()]
        cand = [(0, lines[0])]
        if not want_hangul and len(lines) > 1:
            cand.append((1, lines[1]))   # 英题可能在次行(副标题在首行的形态)
        for li, t in cand:
            f = _fold(t)
            if not (10 <= len(f) <= 220):
                continue
            y1 = u["box"][1] / H
            if not (TITLE_ZONE[0] <= y1 <= (TITLE_ZONE[1] if want_hangul else TITLE_EN_ZONE[1])):
                continue
            hr = _hangul_ratio(t)
            if want_hangul and hr < 0.3:
                continue
            if not want_hangul and hr > 0.35:
                continue
            # 题名不结句(1536751 实证): 正文段落以 '…다./…요.' 收尾、宽度又占满整栏, 靠宽度+长度
            # 加分压过真题名(Section-header)当选, 随后整段正文被当题名、正文词被当作者。
            if re.search(u"(?:다|요|음|임|함|됨)\\.\\s*$", t.strip()) \
                    or len(re.findall(u"[다요음임]\\.\\s", t)) >= 1:
                continue
            first = t.strip()[:12]
            if HEAD_EXCLUDE.match(t.strip()) or HEAD_EXCLUDE.match(first):
                continue
            if u["type"] == "Section-header" and t in (u2["main_text"] or "" for u2 in j["unified"]
                                                       if u2["type"] == "Page-header"):
                continue   # 与页眉完全同文的 '标题' 是 running 头复写(防 1892171 误判)
            score = 0.0
            score += 3.0 if u["type"] == "Section-header" else 1.0
            score += min(2.0, (u["box"][2] - u["box"][0]) / j["w"] * 3)
            score += 2.0 - abs(y1 - 0.20) * 4     # 垂直位置因子(页 10%-45% 最佳)
            score += min(1.5, len(f) / 80.0)
            if score > bs:
                best, bs, bli = u, score, li
    return best, bli

_AFFIL_TAIL = re.compile(
    u"(?:초빙교수|겸임교수|명예교수|전임교수|부교수|조교수|교수|강사|선임연구원|책임연구원|"
    u"전문연구원|연구원|박사후연구원|대학원생|박사과정|석사과정|(?<=과)\\s*대학원)\\s*$")


# 机构行形态守卫(2026-11-12 二轮): AFFIL_HEAD 里的 '관'(세계관) / '학회' / '학교' 等泛词会命中
# 论文标题、摘要句、致谢句, 整行被当成机构写进 affiliations(13563526/13563529/13563531 实证)。
# 机构署名行必须"以机构词收束"或整行足够短, 且不含句子/致谢特征。
_AFF_STRICT = re.compile(
    u"(?:대학교|대학원|대학|학과|학부|연구소|연구원|연구단|연구재단|교육원|학술원|어학원|개발원|진흥원|"
    u"관리원|센터|박물관|도서관|병원|재단|학회|학교|공단|공사|협회|"
    r"University|College|Institute|Laborator|School\s+of|Department|Faculty|Center|Centre|"
    r"Academy|Hospital|Campus|Association|Society|Foundation)", re.I)
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
    return len(s) - last.end() <= 6


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


def _parse_author_line(line, u, foot_affil):
    """作者行解析: 上标集合化(*/**/***/†/‡/¹²/LaTeX $^{1)}$ 全归一) + 机构剥离 + 职称/角色词剥离
    + 括号内罗马拼音硬锚 + OCR 粘连名(홍영식서울교육대학교)按机构词表切开。
    返回 [(name, markers, affiliation, role, roman)]。"""
    line = (line or "").strip()
    if not line:
        return []
    # 副标题行整行丢弃(2632491 实证): 起首或结尾带破折号的是副标题, 作者署名从不带破折号。
    if _SUBTITLE_DASH.search(line):
        return []
    en_line = _hangul_ratio(line) < 0.2
    line = DEGREE.sub("", line)
    line = line.strip(" ·•,․")
    if not line:
        return []
    out = []
    # 英文行逗号分层; 韩文行 ·•․ 与'逗号+韩文名'分层(英文逗号多为机构内分隔)
    if en_line:
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
        mrom = re.match(u"^([가-힣\\s]{2,6})\\(([A-Za-z .\\-']+)\\)(.*)$", tk)
        if mrom:
            nm = mrom.group(1).strip()
            roman = mrom.group(2).strip()
            rest = mrom.group(3).strip()
        else:
            # OCR 粘连: 人名后直接跟机构(홍영식서울교육대학교) — 取最长 2-4 音节人名前缀,
            # 余部前 4 字内出现机构词即切开(机构专有名词 '서울교육'+대학교 实证)
            split_at = None
            for i0 in range(2, min(5, len(tk))):
                if re.fullmatch(u"[가-힣]{2,4}", tk[:i0]):
                    mm = AFFIL_HEAD.search(tk[i0:])
                    if mm and mm.start() <= 4:
                        split_at = i0
                        break
            if split_at:
                nm, affil = tk[:split_at], tk[split_at:].strip()
                rest = ""
            else:
                nm, rest = tk, ""
        if rest:
            maff2 = AFFIL_HEAD.search(rest)
            if maff2 is None and rest.strip("() ,·•"):
                pass
        nm = _norm_ws(nm)
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
        if re.search(u"[가-힣]\\s+[가-힣]", nm):
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
        if _front_head(t.split("\n")[0].strip()) or _front_head(t):
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
                if _front_head(s) or ABSTRACT_START.match(s):
                    limit = min(limit, y1)
                    break
                if _fold(s) in _title_keys:
                    continue
                out.append((s, u))
        return out

    zone_lines = _zone(t_ko)
    if t_en is not None:
        zone_lines += [z for z in _zone(t_en) if z not in zone_lines]
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
                    and _looks_affil(m.group(2)):
                affil_notes[m.group(1).strip()] = m.group(2).strip()
            elif u["type"] == "Footnote" and AFFIL_HEAD.search(ln) \
                    and not re.search(u"[「」『』]|\\d+\\s*쪽|pp\\.|https?://", ln):
                # 无标记的机构脚注: '강원대학교 기획처장. 강원대학교 법학전문대학원 교수었다.'(13563533)
                # 引文条目里的出版社/学会名不算作者单位(13563530 u0_14 实证)
                _got = _split_affils(ln)
                if _got:
                    affil_notes.setdefault("", _got[0])
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
        if len(_fold(s)) > 80 and not re.search(u"[·•]", s):
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
                if nk0 == key or (key.isdigit() and nk0 == key) or (nk0 == "*" and key == "*"):
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
    authors = [a for a in authors if not a.get("roman_source")]
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
        if line_idx:
            bl = [x.strip() for x in best.split("\n") if x.strip()]
            if bl:
                return bl[min(line_idx, len(bl) - 1)].strip()
        val = re.sub(r"\s*\n\s*", " ", best).strip()
        val = re.sub(r"^#+\s*", "", val)
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
                    if not s or len(s) > 220:
                        continue
                    if re.search(r"(Abstract|Keywords?|Journal|University|학회지|DOI|www\.|©|"
                                 r"Open Access|RESEARCH|저자)", s, re.I):
                        continue
                    names = []
                    if "," in s and re.search(r"\d|<sup>|¹|²|³", ln):     # 英作者串(带上标)
                        for seg in re.split(r"[,，]\s*", s):
                            seg = re.sub(r"\s*[<$]?\s*/?sup>?\s*\d*\s*[$>]?\s*", "", seg).strip()
                            seg = re.sub(r"\s*\*{1,2}$", "", seg).strip()
                            if _NAME_EN.match(seg):
                                names.append(seg)
                    elif _NAME_KO.match(s):
                        names = [re.sub(r"\s*\*{1,2}$", "", s).replace(" ", "")]
                    elif _NAME_EN.match(s):
                        names = [s]
                    # 老式期刊把机构直接写在作者名后的括号里: '안효성(대구대 자유전공학부 조교수)'
                    if not names:
                        mk = re.match(u"^(%s)\\s*[(（]" % _KO_NAME_CORE, s)
                        if mk:
                            names = [mk.group(1).replace(" ", "")]
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

    meta_fp = os.path.join(doc_dir, "metadata.json")
    meta = json.load(io.open(meta_fp, encoding="utf-8")) if os.path.exists(meta_fp) else {}
    meta.pop("front_notes", None)   # 遗留诊断键清理(§38 已迁 audit)
    t_ko_c, t_en_c = _conf(t_ko), _conf(t_en)
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
                  "front_notes": {"author_zone_lines": [s for s, _ in zone_lines],
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
    oks = (ok1, ok2, ok3, ok4, ok5, ok6, ok7, ok8)
    for i, ok in enumerate(oks, 1):
        if not ok:
            print("FAIL [front case %d]" % i)
    print("front selftest: %d/8 PASS" % sum(oks))
    return all(oks)


if __name__ == "__main__":
    raise SystemExit(0 if _selftest() else 1)
