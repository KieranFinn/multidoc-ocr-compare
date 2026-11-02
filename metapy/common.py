__all__ = ['ABSTRACT_START', 'ABS_HEAD_EN', 'ABS_HEAD_KO', 'ABS_INLINE_KO', 'AFFIL_HEAD', 'AGREE_CHARS', 'AY_HEAD', 'BOUND_DICE', 'CITE_HINT', 'CONTENT_TYPES', 'CONT_GAP', 'DEGREE', 'DOI_RE', 'ENTRY_MIN_LEN', 'FOOTNOTE_CITE', 'HEAD_EXCLUDE', 'HEAD_ZONE_LINES', 'HONORIFIC', 'ITALIC_TITLE', 'KNAME', 'KW_LINE', 'KW_NO', 'KW_VOL', 'LIST_MARK', 'LV_COLOR', 'MARGIN_X', 'MARKER_LINE', 'MIN_GLUE_LEN', 'NARROW_W', 'NEW_START', 'NEW_WORDS', 'NOTE_LINE', 'NOTE_WORDS', 'NO_RE', 'NUM_MARK', 'PGNO_LINE', 'PG_RANGE', 'PP_RE', 'QUOTE_TITLE', 'ROLE_WORDS', 'ROOT', 'STRUCT_TYPES', 'SUP_LATEX', 'TAIL_SOFT_LINES', 'TERM', 'TITLE_EN_ZONE', 'TITLE_ZONE', 'URL_LINE', 'VOL_RE', 'YEAR_OK', 'YEAR_RE', '_BIO_MARK', '_clean_lines', '_col_blocks', '_col_of', '_col_of_b', '_complete', '_content_blocks', '_dh_core', '_doc_heads', '_fold', '_frag_ok', '_group_from_members', '_hangul_ratio', '_head_agree', '_is_margin', '_is_running', '_join_sep', '_lang_mismatch', '_line_h', '_norm_ws', '_page_col_boundary', '_rebuild', '_standalone_note', '_strip_glued', '_struct_texts']

# -*- coding: utf-8 -*-
r"""metadata.py 元数据字段层(M5 路线A) + 前置 M3 跨页拼接。
只消费 out\<doc>\unified\ 与 merged\(不改 pipeline/merge_out/unify/regress 现有行为; 对 unified 页文件
做 M3 后处理写回, 重跑 unify.py 后需重跑本脚本才能恢复 M3 标记)。
M3 跨页拼接(unify 阶段④占位 cross_page{spans:false} 的落地):
  页尾无终止标点的块与下页页首块, 信号全满足才拼: ① 页尾块无终止标点(多渠道佐证, 独立注记行豁免)
  ② 栏位续接先验(末栏尾→下页首栏头; 非末栏尾→先查本页次栏头, 兼容则属栏内续接不跨页)
  ③ 文本相似连续(粘入证据 | 边界dice | 片段头渠道一致, 拼接点语言错位一票否决)
  ④ 页码/URL/页眉页脚行剔除; 栏外竖条(学会侧标)与文献尾注不作文本流。
  形态: join=跨页拼接 / join_intra=栏间断点拼接(同机制内页版) / verify=页尾块经查证完整(消 xpage 误标)
  / backfill=MinerU 粘入的下页段首从页脚型块剥离、回填下页空块或并入下页片段头。
  确认后 cross_page{spans:true, pages:[n,n+1]}; 拼后文本重过 T1 消截断误报; 被吞并块标 role=head 不再单独出眼。
用法: python metadata.py <doc> [<doc>...] [--out out] [--stage m3]
"""

import os, re, io, json, argparse, datetime, collections, html

import pipeline

from pipeline import _gdice, _fold_sym

import regress

import unify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # metapy/ 之上即 pipeline/

TAIL_SOFT_LINES = 2.2   # 页尾软 zone: 栏内容底边容差(行高倍数; 页尾块常被引擎粘上页脚/页码拉长几何, 只作信息记录)

HEAD_ZONE_LINES = 3.0   # 页首 zone: 栏顶首个内容块容差(行高倍数)

MIN_GLUE_LEN = 25       # 页脚/页眉型块中"粘入正文"的最小折叠长度

BOUND_DICE = 0.25       # 信号③: 尾头边界 24 字 dice 连续门槛

AGREE_CHARS = 10        # 信号③: 页首块头部渠道一致的最小折叠前缀长度

CONT_GAP = 1.6          # 栏内续接最大垂直间距(倍行高, 同 unify 段块续接口径)

NARROW_W = 0.07         # 栏外竖条判定: 块宽 < 7% 页宽(如 1632041 "군역사 및 역사일반" 侧边条)

MARGIN_X = 0.93         # 且 x0 > 93% 页宽(页缘)

STRUCT_TYPES = {"Page-header", "Page-footer", "Page-number"}

CONTENT_TYPES = {"Text", "Footnote"}          # 可跨页续接的类型(图/表/题注/标题不参与)

TERM = unify.TERM                              # 终止标点口径与 unify 一致

PGNO_LINE = re.compile(r"^\s*[-–—]?\s*\d{1,3}\s*[-–—]?\s*$")

URL_LINE = re.compile(r"^(www\.|https?://|kci\.go)", re.I)

NOTE_LINE = re.compile(r"^\s*[\*∗†‡§¶※]+")     # 独立注记行(* 이 연구는…研究费): 无句点即完整, 非截断

NOTE_WORDS = re.compile(u"(교신저자|제\\s*\\d\\s*저자|연구비|지원을\\s*받아|이\\s*연구는)")

FOOTNOTE_CITE = re.compile(r"^\s*(\d{1,2}\)|\[\d{1,3}\]|〔\d+〕)")

CITE_HINT = re.compile(r"(19|20)\d{2}|pp?\.\s?\d|학회|회지|Vol\.?\s?\d|제\s*\d+\s*(권|집|호)|출판사")

NEW_START = re.compile(
    r"^\s*(\[\s*\d{1,3}\s*\]|\d{1,2}[\).、]\s|\d{1,3}\s(?!\d)|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+[.、]\s|"
    r"(Fig(ure)?|Table|Chart|표|그\s*림)\s*\.?\s*\d|<[표그림]|〈그림|#{1,6}\s|"
    r"Abstract|요\s*약|초\s*록|Keywords?|참고\s*문헌|인용\s*문헌|References|REFERENCES|"
    r"Bibliography|저자\s*소개|머리말|서\s*문|English\s*Abstract|[•·ㆍ∙]|[“\"'『「(（<])", re.I)

NEW_WORDS = re.compile(
    u"^(그러나|그런데|그러므로|그럼에도|그리고|따라서|또한|또는|한편|이러한|이는|이를|이와|이에|이처럼|"
    u"그것은|즉|및|첫째|둘째|셋째|넷째|다섯째|그러면|먼저|마지막으로|결론적으로|요약하면|특히|심지어|"
    u"반면|더욱이|예를 들면|예를 들어|살펴 본|조사한|연구의|본 연구|이번 연구|향후|결론|요약)")

def _fold(s):
    return re.sub(r"\s+", "", _fold_sym(s or ""))

def _line_h(unified_list):
    """行高=P20 小高度分位(同 unify 口径, 中位数会被大段块推高)。"""
    hs = sorted(max(1.0, u["box"][3] - u["box"][1]) for u in unified_list)
    return hs[min(len(hs) - 1, int(len(hs) * 0.2))] if hs else 18.0

def _col_of(u, W):
    return 0 if (u["box"][0] + u["box"][2]) / 2.0 < W / 2.0 else 1

def _is_margin(u, W):
    """栏外竖条(学会侧标/书脊标签): 窄且贴页缘, 不是正文流。"""
    return (u["box"][2] - u["box"][0]) < NARROW_W * W and u["box"][0] > MARGIN_X * W

def _struct_texts(unified_list):
    """该页页眉/页脚/页码块的文本(折叠集合)——页尾清洗时的剔除依据。"""
    return [_fold(u["main_text"]) for u in unified_list
            if u["type"] in STRUCT_TYPES and (u["main_text"] or "").strip()]

def _clean_lines(t, stexts, doc_heads=()):
    """剔页码行/URL行/页眉页脚行(折叠全等或包含running脚+页码残差, 含文档级 running 头/脚)。"""
    kept, dropped = [], []
    for ln in (t or "").split("\n"):
        s = ln.strip()
        if not s:
            continue
        f = _fold(s)
        if PGNO_LINE.match(s) or URL_LINE.match(s) or f in stexts or f in doc_heads:
            dropped.append(s)
            continue
        if any(dh in f and len(f) - len(dh) <= 5 for dh in doc_heads):
            dropped.append(s)  # running 脚带页号残差(如 '208 군사연구 제130집')
            continue
        kept.append(ln.rstrip())
    return "\n".join(kept).rstrip(), dropped

def _strip_glued(raw, head_fold):
    """从渠道读法尾部剥掉"已粘入的下页段首"(折叠前缀匹配, 须紧贴读法末尾)。"""
    hf = head_fold[:48]
    if len(hf) < 6:
        return raw, False
    fr = _fold(raw)
    idx = fr.find(hf)
    if idx <= 0:
        return raw, False
    # 后缀锚定: 粘入段必在末尾(后面只容忍残差 15%), 防块内合法重复文本被误剥
    if idx + len(hf) < len(fr) - max(8, int(len(fr) * 0.15)):
        return raw, False
    n = 0
    for i, ch in enumerate(raw):
        if not ch.isspace():
            if n >= idx:
                return raw[:i].rstrip(), True
            n += 1
    return raw, False

def _complete(t):
    t = (t or "").rstrip()
    return bool(t) and t[-1] in TERM

def _standalone_note(t):
    """独立注记(研究费/作者贡献/隶属脚注): 单行 * 起, 或含 교신저자/연구비 等词——无句点即完整。"""
    s = (t or "").strip()
    if not s:
        return False
    if NOTE_LINE.match(s) and len(s) < 90:
        return True
    if NOTE_WORDS.search(s) and len(s) < 200:
        return True
    return False

def _lang_mismatch(tail, head):
    """拼接点语言错位(韩尾英头/英尾韩头): 无粘入证据时判不连续(句中换语必带终止标点或空格)。"""
    a, b = (tail or "").rstrip(), (head or "").lstrip()
    if not a or not b:
        return False
    def _k(ch):
        return u"가" <= ch <= u"힣" or u"一" <= ch <= u"鿿" or ch in u"々〆"
    return _k(a[-1]) != _k(b[0])

def _join_sep(tail, head):
    a, b = (tail or "").rstrip(), (head or "").lstrip()
    if not a or not b:
        return ""
    def _k(ch):
        return u"가" <= ch <= u"힣" or u"一" <= ch <= u"鿿" or ch in u"々〆"
    return "" if (_k(a[-1]) and _k(b[0])) else " "

def _head_agree(head_u):
    starts = [_fold(r.get("text") or "")[:AGREE_CHARS] for r in head_u["readings"]
              if (r.get("text") or "").strip()]
    return max([starts.count(s) for s in set(starts)] + [0])

def _frag_ok(tail_clean, head_u):
    """片段头判定: 页首块以"句中片段"起(非新元素/非独立连接词、渠道头一致、拼接点语言不错位)。"""
    head = (head_u["main_text"] or "").strip()
    return bool(head) and not NEW_START.match(head) and not NEW_WORDS.match(head) \
        and _head_agree(head_u) >= 2 and not _lang_mismatch(tail_clean, head)

def _rebuild(g, pno, W, H, in_ref, uid, h_line):
    """以重构组 g 重跑 unify.make_unified(置信/分歧/主读法全链路口径一致)。"""
    return unify.make_unified(g, pno, W, H, in_ref, uid, h_line)

def _group_from_members(a_u, b_u, texts_a=None, texts_b=None):
    """把统一对象的 members 还原成 make_unified 的组结构; 可逐渠道替换文本(剥离粘入段用)。"""
    g = {}
    for u, repl in ((a_u, texts_a), (b_u, texts_b)):
        if u is None:
            continue
        for ch, units in u["members"].items():
            for m in units:
                mm = dict(m)
                if repl and ch in repl:
                    mm["text"] = repl[ch]
                g.setdefault(ch, []).append(mm)
    return g

def _content_blocks(unified_list, W):
    """参与文本流判定内容块: Text/Footnote, 排除已并入跨页块的 head 与栏外竖条。"""
    return [u for u in unified_list
            if u["type"] in CONTENT_TYPES
            and not u.get("cross_page", {}).get("role")
            and not _is_margin(u, W)]

def _col_blocks(unified_list, W):
    cols = {}
    for u in _content_blocks(unified_list, W):
        cols.setdefault(_col_of(u, W), []).append(u)
    return cols

def _dh_core(f):
    """running 头/脚核心: 折叠后剥去首/尾数字串(页码随页变化, '군사연구 제130집 211'->'군사연구제130집')。"""
    return re.sub(r"^\d+|\d+$", "", f or "")

def _doc_heads(pdata, pages):
    """文档级 running 头/脚/侧标核心集合: 顶/底带块(顶12%/底20%, 任意类型)逐行取核心,
    4~22 折叠字符、无终止标点、>=3 页出现。覆盖误标 Text 的刊名头/学会脚/卷期脚/页脚品牌。"""
    cnt = collections.Counter()
    for p in pages:
        j = pdata[p]
        seen = set()
        for u in j["unified"]:
            if not (u["box"][1] < 0.12 * j["h"] or u["box"][1] > 0.80 * j["h"]):
                continue
            for ln in (u["main_text"] or "").split("\n"):
                s = ln.strip()
                if not s or PGNO_LINE.match(s):
                    continue
                core = _dh_core(_fold(s))
                if 4 <= len(core) <= 22 and core[-1] not in TERM:
                    seen.add(core)
        for c in seen:
            cnt[c] += 1
    return set(k for k, v in cnt.items() if v >= 3)

def _is_running(text, doc_heads):
    """整段判定为 running 头/脚: 每个非空行(页码行豁免)的核心都命中 doc_heads。"""
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    if not lines:
        return False
    body = [ln for ln in lines if not PGNO_LINE.match(ln)]
    if not body:
        return True
    return all(_dh_core(_fold(ln)) in doc_heads for ln in body)

ENTRY_MIN_LEN = 15          # 条目最短折叠长度(防碎片)

YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")

YEAR_OK = range(1900, 2027)

DOI_RE = re.compile(r"10\.\d{4,}/[^\s\"”’]+", re.I)

QUOTE_TITLE = re.compile(u"[“\"『「](.+?)[”\"』」]")

ITALIC_TITLE = re.compile(r"\*([^*\n]{4,})\*")

NUM_MARK = re.compile(r"^\s*\[\s*(\d{1,3})\s*\]")

LIST_MARK = re.compile(r"^\s*(\d{1,3})\s*[.、]\s")

AY_HEAD = re.compile(
    r"(?m)^(?=[A-Z가-힣])[^\n]{0,200}?(?:\((?:19|20)\d{2}[a-z]?|,\s*(?:19|20)\d{2}[a-z]?(?=[,.]))")

VOL_RE = re.compile(r"(?:vol\.?|V\.?)\s*(\d+)", re.I)

NO_RE = re.compile(r"(?:no\.?|No\.?)\s*(\d+)", re.I)

KW_VOL = re.compile(r"제\s*(\d+)\s*권")

KW_NO = re.compile(r"제\s*(\d+)\s*호")

PP_RE = re.compile(r"pp?\.\s*(\d+)\s*[-–]\s*(\d+)", re.I)

PG_RANGE = re.compile(r"(?:^|[\s,(])((?:\d{1,4}))\s*[-–]\s*(\d{1,4})(?=[.,)\s]|$)")

HONORIFIC = re.compile(
    u"(교수|부교수|조교수|연구원|박사|정회원|우수회원|특별회원|학회장|이사장|명예교수|객원교수)$")

KNAME = re.compile(u"[가-힣]{2,4}")

def _norm_ws(t):
    """空白归一: OCR 会把 '홍 영 식' 切成孤立音节, 拼接成 '홍영식' 前先看整体(逐 token 判孤立音节)。"""
    toks = (t or "").split()
    out, buf = [], []
    for tk in toks:
        if len(tk) == 1 and u"가" <= tk <= u"힣":
            buf.append(tk)
            continue
        if buf:
            out.append("".join(buf))
            buf = []
        out.append(tk)
    if buf:
        out.append("".join(buf))
    return " ".join(out)

_BIO_MARK = re.compile(r"[▪■•]|현재|e-mail|@|월\s*[:：]|년\s*월|정회원|우수회원|본\s*연구는|연구비|지원을\s*받아|감사")

def _page_col_boundary(j):
    """页栏判定: 内容块左边缘的最大空隙分两栏(>0.15W 且两侧各>=2块)才认双栏,
    否则单栏(返回 None)。防 1632041 单栏变宽行被中心点切分误认双栏(条目顺序被打乱)。"""
    W = j["w"]
    xs = sorted(u["box"][0] for u in j["unified"]
                if u["type"] in CONTENT_TYPES and not u.get("cross_page", {}).get("role")
                and not _is_margin(u, W))
    if len(xs) < 4 or xs[-1] - xs[0] <= 0.30 * W:
        return None
    gap, gi = 0.0, -1
    for i in range(len(xs) - 1):
        g = xs[i + 1] - xs[i]
        if g > gap:
            gap, gi = g, i
    if gap > 0.15 * W and (gi + 1) >= 2 and (len(xs) - gi - 1) >= 2:
        return (xs[gi] + xs[gi + 1]) / 2.0
    return None

def _col_of_b(u, boundary, W):
    if boundary is None:
        return 0
    return 0 if (u["box"][0] + u["box"][2]) / 2.0 < boundary else 1

TITLE_ZONE = (0.06, 0.52)      # 标题区(页高比例, 打分因子非硬切——running头/专题可能更大)

TITLE_EN_ZONE = (0.06, 0.62)

ABSTRACT_START = re.compile(u"^(본 연구|이 연구|본 논문|The purpose|This (study|paper|article|research)|In this|In the present|The present|We (compared|investigated|examined)|Abstract\\s*[:：])", re.I)

HEAD_EXCLUDE = re.compile(u"^(차\\s*례|목\\s*차|요\\s*약|국문\\s*요약|·?국문요약|Abstract|ABSTRACT|Keywords?|중심\\s*어|핵심\\s*어|서\\s*론|머리\\|Ⅰ\\.|I\\.)", re.I)

SUP_LATEX = re.compile(r"\$\s*\^\{([^}]*)\}\s*\$")

AFFIL_HEAD = re.compile(u"(대학교|대학원|대학|학과|학부|연구소|연구원|학회|학원|병원|공사|학교|위원회|재단|센터|기업체|관|University|College|Institute|Laboratory|Dept\\.|School of|Hospital)")

ROLE_WORDS = re.compile(u"(교신저자|제\\s*\\d+\\s*저자|공동저자|책임저자|선행\\s*연구)")

DEGREE = re.compile(u"\\((?:문학|이학|교육|법학|정치|경제|역사)?박사\\)")

MARKER_LINE = re.compile(u"^(\\*{1,4}|\\d{1,2}\\)|†|‡|§)\\s*(.+)$", re.S)

ABS_HEAD_KO = re.compile(u"^(요\\s*약|·?국문\\s*요약|국문\\s*요약|초\\s*록)$")

ABS_INLINE_KO = re.compile(u"^(요\\s*약|국문\\s*요약|초\\s*록)\\s*[:：]")

ABS_HEAD_EN = re.compile(r"^(<\s*)?(Abstract|ABSTRACT)(\s*>)?$|^\(Abstract\)$")

KW_LINE = re.compile(u"^(키\\s*워드|중심\\s*어|핵심\\s*어|주요\\s*어|Key\\s*words?|Keywords?|Keyword\\s*:|■\\s*중심|■\\s*Keyword)", re.I)

LV_COLOR = {"high": "#16a34a", "mid": "#d97706", "low": "#dc2626"}

if __name__ == "__main__":
    main()


def _hangul_ratio(t):
    h = len(re.findall(u"[가-힣]", t or ""))
    a = len(re.findall(r"[A-Za-z]", t or ""))
    return h / max(1.0, h + a)
