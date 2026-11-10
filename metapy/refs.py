# -*- coding: utf-8 -*-
import os, re, io, json, datetime, collections
import regress
from .common import *


def _split_entries_block(t, style):
    """块文本 -> [(num|None, entry_text)]。bracketed: [n] 切; numbered: 行首 N. 切; authoryear: 条目头行切。"""
    t = (t or "").strip()
    if not t:
        return []
    if style == "bracketed":
        out = []
        for seg in regress.split_entries(t):
            m = NUM_MARK.match(seg)
            out.append((int(m.group(1)) if m else None, seg.strip()))
        return [x for x in out if x[1]]
    if style == "numbered":
        parts = re.split(r"(?m)(?=^\s*\d{1,3}\s*[.、]\s)", t)
        out = []
        for seg in parts:
            seg = seg.strip()
            if not seg:
                continue
            m = LIST_MARK.match(seg)
            if m and int(m.group(1)) <= 300:
                out.append((int(m.group(1)), seg))
            elif out:
                out[-1] = (out[-1][0], out[-1][1] + " " + seg)   # 无行首编号的行并入上条
            elif len(_fold(seg)) >= ENTRY_MIN_LEN:
                out.append((None, seg))
        return out
    # authoryear: 条目头行切, 头前行(无年份)并入
    marks = [m for m in AY_HEAD.finditer(t)]
    if len(marks) >= 1:
        out = []
        pos = [m.start() for m in marks]
        head, tail0 = t[:pos[0]], t[pos[0]:]
        # 按条目头行重组
        cur = ""
        for ln in tail0.split("\n"):
            if AY_HEAD.match(ln) and cur:
                out.append((None, cur.strip()))
                cur = ln
            else:
                cur = (cur + "\n" + ln) if cur else ln
        if cur.strip():
            out.append((None, cur.strip()))
        if head.strip() and len(_fold(head.strip())) >= ENTRY_MIN_LEN:
            out.insert(0, (None, head.strip()))
        return [x for x in out if x[1]]
    return [(None, t)] if len(_fold(t)) >= ENTRY_MIN_LEN else []

def _detect_style(page_blocks):
    for u in page_blocks:
        t = u["main_text"] or ""
        if re.search(r"(?m)^\s*\[\s*\d{1,3}\s*\]", t):
            return "bracketed"
    for u in page_blocks:
        t = u["main_text"] or ""
        if re.search(r"(?m)^\s*\d{1,3}\s*[.、]\s", t):
            return "numbered"
    return "authoryear"

def _looks_cite(etext):
    """条目像不像文献: 年份(合法)+引号题名; 或 卷期页/学会/出版社线索(裸年份不算)。"""
    has_y = any(int(m.group(1)) in YEAR_OK for m in YEAR_RE.finditer(etext))
    if has_y and QUOTE_TITLE.search(etext):
        return True
    return bool(re.search(r"pp?\.\s?\d|학\s*회|회\s*지|제\s*\d+\s*[권호]|출판사|University|Press|Journal|학술",
                          etext))

def _entry_like(etext, num):
    """条目准入: 编号条目=有合法年/引号/来源线索(编号本身强信号, 结论编号项无年);
    无编号条目= looks_cite 或 (有年 且 非简历/注记特征)。"""
    has_y = any(int(m.group(1)) in YEAR_OK for m in YEAR_RE.finditer(etext))
    if num is not None:
        return has_y or _looks_cite(etext)
    if _looks_cite(etext):
        return True
    return has_y and not _BIO_MARK.search(etext)


def _year_pick(e):
    """年份候选生成+评分排序(消歧策略集中于此, 不再 first-match-wins):
      +3 串尾收尾('年.'/'年' 著录惯例)   +1 后随 , . ) 或前接 ( (년) 式)
      -3 后随 -数字('2015-74' 行政号陷阱) 或紧接 'No.' 之后(公告号)
    平分取位置靠后者; 捕获 1982a 式著录后缀。返回 (match|None, suffix)。"""
    best = None
    for m in YEAR_RE.finditer(e):
        y = int(m.group(1))
        if y not in YEAR_OK:
            continue
        # 1.3: 检索日拒收——括号组定位为主+head/tail 双窗为辅, 含 '최종 검색/검색일/retrieved/accessed' 的年份候选排除
        op = e.rfind("(", max(0, m.start() - 60), m.start())
        cl = e.find(")", m.end())
        grp_hit = op >= 0 and cl > m.end() and \
            re.search(r"최종\s*검색|검색\s*일|retrieved|accessed", e[op:cl + 1], re.I)
        win = e[max(0, m.start() - 14):m.end() + 14]
        win_hit = re.search(r"최종\s*검색|검색\s*일|retrieved|accessed", win, re.I)
        if grp_hit or win_hit:
            continue
        tail = e[m.end():m.end() + 10]
        head = e[max(0, m.start() - 14):m.start()]
        s = 0.0
        if re.match(r"^\s*[.。]?\s*$", tail):
            s += 3.0
        elif re.match(r"^\s*[.,)]", tail):
            s += 1.0
        if re.match(r"^\s*-\s*\d", tail):
            s -= 3.0
        if re.search(r"No\.?\s*$", head, re.I):
            s -= 3.0
        if re.search(r"\(\s*$", head):
            s += 1.0
        suf = ""
        msuf = re.match(r"([a-z])[\s,]", tail)   # 1982a 式著录后缀(后随分隔)
        if msuf:
            suf = msuf.group(1)
            s += 1.5   # 带后缀=刻意的同年消歧著录, 等价于"后随标点"惯例且优先于同年无后缀候选
        key = (s, m.start())
        if best is None or key >= best[0]:
            best = (key, m, suf)
    if not best:
        return None, None
    return best[1], best[2]


KW_JI = re.compile(r"제\s*(\d{1,3})\s*집")   # A6: 집(辑)=호(期), 期号提取补 '제N집'(u36_0 实证)
T_NULL_KEYS = ("题名碎片", "题名与来源雷同", "题名页码")   # 1.9: 校验层拦下题名的 issues 全集(refs/frefs/_grade_entry 三处共用口径)
POS_ALARMS = (u"疑似错位", u"疑似引导语", u"书章未拆残留", u"题名含页标记")   # 批次3 位置合理性组 + 批次4b 页标记族: 只报警不动 level, 进 audit.pos_alarm; level=high 且报警=LLM 待办矛盾源

def _seg_clean(s):
    """noquote 段清洁: 剥逗/点/空白; 括号仅在不配成对时剥单边——防吃掉 '江原道史(역사편)' 的右括号(A4)。"""
    s = (s or "").strip(u" ,. ")
    if s.startswith("(") and s.count("(") > s.count(")"):
        s = s[1:].strip()
    if s.endswith(")") and s.count(")") > s.count("("):
        s = s[:-1].strip()
    return s

def _clean_src(s):
    """1.8: 出产品前 source 字段内清理——'In :' 变体归一/成对斜体星号剥除/前导冒号剥离。
    只动 source 字段(成对剥), 不全局替换——正文脚注符号 * 不误伤。"""
    if not s:
        return s
    s = re.sub(r"\bIn\s+:\s*", "In: ", s)
    s = re.sub(r"\*([^*\n]+)\*", r"\1", s)
    return s.lstrip(u":： ").rstrip(u" ,.")

MK = re.compile(r"제\s*(\d{1,4})\s*([권호집])(?![을를의은는과와\s]*[가-힣])\s*[.]?\s*$")
BARE = re.compile(r"^(.*?)[\s․](\d{1,3})[.]?\s*$")
BK_TAIL = re.compile(u"史|사$|論|연구$|저술|전집")
JF_TAIL = re.compile(u"문화|학보|회보|저널|학회|회지|지$|연구$")
ORG_TAIL = re.compile(u"주관|학회$|연구회$|연구소$|연구원$|위원회$|대학교$|대학원$|출판사$|출판부$|박물관$|청$|도$|시$|부$|군$|문화원$|재단$")
_ALNUM = r"[0-9A-Za-z가-힣一-鿿]"


def _manual_like(t):
    """批次2/D2 保守路线: en_manual/ko 无标记尾年著录判据(AMOS/NIMH/1892171 단행본簇实证)——
    无引号/斜体 + 年落尾 + ≥3 逗号段 → 逐块开 tailflow 支(非全局开关; 前置年/带引号条目不触发, 波2基线零扰)。"""
    if QUOTE_TITLE.search(t) or ITALIC_TITLE.search(t):
        return False
    myear, _ = _year_pick(t)
    if not myear:
        return False
    if t[myear.end():].strip().strip(u". .").strip():
        return False   # 年后有实质内容=非尾年
    head = t[:myear.start()]
    segs = [s for s in head.split(",") if re.search(_ALNUM, s)]
    return len(segs) >= 2


def _layout_fields(e, f):
    """批次2: 体例识别+段角色化。命中返回覆盖字段(layout/title/container_title/publisher/editor/
    volume/issue), 未命中 {} 维持 base。R1-R5=批次0 dry-run 实测判据(9 锚 8 全对, §45);
    en_chapter='In:'+`(eds)` 书章编者回收。source 语义不变(原始著录串), 新字段供下游消费(D3 CSL 命名)。"""
    if f.get("grammar") == "notice":
        return {"layout": "notice"}
    if re.search(r"https?://|www\.", e):
        return {"layout": "url"}
    out = {}
    # —— en_chapter: 'In:' + '(eds)' ——
    mc = re.search(r"\bIn\s*:\s*(.+?)\s*,?\s*\(eds?\.?\)\s*(.+)$", e)
    if mc:
        out["layout"] = "en_chapter"
        ed = _split_authors(mc.group(1).strip().rstrip(","), latcorp=True)
        out["editor"] = ed or None
        segs = [s.strip() for s in mc.group(2).split(",") if re.search(_ALNUM, s)]
        if segs:
            out["container_title"] = _seg_clean(segs[0])
            pubs = []
            for s in segs[1:]:
                if re.search(r"pp?\.|vol\.?|no\.|제\s*\d", s, re.I):
                    break
                pubs.append(s)
            if pubs:
                out["publisher"] = _norm_ws(", ".join(pubs))
        return out
    myear, _ = _year_pick(e)
    if not myear:
        return {}
    mend = myear.end()
    if e[mend:mend + 1] in "abcdefghijklmnopqrstuvwxyz":
        mend += 1
    rest = e[mend:].strip().lstrip(",. ").lstrip(")").strip()
    rest = re.sub(u"^년[^,)]*\\),?\\s*", "", rest)   # A7 残余
    segs = [s.strip() for s in rest.split(",") if re.search(_ALNUM, s)]
    tail_empty = not rest.strip(u". .")
    # 守卫口径: base 题名来自引号/斜体(quoted/italic/remnant/ambiguous)才只补不覆——
    # noquote 的 base 题名=year-first 首段(正是欠切的那半), R1-R5 覆盖即本批目的
    has_qtitle = bool(f.get("title")) and f.get("grammar") in ("quoted", "italic", "remnant_quote", "ambiguous_quote")
    if len(segs) >= 2:
        # R3 学位论文
        for i, s in enumerate(segs):
            if re.search(u"학위\\s*논문", s):
                out.update(layout="ko_thesis", container_title=s)
                if i > 0 and not has_qtitle:
                    out["title"] = ", ".join(segs[:i])
                if len(segs) > i + 1:
                    out["publisher"] = ", ".join(segs[i + 1:])
                return out
        # R4b 段内『』章 / R4c 段首『』书
        for i, s in enumerate(segs):
            mq = re.search(u"(.+?)[『「](.+?)[』」]", s)
            if mq:
                out.update(layout="ko_chapter", title=mq.group(1).strip(u" ,."),
                           container_title=mq.group(2).strip())
                if len(segs) > i + 1:
                    out["publisher"] = ", ".join(segs[i + 1:])
                return out
            mq = re.search(u"^[『「](.+?)[』」]", s)
            if mq:
                out.update(layout="ko_book", title=mq.group(1).strip())
                if len(segs) > i + 1:
                    out["publisher"] = ", ".join(segs[i + 1:])
                return out
        # R1 数字标记段切分(粒子守卫+年份守卫; 段内切开: 标记前=container, 此前段拼接=title)
        for i, s in enumerate(segs):
            m = MK.search(s)
            if m:
                num = int(m.group(1))
                if num in YEAR_OK and len(m.group(1)) == 4 and m.group(2) in (u"호", u"집"):
                    continue
                out["layout"] = "ko_5seg"
                if i > 0 and not has_qtitle:
                    out["title"] = ", ".join(segs[:i])
                cont = s[:m.start()].strip(u" ,. ")
                if cont:
                    out["container_title"] = cont
                out["volume" if m.group(2) == u"권" else "issue"] = m.group(1)
                if len(segs) > i + 1:
                    out["publisher"] = ", ".join(segs[i + 1:])
                return out
        # R2 裸数字: 书型(container=全段+volume) / 刊型(container=head+issue)
        for i, s in enumerate(segs):
            m = BARE.match(s)
            if m and m.group(2).isdigit():
                n = int(m.group(2))
                if n in YEAR_OK and len(m.group(2)) == 4:
                    continue
                head0 = m.group(1).strip()
                if BK_TAIL.search(head0):
                    rest_segs = segs[:i] + segs[i + 1:]
                    if rest_segs and not has_qtitle:
                        out["title"] = ", ".join(rest_segs)
                    out.update(layout="ko_book", container_title=s, volume=m.group(2))
                    return out
                if JF_TAIL.search(head0) and 1 <= n <= 300:
                    out["layout"] = "ko_jrnl"
                    if i > 0 and not has_qtitle:
                        out["title"] = ", ".join(segs[:i])
                    out["container_title"] = head0
                    out["issue"] = m.group(2)
                    if len(segs) > i + 1:
                        out["publisher"] = ", ".join(segs[i + 1:])
                    return out
        # R5 机构尾段=publisher
        if ORG_TAIL.search(segs[-1]):
            out["layout"] = "ko_agency"
            if not has_qtitle:
                out["title"] = segs[0]
            if len(segs) > 2:
                out["container_title"] = ", ".join(segs[1:-1])
            out["publisher"] = segs[-1]
            return out
    # 尾年无标记(经 _manual_like 开 tailflow 的 en_manual/ko 条目): 仅打标
    if tail_empty and f.get("grammar") in ("noquote", "unknown"):
        hsegs = [s for s in NUM_MARK.sub("", LIST_MARK.sub("", e[:myear.start()])).split(",")
                 if re.search(_ALNUM, s)]
        if len(hsegs) >= 2:
            out["layout"] = "en_manual" if re.search(r"[A-Za-z]", hsegs[0]) else "ko_manual"
    elif f.get("grammar") in ("quoted", "remnant_quote", "italic") and \
            re.search(r"vol\.?|no\.?|pp?\.|제\s*\d", e, re.I):
        out["layout"] = "ko_journal" if _hangul_ratio(e) > 0.3 else "en_journal"
    return out


def _corner_biblio_fields(e, myear):
    """韩中尾年书目: 作者, 「文章」/『图书』, 『载体』/出版社, 年。"""
    if not myear:
        return {}
    segs = [s.strip() for s in re.split(u"[,，]", e[:myear.start()]) if s.strip()]
    if len(segs) < 2:
        return {}
    article = re.fullmatch(u"「([^」]+)」", segs[1])
    book = re.fullmatch(u"『([^』]+)』", segs[1])
    if not article and not book:
        return {}
    auth0 = segs[0].strip()
    if not auth0 or re.search(u"내용은|연구는|참고|참조|서술", auth0):
        return {}
    title = (article or book).group(1).strip()
    if len(_fold(title)) < 2:
        return {}
    out = {"grammar": "quoted", "title": title, "authors_raw": auth0,
           "authors": _split_authors(auth0, corp=True, latcorp=True)}
    if article:
        if len(segs) < 3:
            return {}
        container = re.fullmatch(u"『([^』]+)』(.*)", segs[2])
        if not container:
            return {}
        source = container.group(1).strip()
        out.update(layout="ko_journal" if re.search(
            r"제\s*\d+\s*호|\d+\s*(?:권|호|기)", container.group(2)) else "ko_chapter",
            source=source, container_title=source)
        if len(segs) > 3:
            out["publisher"] = ", ".join(segs[3:])
    else:
        out.update(layout="ko_book", source=None, container_title=None)
        if len(segs) > 2:
            out["publisher"] = segs[-1]
    return out


_CLASSICAL_TITLE = re.compile(
    u"三國史記|삼국사기|高麗史|고려사|大東地志|춘천풍토기|강원도지|강원도사|"
    u"[가-힣一-鿿]+실록|[가-힣一-鿿]+實錄|[가-힣一-鿿]+(?:道史|地志|道志|邑誌|風土記|戰爭史)|"
    u"6[․.]25戰爭史")


def _plain_inline(s):
    return re.sub(r"</?(?:sub|sup|i|b|em|span)>", "", s or "", flags=re.I)


def _reference_fallback_fields(e, f):
    pairs = list(re.finditer(u"「([^」]+)」|『([^』]+)』", e))
    if pairs:
        article = next((m for m in pairs if m.group(1) is not None), None)
        chosen = article or pairs[0]
        title = (chosen.group(1) or chosen.group(2)).strip()
        if len(_fold(title)) < 2:
            return {}
        out = {"grammar": "quoted", "title": title}
        books = [m for m in pairs if m.group(2) is not None]
        if article and books:
            source = books[0].group(2).strip()
            out.update(source=source, container_title=source,
                       layout="ko_journal" if re.search(r"제\s*\d+\s*호|\d+\s*(?:권|호|기|卷|期)", e)
                       else "ko_chapter")
        else:
            out.update(layout="ko_book", source=None, container_title=None)
        auth0 = None
        writers = list(re.finditer(r"([가-힣一-鿿]{2,4})\s*집필", e))
        if writers:
            auth0 = writers[-1].group(1)
        if not auth0:
            pre = e[:chosen.start()]
            first = re.split(u"[,，]", pre)[0].strip()
            standard = (len(re.findall(u"[,，]", pre)) == 1 and u"）" not in pre
                        and not re.search(u"내용은|연구는|참고|참조|서술|필자의|에 대해서는", first))
            if standard:
                auth0 = first
            else:
                ma = re.search(r"([가-힣一-鿿]{2,4})의\s*$", pre)
                mb = re.search(r"([가-힣一-鿿]{2,4})\s*[,，]\s*$", pre)
                if (ma or mb) and not (ma or mb).group(1).endswith((u"은", u"는")):
                    auth0 = (ma or mb).group(1)
                else:
                    post = re.match(r"\(\s*([^,，()]+)\s*[,，]", e[chosen.end():])
                    if post and not re.search(u"내용|참조|참고|서술", post.group(1)):
                        auth0 = post.group(1).strip()
        if not auth0 and re.search(u"내용은|연구는|에 대해서는", e[:chosen.start()]):
            return {}
        out["authors_raw"] = auth0 or None
        out["authors"] = _split_authors(auth0, corp=True, latcorp=True) if auth0 else []
        return out
    mi = ITALIC_TITLE.search(e)
    if mi:
        opening = re.search(r"[\"“]", e[:mi.start()])
        if opening:
            title = e[opening.end():mi.start()].strip(" ,.,")
            if len(_fold(title)) >= 8:
                return {"grammar": "remnant_quote", "title": title, "source": mi.group(1).strip(),
                        "container_title": mi.group(1).strip(), "layout": "en_journal"}
    myear, _ = _year_pick(e)
    legal_years = [m for m in YEAR_RE.finditer(e) if int(m.group(1)) in YEAR_OK]
    if legal_years and re.search(r"\b(?:Case|Cases|Judgments?|Award)\b", e):
        title = _plain_inline(e[:legal_years[0].start()])
        title = re.sub(r"\bST/LEG/.*$", "", title).strip(" ,.([")
        if len(_fold(title)) >= 4:
            return {"grammar": "noquote", "title": title, "source": None,
                    "authors": [], "authors_raw": None}
    edited = re.match(r"^([^,，]+?편찬위원회)\s*[,，]\s*(\d+)\(", e)
    if edited:
        auth0 = edited.group(1).strip()
        return {"grammar": "noquote", "title": auth0[:-5], "authors_raw": auth0,
                "authors": [auth0], "volume": edited.group(2), "layout": "ko_book",
                "source": None, "container_title": None}
    if myear:
        special = re.search(r"필자의\s+(.+?)\s*\(", e[:myear.start()])
        if special:
            return {"grammar": "noquote", "title": special.group(1).strip(),
                    "authors": [], "authors_raw": None}
        segs = [s.strip() for s in re.split(u"[,，]", e[:myear.start()]) if s.strip()]
        if len(segs) >= 2:
            auth0 = segs[0].strip()
            title = _plain_inline(segs[1]).strip(u" ,.，｢｣“”")
            title = re.sub(r"^[\ue000-\uf8ff]+|[\ue000-\uf8ff]+$", "", title)
            title = re.sub(r"\s*\{\s*\d+\s*\}.*$", "", title)
            title = re.sub(r"\s*제\s*\d+\s*장.*$", "", title)
            title = re.sub(r"\s+\d+\([^)]*\)$", "", title).strip(" ,.，")
            bad_auth = re.search(u"내용은|연구는|참고|참조|서술|필자의|에 대해서는", auth0)
            if not bad_auth and len(_fold(title)) >= 2 and re.search(_ALNUM, title) \
                    and not re.match(r"^pp?\.\s*\d", title, re.I) \
                    and not re.search(u"[「」『』]", title):
                out = {"grammar": "noquote", "title": title, "authors_raw": auth0,
                       "authors": _split_authors(auth0, corp=True, latcorp=True),
                       "layout": "ko_manual" if _hangul_ratio(e) > 0.3 else "en_manual",
                       "source": None}
                if len(segs) == 3:
                    src = _seg_clean(segs[2])
                    if ORG_TAIL.search(src) or re.search(r"Press|Inc\.?|프레스|출판|연구소|문화원", src, re.I):
                        out["source"] = src
                elif len(segs) > 3 and any(u"옮김" in s for s in segs[2:-1]):
                    out["publisher"] = segs[-1]
                press = re.search(r"\(([^()]*(?:University Press|Press))[^()]*$", segs[1], re.I)
                if press:
                    out["source"] = press.group(1).strip()
                return out
    if re.search(r"\b(?:Case|Award|Judgment)\b", e):
        title = re.split(r"\((?:Award|Judgment)\b", e, maxsplit=1, flags=re.I)[0]
        title = _plain_inline(title).strip(" ,.([")
        if len(_fold(title)) >= 4:
            return {"grammar": "noquote", "title": title, "source": None,
                    "authors": [], "authors_raw": None}
    classics = [m for m in _CLASSICAL_TITLE.finditer(e)
                if not e[m.end():].startswith(u"편찬위원회")]
    if classics:
        return {"grammar": "noquote", "title": classics[-1].group(0), "layout": "ko_book",
                "source": None, "container_title": None, "authors": [], "authors_raw": None}
    return {}


def _extract_fields(entry, tailflow=False):
    """L3 正则抽取(候选+多策略, 每策略打语法类标):
    语法类: quoted(引号对) / italic(斜体) / remnant_quote(单侧缺引号的残余段) /
           notice(政府公告) / noquote(无引号: 韩(년)式与英段式同路) / unknown(皆未中)。
    作者/年/题名/来源/卷期页/DOI/语种; 失败留 None, 由 _validate_fields 与 ④ LLM 接续。"""
    e = entry.strip()
    f = {"grammar": "unknown", "layout": None, "container_title": None,
         "publisher": None, "editor": None}
    mdoi = DOI_RE.search(e)
    f["doi"] = mdoi.group(0).rstrip(".,;") if mdoi else None
    myear, ysuffix = _year_pick(e)
    f["year"] = (myear.group(1) + ysuffix) if myear else None
    f["year_suffix"] = ysuffix or None
    mend = myear.end() if myear else 0
    if e[mend:mend + 1] in "abcdefghijklmnopqrstuvwxyz":
        mend += 1   # 1.4: 消费著录后缀(1982a/b), rest 起点不再从 'b,' 起算(mcut 位置/source 头清洁)
    # 政府公告型('Author, X Notice No. 2015-74, 公告主题, 2015.'): 无引号无(年)形态, 常规语法覆盖外
    mnotice = re.search(r"Notice\s+No\.?\s*([\d][\d-]*)", e, re.I)
    f["notice_no"] = mnotice.group(1) if mnotice else None
    mq = QUOTE_TITLE.search(e)
    mi = ITALIC_TITLE.search(e)
    if mq and len(_fold(mq.group(1))) < 8:
        # 1.5: 配对结果过短 → 向前重配一次(题名内嵌直引号截断, Schwertmann 实证; 闭合引号后随 , 或 *=美式标点内/外两式);
        # 重配仍不成 → 标 ambiguous_quote 暂置空; 末尾仅回收结构明确的韩中书目, 否则留待复核/④LLM
        mq2 = re.search(r"[“\"](.+)[”\"](?=\s*[,\*])", e)
        if mq2 and len(_fold(mq2.group(1))) >= 8:
            mq = mq2
        else:
            mq = None
            f["grammar"] = "ambiguous_quote"
    if mq:
        f["title"] = mq.group(1).strip().rstrip(",").strip()   # 1.5: 重配支的组含引内逗号(美式标点), 尾逗号剥除
        f["grammar"] = "quoted"
    elif mi:
        if re.search(u"[“\"『「]", e[:mi.start()]):
            # 1.5b: italic 题名可信的前提是全文无未配对开引号——有开无闭时 italic 更像期刊名(1982b Nordstrom 题名/来源对调实证)
            f["title"] = None
            f["grammar"] = "ambiguous_quote"
        else:
            f["title"] = mi.group(1).strip()
            f["grammar"] = "italic"
    else:
        f["title"] = None
    rem = None
    if mq is None and mi is None and f.get("grammar") != "ambiguous_quote":
        # 缺开引号的题名(段内含 ” 而无 “, 如 '…science textbook”, DongA…'): 按闭引号段回收
        rem = re.search(r"(?:^|,\s*|․\s*|·\s*)([^,“\"『「«]{4,90}?[”」』])", e)
        if rem:
            f["title"] = rem.group(1).strip(u" ”」』")
            f["grammar"] = "remnant_quote"
    # 作者: 题名/年份前的一段; 公告型(Notice No.)切在公告号标记前(num10 'Ministry of Education Notice' 混入作者实证)
    if mnotice:
        cut = mnotice.start()
    else:
        cut = len(e)
        tpos = [m.start() for m in (mq, mi, rem) if m]
        if tpos:
            cut = min(tpos)
        elif myear:
            cut = myear.start()
    auth = e[:cut].strip()
    auth = NUM_MARK.sub("", auth)
    auth = LIST_MARK.sub("", auth)
    my2 = YEAR_RE.search(auth)          # 韩式 作者(年) 体型: 作者段内年份截断
    if my2:
        auth = auth[:my2.start()]
    auth = auth.rstrip("(, ").rstrip(",․·. ").strip()
    auth = re.sub(r"[\s,]*No\.?\s*\d[\d-]*\s*[.。]?\s*$", "", auth).strip()   # 剥离尾部公告号(2015-74 类)
    auth = re.sub(r"[\s,]*No\.?\s*$", "", auth).strip()                      # 公告号数字被年切走后残留的 'No'
    if mnotice and "," in auth:
        # 公告型作者段去重(num10 'Ministry of Education, Ministry of Education' 公告名复述实证)
        segs = [s.strip() for s in auth.split(",") if s.strip()]
        auth = ", ".join(dict.fromkeys(segs))
    f["authors_raw"] = auth or None
    # etc/et al./외 省略标记: 入 authors 数组是实体错误, 只留 et_al 旗标, raw 保留原样
    f["et_al"] = bool(re.search(r"(?:\betc\.?|\bet\s+al\.?)\s*$", auth or "", re.I)) or \
                 bool(re.search(u"(?:[\\s,·․]|^)외\\s*$", auth or ""))
    if f["et_al"]:
        auth = re.sub(r"[\s,]*(?:\betc\.?|\bet\s+al\.?)\s*$", "", auth, flags=re.I).strip()
        auth = re.sub(u"[\\s,·․]*외\\s*$", "", auth).strip()
    f["authors"] = _split_authors(auth, corp=bool(mnotice), latcorp=tailflow) if auth else []
    # 来源: 题名后(或年份后)至卷/期/页/年份前的首段
    rest = e[mq.end():] if mq else (e[rem.end(1):] if rem else (e[mend:] if myear else ""))
    src = None
    if rest.strip():
        mcut = re.search(r"(?:vol\.?\s*\d|V\.?\s*\d|no\.?\s*\d|제\s*\d+\s*[권호집]|pp?\.\s*\d|,\s*(?:19|20)\d{2})", rest, re.I)
        seg = rest[:mcut.start()] if mcut else rest
        seg = seg.strip().strip(",. ")
        if seg and not YEAR_RE.fullmatch(seg):
            src = _norm_ws(seg)
    if f["title"] is None and not mq and myear and f.get("grammar") != "ambiguous_quote":
        # 无引号条目(韩 '저자(년), 제목, 출처' / 英 'Author, year, Title, Publisher' 同路):
        # 年后首段=题名, 次段=来源
        rest2 = e[mend:].strip().lstrip(",. ").lstrip(")").strip()
        rest2 = re.sub(u"^년[^,)]*\\),?\\s*", "", rest2)   # A7: '(1915년경 발간)' 年后残余剥离, 防 '년경 발간' 灌入题名(u35_12 实证)
        segs2 = [s.strip() for s in rest2.split(",")
                 if re.search(r"[0-9A-Za-z가-힣一-鿿]", s)]   # 纯标点段(如 A7 剥后残留的 '.')不入段
        if segs2:
            f["title"] = _norm_ws(_seg_clean(segs2[0]))
            src = _norm_ws(_seg_clean(segs2[1])) if len(segs2) > 1 else None
            if src:
                mji = re.search(u"(.+?)\\s*제\\s*\\d{1,3}\\s*[집호]\\s*$", src)
                if mji and len(mji.group(1).strip()) >= 2:
                    src = mji.group(1).strip()   # A6: 来源尾挂 제N집/제N호 拆出, 防 '강원문화사연구 제4집' 混号(u36_0 实证)
            f["grammar"] = "noquote"
        elif mnotice:
            # 公告型: 公告号之后、尾年之前的整段=公告主题(题名)
            after = re.sub(r"^[\s,，]+", "", e[mnotice.end():])
            after = re.sub(r"[\s,，]*((?:19|20)\d{2})\s*[.。]?\s*$", "", after).strip(u" ,.")
            if len(_fold(after)) >= 4:
                f["title"] = _norm_ws(after)
                f["grammar"] = "notice"
        elif tailflow and f["title"] is None and myear and f.get("grammar") != "ambiguous_quote":
            # 尾年逗号流(frefs 开关; 脚注常态): '作者, 제목, 출처, 년도.'——年份在尾, 年前段按逗号分层
            # (refs 主区韩式 author(년) 前置流走首分支; 开关化=references[] 冻结基线零影响)
            head = NUM_MARK.sub("", LIST_MARK.sub("", e[:myear.start()]))
            hsegs = [s.strip() for s in head.split(",") if s.strip()]
            if len(hsegs) >= 2:
                tit0 = _seg_clean(hsegs[1]).strip(u"'\"")
                # 题名内 '. 出版社' 黏连拆分('정서심리학. 시그마프레스'实证): 出版社尾词命中则后段归 src
                pm = re.match(u"^(.{2,}?)[.]\\s+(.*(?:프레스|출판사|Press|Publishing|사))[.]?\\s*$", tit0)
                if pm and not src:
                    f["title"] = _norm_ws(pm.group(1).strip())
                    src = pm.group(2).strip()
                else:
                    f["title"] = _norm_ws(tit0)
                if len(hsegs) > 2:
                    src = _norm_ws(_seg_clean(hsegs[2]))
                    mji = re.search(u"(.+?)\\s*제\\s*\\d{1,3}\\s*[집호]\\s*$", src)
                    if mji and len(mji.group(1).strip()) >= 2:
                        src = mji.group(1).strip()
                f["grammar"] = "noquote"
                # 作者重切: 年前首段才是作者(默认 cut 至年位会把题名/来源段并进作者数组);
                # '/'=原文作者/韩译者分隔(取左), 尾标 '역/저/편' 剥离(1892171 u28_5 Kalat 实证);
                # 拉丁多词机构名整段保留(National Institute of Mental Health 实证), 免空白切拆碎
                auth0 = hsegs[0].split("/")[0].rstrip("(, ").rstrip(",․·. ").strip()
                auth0 = re.sub(u"\\s+(역|저|편|편집)$", "", auth0).strip()
                f["authors_raw"] = auth0 or None
                f["et_al"] = bool(re.search(r"(?:\betc\.?|\bet\s+al\.?)\s*$", auth0 or "", re.I)) or \
                             bool(re.search(u"(?:[\\s,·․]|^)외\\s*$", auth0 or ""))
                if f["et_al"]:
                    auth0 = re.sub(r"[\s,]*(?:\betc\.?|\bet\s+al\.?)\s*$", "", auth0,
                                   flags=re.I).strip()
                    auth0 = re.sub(u"[\\s,·․]*외\\s*$", "", auth0).strip()
                corp0 = bool(re.search(r"University|Institute|Ministry|Committee|Center|Centre|"
                                       r"Corporation|Association|Foundation|National|Government|"
                                       u"대학|연구소|연구원|학회|위원회", auth0))
                f["authors"] = _split_authors(auth0, corp=corp0 or bool(mnotice),
                                              latcorp=True) if auth0 else []
    if src and f.get("year") and re.search(r"\s+" + re.escape(f["year"][:4]) + r"\s*$", src):
        src = re.sub(r"\s+" + re.escape(f["year"][:4]) + r"\s*$", "", src).strip(" ,.")  # 出版社与年间无逗号时尾年粘入(13625974 num16 实证)
    f["source"] = src
    mv = VOL_RE.search(e) or KW_VOL.search(e)
    f["volume"] = mv.group(1) if mv else None
    if mnotice:
        # 公告型: 公告号不占期号/页码(num10 'issue=2015/pages=2015-74' 污染实证), 只归 notice_no
        f["issue"] = f["pages"] = None
    else:
        mn = NO_RE.search(e) or KW_NO.search(e) or re.search(u"권\\s*(\\d{1,3})\\s*호", e) \
            or KW_JI.search(e)
        f["issue"] = mn.group(1) if mn else None
        mpp = PP_RE.search(e)
        pages = None
        if mpp:
            pages = "%s-%s" % (mpp.group(1), mpp.group(2))
        else:
            mpg = PG_RANGE.search(e)
            if mpg:
                pages = "%s-%s" % (mpg.group(1), mpg.group(2))
        if pages:
            a0, _, b0 = pages.partition("-")
            ai, bi = int(a0), int(b0)
            # 1.2 守卫(与 ~ 支持同窗落地, 反例 2015-74/1982-1983): 降序拒收(杀公告号)+双端四位年份区间拒收
            if ai >= bi or (len(a0) == 4 and len(b0) == 4 and ai in YEAR_OK and bi in YEAR_OK):
                pages = None
        f["pages"] = pages
    # 批次2: 体例识别+段角色化(R1-R5/en_chapter 命中时覆盖 base 的 title/container/publisher 等)
    lay = _layout_fields(e, f)
    if lay:
        f.update(lay)
        # base source(多为 year-first 次段)与新题名雷同时, 容器角色已由 container_title 承担 → source 归位容器串
        if f.get("container_title") and _foldv(f.get("source") or "") == _foldv(f.get("title") or ""):
            f["source"] = f["container_title"]
    if f.get("grammar") == "ambiguous_quote":
        corner = _corner_biblio_fields(e, myear)
        if corner:
            f.update(corner)
    title0 = f.get("title") or ""
    if not title0 or re.match(r"^(?:pp?\.\s*\d|\d+(?:\s*[~∼-]\s*\d+)?\s*쪽)", title0, re.I) \
            or not re.search(u"[A-Za-z가-힣一-鿿]", title0) \
            or any(k in _validate_fields(f) for k in T_NULL_KEYS):
        recovered = _reference_fallback_fields(e, f)
        if recovered:
            f.update(recovered)
    # 1.8: 出产品前字段内清理(source 走 _clean_src; 题名著录串成对星号剥除)
    f["source"] = _clean_src(f.get("source"))
    f["container_title"] = _clean_src(f.get("container_title"))
    if f.get("title"):
        f["title"] = re.sub(r"\*([^*\n]+)\*", r"\1", f["title"]).strip()
    h = len(re.findall(u"[가-힣一-鿿]", e))   # A5: 汉字计入韩文(韩旧著录汉混, '春川百年史' lang='?' 实证)
    el = len(re.findall(r"[A-Za-z]", e))
    f["lang"] = "ko" if h > el else ("en" if el > h else "?")
    return f


def _validate_fields(f):
    """校验独立层(与抽取解耦): 机械合法性检查, 返回 issues 列表。
    错值即使被抽出也会被这里拦下(降级/置 None), 是 L2 存在性互证的天坑补丁。
    issue -> 处置: 题名碎片/题名与来源雷同/公告主题缺 -> 题名置 None 且 low;
    其余由 refs_doc 按既有政策降档。"""
    issues = []
    if not f.get("year"):
        issues.append("年缺")
    if not f.get("authors"):
        issues.append("作者缺")
    t = f.get("title")
    if not t:
        issues.append("题名缺")
        if f.get("grammar") == "notice":
            issues.append("公告主题缺")
    else:
        min_len = 3 if re.search(u"[가-힣一-鿿]", t) else 4
        if len(_fold(t)) < min_len or not re.search(u"[A-Za-z가-힣一-鿿]", t):
            issues.append("题名碎片")
        s = f.get("source")
        if s and _foldv(t) == _foldv(s):
            issues.append("题名与来源雷同")
        # 1.9: 全串页码形——剥光页码符号后无字母/韩文/汉字残留(剥光式无实义判据; 字符类含 ․ U+2024 ONE DOT LEADER——
        # u21_7 'pp.61～62․p.171 참조' 实漏, 批次4b 修) → 题名页码
        if not re.sub(u"(?i)pp?\\.?|[\\d.,\\s~～·․\\-]+|쪽|참조", "", t):
            issues.append("题名页码")
    if f.get("authors") and any(re.fullmatch(r"[\W\d_]+", a or "x") for a in f["authors"]):
        issues.append("作者含纯数值")
    if f.get("grammar") == "notice":
        issues.append("公告型著录")
    # —— 批次3 位置合理性组(消费批次2 CSL 字段; 只报警不动 level: 复核留 audit.pos_alarm,
    #    level=high 且报警=LLM 待办矛盾源, 批次4 混合层消费):
    # 错位=source 畸长(>3x题名)且无载体特征词且批次2未给容器(长 source 已被 container_title 解释的不报);
    # 引导语=作者段像句子(≥2词 非机构 段尾는/은/이다/임 且含 참조/내용/자세하 词表——高precision低召回子集);
    # 书章未拆=In: 落进 source 但 container_title 未抽出(en_chapter '(eds)' 正则未覆盖的残留形态)。
    src0 = f.get("source")
    # 批次4b: 题名含页标记(참조/쪽/pp.N)——页码形错值的归因报警(u21_7 型; 与 1.9 剥光式互补: 剥光拦纯页码串,
    # 本族拦"题名里混进页标记"的可疑形态), 只报警不动 level
    if t and re.search(u"참조|쪽|pp?\\.\\s*\\d|^\\d+쪽", t):
        issues.append(u"题名含页标记")
    if t and src0 and not f.get("container_title") and f.get("layout") != "url" \
            and len(_fold(src0)) > 3 * max(len(_fold(t)), 4) \
            and not re.search(r"pp?\.\s?\d|학\s*회|회\s*지|제\s*\d+\s*[권호]|출판사|University|Press|Journal|학술",
                              src0, re.I):
        issues.append(u"疑似错位")
    for a0 in f.get("authors") or []:
        seg = (a0 or u"").strip().strip(u".,;")
        if len(seg.split()) < 2 or not re.search(u"[가-힣]", seg) or ORG_TAIL.search(seg):
            continue
        if seg.endswith((u"는", u"은", u"이다", u"임")) and any(w in seg for w in (u"참조", u"내용", u"자세하")):
            issues.append(u"疑似引导语")
            break
    if src0 and re.search(r"\bIn\s*:", src0, re.I) and not f.get("container_title"):
        issues.append(u"书章未拆残留")
    return issues


# —— 语料语法类样本断言(4 篇实证条目; 改抽取逻辑后跑 python -m metapy.refs 自检) ——
SELFTEST = [
    (u"[1] Y. Kwak, “Research on the effects of Subject Matter Knowledge(SMK)”, Journal of the Korean Association for Science Education, vol.29, no.6, pp.611-625, 2009.",
     {"year": "2009", "grammar": "quoted", "has_title": True, "volume": "29", "issue": "6", "pages": "611-625"}, "corpus-13625974"),
    (u"[10] Ministry of Education, Ministry of Education Notice No. 2015-74, Science and Curriculum, 2015.",
     {"year": "2015", "grammar": "notice", "title": u"Science and Curriculum",
      "notice_no": "2015-74", "authors": ["Ministry of Education"],
      "issue": None, "pages": None}, "corpus-13625974"),
    (u"이훈진, “편집성향집단의 분노표현양식과 귀인 및 추론양식”, 한국심리학회지: 임상, 제20권 3호, 한국심리학회, 2001.",
     {"year": "2001", "grammar": "quoted", "title": u"편집성향집단의 분노표현양식과 귀인 및 추론양식",
      "volume": "20", "issue": "3"}, "corpus-1892171"),
    (u"1. 노혁진·장미영·심재연(1994), 춘천군의 도요지 및 관방유적 『춘천군의 역사와 문화유적』, 한림대 박물관·강원도·춘천군",
     {"year": "1994", "grammar": "quoted", "layout": "ko_chapter",
      "title": u"춘천군의 도요지 및 관방유적", "container_title": u"춘천군의 역사와 문화유적",
      "publisher": u"한림대 박물관·강원도·춘천군"}, "corpus-1632041"),
    (u"Nordstrom, D. K., 1982a, “Aqueous pyrite oxidation and the consequent”, In: Kittrick, J. A., 1982, p. 1.",
     {"year": "1982a", "grammar": "quoted", "volume": None}, "corpus-895567-suffix"),
    (u"사이버경찰청, “순직 및 공상자 현황”, http://www.police.go.kr(2014년 3월 24일 최종검색).",
     {"year": None, "grammar": "quoted", "title": u"순직 및 공상자 현황",
      "v_issues": [u"年缺"]}, "corpus-1892171-url"),
    (u"Grim, R. E., 1968, Clay mineralogy, McGraw-Hill Book Company, 468 p.",
     {"year": "1968", "grammar": "noquote", "title": "Clay mineralogy"}, "corpus-895567-noquote-en"),
    (u"[13] S. Chang etc., Elementary school 6th grade science textbook” DongA Publishing Co. Ltd, 2022.",
     {"year": "2022", "grammar": "remnant_quote", "title": "Elementary school 6th grade science textbook",
      "authors": ["S. Chang"], "et_al": True}, "corpus-13625974-remnant"),
    (u"[16] D. Hyeon etc., Elementary school 6th grade science textbook”, I-Scream Media Co., Ltd. 2022.",
     {"year": "2022", "grammar": "remnant_quote", "source": "I-Scream Media Co., Ltd",
      "authors": ["D. Hyeon"], "et_al": True}, "corpus-13625974-yearglue"),
    (u"Meffert S. Metzler T. Henn-Haase C., “A Prospective study of trait anger”, Journal of Traumatic Stress, Vol.21, No.4, 2008.",
     {"year": "2008", "grammar": "quoted", "title": "A Prospective study of trait anger"}, "corpus-1892171-en"),
    (u"34) 이훈진, “편집성향집단의 분노표현양식”, 한국심리학회, 2001.",
     {"year": "2001", "title": u"편집성향집단의 분노표현양식"}, "footnote-num-prefix"),
    (u"[2] J. Ahn, “Development Science Content Knowledge(SCK) of Elementary School Teacher’s Professionalism”, Master’s Thesis, Daegu National University of Education, Daegu, Korea, 2017.",
     {"year": "2017", "grammar": "quoted", "authors": ["J. Ahn"],
      "source": u"Master’s Thesis, Daegu National University of Education, Daegu, Korea"}, "corpus-13625974-mcut"),
    (u"[15] S. Lee etc., Elementary school 6th grade science textbook”, Visang, 2022.",
     {"year": "2022", "grammar": "remnant_quote", "source": "Visang",
      "authors": ["S. Lee"], "et_al": True}, "corpus-13625974-mcutV"),
    (u"[20] Y. Hong, “Comparative Analysis of Science Textbooks According to the 2015 Revised Curriculum for Future Creative Talent”, Journal of Creative Information Culture, vol.8, no.4, pp.281-290, 2022.",
     {"year": "2022", "grammar": "quoted", "source": "Journal of Creative Information Culture",
      "volume": "8", "issue": "4", "pages": "281-290"}, "corpus-13625974-mcutV"),
    (u"3. 충북대 중원문화연구소(2000), 춘천 삼악산성",
     {"year": "2000", "grammar": "noquote", "authors": [u"충북대 중원문화연구소"],
      "title": u"춘천 삼악산성"}, "corpus-1632041-corpA3"),
    (u"5. 문화재청․강원도․춘천시․강원문화재연구소(2003), 문화유적 분포지도 춘천시",
     {"year": "2003", "grammar": "noquote",
      "authors": [u"문화재청", u"강원도", u"춘천시", u"강원문화재연구소"],
      "title": u"문화유적 분포지도 춘천시"}, "corpus-1632041-multiA2"),
    (u"7. 춘천공립보통학교 편(1915년경 발간), 春川郡郷土史料",
     {"year": "1915", "grammar": "noquote", "authors": [u"춘천공립보통학교"],
      "title": u"春川郡郷土史料"}, "corpus-1632041-pyonA1A7"),
    (u"10. 강원도(1995), 江原道史(역사편)",
     {"year": "1995", "grammar": "noquote", "authors": [u"강원도"],
      "title": u"江原道史(역사편)"}, "corpus-1632041-parenA4"),
    (u"11. 春川市(1996), 春川百年史",
     {"year": "1996", "grammar": "noquote", "authors": [u"春川市"],
      "title": u"春川百年史", "lang": "ko"}, "corpus-1632041-hanjaA5"),
    (u"4. 유재춘(1999), 북한강 유역의 전란과 성곽 연구, 강원문화사연구 제4집",
     {"year": "1999", "grammar": "noquote", "authors": [u"유재춘"],
      "title": u"북한강 유역의 전란과 성곽 연구", "source": u"강원문화사연구",
      "issue": "4", "layout": "ko_5seg", "container_title": u"강원문화사연구"}, "corpus-1632041-jiA6"),
    (u"2. 김기옥(1986), 한국전쟁초기, 춘천·홍천전투가 전세에 미친 영향, 軍史 제12호, 국방부 전사편찬위원회",
     {"year": "1986", "layout": "ko_5seg",
      "title": u"한국전쟁초기, 춘천·홍천전투가 전세에 미친 영향",
      "container_title": u"軍史", "issue": "12", "publisher": u"국방부 전사편찬위원회"}, "corpus-1632041-gunshi"),
    (u"Brown, G., 1980, “Associated minerals,” In: Brindley, G. W. and Brown, G., (eds) Crystal structures of clay minerals and their X-ray identification, Mineralogical Society, London, pp. 361-410.",
     {"year": "1980", "grammar": "quoted", "layout": "en_chapter", "title": "Associated minerals",
      "editor": ["Brindley G. W", "Brown G"],
      "container_title": "Crystal structures of clay minerals and their X-ray identification",
      "publisher": "Mineralogical Society, London", "pages": "361-410"}, "corpus-895567-chap"),
    (u"Arbuckle, AMOS Manuel, SPSS Inc., 1997.",
     {"year": "1997", "grammar": "noquote", "layout": "en_manual", "authors": ["Arbuckle"],
      "title": "AMOS Manuel", "source": "SPSS Inc"}, "corpus-1892171-manual"),
    (u"유재춘, 북한강 유역의 전란과 성곽 연구 , 강원문화사연구 제4집, 1999.",
     {"year": "1999", "grammar": "noquote", "authors": [u"유재춘"],
      "title": u"북한강 유역의 전란과 성곽 연구", "source": u"강원문화사연구",
      "issue": "4"}, "corpus-1632041-tailflow"),
    (u"노병식, 춘천 삼악산성의 축조양상 , 한국성곽학회 2005년도 추계학술대회 발표문, 2005년11월 19일(개최장소 : 춘천국립박물관 대강당).",
     {"year": "2005", "grammar": "noquote", "authors": [u"노병식"],
      "title": u"춘천 삼악산성의 축조양상"}, "corpus-1632041-taildate"),
    (u"Meffert S. 외, “A Prospective study of trait anger”, Journal of Traumatic Stress, Vol.21, No.4, 2008.",
     {"year": "2008", "grammar": "quoted", "authors": ["Meffert S"], "et_al": True}, "corpus-1892171-we"),
    (u"이하 왜구침입과 관계된 주요 내용은 『江原道史』(江原道, 1995, 609~612쪽)와 『高麗史』를 참고하여 서술함.",
     {"year": "1995", "pages": "609-612", "title": u"江原道史", "authors": [u"江原道"],
      "v_issues": []}, "corpus-1632041-tailwave"),
    (u"Schwertmann, U. and Fischer, W. R., 1973, \"Natural \"amorphous\" ferric hydroxide,\" *Geoderma*, v. 10, pp. 237-247.",
     {"year": "1973", "grammar": "quoted", "title": "Natural \"amorphous\" ferric hydroxide",
      "source": "Geoderma", "pages": "237-247"}, "corpus-895567-nestq"),
    (u"Nordstrom, D. K., 1982b, \"The effect of sulfate on aluminum concentrations in natural waters: some stability relation in the system $Al_2O_3$-$SO_3$-$H_2O$ at 298K, *Geochimica et Cosmochimica Acta*, v. 46, pp. 681-692.",
     {"year": "1982b", "title": "The effect of sulfate on aluminum concentrations in natural waters: some stability relation in the system $Al_2O_3$-$SO_3$-$H_2O$ at 298K",
      "grammar": "remnant_quote", "source": "Geochimica et Cosmochimica Acta"}, "corpus-895567-ambq"),
    # 批次3 位置合理性组 frozen 语料/守卫例(v_issues=校验 issue 精确集合)
    (u'Carson, C. D., Fanning, D. S. and Dixon, J. B., 1982, "Alfisols and Ultisols with acid sulfate weathering features in Texas," In: Kittrick, J. A., Fanning, D. S. and Hossner, L. R., eds) Acid sulfate weathering, SSSA Special Publication Number 10, pp. 127-146.',
     {"year": "1982", "grammar": "quoted", "v_issues": [u"书章未拆残留"]}, "corpus-895567-inres"),
    (u"이하 주요 내용은, 「春川」, 강원도, 1995.",
     {"year": "1995", "v_issues": [u"题名缺", u"疑似引导语"]}, "guard-guide"),
    (u"홍길동(2020), 짧은 제목, 이것은 아주 아주 아주 아주 아주 아주 아주 아주 아주 길고 설명적인 그냥 긴 문자열이다.",
     {"year": "2020", "title": u"짧은 제목", "v_issues": [u"疑似错位"]}, "guard-misplace"),
    # 批次4b frozen 语料/守卫例: u21_7 页码成题名(1.9 补 ․ U+2024 后剥光式生效 + 题名含页标记归因报警);
    # u35_18 守卫(강원도사 2(선사시대): (선사시대) 是括号注非卷次, '2' 不得与括号注拆散——R2 BARE 锚段尾天然安全, 冻结锁定)
    (u"심경호, 茶山과 春川 , 강원대학교 출판부, 1995, pp.61～62․p.171 참조",
     {"year": "1995", "title": u"茶山과 春川", "authors": [u"심경호"],
      "source": u"강원대학교 출판부", "v_issues": []}, "corpus-1632041-pmark"),
    (u"18. 강원도사편찬위원회(2010), 강원도사 2(선사시대)",
     {"year": "2010", "title": u"강원도사 2(선사시대)", "volume": None, "v_issues": []}, "corpus-1632041-u35_18guard"),
    (u"심재연, 「봉의산성 소고」, 『춘주문화』 제15호, 춘천문화원, 2000.",
     {"year": "2000", "grammar": "quoted", "layout": "ko_journal", "authors": [u"심재연"],
      "title": u"봉의산성 소고", "source": u"춘주문화", "container_title": u"춘주문화",
      "issue": "15", "publisher": u"춘천문화원"}, "corner-article"),
    (u"배르벨 바르데츠키, 『나는 괜찮지 않다』, 강희진 옮김, 와이즈베리, 2016.",
     {"year": "2016", "layout": "ko_book", "authors": [u"배르벨 바르데츠키"],
      "title": u"나는 괜찮지 않다", "source": None, "container_title": None,
      "publisher": u"와이즈베리"}, "corner-book-translator"),
    (u"黎名邨, 「中國棉業問題」, 『東方雜誌』22권 18호, 1931, 33-35쪽.",
     {"year": "1931", "layout": "ko_journal", "authors": [u"黎名邨"],
      "title": u"中國棉業問題", "source": u"東方雜誌", "container_title": u"東方雜誌"}, "corner-zh-article"),
    (u"춘천 이궁에 대한 연구는 오영섭, 「春川離宮殻」, 『아시아문화』 12, 한림대학교 아시아문화연구소, 1996에 자세하다.",
     {"year": "1996", "title": u"春川離宮殻", "authors": [u"오영섭"],
      "source": u"아시아문화", "grammar": "quoted"}, "corner-narrative-guard"),
    (u"搖紗- 운송을 위해 일정 중량 단위로 실을 감는 공정, 打包- 운송을 위해 면사를 포장하는 공정 69）陶平叔，「論染織與棉紡」，『華商紗廠聯合會季刊』4卷1期，1923.1，11巻.",
     {"year": "1923", "title": u"論染織與棉紡", "authors": [u"陶平叔"],
      "source": u"華商紗廠聯合會季刊", "grammar": "quoted"}, "corner-merged-guard"),
    (u"，「，『』국불교사학회, 2019, 174∼175 참고.",
     {"year": "2019", "title": None, "grammar": "ambiguous_quote"}, "corner-ocr-guard"),
    (u"江原道史 (1995), 428쪽 참조.",
     {"year": "1995", "title": u"江原道史", "authors": [], "v_issues": [u"作者缺"]}, "classical-tail-year"),
    (u"강원도사편찬위원회, 2(선사시대), 제5장 · 제6장 참조.",
     {"title": u"강원도사", "authors": [u"강원도사편찬위원회"], "volume": "2"}, "edited-classical-book"),
]


def selftest():
    """语料样本与守卫断言自检: python -m metapy.refs。"""
    n_ok = 0
    for raw, exp, tag in SELFTEST:
        f = _extract_fields(raw, tailflow=bool(tag and (tag.startswith("corpus-1632041-tail")
                                                         or tag.endswith("-manual"))))
        bad = []
        for k, want in exp.items():
            if k in ("has_title", "v_issues"):
                if k == "has_title" and not f.get("title"):
                    bad.append("title missing")
                continue
            got = f.get(k)
            if k == "volume" and want is None:
                if got is not None:
                    bad.append("volume=%s want None" % got)
                continue
            if (got or None) != (want or None):
                bad.append("%s=%r want %r" % (k, got, want))
        # 校验层: 碎片题名必须被拦
        if f.get("title") == "-74":
            bad.append("junk title not caught")
        # 批次3: 校验层 issues 断言(v_issues=期望 issue 集合, 精确匹配)
        if "v_issues" in exp:
            got_is = set(_validate_fields(f))
            want_is = set(exp["v_issues"])
            if got_is != want_is:
                bad.append("issues=%r want %r" % (sorted(got_is), sorted(want_is)))
        if bad:
            print("FAIL [%s] %s" % (tag, "; ".join(bad)))
        else:
            n_ok += 1
    # 批次3.5: 异体折叠互证冒烟(u35_12 实证形态: dots 渠道读 鄕, 共识/其余 郷 → 折叠口径全 hit + fold_applied 留痕)
    _bu = {"readings": [
        {"ch": "mineru", "text": u"7. 춘천공립보통학교 편(1915년경 발간), 春川郡郷土史料"},
        {"ch": "dots", "text": u"7. 춘천공립보통학교 편(1915년경 발간), 春川郡鄕土史料"},
        {"ch": "paddle", "text": u"7. 춘천공립보통학교 편(1915년경 발간), 春川郡郷土史料"}]}
    _fa = _field_agree({"year": None, "title": u"春川郡郷土史料", "authors": []}, _bu)
    _ft = _fa.get("title") or {}
    if _ft.get("agree") != 1.0 or not _ft.get("fold_applied"):
        print("FAIL [foldv-probe] agree=%s fold_applied=%s" % (_ft.get("agree"), _ft.get("fold_applied")))
    else:
        n_ok += 1
    import tempfile
    texts = [u"36) " + u"산성의 위치와 복원 경위를 설명한다. " * 8,
             u"45) " + u"人物의 생애와 반란 경위를 설명한다. " * 8,
             u"55) " + u"山城 축조 여부에 대한 추정을 서술한다. " * 8,
             u"56) 검색일: 2025.6.26."]
    with tempfile.TemporaryDirectory() as tmp:
        udir = os.path.join(tmp, "all_notes", "unified")
        os.makedirs(udir)
        units = [{"uid": "u0_%d" % i, "type": "Footnote", "main_text": text,
                  "box": [10, 100 + i * 30, 90, 125 + i * 30], "cross_page": {},
                  "confidence": {"level": "high"}, "review": False, "readings": []}
                 for i, text in enumerate(texts)]
        io.open(os.path.join(udir, "page_0.json"), "w", encoding="utf-8").write(
            json.dumps({"doc": "all_notes", "page": 1, "w": 100, "h": 1000,
                        "in_ref": False, "unified": units}, ensure_ascii=False))
        got = frefs_doc("all_notes", tmp)
        if [r["num"] for r in got["footnote_refs"]] == ["36", "45", "55", "56"] \
                and all(not r["title"] for r in got["footnote_refs"]):
            n_ok += 1
        else:
            print("FAIL [all-numbered-footnotes]")
    previous = [
        {"num": "7", "title": u"春川 牛頭山城考", "authors_raw": u"유재춘"},
        {"num": "8", "title": "Brownlie's Principles", "authors_raw": "James Crawford"},
    ]
    by_note, by_author, by_last = {}, {}, {}
    _resolve_repeat_reference("Crawford, supra note 8, p.222", by_note, previous)
    _resolve_repeat_reference(u"유재춘, 앞의 논문, 173쪽.", by_author, previous)
    _resolve_repeat_reference("Ibid., p.5", by_last, previous)
    if by_note.get("title") == "Brownlie's Principles" \
            and by_author.get("title") == u"春川 牛頭山城考" \
            and by_last.get("title") == "Brownlie's Principles":
        n_ok += 1
    else:
        print("FAIL [repeat-resolution]")
    with tempfile.TemporaryDirectory() as tmp:
        text = "Barcelona, M. J., Holm, T. R., Schock, M. R., and George, G. K., 1989, Spatial gradients."
        entries = [{"text": text, "page": 13}, {"text": "No matching entry", "page": 13}]
        merged = {"page_data": [
            {"page": 14, "channels": {"mineru": [{"text": text, "box": [1, 2, 3, 4]}]}}
        ]}
        io.open(os.path.join(tmp, "merged.json"), "w", encoding="utf-8").write(json.dumps(merged))
        target = {"uid": "u13_2", "box": [1, 2, 3, 4], "review": False,
                  "confidence": {"level": "high"}, "main_text": text, "members": {}}
        changed = _reanchor_reference_entries(entries, tmp, {13: {"unified": [target]}})
        if changed == 1 and entries[0]["page"] == 14 and entries[0]["uid"] == "u13_2" \
                and entries[1]["page"] == 13:
            n_ok += 1
        else:
            print("FAIL [reference-page-reanchor]")
    # 脚注标记形态扩展(2026-11-09 用户拍板): 裸数字式(13563525) + 句点式(FI001004818_2002)
    # + Page-footer 型脚注回退(u8_8 '17 김은혜…' dots 成员带号); 符号/年份行首不误切
    if FN_CITE_X.match("2 이뭇별, 「글로컬」, 2020.") and FN_CITE_X.match("1. Ahn Byung-ook") \
            and FN_CITE_X.match("3、 오세미나") \
            and not FN_CITE_X.match("18 • Police Science Institute") \
            and not FN_CITE_X.match("2020, 전북대 인문학연구소.") \
            and not FN_CITE_X.match("2025년 9월 20일에 투고"):
        n_ok += 1
    else:
        print("FAIL [fnote-marker-forms]")
    with tempfile.TemporaryDirectory() as tmp:
        udir = os.path.join(tmp, "dot_bare", "unified")
        os.makedirs(udir)
        def _fu(uid, ty, text, y):
            return {"uid": uid, "type": ty, "main_text": text,
                    "box": [10, y, 90, y + 24], "cross_page": {},
                    "confidence": {"level": "high"}, "review": False, "readings": [],
                    "members": {"dots": [{"text": text, "box": [10, y, 90, y + 24]}]}}
        units = [
            _fu("u0_1", "Footnote", u"2 이뭇별, 「글로컬」, 2020.\n3 문승렬, 「장수」, 2018.", 100),
            _fu("u0_2", "Footnote", u"1. Ahn Byung-ook, “4 wol”, 1960.\n2. Yi Gap-yun, “Je 2”, 1961.", 200),
            _fu("u0_3", "Page-footer", u"김은혜, 앞의 논문, 24쪽 참조.", 300),
            _fu("u0_4", "Page-footer", u"www.kci.go.kr", 330),
        ]
        units[2]["members"]["dots"][0]["text"] = u"17 김은혜, 앞의 논문, 24쪽 참조."
        io.open(os.path.join(udir, "page_0.json"), "w", encoding="utf-8").write(
            json.dumps({"doc": "dot_bare", "page": 1, "w": 100, "h": 1000,
                        "in_ref": False, "unified": units}, ensure_ascii=False))
        got = frefs_doc("dot_bare", tmp)
        nums = [(r["num"], r["uid"]) for r in got["footnote_refs"]]
        raws = [r["raw"] for r in got["footnote_refs"]]
        if nums == [("1", "u0_2"), ("2", "u0_1"), ("2", "u0_2"), ("3", "u0_1"), ("17", "u0_3")] \
                and "www.kci" not in " ".join(raws) \
                and all("김은혜" not in r or r.startswith("김은혜") for r in raws):
            n_ok += 1
        else:
            print("FAIL [fnote-dot-bare-footer] %s" % (nums,))
    print("selftest: %d/%d PASS" % (n_ok, len(SELFTEST) + 6))
    return n_ok == len(SELFTEST) + 6


def _split_authors(auth, corp=False, latcorp=False):
    """L2 作者切分(韩语特性): ·/․ 与逗号分层, 空白只在无分隔符时用; 英文 首字母+姓 配对
    ("Y. Kwak" 不拆); and/& 连接拆分; 机构/出版社段不入作者(多段时, 人名锚 김철수(…) 除外);
    缺开引号的书名残留(段内含 ” 而无 “)截到引号前; 剥职称词表(교수/연구원/정회원/우수회원…);
    括号内罗马拼音是作者名硬锚, 保留。"""
    if not auth:
        return []
    a = _norm_ws(auth).strip().strip(",․·. ")
    a = re.sub(u"\\s+(편|편집)$", "", a).strip()   # A1: 编辑后缀 '편(编)' 非作者(춘천공립보통학교 편 实证)
    if not a:
        return []
    org = re.compile(r"(Co\.?|Ltd\.?|LLC|INC|사|출판|Press|Publishing|Publisher|University|"
                     r"학\s*회|회\s*지|연구\s*소|연구\s*원|학\s*교|공\s*사|청$|부$|처$|위원회|박물관)$")
    comma_mode = False
    if "·" in a or "․" in a:
        parts = [p.strip() for p in re.split(r"[·․]", a) if p.strip()]
        sep_mode = True   # ·路径: 机构剔除关闭(韩文·=多机构连接符, 문화재청․강원도․춘천시․강원문화재연구소 4机构全保留实证)
    elif "," in a:
        comma_mode = True
        parts = [p.strip() for p in a.split(",") if p.strip()]
        # 英文 'Surname, X. X.' 合并为单作者(韩式逗号分隔的全名不受影响)
        _m2 = []
        i = 0
        while i < len(parts):
            if i + 1 < len(parts) and re.match(r"^[A-Z][A-Za-z\-']+$", parts[i]) \
                    and re.match(r"^([A-Z]\.?\s*){1,3}$", parts[i + 1]):
                _m2.append(parts[i] + " " + parts[i + 1])
                i += 2
                continue
            _m2.append(parts[i])
            i += 1
        parts = _m2
        sep_mode = True
    else:
        if corp:
            parts, sep_mode = [a], True   # 机构作者(公告型): 无分隔符整段保留, 免空白切拆散 'Ministry of Education'
        else:
            parts = a.split()
            sep_mode = False   # 空白切分: 不套机构剔除, 免误杀 '충북대 중원문화연구소'
            if len(parts) > 1 and (
                (not re.search(r"[A-Za-z]", a) and (AFFIL_HEAD.search(a) or org.search(parts[-1])))
                or (latcorp and " " in a and re.search(
                    r"University|Institute|Association|Ministry|Department|Committee|Center|Centre|"
                    r"Corporation|Foundation|Council|Agency|National|Bureau", a))
            ):
                parts = [a]   # A3: 机构名单一作者——韩文看 AFFIL/org 尾; 拉丁机构词仅在 tailflow(frefs)开,
                # 保 references[] 冻结基线零影响(충북대 중원문화연구소/육군사관학교 육군박물관/국방부 군사편찬연구소/
                #  National Institute of Mental Health/American Psychiatric Association 实证)
    expanded = []
    for p in parts:
        p = re.sub(r"^(?:and|&|및)\s+", "", p.strip())
        m = re.match(r"^(.+?)\s+(?:and|&)\s+(.+)$", p)
        if m and len(m.group(1)) > 1:
            expanded.extend([m.group(1).strip(), m.group(2).strip()])
        else:
            expanded.append(p)
    merged = []
    i = 0
    while i < len(expanded):
        p = expanded[i]
        if i + 1 < len(expanded):
            q = expanded[i + 1]
            if re.match(r"^[A-Z][A-Za-z\-']+$", p) and re.match(r"^([A-Z]\.?\s*){1,3}$", q):
                # 1.1: 姓后连续缩写 token 一并吞(Spielberger C. D. 三碎片实证; 原相邻合并只吃一个)
                j = i + 1
                buf = [p, q]
                while j + 1 < len(expanded) and re.match(r"^([A-Z]\.?\s*){1,3}$", expanded[j + 1]):
                    j += 1
                    buf.append(expanded[j])
                merged.append(" ".join(buf))
                i = j + 1
                continue
            if re.match(r"^([A-Z]\.?\s*){1,3}$", p) and re.match(r"^[A-Z][A-Za-z\-']+$", q):
                merged.append(p + " " + q)
                i += 2
                continue
        merged.append(p)
        i += 1
    out = []
    for p in merged:
        if p in ("and", "&", u"및"):
            continue
        p = HONORIFIC.sub("", p).strip(" ,․·.")
        if re.fullmatch(r"etc\.?|et\s+al\.?", p, re.I):
            continue
        if re.fullmatch(u"외", p):
            continue   # 1.1: 韩文省略标记(=et al.)不入作者数组, et_al 旗标由 _extract_fields 置
        if re.fullmatch(u"편|편집|편저", p):
            continue
        if "”" in p and "“" not in p:
            p = p.split("”")[0].strip(" ,․·.")
            if not p:
                continue
        if not p:
            continue
        if comma_mode and len(merged) > 1 and org.search(p) and not re.search(u"[가-힣]{2,4}\\(", p):
            continue   # 机构剔除只留英文逗号路径(出版社段); ·路径多机构清单不剔除(A2)
        out.append(p)
    return out

def _head_tail(s, n=12):
    """片段截取: 首尾各 n 字, 中间省略(够对比即可)。"""
    s = (s or "").strip()
    if len(s) <= 2 * n:
        return s
    return s[:n] + u"…" + s[-n:]


def _diff_core(a, b, cap=12):
    """去公共前后缀后的差异核(各截 cap 字, 空核以 ∅ 显式标出)——指出差异本身, 如 主“’”/读法“∅”。"""
    a = a or ""
    b = b or ""
    p = 0
    for x, y in zip(a, b):
        if x != y:
            break
        p += 1
    s = 0
    lim = min(len(a), len(b)) - p
    while s < lim and a[len(a) - 1 - s] == b[len(b) - 1 - s]:
        s += 1
    ca = a[p:len(a) - s if s else len(a)]
    cb = b[p:len(b) - s if s else len(b)]
    fa = ca[:cap] + (u"…" if len(ca) > cap else "")
    fb = cb[:cap] + (u"…" if len(cb) > cap else "")
    return fa or u"∅", fb or u"∅"


def _win_snip(t, pos, win):
    """原文窗口 [pos-win, pos+win] 去空白; 位置无效时退回首部窗口。"""
    if not t:
        return None
    if pos is None or pos < 0:
        s = t[:win * 2].strip()
        return s or None
    s = t[max(0, pos - win):pos + win].strip()
    return s or None


def _near_snip(t, anchor, win=16):
    """在读法文本中截取 anchor 对应位置附近的片段: 原文直找 -> 折叠定位回映原文 -> 首部窗口。"""
    if not t or not anchor:
        return _win_snip(t, None, win)
    pos = t.find(anchor)
    if pos < 0:
        fa, ft = _fold(anchor)[:12], _fold(t)
        k = ft.find(fa) if fa else -1
        if k >= 0:
            cnt, pos = 0, 0
            for i, c in enumerate(t):
                if not c.isspace():
                    cnt += 1
                if cnt > k:
                    pos = i
                    break
            else:
                pos = len(t) - 1
    return _win_snip(t, pos, win)


def _year_snip(y, t):
    """年份变体: 直找该年; 找不到时取读法中出现的年份候选附近(给 reviewer 看到竞争读法)。"""
    pos = t.find(y) if y else -1
    if pos < 0:
        m = YEAR_RE.search(t or "")
        if m:
            pos = m.start()
    return _win_snip(t, pos, 14)


def _title_snip(title, t):
    """题名变体: 优先取读法中的引号题名段(与抽取同口径 QUOTE_TITLE), 无引号对时同位置 40 字窗口。"""
    m = QUOTE_TITLE.search(t or "")
    if m and m.group(1).strip():
        s = m.group(1).strip()
        return s[:80] + (u"…" if len(s) > 80 else "")
    return _near_snip(t, title, win=20)


def _field_agree(entry_fields, block_u, probe_suspect=False):
    """L2-lite 三渠道互证: 年/题名/首作者 在各家读法中的出现/一致性。
    返回 {field: {"agree": 原小数, "missing": [未命中渠道...], "variant": {ch: 差异片段}}}——
    agree 数值口径不变(只增证据, link-not-copy 不回贴全文); variant 截取该渠道读法中
    与主字段对应位置附近的片段(title 用 QUOTE_TITLE 或同位置 40 字窗口), 够 reviewer 核对。
    批次3 probe_suspect(§45 M3 计数制): 同 uid 块切出≥3条时各渠道 readings 只覆盖块片段
    (实测截断), 探针结构性失真——year/title 假象命中/agree 恒 1.0, authors 假象 miss/0.0,
    全字段标 "probe_suspect": True 供 _grade_entry 排除出分级(证据仍留 audit)。"""
    agree = {}
    readings = [r for r in block_u["readings"] if (r.get("text") or "").strip()]
    n = max(1, len(readings))

    def _probe(pred, snip):
        hits, missing, variant = 0, [], {}
        for r in readings:
            t = r.get("text") or ""
            if pred(t):
                hits += 1
            else:
                ch = r.get("ch") or "?"
                missing.append(ch)
                s = snip(t)
                if s:
                    variant[ch] = s
        return {"agree": hits / float(n), "missing": missing, "variant": variant}

    if entry_fields["year"]:
        y = entry_fields["year"]
        agree["year"] = _probe(lambda t: y in t, lambda t: _year_snip(y, t))
    # 互证匹配口径去引号('’‘“” 等): 单撇号差异(OCR 丢 ’/换成 ”)不算分歧(13625974 num9/23 伪 low 实证)
    _fq = lambda s: _foldv(re.sub(u"[\"'`‘’“”«»‹›『』「」]", "", s or ""))    # 批次3.5: 比较口径挂异体折叠
    _fq0 = lambda s: _fold(re.sub(u"[\"'`‘’“”«»‹›『』「」]", "", s or ""))   # 未折叠口径(fold_applied 留痕判据)
    if entry_fields["title"]:
        tf = _fq(entry_fields["title"])[:30]
        tf0 = _fq0(entry_fields["title"])[:30]
        info = _probe(lambda t: tf in _fq(t),
                      lambda t: _title_snip(entry_fields["title"], t))
        # 批次3.5 fold_applied 留痕: 存在"原始口径 miss 而折叠口径 hit"的渠道 = 该字段互证依赖异体折叠
        if any(tf0 not in _fq0(r.get("text") or "") and tf in _fq(r.get("text") or "")
               for r in readings):
            info["fold_applied"] = True
        agree["title"] = info
    if entry_fields["authors"]:
        head = _fq(entry_fields["authors"][0])[:6]
        head0 = _fq0(entry_fields["authors"][0])[:6]
        ainfo = _probe(lambda t: head in _fq(t) if head else False,
                       lambda t: _near_snip(t, entry_fields["authors"][0], win=14))
        if any(head0 not in _fq0(r.get("text") or "") and head in _fq(r.get("text") or "")
               for r in readings):
            ainfo["fold_applied"] = True
        agree["authors"] = ainfo
    if probe_suspect:
        for v in agree.values():
            v["probe_suspect"] = True
    return agree

def _grade_entry(f, agree, block_review=False, block_level="high", u0=None, issues=()):
    """置信分级(refs/frefs 共用, 口径一律不动): 块级继承 + 互证<0.67 + 校验 issues + 缺字段降档。
    返回 (level, basis_str, review_comment_str|None)。"""
    level = "high"
    basis = []
    review = []
    if block_review:
        level = "mid" if block_level != "low" else "low"
        basis.append("块级%s继承" % block_level)
        # review_comment ①: 块级依据透传(块级 confidence.basis + divergence.kind/detail),
        # 比 basis 的"块级mid继承"更可读——评审者据此知道该核对什么
        u0 = u0 or {}
        kind0 = "跨页拼接块" if (u0.get("cross_page") or {}).get("spans") else "复检块"
        bconf = u0.get("confidence") or {}
        dv = u0.get("divergence") or {}
        parts0 = []
        if bconf.get("basis"):
            parts0.append(bconf["basis"])
        if dv.get("kind"):
            parts0.append("%s: %s" % (dv["kind"], dv.get("detail") or ""))
        review.append("%s(块级依据: %s)" % (kind0, "; ".join(parts0)))
    sus = [k for k, v in agree.items() if isinstance(v, dict) and v.get("probe_suspect")]
    if sus:
        # 批次3: 块切分≥3条 → 探针结构性失真, 全部不参分级(标记在 ch_agree.probe_suspect)
        basis.append("探针存疑:%s(同块>=3条,互证不参分级)" % ",".join(sus))
    low_agree = [k for k, v in agree.items()
                 if k not in sus
                 and v.get("agree") < 0.67]
    if low_agree:
        level = "low"
        # basis 文案: "title 互证不足: mineru 读法缺失(变体已存 ch_agree.variant)"
        msgs = []
        for k in low_agree:
            info = agree.get(k) or {}
            miss = info.get("missing") or []
            if miss:
                msgs.append("%s 互证不足: %s 读法缺失(变体已存 ch_agree.variant)"
                            % (k, ",".join(miss)))
            else:
                msgs.append("%s 互证不足" % k)
        basis.append("; ".join(msgs))
        # review_comment ②: 字段级问题摘要(variant 首尾各 12 字 + 差异核, 指出差异本身)
        val_of = {"year": f["year"], "title": f["title"],
                  "authors": (f["authors"] or [""])[0]}
        for k in low_agree:
            info = agree.get(k) or {}
            miss = info.get("missing") or []
            if not miss:
                continue
            ch0 = miss[0]
            var = (info.get("variant") or {}).get(ch0) or ""
            if var:
                ca, cb = _diff_core(val_of.get(k) or "", var)
                review.append("%s: %s 读法片段“%s”差异核 主“%s”vs 读法“%s”"
                              % (k, ch0, _head_tail(var), ca, cb))
            else:
                review.append("%s: %s 读法未命中" % (k, ch0))
    if "作者缺" in issues or "年缺" in issues:
        level = "low" if level == "high" else level
        basis.append("作者/年缺")
    if any(k in issues for k in T_NULL_KEYS):
        level = "low"
        basis.append("题名被校验层拦下(%s)" % ("雷同" if "题名与来源雷同" in issues else
                                               "页码" if "题名页码" in issues else "碎片"))
    if "作者含纯数值" in issues:
        level = "low" if level == "high" else level
        basis.append("作者含纯数值")
    if not f["title"]:
        if level == "high":
            level = "mid"
        if "公告主题缺" in issues:
            level = "low"
        basis.append("题名未抽出(④LLM)")
    if "公告型著录" in issues and level == "high":
        level = "mid"
        basis.append("政府公告型著录(Notice No. 规则)")
    if issues:
        # review_comment ③: 校验层 issues 追加
        review.append("校验: %s" % ", ".join(issues))
    if block_review and not low_agree and not issues:
        review.append("字段一致")
    return level, "; ".join(basis) or "三渠道一致·块级无分歧", "；".join(review) if review else None


def _reanchor_reference_entries(entries, doc_dir, pdata):
    merged_fp = os.path.join(doc_dir, "merged.json")
    if not os.path.exists(merged_fp):
        return 0
    merged = json.load(io.open(merged_fp, encoding="utf-8"))
    changed = 0
    for e in entries:
        anchor = _foldv((e.get("text") or "")[:RAW_HEAD_LEN])
        if not anchor:
            continue
        hits = []
        for pg in merged.get("page_data") or []:
            for blocks in (pg.get("channels") or {}).values():
                for b in blocks:
                    if anchor in _foldv(b.get("text") or ""):
                        hits.append(pg.get("page"))
        hit_pages = sorted(set(h for h in hits if h is not None))
        if len(hit_pages) != 1 or hit_pages[0] == e.get("page"):
            continue
        target_page = hit_pages[0]
        page_data = pdata.get(target_page - 1) or {}
        candidates = []
        for u in page_data.get("unified") or []:
            texts = [u.get("main_text") or ""]
            for members in (u.get("members") or {}).values():
                texts.extend(m.get("text") or "" for m in members)
            if any(anchor in _foldv(t) for t in texts):
                candidates.append(u)
        if not candidates:
            continue
        best = min(candidates, key=lambda u: max(0, u["box"][2] - u["box"][0])
                   * max(0, u["box"][3] - u["box"][1]))
        e.update(page=target_page, uid=best["uid"], box=best["box"],
                 block_review=best.get("review"),
                 block_level=(best.get("confidence") or {}).get("level", "high"))
        changed += 1
    return changed


def refs_doc(doc, out_root):
    """单篇①: 文献区定位(页级 in_ref + 条目模式过滤) → 条目切分(跨块缝合) → 字段抽取 → 校验。
    产出 out 中文档目录下 metadata.json 的 references 段 + 校验摘要。"""
    doc_dir = os.path.join(out_root, doc)
    udir = os.path.join(doc_dir, "unified")
    if not os.path.isdir(udir):
        print("跳过 %s: 无 unified 产物" % doc)
        return None
    pages = sorted(int(re.match(r"page_(\d+)\.json$", f).group(1))
                   for f in os.listdir(udir) if re.match(r"page_\d+\.json$", f))
    pdata = {p: json.load(io.open(os.path.join(udir, "page_%d.json" % p), encoding="utf-8"))
             for p in pages}
    doc_heads = _doc_heads(pdata, pages)
    # —— L1: 文献区= in_ref 页(复用 regress.ref_region 口径); 起始页的 참고문헌 头定位 ——
    ref_pages = [p for p in pages if pdata[p].get("in_ref")]
    if not ref_pages:
        print("== ① %s == 无文献区" % doc)
        return None
    # unify in_ref 起点晚一页实证(1892171 p28: '참고문헌'+'I. 국남문헌' 头页 in_ref=False——unify.ref_region
    # 取'最后一个 REF_HEAD 命中页'为起点, 两小节头都含 '문헌' → 起点落到 'Ⅱ. 외국문헌' 页, 首页 7 条全丢):
    # refs 本地回退——in_ref 起点前一页有 REF_HEAD 节头 → 自该头页并入(只扩不缩, 不动 unify 标记, 免重跑 M3)
    p0 = min(ref_pages) - 1
    if p0 >= 0 and any(u["type"] == "Section-header"
                       and regress.REF_HEAD.search((u["main_text"] or "").split("\n")[0] or "")
                       for u in pdata[p0]["unified"]):
        ref_pages = sorted(set(ref_pages) | set(range(p0, max(ref_pages) + 1)))

    def _in_ref_region(u, p):
        """块属于文献区: 页 in_ref, 或跨页块(tail)的页首段落在 in_ref 页(M3 拼条目前溢的文献块)。"""
        if p in ref_pages:
            return True
        cp = u.get("cross_page") or {}
        return bool(cp.get("spans") and cp.get("role") == "tail"
                    and any((pg - 1) in ref_pages for pg in cp.get("pages", [])[1:]))

    head_pos = None   # (page, col, y1, y3) of 참고문헌 header
    for p in ref_pages:
        for u in pdata[p]["unified"]:
            if u["type"] == "Section-header" and regress.REF_HEAD.search(u["main_text"] or ""):
                head_pos = (p, _col_of(u, pdata[p]["w"]), u["box"][1], u["box"][3])
    # —— L2: 收集条目块(栏序), 定风格 ——
    blocks = []
    colb = {}
    for p in pages:
        j = pdata[p]
        for u in j["unified"]:
            if not _in_ref_region(u, p):
                continue
            if u.get("cross_page", {}).get("role") == "head":
                continue
            if u["type"] not in CONTENT_TYPES:
                continue
            t = (u["main_text"] or "").strip()
            if not t:
                continue
            t, _ = _clean_lines(t, (), doc_heads)   # 条目级再剔 running 行
            if not t or _is_running(t, doc_heads):
                continue
            if p not in colb:
                colb[p] = _page_col_boundary(j)
            blocks.append({"p": p, "col": _col_of_b(u, colb[p], j["w"]), "y": u["box"][1],
                           "uid": u["uid"], "u": u, "t": t})
    style = _detect_style([b["u"] for b in blocks])
    # —— L2: 块→条目(保持页/栏/纵序); 块级风格(bracketed>numbered>authoryear)防跨流污染 ——
    entries = []
    for b in sorted(blocks, key=lambda x: (x["p"], x["col"], x["u"]["box"][3])):  # 栏内按底边(M3跨页块并集框顶会跨页)
        bstyle = "bracketed" if re.search(r"(?m)^\s*\[\s*\d{1,3}\s*\]", b["t"]) else (
            "numbered" if re.search(r"(?m)^\s*\d{1,3}\s*[.、]\s", b["t"]) else "authoryear")
        pieces = _split_entries_block(b["t"], bstyle)
        glued = None
        if bstyle in ("numbered", "authoryear") and len(_fold(b["t"])) >= 200:
            g = _split_glued(b["t"])
            if g and len(g) > len(pieces):
                glued = g   # 批次2: 作者簇再切分仅在'锚数>已有段数'时启用(u12_14 13锚 vs 12段实证)
        for num, etext in ([(None, s) for s in glued] if glued else pieces):
            if len(_fold(etext)) < ENTRY_MIN_LEN:
                continue
            # 过滤在缝合后统一做(无头段可能是上一条的跨块续尾, 先给缝合机会)
            entries.append({"num": num, "text": etext, "page": b["p"] + 1, "col": b["col"],
                            "box": b["u"]["box"], "uid": b["uid"],
                            "block_review": b["u"]["review"],
                            "block_level": b["u"]["confidence"]["level"]})
    # —— L2: 跨块缝合(条目尾无终止标点且下一条非新头) ——
    merged = []
    for e in entries:
        if merged:
            prev = merged[-1]
            prev_open = not _complete(prev["text"])
            new_head = e["num"] is not None or (e["num"] is None and
                                                AY_HEAD.match(e["text"]))
            if prev_open and not new_head:
                prev["text"] += " " + e["text"].strip()
                continue
        merged.append(dict(e))
    entries = []
    for e in merged:
        if not _entry_like(e["text"], e["num"]):
            continue
        if e["num"] is None:
            # 无编号条目: 作者-年头起 + 带 引号/斜体题名/来源线索 之一(防正文句含年份混入)
            if not AY_HEAD.match(e["text"]):
                continue
            if not (QUOTE_TITLE.search(e["text"]) or ITALIC_TITLE.search(e["text"])
                    or re.search(r"pp?\.\s?\d|학\s*회|회\s*지|제\s*\d+\s*[권호]|출판사|University|Press|"
                                 r"Journal|학술|Inc\.|Book|[A-Za-z]{4,}", e["text"])):
                continue
        entries.append(e)
    # —— 批次2: num 恢复(895567 41 条全 null 实测)——numbered 且 num 过半为空 → 按序重编 1..N(验收: int 连续) ——
    if style == "numbered":
        n_none = sum(1 for e in entries if e["num"] is None)
        if entries and n_none * 2 > len(entries):
            for i, e in enumerate(entries, 1):
                e["num"] = i
    _reanchor_reference_entries(entries, doc_dir, pdata)
    # 批次3(§45 M3 计数制): 同 uid 条目数≥3 → 该块各渠道 readings 只覆盖片段, 探针标 probe_suspect 不参分级
    uid_count = collections.Counter(e["uid"] for e in entries)
    # —— L3: 字段抽取 + 置信 ——
    umap = {}
    for p in pages:
        for u in pdata[p]["unified"]:
            umap[u["uid"]] = u
    refs = []
    audits = []
    for e in entries:
        f = _extract_fields(e["text"], tailflow=_manual_like(e["text"]))
        # 校验独立层: 机械合法性(碎片题名/雷同/公告主题缺等), 错值抽出也会被拦下降级
        issues = _validate_fields(f)
        if any(k in issues for k in T_NULL_KEYS):
            f["title"] = None
        agree = _field_agree(f, umap[e["uid"]], probe_suspect=uid_count[e["uid"]] >= 3)
        level, basis_str, review_str = _grade_entry(
            f, agree, e["block_review"], e["block_level"], umap.get(e["uid"]), issues)
        refs.append({
            "num": e["num"], "style": style, "raw": e["text"], "page": e["page"],
            "box": e["box"], "uid": e["uid"],
            "authors": f["authors"], "authors_raw": f["authors_raw"], "et_al": f.get("et_al"),
            "year": f["year"], "title": f["title"], "source": f["source"],
            "volume": f["volume"], "issue": f["issue"], "pages": f["pages"],
            "doi": f["doi"], "lang": f["lang"], "notice_no": f.get("notice_no"),
            "grammar": f.get("grammar"),
            "layout": f.get("layout"), "container_title": f.get("container_title"),
            "publisher": f.get("publisher"), "editor": f.get("editor"),
            "confidence": {"level": level, "source": "regex"}})
        audits.append({"num": e["num"], "uid": e["uid"], "page": e["page"],
                       "block_review": e["block_review"],
                       "basis": basis_str,
                       "ch_agree": agree,
                       "pos_alarm": [i for i in issues if i in POS_ALARMS] or None,
                       "review_comment": review_str})
    # —— L1 机械校验: 编号连续/年份合法/页码起止 + 语法类分布(未知语法可见) ——
    val = {"year_bad": [], "pages_bad": [], "entry_count": len(refs),
           "grammar_dist": dict(collections.Counter(r.get("grammar") for r in refs)),
           "layout_dist": dict(collections.Counter(r.get("layout") for r in refs))}
    nums = [r["num"] for r in refs if r["num"] is not None]
    if nums:
        # 分子序列(小节头后重新计数, 如 1632041 國史/論文两段 1..13/1..14 各自连续即合法)
        runs, cur = [], []
        for n0 in nums:
            if cur and n0 <= cur[-1]:
                runs.append(cur)
                cur = []
            cur.append(n0)
        if cur:
            runs.append(cur)
        seqs = []
        for run in runs:
            seen, gaps, dups = set(), [], []
            for n0 in run:
                if n0 in seen:
                    dups.append(n0)
                seen.add(n0)
            for n0 in range(min(seen), max(seen) + 1):
                if n0 not in seen:
                    gaps.append(n0)
            seqs.append({"min": min(run), "max": max(run), "gaps": gaps,
                         "dups": sorted(set(dups)), "count": len(run)})
        val["num_sequence"] = {"runs": seqs, "ok": all(not s["gaps"] and not s["dups"] for s in seqs)}
    # 1.10: 编号缺失旗标(audit 层可见, 不降级)——bracketed/numbered 体例下 num=None 即 layout 分类器的输入信号
    if style in ("bracketed", "numbered"):
        val["num_missing"] = [r["uid"] for r in refs if r.get("num") is None]
    for r in refs:
        if r["year"] and int(r["year"][:4]) not in YEAR_OK:
            val["year_bad"].append(r["num"])
        if r["pages"]:
            a, _, b0 = r["pages"].partition("-")
            if b0 and int(a) >= int(b0):
                val["pages_bad"].append(r["num"])
    meta_fp = os.path.join(doc_dir, "metadata.json")
    meta = {}
    if os.path.exists(meta_fp):
        meta = json.load(io.open(meta_fp, encoding="utf-8"))
    # 遗留诊断键清理(§38 分层输出后产品文件不再承载这些键, 防旧产物残留)
    for k in ("ref_validation", "ref_style", "llm", "front_notes", "tail_english_matter"):
        meta.pop(k, None)
    meta.update({"doc": doc, "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                 "references": refs})
    io.open(meta_fp, "w", encoding="utf-8").write(json.dumps(meta, ensure_ascii=False, indent=1))
    # 诊断层双写: review_comment/ch_agree/basis/block_review/L1 校验/风格 → metadata_audit.json(评审工具读, 下游不消费)
    audit = load_audit(doc_dir)
    audit.update({"doc": doc, "references": audits,
                  "ref_validation": val, "ref_style": style})
    save_audit(doc_dir, audit)
    lv = collections.Counter(r["confidence"]["level"] for r in refs)
    print("== ① %s == 风格=%s 条目=%d 置信=%s 编号=%s" % (
        doc, style, len(refs), dict(lv),
        val.get("num_sequence", "无编号")))
    return meta


_TAIL_CITE = re.compile(u"\\(([^()]*(?:19|20)\\d{2}[a-z]?[^()]*)\\)\\s*[.。]?\\s*$", re.S)

def _unwrap_tail_cite(t):
    """句尾括注引文解包: '…하였다.(김택균(1983), 제목, 출처)' → 只取括内引文本体(1632041 #18 实证)。"""
    m = _TAIL_CITE.search(t or "")
    if m and len(_fold(t[:m.start()])) >= 8:   # 前确有散文才解包; 纯引文条目不套
        return m.group(1).strip()
    return t


FN_CITE_X = re.compile(
    r"^\s*((?:\(\d{1,2}\)|\d{1,2}\)|\[\d{1,3}\]|〔\d+〕|"
    r"\d{1,3}\s*[.、]|"                                  # N. / N、 句点式(FI001004818_2002 全篇实证)
    r"\d{1,2}(?=\s+(?:[^\W\d_]|[「『“‘]))))")             # 裸数字式(13563525 上标号 OCR 成行首裸数字);
# 裸数字分支要求数字后紧跟字母/开引号, 防 '18 • Police Science Institute'(•为符号)误切
# 1.6: frefs 局部扩展正则(补 (N) 圆括号形态, 1892171 脚注22/23 串烧实证)——
# 不动 common.FOOTNOTE_CITE(m3.py 三处共享, 动它=违反 D4 硬闸)

def _split_glued(t):
    r"""批次2: 整页粘连块作者簇切分(895567 u12_14 实测 13 年份锚 vs 12 旧条——+1 恰为串烧对)。
    锚=r',\s*(?:19|20)\d{2}[a-z]?(?=,)'; 边界=句点+(可选空白)+[A-Z][小写/韩文](裸缩写 'G. K., ' 不误切)。
    验收(§45): 切后过准入闸, num 另由 refs_doc 统一序编; 切不干净的段由校验层/人工兜。"""
    anchors = [m.start() for m in re.finditer(r",\s*(?:19|20)\d{2}[a-z]?(?=,)", t)]
    if len(anchors) < 2:
        return None
    bounds = sorted(set([0] + [m.start() for m in re.finditer(r"(?<=[.!?])\s*(?=[A-Z][a-z]|[가-힣])", t)]
                        + [len(t)]))
    cuts = [0]
    for a in anchors[1:]:
        cuts.append(max(c for c in bounds if c <= a))
    cuts.append(len(t))
    cuts = sorted(set(cuts))
    segs = [t[cuts[i]:cuts[i + 1]].strip() for i in range(len(cuts) - 1)]
    segs = [re.sub(r"\s+", " ", s) for s in segs if len(_fold(s)) >= ENTRY_MIN_LEN]
    return segs if len(segs) >= 2 else None


def _fnote_collect(blocks, doc_heads=()):
    """Footnote 块[(p,col,u,t)] → 候选条目[{num,text,page,col,box,uid,block_review,block_level}]:
    行首引文标记(FOOTNOTE_CITE)切条, 一块可多条; 无标记段落并入前一条(1632041 续注散文
    '조선시대에 이륟…' 实证), 跨块续并(条目归属头块 page/uid); 段首无条可并的散文丢弃。
    strict 块(Page-footer 型脚注回退)只收行首带标记的行, 不并散文——防页脚杂行(网址/日期)
    污染上一条。"""
    entries, cur = [], None
    for b in blocks:
        t, _ = _clean_lines(b["t"], (), doc_heads)
        if not t or _is_running(t, doc_heads):
            continue
        strict = bool(b.get("strict"))
        for ln in t.split("\n"):
            s = ln.strip()
            if not s:
                continue
            mh = FN_CITE_X.match(s)
            if mh:
                if cur:
                    entries.append(cur)
                cur = {"text": s[mh.end():].strip(), "num": re.sub(r"\D", "", mh.group(1)),
                       "page": b["p"] + 1, "col": b["col"], "box": b["u"]["box"],
                       "uid": b["u"]["uid"], "block_review": b["u"]["review"],
                       "block_level": b["u"]["confidence"]["level"]}
            else:
                if strict:
                    continue
                if cur is not None:
                    cur["text"] += " " + s
    if cur:
        entries.append(cur)
    return entries


_REPEAT_REF = re.compile(
    r"\bIbid\.?|\bsupra\b|위와 같은|위와 같음|위의 책|앞의 (?:책|논문|글)|"
    u"상게논문|상계논문|전계논문", re.I)
_REF_CONTEXT = re.compile(
    r"참조|참고|쪽|면|pp?\.\s*\d|발간|출판|옮김|집필|보고서|논문|저본|online|"
    r"Case|Award|Judgment|RIAA|note", re.I)
_AUTHOR_YEAR = re.compile(
    r"[^\W\d_]+\s*\(\s*(?:19|20)\d{2}|\(\s*[^\W\d_]+(?:\s+외)?\s+(?:19|20)\d{2}|"
    r"^[^\W\d_· ]+\s+(?:19|20)\d{2}(?::\d+)?$", re.U)


def _fref_has_biblio_evidence(t):
    """只决定是否抽取书目字段；所有编号脚注无论此结果都保留。"""
    plain = _plain_inline(t)
    if _REPEAT_REF.search(plain) or re.search(r"https?://|www\.", plain, re.I):
        return True
    if QUOTE_TITLE.search(plain) or ITALIC_TITLE.search(plain):
        return True
    if _CLASSICAL_TITLE.search(plain) and re.search(
            r"卷|권|제\s*\d|쪽|면|본기|本紀|列傳|志|장|章|節|절|pp?\.", plain, re.I):
        return True
    if re.search(r"\b(?:Case|Award|Judgment)\b", plain):
        return True
    has_y = any(int(m.group(1)) in YEAR_OK for m in YEAR_RE.finditer(plain))
    modern = has_y and (QUOTE_TITLE.search(plain) or ITALIC_TITLE.search(plain)
                        or re.search(r"pp?\.\s?\d|학\s*회|회\s*지|제\s*\d+\s*[권호집]|"
                                     u"출판사|University|Press|Journal|학술", plain, re.I))
    return bool(modern or (has_y and (_REF_CONTEXT.search(plain) or _AUTHOR_YEAR.search(plain)
                                     or re.search(r"(?:19|20)\d{2}\s*:\s*\d+", plain)))
                or (re.search(u"참조|참고", plain) and re.search(
                    r"제\s*\d+\s*(?:장|절)|쪽|면|pp?\.\s*\d", plain, re.I)))


def _repeat_author_match(a, b):
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    if min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    if len(a) > len(b):
        a, b = b, a
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


def _resolve_repeat_reference(t, f, previous):
    plain = _plain_inline(t)
    if not _REPEAT_REF.search(plain) or not previous:
        return
    target = None
    note = re.search(r"(?:note|주)\s*(\d+)", plain, re.I)
    if note:
        target = next((r for r in reversed(previous) if str(r.get("num")) == note.group(1)), None)
    if target is None and re.search(r"\bIbid\.?|위와 같은|위와 같음|위의 책|상게논문|상계논문", plain, re.I):
        target = previous[-1]
    if target is None:
        auth0 = re.split(u"앞의|전계논문|supra", plain, maxsplit=1, flags=re.I)[0].strip(" ,.，")
        auth_fold = _foldv(auth0)
        for candidate in reversed(previous):
            prior = _foldv(candidate.get("authors_raw") or "")
            if _repeat_author_match(auth_fold, prior):
                target = candidate
                break
    if target is None:
        return
    for key in ("authors", "authors_raw", "et_al", "year", "title", "source", "volume",
                "issue", "doi", "layout", "container_title", "publisher", "editor"):
        value = target.get(key)
        if isinstance(value, list):
            value = list(value)
        if value is not None:
            f[key] = value


def frefs_doc(doc, out_root):
    """单篇⑤: 全部编号脚注抽取(--stage frefs)。产物独立键 footnote_refs + footnote_ref_validation,
    不混 references[]; 只依赖 unified, 与 refs 阶段顺序无关。流水: 非 in_ref 页 Footnote 块
    → 行首标记切条(无标记段并入前条) → 全部保留(文献/说明/术语/图源/访问日期)
    → 书目字段抽取+重复引文解析+校验+置信(_extract_fields/_validate_fields/_grade_entry 复用)。
    编号=脚注号(每页重启, 无连续性校验意义), 校验只记 per-page 计数。"""
    doc_dir = os.path.join(out_root, doc)
    udir = os.path.join(doc_dir, "unified")
    if not os.path.isdir(udir):
        print("跳过 %s: 无 unified 产物" % doc)
        return None
    pages = sorted(int(re.match(r"page_(\d+)\.json$", f).group(1))
                   for f in os.listdir(udir) if re.match(r"page_\d+\.json$", f))
    pdata = {p: json.load(io.open(os.path.join(udir, "page_%d.json" % p), encoding="utf-8"))
             for p in pages}
    doc_heads = _doc_heads(pdata, pages)
    ref_pages = set(p for p in pages if pdata[p].get("in_ref"))
    blocks, colb = [], {}

    def _footer_marked_text(u):
        """Page-footer 型脚注回退: 渠道把部分页底脚注(上标号)判为 Page-footer(13563525 u8_8
        '17 김은혜…' 实证, 主读法丢号但 dots 成员带号)。主读法有行首标记用主读法; 否则用
        第一个带行首标记的成员读法。无标记返回 None(普通页脚不收)。"""
        t = (u.get("main_text") or "")
        if any(FN_CITE_X.match(ln.strip()) for ln in t.split("\n") if ln.strip()):
            return t.strip()
        for members in (u.get("members") or {}).values():
            for m in members:
                mt = (m.get("text") or "")
                if any(FN_CITE_X.match(ln.strip()) for ln in mt.split("\n") if ln.strip()):
                    return mt.strip()
        return None

    for p in pages:
        if p in ref_pages:
            continue   # 收集范围=非 in_ref 页
        j = pdata[p]
        for u in j["unified"]:
            if u.get("cross_page", {}).get("role") == "head":
                continue
            t = (u["main_text"] or "").strip()
            if u["type"] != "Footnote":
                if u["type"] == "Page-footer":
                    ft = _footer_marked_text(u)
                    if ft:
                        if p not in colb:
                            colb[p] = _page_col_boundary(j)
                        blocks.append({"p": p, "col": _col_of_b(u, colb[p], j["w"]),
                                       "uid": u["uid"], "u": u, "t": ft, "strict": True})
                continue
            if not t:
                continue
            if p not in colb:
                colb[p] = _page_col_boundary(j)
            blocks.append({"p": p, "col": _col_of_b(u, colb[p], j["w"]),
                           "uid": u["uid"], "u": u, "t": t})
    entries = _fnote_collect(sorted(blocks, key=lambda x: (x["p"], x["col"], x["u"]["box"][3])),
                             doc_heads)
    kept = []
    for e in entries:
        e["text"] = _unwrap_tail_cite(e["text"])
        kept.append(e)
    kept.sort(key=lambda e: (e["page"], int(e["num"]) if str(e["num"]).isdigit() else 0))
    # 批次3: frefs 同 uid 计数(§45 M3 '3:三块脚注' 同口径)——≥3 条探针标 probe_suspect
    uid_count = collections.Counter(e["uid"] for e in kept)
    umap = {}
    for p in pages:
        for u in pdata[p]["unified"]:
            umap[u["uid"]] = u
    frefs, faudits = [], []
    for e in kept:
        if _fref_has_biblio_evidence(e["text"]):
            f = _extract_fields(e["text"], tailflow=True)
        else:
            f = {"authors": [], "authors_raw": None, "et_al": False, "year": None,
                 "title": None, "source": None, "volume": None, "issue": None,
                 "pages": None, "doi": None, "lang": None, "notice_no": None,
                 "grammar": None, "layout": None, "container_title": None,
                 "publisher": None, "editor": None}
        _resolve_repeat_reference(e["text"], f, frefs)
        issues = _validate_fields(f)
        if any(k in issues for k in T_NULL_KEYS):
            f["title"] = None
        agree = _field_agree(f, umap.get(e["uid"]) or {"readings": []},
                             probe_suspect=uid_count[e["uid"]] >= 3)
        level, basis_str, review_str = _grade_entry(
            f, agree, e["block_review"], e["block_level"], umap.get(e["uid"]), issues)
        frefs.append({
            "num": e["num"], "raw": e["text"], "page": e["page"],
            "box": e["box"], "uid": e["uid"],
            "authors": f["authors"], "authors_raw": f["authors_raw"], "et_al": f.get("et_al"),
            "year": f["year"], "title": f["title"], "source": f["source"],
            "volume": f["volume"], "issue": f["issue"], "pages": f["pages"],
            "doi": f["doi"], "lang": f["lang"], "notice_no": f.get("notice_no"),
            "grammar": f.get("grammar"),
            "layout": f.get("layout"), "container_title": f.get("container_title"),
            "publisher": f.get("publisher"), "editor": f.get("editor"),
            "confidence": {"level": level, "source": "regex"}})
        faudits.append({"num": e["num"], "uid": e["uid"], "page": e["page"],
                        "block_review": e["block_review"], "basis": basis_str,
                        "ch_agree": agree,
                        "pos_alarm": [i for i in issues if i in POS_ALARMS] or None,
                        "review_comment": review_str})
    fval = {"entry_count": len(frefs),
            "per_page": dict(collections.Counter(e["page"] for e in frefs))}
    # 1.11: 对账诊断——每页 脚注号全集(markers) vs 已收(collected), 缺口=未收(附录B reconciliation 原料)
    mk_pages = collections.defaultdict(list)
    for b in blocks:
        for mm in re.finditer(r"(?m)^\s*(?:\((\d{1,2})\)|(\d{1,2})\)|\[(\d{1,3})\]|〔(\d+)〕)\s", b["t"]):
            mk_pages[b["p"] + 1].append(next(g for g in mm.groups() if g))
    coll_pages = collections.defaultdict(list)
    for e in frefs:
        coll_pages[e["page"]].append(str(e["num"]))

    def _srt(ns):
        return sorted(set(ns), key=lambda x: int(x) if x.isdigit() else 0)

    fval["per_page_detail"] = {str(p): {"markers": _srt(mk_pages.get(p, [])),
                                        "collected": _srt(coll_pages.get(p, []))}
                               for p in sorted(set(mk_pages) | set(coll_pages))}
    meta_fp = os.path.join(doc_dir, "metadata.json")
    meta = json.load(io.open(meta_fp, encoding="utf-8")) if os.path.exists(meta_fp) else {}
    meta.update({"doc": doc, "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                 "footnote_refs": frefs, "footnote_ref_validation": fval})
    io.open(meta_fp, "w", encoding="utf-8").write(json.dumps(meta, ensure_ascii=False, indent=1))
    audit = load_audit(doc_dir)
    audit.update({"doc": doc, "footnote_refs": faudits})
    save_audit(doc_dir, audit)
    lv = collections.Counter(r["confidence"]["level"] for r in frefs)
    print("== ⑤ %s == 编号脚注=%d 置信=%s 分页=%s" % (doc, len(frefs), dict(lv), fval["per_page"]))
    return meta


if __name__ == "__main__":
    raise SystemExit(0 if selftest() else 1)
