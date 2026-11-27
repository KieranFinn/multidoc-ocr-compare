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
from . import journals as jreg
from . import langid as _langid

_Y_GUARD = re.compile(u"권|호|집|Vol|No|pp|ISSN|DOI|doi|Journal|학회지|학술지|『|』|AUTUMN|SPRING|SUMMER|WINTER|FALL|Том|Номер|№|nº|n°|fasc|année|год|Год|изд")
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
# 연도.호 点分形态(13563525 页眉 '2025.10'): 年前点后号, 点两侧都不许再贴数字
_YDOT = re.compile(r"(?<![\d.])((?:19|20)\d{2})\.(\d{1,2})(?!\d)")
_PAGE_OF = re.compile(r"Page\s+(\d{1,4})\s+of\s+\d{1,4}", re.I)
_SEASON = re.compile(r"\b(AUTUMN|SPRING|SUMMER|WINTER|FALL)\b", re.I)
_DOI = re.compile(r"10\.\d{4,}/[^\s\"”’]+", re.I)
_ISSN = re.compile(r"ISSN\s*[\dXx]{4}[\s\-—]*[\dXx]{3,4}", re.I)
_DOI_YV = re.compile(r"\.((?:19|20)\d{2})\.(\d{1,3})\.")
# 期位留空的双点形态: 'riks.2025..19.001' / 'igll.2025..20.006'(23073 系出版社统一体例:
# 年.期.卷.文号, 该刊不编期号时期位为空)。2025-11-13: 46 篇 한국연구 全部栽在双点上——
# 原来的 _DOI_YV 要求年后面紧跟数字, 双点直接不匹配, 卷号白白丢掉。
_DOI_YEV = re.compile(r"\.((?:19|20)\d{2})\.\.(\d{1,3})\.")
# 同体例的**四段式**(期位有值): 'xxx.2025.10.20.001' = 年.期.卷.文号。
# 2026-11-17 修正: 该解读**仅限 riks 前缀实证**(한국연구); 其余出版社(csgc/kwr/kjcss/jnah/
# espkor/jdca…)四段式实为 年.卷.期.文号, 旧代码一律按 年.期.卷 读, 卷期互换
# (kjcss.2025.7.2.006 实证: 全刊卷=年序 2024→6/2025→7, 读成期7卷2 则同年两篇"卷1/卷2"自相矛盾)。
# 与 _DOI_YV 的区别只在段数: 三段 '2025.20.006' 是 年.卷.文号, 四段才带期位。
_DOI_YIV = re.compile(r"\.((?:19|20)\d{2})\.(\d{1,3})\.(\d{1,3})\.\d{1,4}")
_YIV_RIKS = re.compile(r"/(riks|jnah)\.", re.I)   # 年.期.卷.文号 前缀白名单: riks(한국연구) + jnah(유라시아문화, 2026-11-18 增补: 页眉只印 통권 "제 N 집"=DOI 第三段, 15 篇印刷实证, 按 11-17 非 riks 默认(年.卷.期)会把它误读成期号+编造卷号)
_DOI_VIY = re.compile(r"(?<!\d)(\d{1,3})\.(\d{1,3})\.((?:19|20)\d{2})\d{0,2}\.")
# 卷..年月 DOI 形态(2026-11-19, mses=다문화사회와 교육연구 29 篇缺出版年): 'mses.11..202207.97'
# 第三段 yyyymm 即出版年月, 期位结构上不存在 —— _DOI_YEV(年..卷) 的姊妹式。
_DOI_VYY = re.compile(r"\.(\d{1,3})\.\.((?:19|20)\d{2})\d{2}\.")
# 横幅 '2023, Vol. 6, No. 2, …' 题录信号(JAMS 系): 第 1 页含此形态的块整体进来源
_YEAR_VOL = re.compile(r"(?:19|20)\d{2}\s*,\s*Vol\b", re.I)
# 法文卷号体例(fr_07 实证): '2009/6 64e année' — année 序数即卷号。
_ANNEE_VOL = re.compile(r"(?i)\b(\d{1,3})\s*(?:re)?e?\s+année\b")
# 迟到页眉/页脚题录(pt_03 实证): 'Ciência & Saúde Coletiva, 17(3):621-626, 2012'。
# 仅 Page-header/footer 命中本形态才进 source, 防正文引文被当刊头。
_VI_PY = re.compile(r"\b(\d{1,3})\s*\((\d{1,4})\)\s*:\s*(\d{1,4})\s*[-–]\s*(\d{1,4})\s*,\s*((?:19|20)\d{2})\b")
_LATE_MAST = re.compile(r"^[A-ZÀ-Ú][^,\n]{2,60},\s*\d{1,3}\s*\(\d{1,4}\)\s*:\s*\d{1,4}\s*[-–]\s*\d{1,4},\s*(?:19|20)\d{2}\b")
# 页脚刊例(2026-11-19, 산업연구 系 37 篇实测, 4 篇红): '34 산업연구 | 2025년 제9권 제2호'。
# 该刊带日期的页脚出现位置不定(13578203 拖到第 29 页才有), 首页 p0-1 来源行永远够不着,
# 故 issue_doc 对"任意页 Page-header/footer 命中本形态"的行放行进 source。
# 误报边界: 需页脚区同时印 '| 年 제권 제호' 四要素; 正文块不会被类型标成 Page-footer。
_FOOT_MAST = re.compile(r"[|｜]\s*((?:19|20)\d{2})\s*년\s*제\s*(\d{1,3})\s*권\s*제\s*(\d{1,3})\s*호")
# 页码与出版年粘连(2026-11-19, 한국진로창업경영학회지 13 篇实测): 'pp. 1 ~ 312022년 8월'。
# _YEAR 的数位 lookaround 够不着粘连位 → 出版年漏抽; _PP 同时把 '3120' 当页末 →
# pages 抽成 1-3120(13583589 实证)。一条正则同时切出真页区间与年, 并让 _PP 跳过粘连匹配。
# 误报边界: 需 'pp' 引导 + 区间 + 数字直接粘 19/20 年 + '년'; 引文页码与年份间必有分隔。
_PP_GLUE = re.compile(r"pp?\.?\s*(\d{1,4})\s*[-–~～]\s*(\d{1,4})((?:19|20)\d{2})\s*년")
# 独立出版月行(2026-11-19, 한국진로창업경영학회지 3 篇实证): 刊头块内 'pp. 111 ~ 131' 与
# '2022년 8월' 是两行, 后者无一行级刊头信号, 被 _Y_GUARD 挡掉。放行条件: 同行块(同 uid)
# 另有一行带刊头信号 + 本行是纯日期形态。误报边界: 带刊头信号的块里混印的独立日期行
# 即出版月; 正文/脚注块不会带刊头信号行。
_DATE_ONLY = re.compile(r"^\s*(?:19|20)\d{2}\s*년(?:\s*\d{1,2}\s*월)?\s*$")
# 刊头竖线段体例: '한국연구 | THE JOURNAL OF KOREAN STUDIES | 19 | 202506'
# 日期段 = yyyymm 或 yyyymmdd; 紧挨在日期前那一段纯数字就是卷号(글로컬 '| 20 | 202510' 同理)。
_MAST_DATE = re.compile(r"^\d{6}(?:\d{2})?$")
# 首页"稿件历程"脚注: '접수일(YYYY년 …), 수정일(…), 게재확정일(YYYY년 …)'。编辑部固定体例,
# 块内必须同时有 접수일 与 게재확정일 才认(正文/引文凑不齐这两个词); 只取 게재확정일 的年
# (접수일 可能跨年, 如 11월 접수 → 次年 1월 게재확정)。2026-11-17 全库测量: 62 篇命中
# (《국방과 보안》系 2023/2024/2025 三批), 全部 year 为空, 无任何其它结构题录信号。
_PUBDATE_FN = re.compile(r"게재\s*확정일\s*[(（:]?\s*((?:19|20)\d{2}|(\d{2}))\s*년")
# 变体(2026-11-17 二批实证): "게재 확정일" 带空格(13564443)、两位年 "24년"(13564442, OCR 体例);
# 两位年按世纪启发式 YY>26 → 19YY, 否则 20YY, 再交 YEAR_OK(1900-2026) 守卫。
_CH_LINE_MAX = 60          # 渠道兜底只收"刊头/页脚那么短"的行(正文/脚注行一律长于它)
_FN_ENTRY_HEAD = re.compile(r"^\s*[(（]?\d{1,3}\s*[)）.．、]\s*\S")   # 行首脚注编号
# 刊名前缀截断: 最早出现的卷期/页码/括号年/BMC 卷:文号/季节词/横幅"年 卷(期)"之前
# (2026-11-19 增末项: 'Analyses & Alternatives 2025 9(3): 027-047 DOI:…' 横幅式,
#  卷期无 Vol/No 字样, 原 boundary 够不着 → 刊名候选出不来; 双页横幅重复, 误报由投票阈值兜)
_BOUNDARY = re.compile(
    r"제\s*\d+\s*[권호]|제?\s*\d{1,4}\s*집|(?<![A-Za-z])Vol\.?\s*\d|(?<![A-Za-z])No\.?\s*\d|"
    r"pp?\.|\(\s*(?:19|20)\d{2}\s*\)|\(\s*(?:19|20)\d{2}\s*\)\s*\d{1,3}:|"
    r"AUTUMN|SPRING|SUMMER|WINTER|FALL|\s+(?:19|20)\d{2}\s+(?=\d{1,3}\s*\()", re.I)
_ENKEY = re.compile(r"Journal|Review|Quarterly|Bulletin|Annals|Proceedings|Studies|Research|Letters")
_FUNC = {"of", "and", "the", "for", "in", "on", "de", "des", "la", "le", "y", "di", "del", "&", "et"}
_KW_SPLIT = re.compile(u"[,，;；、]")


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


def _masthead_vol(s):
    """刊头竖线段体例取卷: '… | 19 | 202506' → 19;'… | 20 | 2025.10' → 20;否则 None。

    判据只有一条: 按 '|' 切段, 某段是纯数字(1..999)、**紧邻的下一段是 yyyymm[dd] 日期**。
    竖线本身不是证据(表格/正文也有), "卷号紧跟出版年月"这个组合才是——23073 系两家刊
    每页页眉都长这样, 而 '|' 段落里除卷号与日期外再无其它数字段。
    """
    if "|" not in (s or ""):
        return None
    segs = [x.strip() for x in s.split("|")]
    for i in range(len(segs) - 1):
        a, b = segs[i], segs[i + 1]
        if a.isdigit() and 1 <= int(a) <= 999 and _MAST_DATE.match(b.replace(".", "").replace("-", "")):
            return int(a)
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
    # 页脚刊例尾部 '| 2025년'(산업연구 系实证): 全文清洗循环剥不掉(Hangul 是 \w, \b 失效)
    c = re.sub(r"\s*[|｜]\s*(?:19|20)\d{2}\s*년\s*$", "", c).strip()
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
        # of the Sea'): 无 boundary/管道/数字/谚文, 词全为大写或功能词, 含刊名词 → 仅 en。
        # 2026-11-19: 先剥 'p-ISSN 2586-5439 e-ISSN …' 段(JTS 横幅把 ISSN 和刊名印同行,
        # 原数字守卫把整个候选毙掉)——剥后按无数字行走同一套判据。
        # 注意模式不许吃掉段间空格外的字符, 且两段 ISSN 都要剥(自测 ok28 实证)。
        s2 = re.sub(r"\s*[pe]-?ISSN\s*[0-9][0-9-]*", "", s, flags=re.I).strip()
        if _ENKEY.search(s2) and not re.search(u"[가-힣]", s2) and not re.search(r"\d", s2):
            words = s2.split()
            if len(_fold(s2)) >= 8 and all(re.match(r"^[A-Z]", w) or w.lower() in _FUNC
                                          for w in words):
                return None, s2.strip()
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


def _fold_accent(s):
    """口音折叠(2026-11-20 俄葡法适配): NFKD 去音符再折叠——OCR 把 ô/ê 印成不同
    码位时, 标题/作者守卫仍能认出刊名候选在行文中复写(fr_02 作者 running head 实证)。
    间距修饰符(ˆˇ˜ 等, OCR 扬抑符漂移)先行剥除, 否则 NFKD 分解不到它们。"""
    import unicodedata
    s = re.sub(u"[ˆˇ˘˙˚˛˜˝]", "", s or u"")
    s = unicodedata.normalize("NFKD", s)
    return _fold("".join(c for c in s if not unicodedata.combining(c)))


# 栏目/文类词黑名单: 大写 running head 里的栏目名不是刊名(ru_07 临床指南/ru_14 方法栏目实证)
_LATIN_RUBRIC = re.compile(
    u"^(?:клинические рекомендации|методы и методология|горячая тема|исследования|"
    u"экономика и образование|переводы|рецензии|научная жизнь|information|editorial|artigo original|"
    u"documento para|diretrizes brasileiras|valores normais)", re.I)


def _latin_jname(s, lang):
    """俄/葡/法刊名补充形态(2026-11-20, 证据全部来自 91 篇语料 journal_lines 汇总):
    ru: 全大写西里尔刊头(含刊名信号词) / 双语斜杠刊名; pt+fr+en: NLM 缩写型
    'Arq Neuropsiquiatr 2003;61(3-B):777-781'、'Rev. sci. tech. Off. int. Epiz., 2012, 31 (1)',
    'Annals of Mathematics, 171 (2010), 1591–1646', 'Saber (e) Educar 13 | 2008',
    'Revue de communication sociale et publique' 句型。返回 (cand, None) 进主 journal 槽。"""
    s0 = re.sub(r"\*+", "", (s or "")).strip()
    s0 = re.sub(r"</?(?:sup|sub)\b[^>]*>", "", s0, flags=re.I)
    s0 = re.sub(r"\s+", " ", s0).strip()
    if not s0 or s0.startswith("(") or _LATIN_RUBRIC.match(s0):
        return None
    if lang in ("pt", "fr", "en"):
        # JAMS 刊头 OCR 变体: 'JAMS / JOURNAL OF ADVANCES…'、'JAMS JOURNAL…'、
        # 'J A JAMS M S JOURNAL OFADVANCES…' 全部归一到官方题名(2026-11-24 KCI11_17 7 篇实证)。
        if re.search(r"(?i)\bJAMS\b|ADVANCES\s+IN\s+MILITARY\s+STUDIES", s0):
            return ("Journal of Advances in Military Studies", None)
        # KCI11_17 2026-11-24: 'Analyses & Alternatives 2025 9(3): …' 名-年-卷(期) 横幅
        # (该刊被 langid 误判 pt/fr 时走本函数, 不能只吃带 Vol/No 的旧式)。候选限无句读
        # 短名且含 '&' 或刊名信号词, 防把题名/引文行当刊名。
        m = re.match(r"^([A-Z][A-Za-z'’-]*(?:\s+(?:[A-Z][A-Za-z'’-]*|&)){1,7})\s+"
                     r"(?:19|20)\d{2}\s+\d{1,3}\s*\(\d{1,3}\)", s0)
        if m and ("&" in m.group(1) or _ENKEY.search(m.group(1).title())):
            return (_clean_jname(m.group(1).strip(" ,.-")), None)
        # JAMS 多行刊头: 'JAMS / JOURNAL OF ADVANCES IN MILITARY STUDIES' 或连写同形。
        m = re.match(r"^(?:[A-Z]{2,8}\s*/\s*)?([A-Z][A-Z&'’-]*(?:\s+[A-Z][A-Z&'’-]*){2,10})$", s0)
        if m and _ENKEY.search(s0.title()):
            cand = m.group(1)
            if "/" in cand:
                parts = [p.strip() for p in cand.split("/")]
                if parts and _ENKEY.search(parts[-1].title()):
                    cand = parts[-1]
            return (_clean_jname(cand.strip(" ,.-")), None)
    if lang == "fr":
        m = re.match(u"^Dans\\s+(.+?)\\s+(?=(?:19|20)\\d{2}\\s*/)", s0, re.I)
        if m:
            return (_clean_jname(m.group(1).strip(" ,.-")), None)
        m = re.search(u"\\b((?:Revue|Annales|Cahiers|Bulletin|Actes)\\b[^,\\n]{2,60})"
                      u",\\s*(?:19|20)\\d{2}\\s*,\\s*\\d+", s0, re.I)
        if m:
            return (_clean_jname(m.group(1).strip(" ,.-")), None)
        m = re.match(u"^((?:Revue|Annales|Cahiers|Bulletin|Actes)\\b[^,\\n]{2,60})"
                     u",\\s*\\d{1,3}(?:-\\d+)?,\\s*(?:19|20)\\d{2}\\s*,", s0, re.I)
        if m:
            return (_clean_jname(m.group(1).strip(" ,.-")), None)
    if lang == "pt":
        m = re.match(u"^((?:Revista|Jornal|Cadernos|Estudos|Perspectivas|Boletim|Anais)\\b[^,\\n]{2,60})"
                     u"\\s+Ano\\s+(?:[IVXLC]+|\\d+)\\b", s0, re.I)
        if m:
            return (_clean_jname(m.group(1).strip(" ,.-")), None)
        m = re.match(u"^(.+?),\\s+[A-ZÀ-Ú][\\wÀ-ú-]+(?:\\s+[\\wÀ-ú-]+){0,2},\\s+ano\\s+\\d+\\b", s0, re.I)
        if not m:
            m = re.match(u"^(.+?),\\s+ano\\s+(?:[IVXLC]+|\\d+)\\b", s0, re.I)
        if m:
            return (_clean_jname(m.group(1).strip(" ,.-")), None)
    if lang in ("pt", "fr"):
        m = re.match(u"^([^,]{3,60}),\\s*\\d+\\(\\d+\\):\\d+-\\d+,\\s*(?:19|20)\\d{2}\\b", s0)
        if m:
            return (_clean_jname(m.group(1).strip(" ,.-")), None)
        m = re.match(u"^((?:Revista|Jornal|Cadernos|Estudos|Perspectivas|Boletim|Anais|Cahiers)"
                     u"\\b[^,\\n]{2,60}),\\s*(?:n[º°.]|ano\\b|v\\.|vol\\.|\\d)", s0, re.I)
        if m:
            return (_clean_jname(m.group(1).strip(" ,.-")), None)
    if lang == "ru":
        m = re.match(u"^([А-ЯЁ][А-ЯЁ\\s:—-]{3,70}?)\\s+(?:19|20)\\d{2}\\s+(?:№|Vol\\.|Том)", s0)
        if not m:
            m = re.match(u"^([А-ЯЁ][А-ЯЁ\\s:—-]{3,70}?)\\s+№\\s*\\d+", s0)
        if m:
            return (_clean_jname(m.group(1).strip(" ,.-")), None)
        m = re.search(u"//\\s*(.+?)\\s*[.]+\\s*[—–-]?\\s*(?:19|20)\\d{2}\\b", s0)
        if m:
            return (_clean_jname(m.group(1).strip(" ,.-")), None)
        m = re.match(u"^([А-ЯЁ][А-ЯЁ\\s-]+?)\\.\\s*(?:19|20)\\d{2}\\.\\s*ТОМ", s0)
        if m:
            return (_clean_jname(m.group(1).strip(" ,.-")), None)
        m = re.match(u"^[«\\\"'](.+?)[»\\\"']\\s+(?:19|20)\\d{2}\\s+(?:№|no\\.)", s0, re.I)
        if m:
            return (_clean_jname(m.group(1).strip(" ,.-")), None)
        m = re.match(u"^(.+?журнал)\\s*[▪|]?\\s*№", s0, re.I)
        if m:
            return (_clean_jname(m.group(1).strip(" ,.-")), None)
    fl = _fold(s0)
    if not (4 <= len(fl) <= 75):
        return None
    if lang == "ru":
        letters = [c for c in s0 if c.isalpha()]
        cyr = sum(1 for c in letters if u"Ѐ" <= c <= u"ӿ")
        if letters and cyr >= 3 and not re.search(r"\d", s0) \
                and sum(1 for c in letters if c.isupper()) / float(len(letters)) >= 0.85 \
                and re.search(u"(?i)журнал|известия|вестник|труды|наук|эконом|академ|"
                              u"университет|сборник|обозрение|cybernetics|journal", s0):
            return (_clean_jname(s0), None)
        m = re.match(u"^([А-ЯЁ][^/\\n]{2,50}/\\s*[A-Z][^\\n]{2,60}?)[\\.\\s]+(?:19|20)\\d{2}\\s*;", s0)
        if m:
            return (_clean_jname(m.group(1)), None)
        m = re.match(u"^([А-ЯЁ][^/\\n]{2,50}/\\s*[A-Z][^\\n]{2,60})$", s0)
        if m:
            return (_clean_jname(m.group(1)), None)
        return None
    # 拉丁: NLM/温哥华缩写型(缩写词允许句点与小写词: 'Rev. sci. tech.', 后接年;卷或年, 卷)
    m = re.match(r"^((?:[A-ZÀ-Ú]?[A-Za-zÀ-ú\-]{1,15}\.?\s*){1,7})[\.,]?\s+(19|20)\d{2}\s*[;,]\s*\S", s0)
    if m:
        cand = _clean_jname(m.group(1).strip(" .,"))
        if cand and re.search(r"(?i)rev|j\b|arq|acta|ann|bull|arch|bras|paul|esc|univ|off|"
                              r"int|sci|tech|neuro|cardiol|enferm|fisioter|epiz", cand):
            return (cand, None)
    m = re.match(r"^([A-ZÀ-Ú][A-Za-zÀ-ú\-\s]{2,50}?),?\s+\d{1,4}\s*\((?:19|20)\d{2}\)", s0)
    if m:
        return (_clean_jname(m.group(1).strip(" ,")), None)
    m = re.match(u"^((?:[A-ZÀ-Ú(][\\w'’()\\-]*\\s+){1,5}[A-ZÀ-Ú(][\\w'’()\\-]*)\\s+\\d{1,4}\\s*\\|\\s*(?:19|20)\\d{2}", s0)
    if m:
        return (_clean_jname(m.group(1)), None)
    m = re.match(u"^(?:[A-Z][\\w'’-]{1,20}\\.\\s+)?((?:Revue|Annales|Cahiers|Bulletin|[eé]tudes|"
                 u"Travaux|Actes|Communications?)\\s+(?:de|des|du|d'|fran[çc]aise?)\\s+[^\\n]{3,50})$",
                 s0, re.I)
    if m:
        return (_clean_jname(m.group(1)), None)
    m = re.match(u"^((?:Revista|Jornal|Cadernos|Estudos|Perspectivas|Boletim|Anais)\\s+"
                 u"(?:de|da|do|di|brasileir[ao]s?)\\s+[^\\n]{2,50})$", s0, re.I)
    if m:
        return (_clean_jname(m.group(1)), None)
    # 小写 running 刊名(pt_01 'remark revista brasileira de marketing' 实证)
    m = re.match(u"^((?:[a-zà-ú0-9&]+\\s+){1,5}[a-zà-ú0-9&]+)$", s0)
    if m and re.search(r"(?i)revista|journal|jornal|review|science|research|magazine", s0):
        return (_clean_jname(m.group(1)), None)
    # 刊名+城市段: 'Rev. bras. fisioter., São Carlos, v. 11' /
    # 'Resenhas EccoS Rev. Cient., UNINOVE, São Paulo: (1): 81-102'(pt_07/fr_12 实证)
    m = re.match(u"^((?:[A-Za-zÀ-ú&]+\\.?\\s*){2,6}?),\\s+(?:[A-Z]{2,}|[A-ZÀ-Ú][\\wà-ú]+"
                 u"(?:\\s+[\\wà-ú]+)?)\\s*[,:]", s0)
    if m:
        cand = _clean_jname(m.group(1).strip(" ,"))
        if cand and re.search(u"(?i)rev|cient|journal|arq|acta|ann|bull|arch|bras|paul|esc|"
                              u"univ|off|int|sci|tech|neuro|cardiol|enferm|fisioter|epiz|"
                              u"estud|cad|pesqui|saber", cand):
            return (cand, None)
    # 单词小写刊名+年卷: 'haematologica 2012;97(4):…'(fr_20 实证)
    m = re.match(r"^([a-z][a-z\-]{2,20})\s+(19|20)\d{2}\s*[;,]\s*\d", s0)
    if m:
        return (_clean_jname(m.group(1)), None)
    return None


def extract(source, n_pages, excluded_folds, lang=None):
    """核心抽取(纯函数, selftest 直接喂构造行)。
    source: [(page, uid, line)]; excluded_folds: 本篇标题/作者的折叠集合(守卫);
    n_pages: 物理页数。返回结果 dict(含 audit 段)。
    lang(2026-11-20 俄葡法适配): 'ru'/'pt'/'fr' 时行首剥 ** 残留, 且 _jname_from_line
    落空后走 _latin_jname 刊名形态(NLM 缩写/全大写西里尔刊头/双语斜杠/Revue de…)。"""
    journal, journal_en = {}, {}
    year, year_strong = {}, {}      # year_strong: DOI/ISSN 行上的年份(出版社自带, 优先)
    vol_kwan, vol_vol, vol_jip, issue, pages_pp, pages_bare = {}, {}, {}, {}, {}, {}
    issue_jip = {}   # 제N집 老式连续页码期刊: 无 권/호 时"第N集"就是期号(最低优先级候选组)
    issue_season = {}  # 季刊页眉 'KOREA JOURNAL / AUTUMN 2002': 季节词本身即期次标识
    tongkwan, bmc_art = [], []
    guard_uid = {uid for _, uid, s0 in source
                 if uid is not None and _Y_GUARD.search(s0)}   # 独立出版月行放行(见 _DATE_ONLY)
    for page, uid, s in source:
        if len(_fold(s)) > 130 and not (lang in ("ru", "pt", "fr", "en") and "©" not in s
                                        and (re.search(u"//\\s*[^/]+[.]\\s*[—–-]?\\s*(?:19|20)\\d{2}", s)
                                             or re.search(r"(?i)Revue\s+[^,\n]+,\s*(?:19|20)\d{2}\s*,\s*\d+", s))):
            continue   # 正文粘入的误标长行; 首页引文中的本刊题录除外
        if lang in ("ru", "pt", "fr", "en"):
            # 2026-11-20 俄葡法适配: 拉丁文献跳过韩刊专用的 _jname_from_line(《》/권호 体系),
            # 统一走 _latin_jname; OCR 加粗残留(**31**)先行剥离(fr_17 实证)。
            s = re.sub(r"\*+", "", s).strip()
            s = re.sub(r"^©(?=\s*(?:Revista|Revue|Jornal))", "", s, flags=re.I).strip()
            got = None if ("©" in s or u"droits réservés" in s.lower()) else _latin_jname(s, lang)
        else:
            got = None if ("©" in s or u"droits réservés" in s.lower()) else _jname_from_line(s)
        if got:
            ko, en = got
            if ko and _fold(ko) not in excluded_folds \
                    and _fold_accent(ko) not in excluded_folds \
                    and 2 <= len(_fold(ko)) <= (80 if lang in ("ru", "pt", "fr", "en") else 50):
                _add(journal, ko, page, uid)
                if en:
                    _add(journal_en, en, page, uid)
            elif en and _fold(en) not in excluded_folds \
                    and _fold_accent(en) not in excluded_folds \
                    and 2 <= len(_fold(en)) <= 80:
                _add(journal_en, en, page, uid)
        # 年(刊头信号守卫 + 数字/连字符夹住排除; 独立出版月行经 guard_uid 放行)
        if _Y_GUARD.search(s) or (uid in guard_uid and _DATE_ONLY.match(s)):
            for m in _YEAR.finditer(s):
                y = int(m.group(1))
                if y in YEAR_OK:
                    _add(year, y, page, uid)
                    if _DOI.search(s) or _ISSN.search(s):
                        _add(year_strong, y, page, uid)   # 出版社自己的年, 不与被引文献的年份平票
        # 刊头竖线段取卷(见 _masthead_vol): 无 'Vol.' 字样时这是唯一可用的卷号
        mv = _masthead_vol(s)
        if mv is not None:
            _add(vol_vol, mv, page, uid)
        # 卷(三组)/期(통권 旁路)
        for m in _KWAN.finditer(s):
            _add(vol_kwan, int(m.group(1)), page, uid)
        for m in _JIP.finditer(s):
            v = int(m.group(1))
            _add(vol_jip, v, page, uid)
            # 「제N집 / N집」是连续页码期刊的唯一期次标识。它同时进卷组(既有口径)与期组,
            # 期组只在 제N호/No.N 等主候选全空时启用, 否则老期刊的"期"永远为 null。
            if 1 <= v <= 999:
                _add(issue_jip, v, page, uid)
        for m in _VOLEN.finditer(s):
            _add(vol_vol, int(m.group(1)), page, uid)
        for m in _HO.finditer(s):
            if "통권" in s[max(0, m.start() - 3):m.start()]:
                continue
            v = int(m.group(1))
            if 1 <= v <= 999:
                _add(issue, v, page, uid)
        for m in _NOEN.finditer(s):
            v = int(m.group(1))
            if 1 <= v <= 999:        # 脚注里的 "I.D. No. 715579" 不是期号(2002 英文期刊实证)
                _add(issue, v, page, uid)
        if _YEAR.search(s):          # 季刊: 同一行有年份 + 季节词 → 该季节即本期
            ms = _SEASON.search(s)
            if ms:
                _add(issue_season, ms.group(1).upper(), page, uid)
        if lang == "fr":
            for m in _ANNEE_VOL.finditer(s):
                _add(vol_vol, int(m.group(1)), page, uid)
        # 迟到页眉/页脚 '17(3):621-626, 2012' 一次性供出卷/期/页/年。
        for m in _VI_PY.finditer(s):
            _add(vol_vol, int(m.group(1)), page, uid)
            _add(issue, int(m.group(2)), page, uid)
            _add(pages_pp, (int(m.group(3)), int(m.group(4))), page, uid)
            _add(year, int(m.group(5)), page, uid)
            _add(year_strong, int(m.group(5)), page, uid)
        for m in _TONGKWAN.finditer(s):
            tongkwan.append({"value": int(m.group(1)), "page": page})
        # BMC (연도) 권:문号
        for m in _BMC.finditer(s):
            if int(m.group(2)) < 999:
                _add(vol_vol, int(m.group(2)), page, uid)
            _add(year, int(m.group(1)), page, uid)
            bmc_art.append({"value": int(m.group(3)), "page": page})
        # 연도.호 形态: 只在同一行**另有 N호 标记**时才把点分后半当期号。
        # 2025-11-13 修正(글로컬 16 篇实证): 页眉 '… | 20 | 2025.10' 里的 2025.10 是
        # 出版年月(同一位置的单页印的是 '202510'), 把 10 当期号等于拿月份凑字段;
        # 该刊 DOI 的期位本来就是空的(igll.2025..20.006), 即原文不编期号。
        if not _DOI.search(s) and _HO.search(s):
            for m in _YDOT.finditer(s):
                y0, i0 = int(m.group(1)), int(m.group(2))
                if y0 in YEAR_OK and 1 <= i0 <= 99:
                    _add(issue, i0, page, uid)
        # 页码-年粘连: 真区间进页组、年进年组(_YEAR 的 lookaround 够不着粘连位)
        for m in _PP_GLUE.finditer(s):
            b0, y0 = int(m.group(2)), int(m.group(3))
            if b0 < 999 and y0 in YEAR_OK:
                _add(pages_pp, (int(m.group(1)), b0), page, uid)
                _add(year, y0, page, uid)
        # 页
        for m in _PP.finditer(s):
            a, b = int(m.group(1)), int(m.group(2))
            if re.match(r"^\d{1,2}\s*년", s[m.end():]):
                continue   # 粘连形态: 真区间已由 _PP_GLUE 收, 防 '3120' 充页末
            if a < b and b < 9999:
                _add(pages_pp, (a, b), page, uid)
        for m in _BARE_RANGE.finditer(s):
            a, b = int(m.group(1)), int(m.group(2))
            if a < b and a < 999 and b < 999:
                _add(pages_bare, (a, b), page, uid)
        # DOI corroboration
        if _DOI.search(s):
            got4 = list(_DOI_YIV.finditer(s))   # 期位有值: 年.期.卷.文号
            for m in got4:
                _add(year, int(m.group(1)), page, uid)
                _add(year_strong, int(m.group(1)), page, uid)
                if _YIV_RIKS.search(s):   # 年.期.卷.文号(仅 riks 实证)
                    _add(issue, int(m.group(2)), page, uid)
                    _add(vol_vol, int(m.group(3)), page, uid)
                else:                     # 年.卷.期.文号(其余出版社默认)
                    _add(vol_vol, int(m.group(2)), page, uid)
                    _add(issue, int(m.group(3)), page, uid)
            for m in _DOI_YEV.finditer(s):     # 期位留空的双点: 年..卷.
                _add(year, int(m.group(1)), page, uid)
                _add(year_strong, int(m.group(1)), page, uid)
                _add(vol_vol, int(m.group(2)), page, uid)
            for m in _DOI_VYY.finditer(s):     # 卷..年月(期位不存在): mses.11..202207.97
                _add(year, int(m.group(2)), page, uid)
                _add(year_strong, int(m.group(2)), page, uid)
                _add(vol_vol, int(m.group(1)), page, uid)
            if not got4:
                # 四段式命中时不能再跑这条: '2025.10.20.001' 里的 '2025.10.' 会把**期**当卷
                # 塞进 vol_vol, 与上面的 20 打平票。
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
        _full_names = [v for v in p0list if not re.search(r"\b(?:Rev|bras|fisioter)\.", v, re.I)]
        j_w = (_full_names or p0list)[0]
        j_sup = [0]
    else:
        j_w, j_sup = _vote(journal, 0)
    p0en = [v for v, ps in journal_en.items() if any(p == 0 for p, _ in ps)]
    if p0en:
        j_en_w = p0en[0]
        j_en_sup = [0]
    else:
        j_en_w, j_en_sup = _vote(journal_en, 0)
    # 纯英文期刊(2002 KOREA JOURNAL / 2019 SpringerOpen 实证): _jname_from_line 只把它
    # 投进 journal_en, 但 metadata 的 journal 才是主字段(查看器/质检都认它)。
    # 仅在韩文刊名完全没候选时回退, 已有韩文刊名的篇不受影响。
    if j_w is None and j_en_w:
        j_w, j_sup = j_en_w, j_en_sup

    def _lvl(sup):
        return "high" if len(sup) >= 2 else ("mid" if sup else "low")

    def _uid0(cand, winner):
        """胜出值首个命中行的 unified uid(来源行锚): viewer 来源行点击跳转原文块用。
        页码推断兜底(pages_derived)无来源行, uid=None。"""
        if not cand or winner is None or winner not in cand:
            return None
        first = cand[winner][0]
        return first[1] if isinstance(first, tuple) else None

    # 年: DOI/ISSN 行上的年份是一等证据(出版社印的出版年), 有就只在它们之间投票。
    # 否则首页脚注里的被引书年份会跟刊头年份平票, 而 _vote 平票取小者 → 抽成 1907(13563564 实证)。
    if year_strong:
        y_w, y_sup = _vote(year_strong, 0)
    else:
        y_w, y_sup = _vote(year, 0)
    vol_w, vol_sup = None, []
    vol_grp = None
    for grp in (vol_kwan, vol_vol, vol_jip):   # 권 > Vol > 집
        if grp:
            vol_w, vol_sup = _vote(grp, 0)
            vol_grp = grp
            break
    i_w, i_sup = _vote(issue, 0)
    i_grp = issue
    if not issue and issue_jip:
        i_grp = issue_jip
        i_w, i_sup = _vote(issue_jip, 0)
    elif not issue and issue_season:
        i_grp = issue_season
        i_w, i_sup = _vote(issue_season, 0)
    pg_w, pg_sup = None, []
    pg_grp = None
    if pages_pp:
        pg_w, pg_sup = _vote(pages_pp, 0)
        pg_grp = pages_pp
    elif pages_bare:
        pg_w, pg_sup = _vote(pages_bare, 0)
        pg_grp = pages_bare
    # 结构上无期号(2026-11-13, 用户拍板 P1): 只认出版社自己的证据 —— DOI 期位留空
    # (年..卷 / 年.卷.文号)。这类刊按通卷编号, 期号在原文结构里不存在, 不是抽取失败。
    # 证据只写进 confidence.source, 由 qc 决定"期"要不要报缺; 值仍然是 None(不臆造)。
    issue_volume_only = i_w is None and any(
        _DOI_YEV.search(s) or _DOI_YV.search(s) or _DOI_VYY.search(s) for _, _, s in source)
    res = {
        "journal": {"value": j_w, "uid": _uid0(journal, j_w),
                    "confidence": {"level": _lvl(j_sup), "source": "regex"}},
        "journal_en": {"value": j_en_w, "uid": _uid0(journal_en, j_en_w),
                       "confidence": {"level": _lvl(j_en_sup), "source": "regex"}},
        "year": {"value": y_w, "uid": _uid0(year, y_w),
                 "confidence": {"level": _lvl(y_sup), "source": "regex"}},
        "volume": {"value": vol_w, "uid": _uid0(vol_grp, vol_w),
                   "confidence": {"level": _lvl(vol_sup), "source": "regex"}},
        "issue": {"value": i_w, "uid": _uid0(i_grp, i_w),
                  "confidence": ({"level": "high", "source": "volume_only",
                                  "evidence": "DOI 期位留空(年..卷 / 年.卷.文号)"}
                                 if issue_volume_only
                                 else {"level": _lvl(i_sup), "source": "regex"})},
        "pages": {"value": ("%d-%d" % pg_w) if pg_w else None,
                  "uid": _uid0(pg_grp, pg_w),
                  "confidence": {"level": _lvl(pg_sup), "source": "regex"}},
    }
    audit = {"journal_candidates": {k: sorted(p for p, _ in v) for k, v in journal.items()},
             "journal_en_candidates": {k: sorted(p for p, _ in v) for k, v in journal_en.items()},
             "year_candidates": {str(k): sorted(p for p, _ in v) for k, v in year.items()},
             "year_candidates_strong": {str(k): sorted(p for p, _ in v)
                                        for k, v in year_strong.items()},
             "volume_candidates": dict(
                 [("kwan:%d" % k, sorted(p for p, _ in v)) for k, v in vol_kwan.items()]
                 + [("vol:%d" % k, sorted(p for p, _ in v)) for k, v in vol_vol.items()]
                 + [("jip:%d" % k, sorted(p for p, _ in v)) for k, v in vol_jip.items()]),
             "issue_candidates": {str(k): sorted(p for p, _ in v) for k, v in issue.items()},
             "issue_jip_candidates": {str(k): sorted(p for p, _ in v) for k, v in issue_jip.items()},
             "pages_candidates": {"%d-%d" % k: sorted(p for p, _ in v) for k, v in
                                  dict(list(pages_pp.items()) + list(pages_bare.items())).items()},
             "tongkwan": tongkwan, "bmc_article_no": bmc_art}
    if issue_volume_only:
        audit["issue_note"] = "DOI 期位留空(年..卷 / 年.卷.文号), 原文不编期号"
    if pg_w is None:
        res["pages"]["confidence"]["level"] = "low"
    res["_audit"] = audit
    res["_internal"] = {"pages_pp": pages_pp, "pages_bare": pages_bare}
    return res


def _derive_pages(page_values, last_body=None):
    """页码推断: offset=印刷号-物理页序号 众数回填; 返回 (first, last, support, offset) 或 None。

    last_body: 最后一个**有正文块**的物理页序号。末尾只有水印的页(KCI 扫描件末页常见, 整页
    仅 'www.kci.go.kr')不算文章内容页, 否则区间会多算 1 页(1892171 实证: 印刷页 1~33,
    物理 34 页, 末页无正文)。缺省 None 时退回"到最后一个物理页"。
    """
    offs = collections.Counter()
    for p, v in page_values.items():
        if v is not None:
            offs[v - p] += 1
    if not offs:
        return None
    offset, support = offs.most_common(1)[0]
    last_p = max(page_values)
    if last_body is not None:
        last_p = min(last_p, last_body)
    return offset, offset + last_p, support, offset


def _kw_parse(s, lines, i):
    """单条 KW_LINE 行 → (en, items, raw) 或 None。en=头词含 ASCII 字母(英文组);
    有冒号取冒号后, 无冒号剥头词尾部/空白; 剥 '(Key Words)' 式标签残留(B3);
    同块续行(行尾逗/分号/· 未完结, ≤4 行, 遇头词/摘要头/목차行停); 逗号/分号切分。
    items 长度>1、≤15; raw=命中行+续行原文(审计留痕)。"""
    s0 = s
    # '*' 与 ■/◆ 同属列表项符号(13570516/13570522 实证: '* 키워드 : …' 被 KW_LINE 拒之门外,
    # 于是后页正文首行 '키워드 분석에서…' 顶替成关键词)。剥标记后再匹配, 结构与 ■ 等价。
    s = re.sub(u"^\\*+\\s*", "", s)
    m = KW_LINE.match(s)
    if not m:
        return None
    en = bool(re.search(r"[A-Za-z]", m.group(1) or ""))
    rest = s[m.end():]
    rest = re.sub(u"^\\s*[\\]】）)]\\s*", "", rest)   # '[Key-words]' / '【키워드】' 尾括号角
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
    raw = [s0] + [lines[k].strip() for k in range(i + 1, jj) if lines[k].strip()]
    items = [x.strip().strip(".").strip() for x in _KW_SPLIT.split(acc)]
    if len(items) == 1 and re.match(r"^(?:关\s*键\s*词|關\s*鍵\s*詞|关\s*键\s*字)", s):
        items = [x.strip().strip(".。") for x in items[0].split() if x.strip()]
    items = [x for x in items if x and len(x) > 1][:15]
    return en, items, raw


def _strip_glued_byline(kws, kw_loc, pdata, meta):
    """[结构判据] 剥掉粘在关键词尾部的署名+机构。

    根因①(整列不同): 老式版式把署名行压在关键词行同一 Text 块, 文本层读成
    '…, 복지사업황해동 한국지방행정연구원' —— 最后一个关键词与署名行零空格粘连。
    不用机构词表/宽正则(此前在 abs.py 试正则误切真关键词, 已回退); 改用两条硬证据:
      1. 同页存在**独立块**, 其整行文本 == 粘连后缀;
      2. 该独立块含本篇已抽出的作者名(authors[].name)。
    两条同时满足才剥, 且只剥"整段后缀", 不切词内部。幂等: 重跑仍从原文抽到同一粘连行。
    """
    if not kws or not kw_loc:
        return kws
    names = [str(a.get("name") or "").strip() for a in (meta.get("authors") or [])
             if isinstance(a, dict) and a.get("name") and len(str(a["name"]).strip()) >= 2]
    if not names:
        return kws
    page = kw_loc.get("page")
    pg = pdata.get((page or 1) - 1) if page else None
    if pg is None:
        return kws
    bylines = []
    for u in pg.get("unified", []):
        t = (u.get("main_text") or "").strip()
        if not t or len(t) > 60 or KW_LINE.match(t):
            continue
        if any(n in t for n in names):
            bylines.append(t)
    if not bylines:
        return kws
    out = list(kws)
    for i, item in enumerate(out):
        for bl in bylines:
            pos = item.rfind(bl)
            if pos <= 0:               # 只剥尾部; pos==0 表示整项就是署名, 不当关键词处理
                continue
            head = item[:pos].rstrip(u" ,，·")
            if len(head) > 1:
                out[i] = head
                break
    return out


def _publication_year(source):
    for _page, _uid, line in source:
        m = re.search(r"출판연도\s*[:：]\s*((?:19|20)\d{2})(?!\d)", line)
        if m and int(m.group(1)) in YEAR_OK:
            return int(m.group(1))
    return None


def _pubdate_fn_year(pdata):
    u"""稿件历程脚注(접수일…게재확정일)→ (year, uid) 或 (None, None)。

    仅在年/卷/期抽取全空(年无候选)时由 issue_doc 调用; 已有年份的篇一律不触发。
    页级双词守卫(正文/引文凑不齐这两个编辑部固定词); 行首锚 (원고)?접수일 / 게재확정일;
    只取 게재확정일 的年(접수일 可能跨年, 如 11월 접수 → 次年 1월 게재확정)。
    2026-11-18 增补体例(중국사회과학논총 8 篇实证): '원고접수일: YYYY년 MM월 DD일' 冒号体例,
    日期行在末页; 13569971/13569973 实证 원고접수일 与 게재확정일 分处两个块 → 守卫放宽到页级
    (行首锚不变, 特异性不降级)。"""
    for p in sorted(pdata.keys()):
        anchored = []
        for u in pdata[p]["unified"]:
            for ln in (u.get("main_text") or "").splitlines():
                s0 = ln.strip()
                if s0.startswith(u"접수일") or s0.startswith(u"원고접수일") \
                        or s0.startswith(u"게재확정일"):
                    anchored.append((s0, u.get("uid")))
        if not anchored:
            continue
        has_recv = any(s0.startswith(u"접수일") or s0.startswith(u"원고접수일")
                       for s0, _u in anchored)
        for s0, uid in anchored:
            if not re.search(u"게재\\s*확정일", s0) or not has_recv:
                continue
            m = _PUBDATE_FN.search(s0)
            if m:
                y = int(m.group(1))
                if m.group(2):                      # 两位年: 26 이하 → 2000년대
                    y = 2000 + y if y <= 26 else 1900 + y
                if y in YEAR_OK:
                    return y, uid
    return None, None

def _apply_running_journal(res, source, excluded):
    current = res["journal"]["value"]
    if current is not None and not re.search(r"^\W*\d+\)|^[\[<`]|등$|그림", str(current)):
        return False
    votes = collections.defaultdict(set)
    display = {}
    for page, _uid, line in source:
        s = line.strip()
        # 页脚刊名带页码(2026-11-17: 중국사회과학논총 'The Korea Journal of Chinese Social Science 31'
        # / '4 중국사회과학논총' 6 篇实证): 先剥行首/行尾页码再进守卫; 剥后仍带数字的行维持原排除。
        _s2 = re.sub(r"^\d{1,4}\s+", "", re.sub(r"\s+\d{1,4}\s*$", "", s))
        if _s2 != s and not re.search(r"\d", _s2):
            s = _s2
        folded = _fold(s)
        if not (4 <= len(folded) <= 50) or re.search(r"\d|`|www\.", s):
            continue
        if re.search(r"\\[A-Za-z]+\{", s):   # LaTeX 残片不当刊名 (2000년대 스캔본 수식 OCR \end{bmatrix} 当选 실증, KCI_FI001536751_2000)
            continue
        if re.search(u"[<>\u3008\u3009\u3010\u3011\u2460-\u2473\u005b\u005d]", s):
            # 公式残片第二形态: OCR 矩阵/特征结构碎片带角括号/圈码/方括号(TFA [TOP <1,2>] 实证);
            # 误报边界: 正常刊名不含 〈〉【】①-⑳ 与 [] (韩刊刊名用 ()/『』, 已保留)。
            continue
        if not re.search(u"[가-힣]", s) and len(re.findall(r"[A-Za-z]+", s)) < 2:
            # 无韩文且连两个英文单词都凑不齐 → 不当刊名(PHON/TOP 类公式特征词实证,
            # KCI_FI001536751_2000 连投三票: bmatrix→TFA[TOP…]→PHON);
            # 误报边界: 单词刊名未见实例, 若真出现仍可由 DOI 系列/渠道兜底, 不会丢刊。
            continue
        if any(len(ex) >= 8 and (folded in ex or ex in folded or _gdice(folded, ex) >= 0.80)
               for ex in excluded):
            continue
        if re.search(r"[.!?:：;；|]|https?://|www\.", s):
            continue
        # 2026-11-25: 机构署名行不得当选 running 刊名(13570501 等 11 篇实证):
        # '전북대학교 문화융복합아카이빙연구소' 每页重复出现被当刊名, 压过页眉首行的
        # 真刊名 '디지털문화아카이브지'。全库实测以大学/研究所/学科结尾的刊名仅这 11 篇,
        # 全是错误值, 零误伤; 拦掉后真刊名(本来就在候选池)自然胜出。
        if re.search(u"(?:대학교|연구소|연구원|연구재단|학과|교수|대학)$", s.strip()):
            continue
        votes[folded].add(page)
        display[folded] = s
    candidates = [(len(pages), key) for key, pages in votes.items() if len(pages) >= 2]
    if not candidates:
        return False
    _count, key = max(candidates)
    res["journal"] = {"value": display[key], "uid": None,
                      "confidence": {"level": "mid", "source": "running_header"}}
    return True


def _is_journal_fragment(value):
    """断行残片形态的刊名(2026-11-25, 5 篇 jdca 实证): 原文页眉 'Journal of | Culture |
    Archives' 被 OCR 断成三行, 抽取只吃到前两行 'Journal of'。全库精确命中这 5 篇。"""
    return bool(re.match(r"^(?:Journal\s*of|Journalof|The\s+Journal|Of\s+[A-Z]|Vol\.?|No\.?)\s*$",
                         str(value or "").strip(), re.I))


def _should_registry_override(journal_field, registry_value, manual_mismatch=False):
    """低置信猜测 / manual 注册表错值可被高置信值纠正(2026-11-25)。

    两条放行路径:
      ① running_header / channel_fallback 来源 + 注册表值韩文 —— 纠正题名/作者名当刊名
         (13568652/677、13570527/529/564 五篇实证, 零误伤)。
      ② manual_mismatch: DOI 前缀命中 manual 行且当前值≠manual 刊名 —— 人工双证实的
         封闭值域纠正自动判定的错值(kjcss 18 篇英文名塞进 journal 槽, 零误伤)。
    corpus 投票行一律不参与纠正(会自我强化错误), 只有 manual 行有此权力。
    """
    src = ((journal_field or {}).get("confidence") or {}).get("source")
    if manual_mismatch:
        return True
    # 断行残片: 不管来源(regex 也可)与注册表行类型(corpus 也可), 残片不是有效刊名。
    if _is_journal_fragment(journal_field.get("value") if isinstance(journal_field, dict) else None):
        return bool(registry_value)
    return (src in ("running_header", "channel_fallback")
            and bool(re.search(u"[가-힣]", registry_value or "")))


def _apply_doi_series(res, source):
    for _page, _uid, line in source:
        if _page not in (0, 1):
            continue
        m = re.search(r"(defsec|BJH)\.((?:19|20)\d{2})\.(\d+)\.(\d+)(?:\.(\d+))?", line, re.I)
        if not m:
            continue
        year, volume, issue_no = map(int, m.group(2, 3, 4))
        if year not in YEAR_OK:
            continue
        values = [("year", year), ("volume", volume), ("issue", issue_no)]
        if m.group(1).lower() == "defsec":
            values.insert(0, ("journal", "국방과 보안"))
        for key, value in values:
            res[key] = {"value": value, "uid": None,
                        "confidence": {"level": "high", "source": "doi"}}
        return True
    return False


def _apply_year_ladder(res, source, pdata):
    """年份兜底阶梯(2026-11-17 收敛, 原 issue_doc 内两段分散逻辑合一):
    ① 출판연도 出版社标签行 → ② 稿件历程脚注(접수일+게재확정일, 见 _pubdate_fn_year)。
    只填空不覆盖; 返回 (阶梯命中说明|None) 供 audit。"""
    if res["year"]["value"] is None:
        pub_year = _publication_year(source)
        if pub_year is not None:
            res["year"] = {"value": pub_year, "uid": None,
                           "confidence": {"level": "high", "source": "publisher_label"}}
            return "publisher_label:%d" % pub_year
    if res["year"]["value"] is None:
        y_fn, uid_fn = _pubdate_fn_year(pdata)
        if y_fn is not None:
            res["year"] = {"value": y_fn, "uid": uid_fn,
                           "confidence": {"level": "mid", "source": "pub_date_fn"}}
            return "pub_date_fn:%d" % y_fn
    return None


def _latin_biblio_overrides(res, source, lang):
    if lang not in ("ru", "pt", "fr"):
        return
    volume_found = False
    for page, uid, s in source:
        if page > 2 or not re.search(r"(?i)dans|revue|revista|jornal|cadernos|estudos|annales|actes|"
                                     u"журнал|вестник|известия|обозрение|успехи|мониторинг|"
                                     r"doi|volume|vol\.|tome|ano\b|v\.\s*\d|n[°º]|№", s):
            continue
        s0 = re.sub(r"</?(?:sup|sub)\b[^>]*>", "", s, flags=re.I)
        s0 = re.sub(r"\s+", " ", s0)
        m = re.search(r"(?i)\bpages?\s+(\d+)\s+à\s+(\d+)", s0)
        if not m:
            m = re.search(r"(?i)(?:pp?\.\s*|С\.\s+)(\d+)\s*[-–—]\s*(\d+)", s0)
        if m:
            start, end = int(m.group(1)), int(m.group(2))
            if end < start and len(m.group(2)) < len(m.group(1)):
                end = start // 100 * 100 + end
                if end < start:
                    end += 100
        if m and start <= end:
            res["pages"] = {"value": "%d-%d" % (start, end), "uid": uid,
                            "confidence": {"level": "high", "source": "latin_biblio"}}
        ym = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", s0)
        if ym and int(ym.group(1)) in YEAR_OK:
            res["year"] = {"value": int(ym.group(1)), "uid": uid,
                           "confidence": {"level": "high", "source": "latin_biblio"}}
        patterns = [
            (r"(?i)\bTome\s+(\d+)", 1, None),
            (r"(?i)\bVol\.?\s*(\d+)", 1, None),
            (r"(?i)\bVOLUME\s+(\d+)", 1, None),
            (r"(?i)\bv\.\s*(\d+)", 1, None),
            (u"Т\\.\s*(\\d+)|ТОМ\\s*(\\d+)", 1, 2),
            (r"(?i)\bAno\s+(\d+)", 1, None),
            (r"[;,]\s*(\d+)\s*\((\d+(?:[-/]\d+)?)\)", 1, 2),
        ]
        for pat, vi, ii in patterns:
            m = re.search(pat, s0)
            if not m:
                continue
            v = next((x for x in m.groups() if x), "")
            if v.isdigit():
                res["volume"] = {"value": int(v), "uid": uid,
                                 "confidence": {"level": "high", "source": "latin_biblio"}}
                volume_found = True
            if ii:
                iv = m.group(ii)
                res["issue"] = {"value": int(iv) if iv.isdigit() else iv, "uid": uid,
                                "confidence": {"level": "high", "source": "latin_biblio"}}
            break
        m = re.search(u"Т\\.\\s*([IVX]+)", s0)
        if m:
            res["volume"] = {"value": {"I": 1, "V": 5, "X": 10}[m.group(1)], "uid": uid,
                             "confidence": {"level": "high", "source": "latin_biblio"}}
            volume_found = True
        m = re.search(r"(?:19|20)\d{2}\s*/\s*(\d+)", s0)
        if m:
            res["issue"] = {"value": int(m.group(1)), "uid": uid,
                            "confidence": {"level": "high", "source": "latin_biblio"}}
        m = re.search(r"(?i)\bn[°º.]?\s*(\d+)|NÚMERO\s+(\d+)|№\s*(\d+)", s0)
        if m:
            v = next(x for x in m.groups() if x)
            res["issue"] = {"value": int(v), "uid": uid,
                            "confidence": {"level": "high", "source": "latin_biblio"}}
        m = re.search(r"(?i)\bAno\s+(XXIII|XXII|XXI|XX|XIX|XVIII|XVII|XVI|XV|XIV|XIII|XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I)\b", s0)
        if m:
            roman = m.group(1)
            values = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8,
                      "IX": 9, "X": 10, "XI": 11, "XII": 12, "XIII": 13, "XIV": 14, "XV": 15,
                      "XVI": 16, "XVII": 17, "XVIII": 18, "XIX": 19, "XX": 20, "XXI": 21,
                      "XXII": 22, "XXIII": 23}
            res["volume"] = {"value": values[roman], "uid": uid,
                             "confidence": {"level": "high", "source": "latin_biblio"}}
            volume_found = True
        m = re.search(r"(?i)Número\s+(I{1,3})\b", s0)
        if m:
            res["issue"] = {"value": len(m.group(1)), "uid": uid,
                            "confidence": {"level": "high", "source": "latin_biblio"}}
        m = re.search(r"(?i)(1|2)\s*[º°]\s*SEMESTRE", s0)
        if m:
            res["issue"] = {"value": int(m.group(1)), "uid": uid,
                            "confidence": {"level": "high", "source": "latin_biblio"}}
    if res["issue"]["value"] is not None and not volume_found \
            and res["volume"]["value"] is None:
        # 不覆盖 extract() 已从 DOI/刊头收到的卷号(Analyses & Alternatives 2025 9(2)
        # 实证): 本函数只在没有卷证据时把 volume 明确置空, 否则会把 9 清掉。
        res["volume"] = {"value": None, "uid": None,
                         "confidence": {"level": "high", "source": "latin_biblio"}}


def issue_doc(doc, out_root):
    """单篇: 来源行收集 → extract() → 页码推断兜底 → metadata.json 新字段 + audit.issue。"""
    doc_dir = os.path.join(out_root, doc)
    udir = os.path.join(doc_dir, "unified")
    meta_fp = os.path.join(doc_dir, "metadata.json")
    if not os.path.isdir(udir) or not os.path.exists(meta_fp):
        print("跳过 issue %s: 缺 unified/metadata" % doc)
        return None
    meta = json.load(io.open(meta_fp, encoding="utf-8"))
    pages, pdata = read_unified(udir)
    source = []
    page_values = {}
    last_body = None      # 最后一个有正文块(非页眉/页脚/页码)的物理页, 供页码推断封顶
    for p in pages:
        j = pdata[p]
        if any(u["type"] not in STRUCT_TYPES and (u["main_text"] or "").strip()
               for u in j["unified"]):
            last_body = p
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
            if (p <= 1 and (u["type"] in STRUCT_TYPES or (p == 0 and u["box"][1] < 0.12 * j["h"]))) \
                    or (p == 0 and (_DOI.search(t) or re.search(r"출판연도\s*[:：]", t)
                                    or _YEAR_VOL.search(t) or re.search(r"(?im)^Dans\s+", t)
                                    or (p == 0 and re.match(r"(?i)(?:Revista|Revue|Jornal)\b", t))
                                    or re.search(r"(?i)Revue\s+[^,\n]+,\s*(?:19|20)\d{2}\s*,\s*\d+", t)
                                    or re.search(u"//\\s*[^/]+[.]\\s*[—–-]?\\s*(?:19|20)\\d{2}", t))) \
                    or (p > 1 and len(_fold(t)) <= 100 and _LATE_MAST.search(t)) \
                    or (u["type"] in ("Page-header", "Page-footer")
                        and (_FOOT_MAST.search(t) or _LATE_MAST.search(t)
                             or re.search(u"(?i)обозрение|журнал|ciência|saúde", t))):
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
    excluded |= {_fold_accent(x) for x in list(excluded)}   # 2026-11-20 口音折叠双形态守卫
    _j0 = pdata[0] if 0 in pdata else None
    _lang = _langid.classify_page(_j0) if _j0 else None
    res = extract(source, len(pages), excluded, lang=_lang)
    _ladder_hit = _apply_year_ladder(res, source, pdata)
    pubdate_fn_hit = None
    if _ladder_hit and _ladder_hit.startswith("pub_date_fn"):
        pubdate_fn_hit = {"value": res["year"]["value"], "uid": res["year"]["uid"]}
    # —— 页: pp./裸区间无 → 页码推断。**必须在渠道兜底之前**: 全篇 Page-number 一致 offset
    #    (2309666 是 35/35 页)是硬证据; 而兜底源把"每页前 4 块"当页眉区, 无页眉的页会拿下
    #    正文句 —— 2309666 p16 '…20-30 둥 정도의 순익을 거둘 수 있었지만…' 是盈利数字, 被当成
    #    页码范围, 正是它压掉了整篇一致的书页码(抽成 20-30, 实际 589-623)。先后搞反, 一句正文
    #    数字就能盖掉账本。
    derived = None
    if res["pages"]["value"] is None:
        derived = _derive_pages(page_values, last_body)
        if derived:
            first, last, support, offset = derived
            res["pages"]["value"] = "%d-%d" % (first, last)
            res["pages"]["confidence"] = {"level": "mid" if support >= 3 else "low",
                                          "source": "regex"}
    # —— 体例全覆盖: unified 归并会丢页眉/题录行(老式 제N집 期刊、英文期刊、SpringerOpen 实证),
    #     merged.json 三渠道仍保留逐块原文。仅在既有判据某字段为空时, 用原始读法跑同一套
    #     extract() 规则补该字段——不是改结果、不是绕过校验, 是给同一规则更完整的输入。
    ch_filled = {}
    ch_lines = channel_lines(doc_dir)
    if ch_lines and any(res[k]["value"] is None for k in
                        ("journal", "journal_en", "volume", "issue", "pages")):
        # 每页最前几行=页眉区。正文里的编号脚注("I.D. No. 715579")不进题录投票,
        # 否则会把档案编号当成期号(2002 KOREA JOURNAL 实证, 已修)。
        # 2026-11-13: 再加两道行级闸——① 折叠长度 ≤ _CH_LINE_MAX: 刊头/页脚行都很短,
        #   而"每页前 4 块"在没有页眉的页上拿到的是正文段落(13563546 的期号 27 来自正文
        #   ★'국가등록문화유산 제27호인 인민군사령부 막사…'——文物登记号); ② 行首是脚注编号
        #   (1)/2./N、)的整行不进(13563579 把脚注里的书年份 2020/2021 当出版年, 与刊头
        #   DOI 的 2024 平票后取小者)。
        by_page = {}
        for p, ch, t in ch_lines:
            by_page.setdefault(p, []).append(t)
        src2 = []
        for p, texts in by_page.items():
            if p > 4:
                continue
            for t in texts[:4]:
                for ln in t.split("\n"):
                    ln = ln.strip()
                    if not ln or len(_fold(ln)) > _CH_LINE_MAX:
                        continue
                    if _FN_ENTRY_HEAD.match(ln):
                        continue
                    if re.search(r"\b(?:I\.?D\.?|Subject|RG\s*\d|Entry\s*\d|Fig\.?|Table|©|"
                                 r"Copyright|Abstract|Keywords)\b", ln, re.I):
                        continue
                    src2.append((p - 1, None, ln))
        res2 = extract(src2, len(pages), excluded, lang=_lang)

        def _ok_val(k, v):
            if v is None:
                return False
            if k in ("issue", "volume"):
                return isinstance(v, int) and 1 <= v <= 999
            if k == "year":
                return isinstance(v, int) and v in YEAR_OK
            if k in ("journal", "journal_en"):
                s = str(v)
                if not (4 <= len(_fold(s)) <= 50) or re.search(r"[,;|]|et\s+al", s, re.I):
                    return False
                if re.fullmatch(r"[A-Za-z]{1,4}", s):      # 正文缩写('SSP')/人名('Iida')不是刊名
                    return False
                if _lang in ("ru", "pt", "fr", "en"):
                    return True
                return bool(re.search(r"(Journal|Review|Quarterly|Bulletin|Annals|Proceedings|"
                                      r"Studies|Research|Letters|학회|학술|연구|논문|문화|사학|"
                                      r"철학|법학|음악|문학|언어|교육|과학|論|學|誌|志)", s, re.I)
                            or re.search(u"[가-힣一-龥]", s) or s.isupper())
            return True   # pages

        # year 不在兜底范围(2026-11-13): 兜底源是"每页前 4 块", 参考条目与脚注在参考文献页
        # 恰好也在前 4 块里, 而它们又短又带『』(过得了 _Y_GUARD), 于是出版年被引用年份顶掉
        # —— 2309666 抽成 1957/1915(『中國棉紡織史稿』)、1632041 抽成 1999, 全错。年份一栏
        # 只认 unified 源行 + DOI/ISSN 行, 宁可空。实测: 全库只有这 2 篇曾被兜底填 year。
        for k in ("journal", "journal_en", "volume", "issue", "pages"):
            if res[k]["value"] is None and _ok_val(k, res2[k]["value"]):
                res[k] = {"value": res2[k]["value"], "uid": None,
                          "confidence": {"level": "mid", "source": "channel_fallback"}}
                ch_filled[k] = res2[k]["value"]
    # —— 单词型/全大写 running 刊名(2026-11-20 俄葡法适配; fr_20 'haematologica' 页脚跨年复写、
    #    pt_11 'ESTUDOS FEMINISTAS' 全大写 running head 实证): _latin_jname 按单行匹配够不到
    #    跨年频率, 这里按页眉/页脚统计补空。running_journal 的"任意重复行"判据对拉丁
    #    running head(作者名/副题)敞开, 故仅限带刊名信号的形态。
    if _lang in ("ru", "pt", "fr", "en") and res["journal"]["value"] is None:
        _JWORD = re.compile(r"(?i)revista|journal|jornal|annals|bulletin|cahiers|estudos|"
                            r"cad|pesqui|research|review|proceedings|transactions|archive|"
                            r"quarterly|cybernetics|neuro|cardiol|enferm|fisioter|epiz|sci|"
                            r"tech|saber|resenhas")
        _jw = collections.Counter()
        _jpages = collections.defaultdict(set)
        _disp = {}
        for p in pages:
            for u in pdata[p]["unified"]:
                if u["type"] not in ("Page-footer", "Page-header"):
                    continue
                for ln in re.split(r"[\n|]", (u.get("main_text") or "")):
                    s = ln.strip()
                    _ok = False
                    if re.fullmatch(r"[a-z][a-z\-]{3,20}", s):
                        _ok = True
                    elif re.fullmatch(u"[A-ZÀ-Ú0-9][A-ZÀ-Ú0-9&\\.\\-\\s]{3,40}", s) \
                            and _JWORD.search(s):
                        _ok = True
                    if not _ok or "." in s or _fold_accent(s) in excluded:
                        continue   # 带点=域名/网址(ru_13 WWW.DEMREVIEW.HSE.RU 实证)
                    if s.upper() in (u"REVISTA", u"JOURNAL", u"BOLETIM", u"ANAIS", u"BULLETIN"):
                        continue   # 泛词单行不当刊名(pt_30 实证)
                    _jw[_fold(s)] += 1
                    _jpages[_fold(s)].add(p)
                    _disp.setdefault(_fold(s), s)
        _good = [k for k in _jw if _jw[k] >= 2 and min(_jpages[k]) <= 1]
        if _good:
            _key = max(_good, key=lambda k: _jw[k])
            res["journal"] = {"value": _disp[_key], "uid": None,
                              "confidence": {"level": "mid", "source": "running_head_latin"}}
    full_source = source + [(p, None, line) for p, _ch, t in ch_lines for line in t.split("\n")]
    # 2026-11-20 俄葡法适配: 拉丁文献跳过 _apply_running_journal——它的"跨年≥2 次重复行即刊名"
    # 判据对拉丁 running head(作者名/副题)是敞开的(fr_02/fr_18/fr_24 实证); 拉丁刊名已由
    # _latin_jname + 下方单词型页脚规则覆盖。韩文文献行为不变。
    running_journal = None if _lang in ("ru", "pt", "fr", "en") \
        else _apply_running_journal(res, full_source, excluded)
    publisher_series = _apply_doi_series(res, full_source)
    # —— DOI 注册表回填刊名(2026-11-18 白名单): 投票/捞源都没刊名而 DOI 前缀在注册表
    #    → 查表填空(只填空不覆盖; corpus 行由 failures.py --round 每轮重扫维护,
    #    manual 行永驻——BJH=방정환연구 经 doi.org CSL + KCI 著录页双证实)。
    _jk = _fit = None
    # 2026-11-25: running_header 是低置信启发式(mid), 会把文章内部行当刊名
    #   (13568641 '…중략…' / 13568660 '반갑게 먼저 말을 걸어 줘' / 13568677 '천공의 용소년…')。
    #   这类篇原文不印刊名, 只有 DOI 前缀 BJH(10.33957) = 방정환연구(注册表双证实 high)。
    #   running_header 来源 + DOI 在注册表 → 让 high 证据纠正 mid 猜测。全库精确命中这 3 篇,
    #   零误伤; 其它来源(作者直接判定/渠道兜底)一概不动。
    _doi_txt = "\n".join(s for _p, _u, s in source)
    _jk, _je = jreg.journal_from_doi(_doi_txt)
    if not _jk:
        # 无 DOI 篇(13569945 等 5 篇 kjcss): 页脚印英文刊名, 靠英文名反查 manual 行。
        _jk, _je = jreg.journal_from_name(_doi_txt)
    # manual 行是人工双证实的封闭值域: 当前值不符即可纠正(不受"只填空"限制)。
    # corpus 投票行无此权力——它会自我强化错误(kjcss 18 篇曾把英文名投进 journal 槽)。
    _manual_row = next((row for row in jreg.load_registry().values()
                        if row.get("source") == "manual" and row.get("journal") == _jk), None)
    _manual_mismatch = bool(_manual_row and res["journal"]["value"] != _manual_row.get("journal"))
    if _jk and (res["journal"]["value"] is None
                or _should_registry_override(res["journal"], _jk, _manual_mismatch)):
        res["journal"] = {"value": _jk, "uid": None,
                          "confidence": {"level": "high", "source": "doi_registry"}}
        # 英韩双全: manual 行给的 journal_en 要用上; 旧值即便非空, 若等于错误的 journal 旧值
        # (18 篇把英文名同时塞进两槽)也要纠正为 manual 的英文刊名。
        if _je and (res["journal_en"]["value"] is None or _manual_mismatch):
            res["journal_en"] = {"value": _je, "uid": None,
                                 "confidence": {"level": "high", "source": "doi_registry"}}
    # —— 同刊卷期账本推断(只填空): 缺卷/期且同刊存在"印刷页码完整落入其跨度、又不与
    #    已知文章撞车"的唯一卷期 → 继承(语料自带冗余; 无 DOI 篇如 KCI_FI/중국사회과학논총)。
    if res["volume"]["value"] is None or res["issue"]["value"] is None:
        _fit = jreg.fit_issue(res["journal"]["value"], res["year"]["value"],
                              res["pages"]["value"], jreg.issue_ledger(out_root))
        if _fit:
            if res["volume"]["value"] is None:
                res["volume"] = {"value": _fit[1], "uid": None,
                                 "confidence": {"level": "mid", "source": "sibling_issue"}}
            if res["issue"]["value"] is None:
                res["issue"] = {"value": _fit[2], "uid": None,
                                "confidence": {"level": "mid", "source": "sibling_issue"}}
    _latin_biblio_overrides(res, source, _lang)
    audit = res.pop("_audit")
    audit["channel_fallback"] = ch_filled
    if running_journal:
        audit["journal_running_header"] = True
    if publisher_series:
        audit["publisher_series"] = "DOI 期段位"
    if pubdate_fn_hit:
        audit["year_pub_date_fn"] = pubdate_fn_hit
    if _jk:
        audit["journal_registry"] = _jk
    if _fit:
        audit["issue_sibling_inherit"] = {"year": _fit[0], "volume": _fit[1],
                                              "issue": _fit[2]}
    res.pop("_internal")
    if derived:
        first, last, support, offset = derived
        audit["pages_derived"] = {"first": first, "last": last, "offset": offset,
                                  "support": support,
                                  "per_page": {str(k): v for k, v in page_values.items()},
                                  "last_body": last_body}
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
                if not items or max(len(x) for x in items) >= 60 \
                        or any("。" in x for x in items):
                    continue
                if en:
                    if kws_en:
                        continue
                    kws_en, kw_raw_en, kw_loc_en = items, raw, {"page": p + 1, "uid": u["uid"]}
                else:
                    if kws:
                        continue
                    kws, kw_raw, kw_loc = items, raw, {"page": p + 1, "uid": u["uid"]}
    # OCR 坏行守卫(13570225 实证): 冒号后前导空字段('주제어 : , , X Y Z')说明该渠道
    # 逗号丢失, 切出的单项是伪结果; 同篇 paddle 渠道有完整逗号版。置空走渠道兜底,
    # 用"有 ≥2 个逗号分项"的结构证据择善, 不从 ODPS 取词。
    if kws and kw_raw and re.match(u"^[^:：]*[:：]\\s*[,，]", kw_raw[0]):
        kws, kw_raw, kw_loc = [], [], None
    # 关键词渠道兜底: 老式期刊把 주제어/핵심어 行放在正文里, unified 归并成 Text 块后
    # KW_LINE(要求行首头词)抓不到; 只在 keywords[] 仍为空时, 从三渠道原文按行内头词补。
    # 注意: 这是"行内任意位置"的松匹配, 命中正文句/引文句时会拿整句当关键词(整列不同根因②);
    # 它只是兜底, 证据等级低于首页方括号双语块——下方 bracket 路径可覆盖它。
    kw_from_channel = False
    if not kws:
        _KW_ANY = re.compile(u"(?:주제\\s*어|핵심\\s*어|키\\s*워드|중심\\s*어|주요\\s*어|关\\s*键\\s*词|關\\s*鍵\\s*詞|关\\s*键\\s*字|Key\\s*words?|Keywords?)", re.I)
        for p, ch, t in ch_lines:
            for ln in t.split("\n"):
                mm = _KW_ANY.search(ln)
                if not mm:
                    continue
                if mm.start() > 0:
                    # 头词嵌在列表行中间(OCR 把标签粘进词内, 如 '…생주제어각' / '이주제어주영')。
                    # 若整行是"多项短列表"且无句子特征(句末点号/引文年/URL), 则整行才是关键词表;
                    # 只剥标签 token, 保留头词前面的项目 —— 不靠词表, 靠"列表结构 + 无句子特征"。
                    whole = [x.strip().strip("。") for x in re.split(u"[,，;；·、]", ln) if x.strip()]
                    if len(whole) >= 4 and max(len(x) for x in whole) <= 40 \
                            and not re.search(u"[。]|\\((?:19|20)\\d{2}\\)", ln) \
                            and not re.search(r"https?://|www\.", ln):
                        whole = [re.sub(u"(?:주제\\s*어|핵심\\s*어|키\\s*워드|중심\\s*어|주요\\s*어)",
                                        "", x).strip() for x in whole]
                        items = [x for x in whole if len(x) > 1]
                        if 2 <= len(items) <= 15:
                            kws, kw_raw, kw_loc = items, [ln.strip()], {"page": p, "uid": None}
                            kw_from_channel = True
                            break
                rest = ln[mm.end():].lstrip(u" 】〉>）)]：:·.-*")
                items = [x.strip().strip("。") for x in re.split(u"[,，;；·、]", rest) if x.strip()]
                if 2 <= len(items) <= 15 and max(len(x) for x in items) < 60:
                    kws, kw_raw, kw_loc = items, [ln.strip()], {"page": p, "uid": None}
                    kw_from_channel = True
                    break
            if kws:
                break
        if kws:
            audit["keywords_channel_fallback"] = {"items": kws, "raw_lines": kw_raw,
                                                  "source": kw_loc}
    if not kws or not kws_en:
        for u in pdata[0]["unified"]:
            mt = u.get("main_text") or ""
            # 整块括号体例之外, 再试块内行级括号(2026-11-18 13564504 实证: 关键词行并进
            # 英文摘要块末行, 整块不以 [ 起故旧判据够不着); 守卫不变: 括号行 + '/' 分隔
            # + 韩英双板齐(ko and en), 数式/页码行过不了双板闸。
            _cands = [mt.strip()] if mt.strip().startswith("[") else []
            _cands += [ln.strip() for ln in mt.split("\n")
                       if ln.strip().startswith("[") and ln.strip().endswith("]")]
            for t in _cands:
                if not (t.startswith("[") and t.endswith("]") and "/" in t):
                    continue
                items = [x.strip() for x in t[1:-1].split("/") if x.strip()]
                ko, en = [], []
                for item in items:
                    # 双语分界是结构: 组内用 '/', 英→韩组界用 ','. 旧实现给整项加
                    # _hangul_ratio>=0.2 门槛, 短韩词(如 '어휘 습득' 仅 4 字)被门槛刷掉,
                    # 于是末英词+首韩词粘成一项并判成英文, 韩文关键词永远丢第一个。
                    # 改用分界两侧各自的语种归属判定, 不靠整项比例, 也不靠词表。
                    if "," in item:
                        left, right = item.rsplit(",", 1)
                        if _hangul_ratio(left) < 0.2 and re.search(r"[A-Za-z]", left) \
                                and _hangul_ratio(right) >= 0.2:
                            en.append(left.strip())
                            ko.append(right.strip())
                            continue
                    if _hangul_ratio(item) >= 0.2:
                        ko.append(item)
                    elif re.search(r"[A-Za-z]", item):
                        en.append(item)
                if ko and en:
                    # 首页方括号双语块是印在原文上的关键词表, 证据强于"正文行内命中头词"的
                    # 渠道兜底。兜底值存在时以本路径覆盖, 避免整段正文/引文占关键词槽。
                    if not kws or kw_from_channel:
                        kws, kw_raw, kw_loc = ko, [t], {"page": 1, "uid": u["uid"]}
                        kw_from_channel = False
                        audit["keywords_bracket_over_channel"] = True
                    if not kws_en:
                        kws_en, kw_raw_en, kw_loc_en = en, [t], {"page": 1, "uid": u["uid"]}
                    break
    # —— 落盘 ——
    # 关键词终滤(2026-11-17 全库扫描): 长句/含句号/含 URL 的"关键词"是摘要或文献串混入, 丢弃。
    kws = [k for k in kws if len(k) <= 50 and u"。" not in k
           and not re.search(r"https?://|www\.", k)]
    kws_en = [k for k in kws_en if len(k) <= 50 and u"。" not in k
              and not re.search(r"https?://|www\.", k)]
    # 根因①: 关键词尾粘署名+机构 —— 同页独立署名块 + 已抽作者名双证据才剥, 见函数注释。
    _kws_before = list(kws)
    kws = _strip_glued_byline(kws, kw_loc, pdata, meta)
    if kws != _kws_before:
        audit["keywords_byline_glue_stripped"] = {"before": _kws_before, "after": kws}
    # 卷期同源复制守卫(2026-11-17): issue==volume 且两候选页集相同 = 同一行被双写(제N집/
    # BMC 卷:文号 体例), 期号是复制值不是证据 → 置空, 由 audit 记 note。
    _ia = res.get("_audit") or {}
    _iv = _ia.get("issue_candidates") or {}
    _vv = _ia.get("volume_candidates") or {}
    if res["issue"]["value"] is not None and res["issue"]["value"] == res["volume"]["value"]:
        _ik = str(res["issue"]["value"]); _vk = "kwan:%d" % res["volume"]["value"]
        _ik2 = str(res["issue"]["value"])
        _ip = sorted(_iv.get(_ik) or _iv.get(_ik2) or [])
        _vp = sorted(sum((_vv.get(k) or [] for k in _vv if k.endswith(":%d" % res["volume"]["value"])), []))
        if (res["volume"]["value"] or 0) > 20 or (_ip and _ip == _vp):
            audit["issue_dup_of_volume"] = True
            res["issue"] = {"value": None, "uid": None,
                            "confidence": {"level": "low", "source": "regex"}}
            # 置空的期号若有无期位 DOI 旁证(年.卷.页码 / 年..卷), 即原文结构上不编期号,
            # 按 P1 拍板标 volume_only 交质检豁免(2026-11-18 KCI_FI002983265_2023 实证:
            # dasein.2023.70.33 三段式, 70 被 vol_jip/issue_jip 双写)。四段式(期位有值)
            # 不得误标——YIV 命中即说明期位存在。
            if any(_DOI_YEV.search(s) or _DOI_VYY.search(s)
                   or (_DOI_YV.search(s) and not _DOI_YIV.search(s))
                   for _, _, s in source):
                res["issue"]["confidence"] = {
                    "level": "high", "source": "volume_only",
                    "evidence": "issue==volume 同源复制; DOI 期位留空(年.卷.页码 / 年..卷)"}
                audit["issue_note"] = "issue==volume 同源复制置空; DOI 无期位, 原文不编期号"
    if _lang in ("ru", "pt", "fr", "en"):
        # 旧值只在本轮没抽到同语种组时保留; 不得把刚按 keywords_en 填好的主 keywords 又盖空
        # (13582999 实证)。
        if not kws:
            kws = (meta.get("keywords") or []) or kws
        if not kws_en:
            kws_en = (meta.get("keywords_en") or []) or kws_en
    # 英文/原文只印英文关键词时, 主 keywords[] 不空置(13573745 '[Key-words]' /
    # 13582999 实证; 与 13582997 既有口径一致)。必须在旧值保留之后执行。
    if not kws and kws_en:
        kws = list(kws_en)
        kw_raw = kw_raw_en
        kw_loc = kw_loc_en
        audit["keywords_language_fallback"] = u"keywords[] 原语种空缺, 以 keywords_en 同值填充"
    # 2026-11-25: 重跑不得冲掉外部回填值(webfill 的 web_record / volume_only)。
    # 旧值来自联网著录、原文往往不印该字段, 重跑 extract 算出 None 会把著录值抹掉
    # (13564373 实证: web_record 卷7期1 被冲成 None; 同批 5 篇 kjcss webfill 卷期亦如此)。
    # 只保留"旧值有值 且 来源在白名单"的字段; 其余照常用新算结果, 幂等。
    _KEEP_SRC = ("web_record", "volume_only")
    for _f in ("journal", "journal_en", "year", "volume", "issue", "pages"):
        _old = meta.get(_f) or {}
        _src = (_old.get("confidence") or {}).get("source") if isinstance(_old, dict) else None
        if _src in _KEEP_SRC and _old.get("value") is not None:
            res[_f] = _old
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
    # 7) '|' 行: ko/en 段拆分; 日期前那段纯数字=卷(2026-11-13 改判, 见下), 日期本身不出期
    #    旧断言是"20/202510 都不出卷期", 但 46 篇 한국연구 + 16 篇 글로컬 实证: 这段就是卷号,
    #    与 DOI 第三段一致(riks.2025..19.001 / igll.2025..20.006), 而 DOI 期位是空的。
    r = extract([(0, "u0", "글로컬 어문학 문화 연구 | International Journal of Glocal Language "
                          "and Literary Studies | 20 | 202510")], 1, set())
    ok7 = r["journal"]["value"] == u"글로컬 어문학 문화 연구" \
        and r["journal_en"]["value"] == "International Journal of Glocal Language and Literary Studies" \
        and r["volume"]["value"] == 20 and r["issue"]["value"] is None
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
        and _kw("[Key-words] Korean Language Education, Family Culture") == ["Korean Language Education", "Family Culture"] \
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
    # 24) 页眉 LaTeX 残片: 2000년대 스캔본 수식 OCR 混进页眉区, 不得当选刊名
    _bi = {"uid": "u0", "page": 1, "type": "Page-header", "main_text": "\\end{bmatrix}",
           "box": [10, 100, 500, 200], "cross_page": {"spans": False}, "readings": [],
           "confidence": {"level": "high"}, "review": False, "members": {}}
    blk_i0 = dict(_bi); blk_i1 = dict(_bi, uid="u1", page=2); blk_i2 = dict(_bi, uid="u2", page=3, main_text="PHON")

    # 25) DOI 注册表回填刊名: 前缀在注册表(manual 行) → journal 查表填出(BJH 实证)
    blk_j1a = {"uid": "u0", "page": 1, "type": "Page-header",
              "main_text": "https://doi.org/10.33957/BJH.2025.14.4", "box": [10, 100, 500, 200],
              "cross_page": {"spans": False}, "readings": [], "confidence": {"level": "high"},
              "review": False, "members": {}}
    blk_j1b = dict(blk_j1a, uid="u1", page=2)
    # 26) 同刊卷期账本推断: j2(4-100)/j3(110-117) 构成 (2023,5,2) 账本(跨度4-117),
    #     j4 页码 101-109 完整落入且不撞已知区间 → 唯一解, 继承卷期
    _bjk = {"uid": "u0", "page": 1, "type": "Page-header", "box": [10, 100, 500, 200],
            "cross_page": {"spans": False}, "readings": [], "confidence": {"level": "high"},
            "review": False, "members": {}}
    blk_j2a = dict(_bjk, main_text=u"시험저널 제5권 제2호 (2023) pp. 4-100")
    blk_j2b = dict(_bjk, uid="u1", page=2, main_text=u"시험저널")
    blk_j3a = dict(_bjk, main_text=u"시험저널 제5권 제2호 (2023) pp. 110-117")
    blk_j3b = dict(_bjk, uid="u1", page=2, main_text=u"시험저널")
    blk_j4a = dict(_bjk, main_text=u"시험저널 pp. 101-109")
    blk_j4b = dict(_bjk, uid="u1", page=2, main_text=u"시험저널")

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
    # 17) 연도.호 点分: 只有同页还印着 N호 时才当期号。旧断言把 '… | 20 | 2025.10' 的 10 当期号,
    #     2026-11-13 改判——同一位置单页印 '202510'(出版年月), DOI 期位 '2025..20.006' 是空的,
    #     16 篇 글로컬 因此都多出一个假期号 10。
    r = extract([(0, "u0", u"글로컬 어문학 문화 연구 | International Journal of Glocal Language "
                          u"and Literary Studies | 20 | 2025.10")], 1, set())
    r2 = extract([(0, "u0", "https://doi.org/10.23073/igll.2025.20.013")], 1, set())
    r3 = extract([(0, "u0", u"한국연구 제19권 제3호 2025.10")], 1, set())
    ok17 = r["issue"]["value"] is None and r["year"]["value"] == 2025 \
        and r["volume"]["value"] == 20 \
        and r2["issue"]["value"] is None and r3["issue"]["value"] == 3
    # 18) DOI 四段式 年.期.卷.文号: 期位有值时必须把**期**认出来, 且不能把期当卷。
    r = extract([(0, "u0", "https://doi.org/10.23073/riks.2025.3.19.007")], 1, set())
    ok18 = r["issue"]["value"] == 3 and r["volume"]["value"] == 19 \
        and r["year"]["value"] == 2025
    # 19) 期位留空 → 值仍为 None, 但 confidence.source 标 volume_only(质检据此免报缺);
    #     无 DOI 的同形行不得标(P1 的"证据不足仍报缺")。
    r = extract([(0, "u0", "https://doi.org/10.23073/riks.2025..19.007")], 1, set())
    r2 = extract([(0, "u0", u"한국연구 | THE JOURNAL OF KOREAN STUDIES | 19 | 202506")], 1, set())
    ok19 = r["issue"]["value"] is None \
        and r["issue"]["confidence"]["source"] == "volume_only" \
        and r["volume"]["value"] == 19 \
        and r2["issue"]["confidence"]["source"] == "regex"
    # 19b) 四段式 DOI 前缀感知(2026-11-17 修正): 非 riks 出版社为 年.卷.期.文号
    _r19b = extract([(0, "u0", "https://doi.org/10.23192/kjcss.2025.7.2.006")], 1, set())
    _r19b2 = extract([(0, "u0", "DOI 10.23166/csgc.2024.6.1.004")], 1, set())
    ok19b = (_r19b["volume"]["value"] == 7 and _r19b["issue"]["value"] == 2
             and _r19b2["volume"]["value"] == 6 and _r19b2["issue"]["value"] == 1)
    # 20) \u7a3f\u4ef6\u5386\u7a0b\u811a\u6ce8(issue_doc \u7ea7): \uc811\uc218\uc77c+\uac8c\uc7ac\ud655\uc815\uc77c \u53cc\u8bcd\u5b88\u536b \u2192 \u53d6 \uac8c\uc7ac\ud655\uc815\uc77c \u7684\u5e74;
    #     \u7f3a \uc811\uc218\uc77c \u4e0d\u8ba4; \u520a\u5934\u5df2\u6709\u5e74\u4efd\u65f6\u4e0d\u8986\u76d6(\u4e0d\u4e0e\u65e7\u89c4\u5219\u51b2\u7a81)\u3002
    blk_a = {"uid": "u0", "page": 1, "type": "Footnote", "main_text": u"\uc811\uc218\uc77c(2024\ub144 11\uc6d4 2\uc77c), \uc218\uc815\uc77c(2024\ub144 12\uc6d4 1\uc77c), \uac8c\uc7ac\ud655\uc815\uc77c(2025\ub144 1\uc6d4 5\uc77c)", "box": [10, 1200, 500, 1300], "cross_page": {"spans": False}, "readings": [], "confidence": {"level": "high"}, "review": False, "members": {}}
    blk_b = {"uid": "u0", "page": 1, "type": "Footnote", "main_text": u"\uac8c\uc7ac\ud655\uc815\uc77c(2025\ub144 6\uc6d4 24\uc77c)", "box": [10, 1200, 500, 1300], "cross_page": {"spans": False}, "readings": [], "confidence": {"level": "high"}, "review": False, "members": {}}
    blk_c1 = {"uid": "u0", "page": 1, "type": "Page-header", "main_text": u"\u300e\uad6d\ubc29\uacfc \ubcf4\uc548\u300f \uc81c3\uad8c \uc81c1\ud638, 2024, pp. 10-30", "box": [10, 100, 500, 1300], "cross_page": {"spans": False}, "readings": [], "confidence": {"level": "high"}, "review": False, "members": {}}
    blk_c2 = {"uid": "u0", "page": 1, "type": "Footnote", "main_text": u"\uc811\uc218\uc77c(2024\ub144 3\uc6d4 2\uc77c), \uac8c\uc7ac\ud655\uc815\uc77c(2024\ub144 5\uc6d4 5\uc77c)", "box": [10, 1200, 500, 1300], "cross_page": {"spans": False}, "readings": [], "confidence": {"level": "high"}, "review": False, "members": {}}
    blk_d = {"uid": "u0", "page": 1, "type": "Footnote",
             "main_text": u"\uc811\uc218\uc77c(2024\ub144 5\uc6d4 24\uc77c), \uac8c\uc7ac \ud655\uc815\uc77c(2024\ub144 6\uc6d4 18\uc77c)",
             "box": [10, 1200, 500, 1300], "cross_page": {"spans": False},
             "readings": [], "confidence": {"level": "high"}, "review": False, "members": {}}
    blk_e = {"uid": "u0", "page": 1, "type": "Footnote",
             "main_text": u"\uc811\uc218\uc77c(24\ub144 5\uc6d4 27\uc77c), \uc218\uc815\uc77c(1차 : 24\ub144 6\uc6d4 7\uc77c), \uac8c\uc7ac\ud655\uc815\uc77c(24\ub144 6\uc6d4 18\uc77c)",
             "box": [10, 1200, 500, 1300], "cross_page": {"spans": False},
             "readings": [], "confidence": {"level": "high"}, "review": False, "members": {}}
    # 21) 원고접수일 冒号体例 + 日期行在末页(2026-11-18 중국사회과학논총 8 篇实证):
    #     块级双词守卫 + 行首 (원고)?접수일/게재확정일 锚 + 全物理页扫描。
    blk_f0 = {"uid": "u0", "page": 1, "type": "Text", "main_text": u"중국의 정책 과정 분석 본문", "box": [10, 100, 500, 900], "cross_page": {"spans": False}, "readings": [], "confidence": {"level": "high"}, "review": False, "members": {}}
    blk_f1 = {"uid": "u1", "page": 2, "type": "Footnote", "main_text": u"원고접수일: 2023년 12월 21일\n심사완료일: 2024년 02월 20일\n게재확정일: 2024년 02월 26일", "box": [10, 1200, 500, 1300], "cross_page": {"spans": False}, "readings": [], "confidence": {"level": "high"}, "review": False, "members": {}}
    # 22) 关键词行级方括号(2026-11-18 13564504 实证): 关键词行并进英文摘要块末行,
    #     整块不以 [ 起, 旧整块判据够不着 → 行级括号候选 + 韩英双板守卫。
    blk_g = {"uid": "u0", "page": 1, "type": "Text", "main_text": "The purpose of this study was to investigate and gain insight into the learning process.\n[Aviation English/Communication Difficulties/Radiotelephony, 항공영어/의사소통의 어려움/항공무선통신]", "box": [10, 100, 500, 900], "cross_page": {"spans": False}, "readings": [], "confidence": {"level": "high"}, "review": False, "members": {}}
    # 23) issue==volume 同源复制置空后, 有三段式 DOI(年.卷.页码, 期位不存在)旁证 → volume_only
    #     交质检豁免(2026-11-18 KCI_FI002983265_2023 dasein.2023.70.33 实证);
    #     四段式(期位有值)同形行不得误标。
    blk_h = {"uid": "u0", "page": 1, "type": "Page-header", "main_text": u"『현대유럽철학연구』 제70집, pp.33~63. DOI 10.20974/dasein.2023.70.33", "box": [10, 100, 500, 200], "cross_page": {"spans": False}, "readings": [], "confidence": {"level": "high"}, "review": False, "members": {}}
    with tempfile.TemporaryDirectory() as tmp:
        for name, pages in (("a", {0: [blk_a]}), ("b", {0: [blk_b]}),
                            ("c", {0: [blk_c1, blk_c2]}), ("d", {0: [blk_d]}),
                            ("e", {0: [blk_e]}), ("f", {0: [blk_f0], 1: [blk_f1]}),
                            ("g", {0: [blk_g]}), ("h", {0: [blk_h]}),
                            ("i", {0: [blk_i0], 1: [blk_i1], 2: [blk_i2]}),
                            ("j1", {0: [blk_j1a], 1: [blk_j1b]}),
                            ("j2", {0: [blk_j2a], 1: [blk_j2b]}),
                            ("j3", {0: [blk_j3a], 1: [blk_j3b]}),
                            ("j4", {0: [blk_j4a], 1: [blk_j4b]})):
            sub = os.path.join(tmp, name)
            udir = os.path.join(sub, "unified")
            os.makedirs(udir)
            for pno, blocks in pages.items():
                io.open(os.path.join(udir, "page_%d.json" % pno), "w", encoding="utf-8").write(
                    json.dumps({"doc": name, "page": pno + 1, "w": 1000, "h": 1500,
                                "in_ref": False, "unified": blocks}, ensure_ascii=False))
            io.open(os.path.join(sub, "metadata.json"), "w", encoding="utf-8").write("{}")
            got = issue_doc(name, tmp)
            if name == "a":
                ok20 = (got.get("year", {}).get("value") == 2025
                        and got["year"]["confidence"]["source"] == "pub_date_fn")
            elif name == "b":
                ok20 = ok20 and got.get("year", {}).get("value") is None
            elif name == "c":
                ok20 = (ok20 and got.get("year", {}).get("value") == 2024
                        and got["year"]["confidence"]["source"] == "regex")
            elif name == "d":
                ok20 = ok20 and got.get("year", {}).get("value") == 2024 \
                    and got["year"]["confidence"]["source"] == "pub_date_fn"
            elif name == "e":
                ok20 = ok20 and got.get("year", {}).get("value") == 2024 \
                    and got["year"]["confidence"]["source"] == "pub_date_fn"
            elif name == "f":
                ok21 = (got.get("year", {}).get("value") == 2024
                        and got["year"]["confidence"]["source"] == "pub_date_fn")
            elif name == "g":
                ok22 = (got.get("keywords") == [u"항공영어", u"의사소통의 어려움", u"항공무선통신"]
                        and got.get("keywords_en") == ["Aviation English", "Communication Difficulties", "Radiotelephony"])
            elif name == "h":
                ok23 = (got.get("issue", {}).get("value") is None
                        and got["issue"]["confidence"]["source"] == "volume_only")
            elif name == "i":
                ok24 = (got.get("journal", {}).get("value") is None)
            elif name == "j1":
                ok25 = (got.get("journal", {}).get("value") == u"방정환연구"
                        and got.get("volume", {}).get("value") == 14)
            elif name == "j4":
                ok26 = (got.get("volume", {}).get("value") == 5
                        and got.get("issue", {}).get("value") == 2)
    # 27-30) 二次收敛(2026-11-19, KCI11_17 171 篇真缺口三族):
    #   27 '名 年 卷(期):' 横幅式(Analyses & Alternatives 系 ~46 篇缺刊名)
    r27 = extract([(0, "u0", "Analyses & Alternatives 2025 9(3): 027-047 DOI: 10.22931/aanda.2025.9.3.002")], 1, set())
    ok27 = r27["journal_en"]["value"] == "Analyses & Alternatives" \
        and r27["journal"]["value"] == "Analyses & Alternatives" \
        and r27["year"]["value"] == 2025
    #   28 '名 p-ISSN … e-ISSN …' 横幅式(JTS 系): 剥 ISSN 段后走独立英文刊名行判据
    r28 = extract([(0, "u0", "The Journal of Transdisciplinary Studies p-ISSN 2586-5439 e-ISSN 2586-5417")], 1, set())
    ok28 = r28["journal_en"]["value"] == "The Journal of Transdisciplinary Studies" \
        and r28["journal"]["value"] == "The Journal of Transdisciplinary Studies"
    #   29 'YYYY, Vol. N, No. M, …' 横幅行(JAMS 系; 来源门在 issue_doc(_YEAR_VOL), 此例行机制)
    r29 = extract([(0, "u0", "2023, Vol. 6, No. 2, 163-180")], 1, set())
    ok29 = r29["year"]["value"] == 2023 and r29["volume"]["value"] == 6 \
        and r29["issue"]["value"] == 2
    #   30 '卷..年月' DOI(mses 系): 年+卷进票, 期位结构上不存在 → volume_only
    r30 = extract([(0, "u0", "DOI http://dx.doi.org/10.22957/mses.11..202207.97")], 1, set())
    ok30 = r30["year"]["value"] == 2022 and r30["volume"]["value"] == 11 \
        and r30["issue"]["value"] is None \
        and r30["issue"]["confidence"]["source"] == "volume_only"
    # 31) 页码-年粘连(한국진로창업경영학회지 系): 年补出 + pages 真值(不再 1-3120)
    r31 = extract([(0, "u0", u"한국진로창업경영학회지 제6권 3호: pp. 1 ~ 312022년 8월")], 1, set())
    ok31 = r31["year"]["value"] == 2022 and r31["pages"]["value"] == "1-31" \
        and r31["volume"]["value"] == 6 and r31["issue"]["value"] == 3
    # 32) 页脚刊例尾部 '| 2025년' 剥除( Hangul 是 \w, 旧 \b 年剥除对它失效)
    ok32 = _clean_jname(u"34 산업연구 | 2025년 ") == u"산업연구" \
        and _clean_jname(u"안전관리자 및 보건관리자 채용과 산업재해 예방 간의 관계 분석") \
        == u"안전관리자 및 보건관리자 채용과 산업재해 예방 간의 관계 분석"
    # 33) 页脚刊例放行(산업연구 系): 迟到第 29 页的日期页脚也能供出四字段, 刊名不夹年
    r33 = extract([(29, "u29", u"114 산업연구 | 2025년 제9권 제1호")], 30, set())
    ok33 = r33["journal"]["value"] == u"산업연구" and r33["year"]["value"] == 2025 \
        and r33["volume"]["value"] == 9 and r33["issue"]["value"] == 1
    # 34) 独立出版月行: 同行块带刊头信号 → 年补出; 异行块不补(防正文日期)
    r34 = extract([(0, "u0", u"한국진로창업경영학회지 제6권 3호: pp. 111 ~ 131"),
                   (0, "u0", u"2022년 8월")], 1, set())
    ok34 = r34["year"]["value"] == 2022
    r34b = extract([(0, "u0", u"요약 본문 블록"),
                    (1, "u1", u"2022년 8월")], 2, set())
    ok34 = ok34 and r34b["year"]["value"] is None
    # 35-37) 2026-11-24 KCI11_17: 英文刊被 langid 误判 pt/fr 时, 刊名/卷号不得丢。
    r35 = extract([(0, "u0", "Analyses & Alternatives 2025 9(2): 095-118 DOI: 10.22931/aanda.2025.9.2.004")],
                  1, set(), lang="pt")
    ok35 = r35["journal"]["value"] == "Analyses & Alternatives" \
        and r35["volume"]["value"] == 9 and r35["issue"]["value"] == 2
    r36 = extract([(0, "u0", "JAMS / JOURNAL OF ADVANCES IN MILITARY STUDIES"),
                   (0, "u1", "2025, Vol. 8, No. 2, 21-41")], 1, set(), lang="fr")
    ok36 = r36["journal"]["value"] == "Journal of Advances in Military Studies" \
        and r36["year"]["value"] == 2025 and r36["volume"]["value"] == 8 \
        and r36["issue"]["value"] == 2
    r37 = extract([(0, "u0", u"Analyses & Alternatives 2023 7(1): 007-043 DOI: 10.22931/aanda.2023.7.1.001")],
                  1, set(), lang="fr")
    ok37 = r37["volume"]["value"] == 7 and r37["issue"]["value"] == 1 \
        and r37["journal"]["value"] == "Analyses & Alternatives"
    # 38-39) 2026-11-24 俄葡法工作区: 法文 '64e année' 卷号 / 葡文迟到页眉 '17(3):621-626, 2012'。
    r38 = extract([(0, "u0", u"Dans Annales. Histoire, Sciences Sociales 2009/6 64e année, pages 1237 à 1266")],
                  1, set(), lang="fr")
    # 该守卫只负责 '64e année' → 卷号; 期/页由同行/其他题录行另供。
    ok38 = r38["volume"]["value"] == 64 and r38["year"]["value"] == 2009
    r39 = extract([(2, "u2", u"Ciência & Saúde Coletiva, 17(3):621-626, 2012")], 3, set(), lang="pt")
    ok39 = r39["year"]["value"] == 2012 and r39["volume"]["value"] == 17 \
        and r39["issue"]["value"] == 3 and r39["pages"]["value"] == "621-626"
    # 41) 机构署名行不得当 running 刊名(2026-11-25, 13570501 等 11 篇实证):
    #   机构行每页重复会被 _apply_running_journal 选中, 压过页眉首行真刊名。
    _j41 = extract([(0, "u0", u"디지털문화아카이브지 Vol.7 No.2, 2024.10."),
                    (0, "u1", u"전북대학교 문화융복합아카이빙연구소"),
                    (1, "u2", u"디지털문화아카이브지 Vol.7 No.2, 2024.10."),
                    (1, "u3", u"전북대학교 문화융복합아카이빙연구소")], 2, set())
    ok41 = _j41["journal"]["value"] == u"디지털문화아카이브지"
    # 42) running_header 垃圾值被 DOI 注册表纠正(2026-11-25, BJH 3 篇):
    #   只放行 running_header 来源; 其它来源(原文直接判定)不得被覆盖。
    _bjh = u"방정환연구"
    ok42 = (_should_registry_override({"confidence": {"source": "running_header"}}, _bjh)
            and _should_registry_override({"confidence": {"source": "channel_fallback"}}, _bjh)
            and not _should_registry_override({"confidence": {"source": "regex"}}, _bjh)
            and not _should_registry_override({"confidence": {"source": "doi"}}, _bjh)
            and not _should_registry_override({"confidence": {"source": "running_header"}}, "The Korea Journal")
            and not _should_registry_override(None, _bjh)
            # manual 行封闭值域: 值不符即纠正(不受来源限制), 即使来源 regex。
            and _should_registry_override({"confidence": {"source": "regex"}}, _bjh, True)
            and not _should_registry_override({"confidence": {"source": "regex"}}, _bjh, False))
    # 43) 断行残片 Journal of 可被 DOI 注册表(corpus 行也可)纠正; 正常刊名不受影响。
    ok43 = (_is_journal_fragment(u"Journal of")
            and _is_journal_fragment(u"Journalof")
            and not _is_journal_fragment(u"Journal of Culture Archives")
            and _should_registry_override({"value": u"Journal of", "confidence": {"source": "regex"}},
                                          u"디지털문화아카이브지")
            and not _should_registry_override({"value": u"ESP Review", "confidence": {"source": "regex"}},
                                               u"디지털문화아카이브지"))
    for i, ok in enumerate((ok1, ok2, ok3, ok4, ok5, ok6, ok7, ok8, ok9, ok10, ok11, ok12, ok13,
                            ok14, ok15, ok16, ok17, ok18, ok19, ok19b, ok20, ok21, ok22, ok23, ok24, ok25, ok26,
                            ok27, ok28, ok29, ok30, ok31, ok32, ok33, ok34, ok35, ok36, ok37, ok38, ok39,
                            ok41, ok42, ok43), 1):
        if ok:
            n_ok += 1
        else:
            print("FAIL [issue case %d]" % i)
    print("issue selftest: %d/43 PASS" % n_ok)
    return n_ok == 43


if __name__ == "__main__":
    raise SystemExit(0 if _selftest() else 1)
