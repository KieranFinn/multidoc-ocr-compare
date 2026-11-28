import argparse
import collections
import difflib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PIPE = os.path.dirname(HERE)
sys.path.insert(0, PIPE)
from metapy import front
from fingerprint import clean, is_abstract, is_keywords, load, margin, title_candidate

OLD = os.path.join(PIPE, "out")
NEW = os.path.join(PIPE, "out_layout")


def fold(s):
    return re.sub(r"\s+", "", s or "")


# 页眉/页脚/页码/脚注均不是署名区；front 的 margin() 漏掉了 Footnote。
MARGIN_TYPES = ("Page-header", "Page-footer", "Page-number", "Footnote")
# 星标/罗马名/角色/机构/分栏竖线：署名行独有的结构证据。
AUTHOR_STRUCT = re.compile(
    r"[*†‡¹²³⁴⁵⁶⁷⁸⁹<]|\([A-Za-z]|주\s*저자|교신\s*저자|제\s*\d+\s*저자|공동저자"
    r"|대학교|대학원|연구소|연구원|학과|학부|센터|협회|[|｜┃]")


def is_margin_block(u):
    return (u or {}).get("type") in MARGIN_TYPES


def author_zone_ok(u, title, p0, line):
    """通用版面约束：非署名区直接排除；无结构证据的孤立短文本必须紧邻标题。

    正文/脚注区的短文本（例如 OCR 把节标题'초록'读成'조록'）没有星标、罗马名、
    角色或机构配对，且远离标题框，因此不能进入作者候选。
    """
    if is_margin_block(u):
        return False
    y = (u.get("box") or [0, 0, 0, 0])[1]
    if AUTHOR_STRUCT.search(clean(line)):
        return y <= .68 * p0["h"]
    bottom = title["box"][3] if title else 0
    return y <= min(bottom + .18 * p0["h"], .45 * p0["h"])


def heading_echo(u, p0):
    """同一水平位置上的另一个短节标题（如 '조록'/'초록' 并列）说明这是标题而非署名。"""
    if (u.get("type") or "") != "Section-header" or len(fold(u.get("main_text"))) > 6:
        return False
    x1, y1, x2, _ = u.get("box") or [0, 0, 0, 0]
    for o in p0["unified"]:
        if o is u or (o.get("type") or "") != "Section-header" or len(fold(o.get("main_text"))) > 6:
            continue
        ox1, oy1, ox2, _ = o.get("box") or [0, 0, 0, 0]
        if abs(oy1 - y1) < 10 and ox1 < x2 and x1 < ox2:
            return True
    return False


def valid_author_name(name):
    name = (name or "").strip()
    if len(name or "") > 4 and (name or "").endswith("대"):
        return False
    if re.fullmatch(r"(?:주\s*저자|교신\s*저자|제\s*\d+\s*저자|공동저자|저자|교수|강사)", name or ""):
        return False
    if not name or len(name) > 45 or "@" in name or "$" in name or "\\" in name or re.search(r"\d", name):
        return False
    if re.search(r"요약|초록|차례|목차|키워드|주제어|핵심어|중심어|참고문헌|접수일|수정일|게재확정일|분량\s*:|^(?:한국|러시아|중국|일본)$|참모처|과장$|정비본부$|정비과정$", name, re.I):
        return False
    if name.startswith(("#", "❚", "■")) or re.match(r"^[a-z]", name):
        return False
    return True


def byline_structure(line):
    s = clean(re.sub(r"<[^>]+>", "", line or "")).strip(" |｜┃")
    s = re.sub(r"[\w.%+-]+@[\w.-]+(?:\.[A-Za-z]{2,})?", "", s).strip()
    if len(s) > 120:
        return False
    if not s or is_abstract(s) or is_keywords(s) or re.match(r"^[#❚■✚+●○•▪※\s]*(?:차\s*례|목\s*차|초록|요약|서론|결론|맺음말)\b", s):
        return False
    if re.search(r"(?:[가-힣]\s*){2,4}[*†‡¹²³⁴⁵⁶⁷⁸⁹]+", s):
        return True
    if re.search(r"[一-鿿]{2,4}\s*[*†‡¹²³⁴⁵⁶⁷⁸⁹]+", s):
        return True
    if re.match(r"^[A-Z][A-Za-z'-]+\s+[A-Z][A-Za-z'-]+\s+[가-힣]", s):
        return True
    if re.search(r"[가-힣]{2,4}\s+(?:주\s*저자|교신\s*저자|제\s*\d+\s*저자|공동저자)", s):
        return True
    if re.search(r"[가-힣]\s*[*†‡¹²³⁴⁵⁶⁷⁸⁹]+[·•⋅\s]*[가-힣]", s):
        return True
    if re.fullmatch(r"[가-힣]{2,4}\s+[가-힣]{1,4}\s+\S*(?:대학교|대학|연구원|연구소|학과|학부)", s):
        return True
    if re.fullmatch(r"[가-힣]{2,4}\s+[가-힣]{2,4}\s*[*†‡¹²³⁴⁵⁶⁷⁸⁹]+", s):
        return True
    if len(re.findall(r"[가-힣]{2,4}\d*\([^)]*(?:대학|연구|학과)[^)]*\)", s)) >= 2:
        return True
    if re.fullmatch(r"[가-힣]{2,4}\s*/\s*[가-힣]{2,4}", s):
        return True
    if re.search(r"[가-힣]{2,4}\((?=[^)]*(?:[a-z]|[A-Z]\s+[A-Z]))[A-Za-z][^)]*\)", s):
        return True
    if re.fullmatch(r"[가-힣]{2,4}(?:\s*[·•,，]\s*[가-힣]{2,4})*[*†‡¹²³⁴⁵⁶⁷⁸⁹]+", s):
        return True
    if re.search(r"[가-힣]{2,4}\s*,\s*(?:주\s*저자|제\s*\d+\s*저자|교신\s*저자|공동저자)", s):
        return True
    if re.fullmatch(r"[가-힣]{2,4}\s+\S*(?:대학교|대학|연구원|연구소|학과|학부|센터|협회|부)(?:\s+\S+)?(?:-wise)?", s):
        return True
    if re.fullmatch(r"(?:[가-힣]\s*){2,4}[*†‡¹²³⁴⁵⁶⁷⁸⁹]+", s):
        return True
    if re.fullmatch(r"[가-힣]{2,4}\s*[·•,，]\s*[가-힣]{2,4}(?:\s*[·•,，]\s*[가-힣]{2,4})*", s):
        return True
    if re.fullmatch(r"[가-힣]{2,4}", s):
        return True
    if re.search(r"(?:[가-힣]\s*){2,4}\$\s*\^\s*\{", s):
        return True
    if re.search(r"스님\([가-힣]{2,4}\)", s):
        return True
    return False


def nonauthor_context(line, title):
    plain = re.sub(r"[^가-힣A-Za-z]", "", line or "").lower()
    title_fold = fold(strip_title((title or {}).get("main_text"))).lower()
    if plain and title_fold and plain in title_fold:
        return True
    return bool(re.match(r"^[#❚■✚+●○•▪※\s]*(?:\d+|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+|I|V|X)[.)]?\s*", line or ""))


def value(text, u=None, page=1):
    return {"value": text, "page": page if u else None, "box": u.get("box") if u else None,
            "uid": u.get("uid") if u else None, "confidence": {"level": "mid" if text else "low", "source": "layoutx"}}


# 题名尾部 '이름<上标>, 이름<上标>' 署名：逗号分隔姓名 + 可选星标/罗马名。
_TRAILING_NAMES = re.compile(
    r"(?P<byline>\s*(?:[가-힣]{2,4}(?:\s*<sup>[^<]*</sup>|\s*[*†‡¹²³⁴⁵⁶⁷⁸⁹]+)?"
    r"(?:\s*\([A-Za-z][^)]*\))?\s*[,，]\s*)+"
    r"[가-힣]{2,4}(?:\s*<sup>[^<]*</sup>|\s*[*†‡¹²³⁴⁵⁶⁷⁸⁹]+)?"
    r"(?:\s*\([A-Za-z][^)]*\))?)\s*$")


def split_trailing_byline(text):
    """把粘在题名尾部的署名片段切出来，返回 (净题名, 署名片段或 None)。"""
    text = text or ""
    m = _TRAILING_NAMES.search(text)
    if not m:
        return text, None
    head = text[:m.start()].rstrip(" ,，")
    byline = m.group("byline").strip(" ,，")
    if len(fold(head)) < 6 or len(re.findall(r"[가-힣]{2,4}", byline)) < 2:
        return text, None
    # 只有带星标/罗马名才算署名；否则是题名里的并列术语（'발단, 전개, 위기 …'）。
    if not re.search(r"<sup>[^<]*[*†‡¹²³⁴⁵⁶⁷⁸⁹]|(?<![가-힣])[*†‡¹²³⁴⁵⁶⁷⁸⁹]+|\([A-Za-z]", byline):
        return text, None
    return head, byline


def authors_from_title_byline(title_u, p0):
    """从题名候选的原始文本尾部恢复被粘入题名的署名（通用规则：逗号+星标+罗马名）。"""
    if not title_u:
        return []
    _, byline = split_trailing_byline(title_u.get("main_text") or "")
    if not byline:
        return []
    out = []
    for seg in re.split(r"\s*[,，]\s*", byline):
        m = re.match(r"\s*([가-힣]{2,4})"
                     r"(?:\s*(?:<sup>\s*(?P<sup>[*†‡¹²³⁴⁵⁶⁷⁸⁹]+)\s*</sup>|(?P<star>[*†‡¹²³⁴⁵⁶⁷⁸⁹]+)))?"
                     r"(?:\s*\((?P<roman>[A-Za-z][^)]*)\))?", seg)
        if not m or not valid_author_name(m.group(1)):
            continue
        marks = m.group("sup") or m.group("star") or ""
        out.append({"name": m.group(1), "markers": list(marks), "affiliation": None, "role": None,
                    "roman": m.group("roman"), "uid": title_u.get("uid"), "box": title_u.get("box"),
                    "page": 1, "source": "layoutx_title_byline", "_source_line": byline,
                    "_source_type": "Text"})
    return out


def transliterated_authors(line):
    """'音译名 音译名 $ ^{*} $' 类署名：按空格切为两名，避免被 front 粘成错名。"""
    m = re.fullmatch(r"\s*([가-힣]{2,4})\s+([가-힣]{2,4})\s+(.+?)\s*", line or "")
    if not m:
        return None
    tail = m.group(3)
    if "*" not in tail or not re.fullmatch(r"(?:\$|\^|\{|\}|\*|\d|\s|\(|\)|\+)+", tail):
        return None
    markers = ["*"] * len(re.findall(r"\*", tail))
    return [{"name": m.group(1), "markers": markers, "affiliation": None, "role": None, "roman": None},
            {"name": m.group(2), "markers": markers, "affiliation": None, "role": None, "roman": None}]


def aff_support(text, p0, doc):
    """机构写法得到几个独立 OCR 渠道支持（每个渠道一票），用于异读折叠选正确读法。"""
    key = fold(text)
    path = os.path.join(OLD, doc, "merged", "page_0.json")
    if not os.path.exists(path):
        return 0
    channels = load(path).get("channels") or {}
    return sum(any(key in fold(b.get("text")) for b in channels.get(ch) or [])
               for ch in ("dots", "paddle", "mineru"))


def strip_title(text):
    raw_lines = (text or "").splitlines()
    if len(raw_lines) > 1 and re.search(r"[가-힣]", raw_lines[0]):
        selected = []
        for line in raw_lines:
            letters = len(re.findall(r"[A-Za-z]", line))
            hangul = len(re.findall(r"[가-힣]", line))
            if selected and (re.search(r"[|｜┃]|@|[가-힣]{2,4}\([A-Za-z]|주\s*저자|교신\s*저자|제\s*\d+\s*저자", line) or re.fullmatch(r"[가-힣\s]{2,20}[*†‡¹²³⁴⁵⁶⁷⁸⁹]+", line) or (letters > 12 and letters > hangul * 3)):
                break
            selected.append(line)
        raw_lines = selected
    # 题名与署名被 OCR 拆成两行时，尾部纯署名行也要从题名切走。
    joined = "\n".join(raw_lines)
    head, _ = split_trailing_byline(joined)
    if head != joined:
        raw_lines = head.splitlines()
    lines = []
    for line in raw_lines:
        line, _ = split_trailing_byline(line)
        # 序数上标（21<sup>st</sup>）是题名文字，不能随标签一起丢掉。
        line = re.sub(r"<sup>([^<]*)</sup>",
                      lambda m: m.group(1) if re.fullmatch(r"(?:st|nd|rd|th)", m.group(1), re.I) else "",
                      line, flags=re.I)
        line = re.sub(r"</?sup>", "", line, flags=re.I)
        line = re.sub(r"</?sub>", "", line, flags=re.I)
        line = re.sub(r"(?:\bISSN\s*[\dXx-]+\s*\(?(?:Print|Online)?\)?\s*)+", "", line, flags=re.I)
        line = re.sub(r"\s*\*{1,4}\s*$", "", line)
        if line.strip():
            lines.append(line.strip())
    text = clean(" ".join(lines)).strip(" *")
    text = re.sub(r"\s+[가-힣]{2,4}\s+\S*(?:대학교|대학|연구소|연구원|학과|학부)$", "", text)
    text = re.sub(r"\s+[가-힣]{2,4}\s*(?:<sup>[^<]*</sup>)?[*†‡¹²³⁴⁵⁶⁷⁸⁹]+\s*,.*$", "", text)
    text = re.sub(r"\s+[가-힣]{2,4}\s*<sup>.*$", "", text)
    text = re.sub(r"\s+[가-힣]{2,4}\*{1,4}\s+\S*대학교.*$", "", text)
    if re.search(r"[가-힣]", text):
        for m in re.finditer(r"\b[A-Z][A-Za-z]+\b", text):
            suffix = text[m.start():]
            if len(suffix.split()) >= 4 and not re.search(r"[가-힣]", suffix) and len(re.findall(r"[A-Za-z]", suffix)) > 20:
                text = text[:m.start()].rstrip(" :-–—")
                break
    return text


def channel_title(doc, title_u, current):
    if not title_u:
        return current
    path = os.path.join(OLD, doc, "merged", "page_0.json")
    if not os.path.exists(path):
        return current
    p = load(path)
    candidates = []
    for blocks in (p.get("channels") or {}).values():
        for b in blocks:
            text = strip_title(b.get("text"))
            box = b.get("box") or [0, 0, 0, 0]
            if b.get("cat") not in ("Text", "Section-header") or abs(box[1] - title_u["box"][1]) > .15 * p["h"]:
                continue
            if len(re.findall(r"[가-힣]", text)) < 4 or len(text) > 400 or is_abstract(text) or is_keywords(text) or "@" in text:
                continue
            if current and (fold(current) in fold(text) or fold(text) in fold(current)):
                candidates.append(text)
    if current and candidates:
        longer = [x for x in candidates if fold(current) in fold(x) and len(fold(x)) > len(fold(current))]
        if longer:
            return max(longer, key=len)
    return current


def vote_title_readings(cleaned):
    """题名异读按独立渠道票数择优，同票取更完整的写法。"""
    groups = collections.defaultdict(list)
    for text, ch in cleaned:
        groups[fold(text)].append((text, ch))
    best = max(groups.values(), key=lambda g: (len({ch for _, ch in g}), len(g), max(len(t) for t, _ in g)))
    return max(best, key=lambda z: len(z[0]))


def best_title(u):
    readings = u.get("readings") or []
    choices = [(r.get("text") or "", r.get("ch")) for r in readings] or [(u.get("main_text") or "", "main")]
    cleaned = [(strip_title(t), ch) for t, ch in choices]
    cleaned = [(t, ch) for t, ch in cleaned if t]
    if not cleaned:
        return None, None
    # 题名块混入 ISSN/页码时，unified 主读法通常已去除结构噪声且最完整；
    # 仅在主读法缺失时才退回多渠道投票，避免固定取 dots 的异读。
    if re.search(r"ISSN", u.get("main_text") or "", re.I):
        main_clean = strip_title(u.get("main_text"))
        if main_clean:
            return main_clean, "main"
        if len(cleaned) > 1:
            return vote_title_readings(cleaned)
    main = strip_title(u.get("main_text"))
    if main:
        return main, "main"
    return cleaned[0]


def latin_title(p0, title, bylines):
    if not title:
        return None, None
    h, w = p0["h"], p0["w"]
    bottom = min([u["box"][1] for u in bylines] + [h * .65])
    cand = []
    for u in p0["unified"]:
        if u is title or margin(u) or is_abstract(u.get("main_text")) or is_keywords(u.get("main_text")):
            continue
        text = strip_title(u.get("main_text"))
        y = u["box"][1]
        if not (title["box"][3] - 5 <= y < bottom + 8 and 8 <= len(text) <= 320):
            continue
        letters = len(re.findall(r"[A-Za-z]", text))
        hangul = len(re.findall(r"[가-힣]", text))
        if letters < 8 or hangul > letters * .1 or "@" in text or re.match(r"^(?:ISSN|DOI|http)", text, re.I):
            continue
        if re.fullmatch(r"[A-Z][A-Za-z' .,&†‡*-]{2,80}", text) and len(text.split()) <= 6:
            continue
        score = (3 if u.get("type") == "Section-header" else 0) + min(len(text), 100) / 100 - y / h
        cand.append((score, u, text))
    if not cand:
        return None, None
    _, u, text = max(cand, key=lambda z: z[0])
    return text, u


def citation_english_title(ps):
    cand = []
    for p in ps:
        if p["page"] != 1:
            continue
        for u in p["unified"]:
            text = clean(u.get("main_text")).replace("*", "")
            m = re.search(r"\(\d{4}\)\.\s+(.+?)\.\s+(?:ESP Review|[A-Z][A-Za-z ]+,\s*\d+\()", text)
            if m and len(m.group(1).split()) >= 4:
                cand.append((p["page"], m.group(1), u))
    if not cand:
        return None, None
    page, text, u = min(cand, key=lambda z: z[0])
    return text, dict(u, page=page)


def expanded_readings(p):
    out = []
    for u in p["unified"]:
        out.append(u)
        for r in u.get("readings") or []:
            if r.get("text") and r["text"] != u.get("main_text"):
                out.append(dict(u, main_text=r["text"]))
    return out


def _norm_en_text(text):
    """英题归一：撇号直/弯同值，忽略大小写与标点，便于跨渠道异读比对。"""
    t = (text or "").lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"<\/?(?:sup|sub)>", "", t, flags=re.I)
    t = re.sub(r"\$\s*\^\s*\{([^}]*)\}\s*\$", r"\1", t)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def english_channel_support(doc, text, page, box=None):
    """英题候选在多少个独立 OCR 渠道的同页近框位置上出现（page 为 1-based 页码）。"""
    key = _norm_en_text(text)
    if len(key) < 20 or not page:
        return 0
    path = os.path.join(OLD, doc, "merged", "page_%d.json" % (page - 1))
    if not os.path.exists(path):
        return 0
    channels = load(path).get("channels") or {}
    count = 0
    for ch in ("dots", "paddle", "mineru"):
        hit = False
        for b in channels.get(ch) or []:
            other = _norm_en_text(b.get("text"))
            if len(other) < 20 or not (key in other or other in key):
                continue
            if box:
                bbox = b.get("box") or [0, 0, 0, 0]
                # 统一块常把多行题名合并成一个更高的框，用纵向重叠而非 y1 差值判定近框。
                if bbox[3] + 10 < box[1] or box[3] + 10 < bbox[1] or abs(bbox[0] - box[0]) > 80:
                    continue
            hit = True
            break
        count += hit
    return count


def abstract_page(p):
    """该页是否为英文摘要页（页内带独立 Abstract 结构标签）。"""
    for u in p["unified"]:
        text = clean(u.get("main_text"))
        if (re.search(r"\bAbstract\b", text, re.I) and u["box"][1] < .35 * p["h"]
                and len(text) < 200):
            return True
    return False


def caption_context(u, p):
    """候选是否紧邻图注/出处/网页来源（这类文字不是论文英题）。"""
    x1, y1, x2, _ = u.get("box") or [0, 0, 0, 0]
    for o in p["unified"]:
        if o is u:
            continue
        text = fold(o.get("main_text"))
        if (o.get("type") in ("Caption", "Picture")
                or re.search(r"출처|검색일|<그림|<figure|figure\s*\d+|https?://|www\.", text, re.I)):
            ox1, oy1, ox2, _ = o.get("box") or [0, 0, 0, 0]
            if abs(oy1 - y1) < 120 and ox1 < x2 and x1 < ox2:
                return True
    return False


def global_english_title(ps, front_title, front_u, doc):
    if front_title:
        text = front_title.lstrip("# ")
        if re.search(r"[가-힣]", text):
            for m in re.finditer(r"\b[A-Z][A-Za-z]+\b", text):
                suffix = text[m.start():]
                if len(suffix.split()) >= 4 and not re.search(r"[가-힣]", suffix):
                    text = suffix
                    break
        text = re.sub(r"\s+[A-Z][a-z-]+,?(?:\s+[A-Z][a-z-]+)+\s+\([^)]*(?:University|College|Institute|UNIVERSITY|COLLEGE|INSTITUTE)[^)]*\)$", "", text)
        text = re.sub(r"\s+[A-Z][a-z]+,\s+[A-Z][a-z]+$|\s+Professor,\s+Department\s+.*$", "", text)
        text = re.sub(r"\s+(?:Department\s+of\s+[^|]*\bUniversity\b|Senior\s+Research|Principal\s+Researcher|Specialized\s+public\s+officer).*$", "", text)
        if len(re.findall(r"\b(?:19|20)\d{2}\b", text)) >= 2:
            front_title = None
        if front_title and not re.search(r"[가-힣]", re.sub(r"\([^)]*[가-힣][^)]*\)", "", text)) and len(re.findall(r"[A-Za-z]", text)) >= 12 and not re.search(r"<table>|^\[.*/|\b(?:Vol\.?|pp\.)\b|\b(?:Received|Accepted|Revised)\b\s*[:：]?\s+(?:[A-Z][a-z]+,?\s+\d|\d)|^#+|www\.|^\.\s*\d{4}|^[a-z]|^[A-Z][a-z]+,.*\b(?:19|20)\d{2}\b|^\([a-z]\).*Figure\s+\d+|^Figure\s+\d+|^†?\s*Corresponding\s+Author|\bis\s+expected\s+to\b|__|^Tel\s*:|^※\s*Keywords|^\$|\btxtin\b|\b(?:New York|Intermission)\b.*\b(?:19|20)\d{2}\b|\b(?:Universitat|World Englishes|ESP Review|Special Education|Cambridge Scholars)\b.*\b(?:19|20)\d{2}\b", text):
            return text, front_u
    candidates = []
    for p in ps:
        for u in expanded_readings(p):
            if margin(u) or is_abstract(u.get("main_text")) or is_keywords(u.get("main_text")):
                continue
            raw_lines = [strip_title(x) for x in (u.get("main_text") or "").splitlines()]
            lines = []
            for line in raw_lines:
                if not line or re.search(r"[|｜┃]|[가-힣]{2,4}\([A-Za-z]", line) or "@" in line:
                    continue
                if re.match(r"^(?:references?|bibliography|acknowledg|appendix|abstract|keywords?)$", line, re.I):
                    continue
                if re.search(r"[가-힣]", line):
                    for m in re.finditer(r"\b[A-Z][A-Za-z]+\b", line):
                        suffix = line[m.start():]
                        if len(suffix.split()) >= 4 and not re.search(r"[가-힣]", suffix):
                            line = suffix
                            break
                if re.search(r"[가-힣]", re.sub(r"\([^)]*[가-힣][^)]*\)", "", line)):
                    continue
                # 以 '-' 包裹结尾的是副题（'- An ... Perspective -'），不能当列表符号剥掉。
                if re.match(r"^-\s+\S", line) and not line.rstrip().endswith("-"):
                    line = line[1:]
                lines.append(line.lstrip(":：–— "))
            text = clean(" ".join(lines))
            text = re.sub(r"^<Abstract>\s*", "", text, flags=re.I)
            text = re.sub(r"\s+[가-힣]{2,4}\([A-Za-z][^)]*\).*$", "", text)
            if re.search(r"<table>|\"image_source\"|^\[.*/|\b(?:Vol\.?|pp\.)\b|\b(?:Received|Accepted|Revised)\b\s*[:：]?\s+(?:[A-Z][a-z]+,?\s+\d|\d)|^#+|www\.|^\.\s*\d{4}|^[A-Z][a-z]+,.*\bUniversity\b|^[A-Z][a-z]+,.*\b(?:19|20)\d{2}\b|^\([a-z]\).*Figure\s+\d+|^Figure\s+\d+|^†?\s*Corresponding\s+Author|\bis\s+expected\s+to\b|__|^Tel\s*:|^※\s*Keywords|^\$|\btxtin\b|\b(?:New York|Intermission)\b.*\b(?:19|20)\d{2}\b|\b(?:Universitat|World Englishes|ESP Review|Special Education|Cambridge Scholars)\b.*\b(?:19|20)\d{2}\b", text, re.I):
                continue
            years = re.sub(r"\b(?:19|20)\d{2}\s*[-–]\s*(?:19|20)\d{2}\b", "", text)
            if len(re.findall(r"\b(?:19|20)\d{2}\b", years)) >= 2:
                continue
            if re.match(r"^[a-z]", text) or re.match(r"^As\s+(?:they|he|she)\b", text, re.I):
                continue
            if re.search(r"[가-힣]", re.sub(r"\([^)]*[가-힣][^)]*\)", "", text)) or re.fullmatch(r"\([^)]*\)\s*\([^)]*\)", text):
                continue
            if len(re.findall(r"(?:\b(?:I|II|III|IV|V|VI)|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ])[.)]", text)) >= 2:
                continue
            text = re.sub(r"\s+[A-Z][a-z-]+,?(?:\s+[A-Z][a-z-]+)+\s+\([^)]*(?:University|College|Institute|UNIVERSITY|COLLEGE|INSTITUTE)[^)]*\)$", "", text)
            text = re.sub(r"\s+[A-Z][a-z]{1,6},\s+[A-Z][a-z]+$|\s+Professor,\s+Department\s+.*$", "", text)
            text = re.sub(r"\s+(?:Department\s+of\s+[^|]*\bUniversity\b|Senior\s+Research|Principal\s+Researcher|Specialized\s+public\s+officer).*$", "", text)
            letters = len(re.findall(r"[A-Za-z]", text))
            if letters < 12 or len(text) > 360 or len(text.split()) < 4 or "@" in text or "http" in text:
                continue
            if re.search(r"\(\d{4}\).*\b(?:Vol\.|Review|Journal|University Press)\b", text):
                continue
            if re.fullmatch(r"[A-Z][A-Za-z' .,&†‡*-]{2,80}", text) and len(text.split()) <= 4 and not re.search(r"\b(?:on|of|the|in|and|for|study|analysis)\b", text, re.I):
                continue
            y = u["box"][1] / p["h"]
            if p["page"] > 1 and y > .48:
                continue
            # 题名后粘作者署名（'... Kim Yoon Young* Sim Chung Sik**'）不是渠道原文里的题名。
            if re.search(r"\b[A-Z][a-z'-]+\s+[A-Z][a-z'-]+\s*\*{1,3}\s*[A-Z][a-z'-]+", text):
                continue
            # 'SURNAME, GIVEN NAME (University/Institution)' 是署名行，不是英题候选。
            if (len(text.split()) <= 15 and re.search(
                    r"\([^)]*\b(?:Univ\.?|University|College|Institute|School|Foundation|Cent(?:er|re))\b[^)]*\)\s*$",
                    text, re.I)):
                continue
            # 后部页候选只要有近框渠道支持、位于上部题名区且无图注上下文即可接受。
            if p["page"] >= 8:
                support = english_channel_support(doc, text, p["page"], u.get("box"))
                if not (support >= 2 and not caption_context(u, p)
                        and u.get("type") in ("Section-header", "Text") and y < .45):
                    continue
            score = (4 if u.get("type") == "Section-header" else 0) + min(len(text), 150) / 150
            score += .05 if re.search(r"\d+(?:st|nd|rd|th)\b", text, re.I) else 0
            score += 5 if p["page"] == 1 else 0
            score -= p["page"] * .1 + y * .1
            candidates.append((score, text, u, p["page"]))
    if not candidates:
        return citation_english_title(ps)
    filtered = []
    for cand in candidates:
        score, text, u, page = cand
        if any(u.get("uid") == other[2].get("uid") and other[1] != text and text.startswith(other[1]) and len(text.split()) - len(other[1].split()) <= 3 for other in candidates):
            continue
        filtered.append(cand)
    _, text, u, page = max(filtered or candidates, key=lambda z: z[0])
    u = dict(u, page=page)
    return text, u


def raw_grid_authors(doc, p0, title):
    path = os.path.join(OLD, doc, "merged", "page_0.json")
    if not os.path.exists(path):
        return []
    raw = load(path).get("channels", {}).get("dots") or []
    out = []
    for b in raw:
        text = clean(b.get("text"))
        box = b.get("box") or [0, 0, 0, 0]
        if title and box[1] < title["box"][3]:
            continue
        if box[1] > .6 * p0["h"] or box[2] - box[0] > .42 * p0["w"]:
            continue
        m = re.fullmatch(r"([가-힣]{2,4})([*†‡¹²³⁴⁵⁶⁷⁸⁹]*)[，,]\s*([A-Z][A-Za-z' .-]{2,70})", text)
        if not m:
            continue
        out.append({"name": m.group(1), "markers": list(m.group(2)), "affiliation": None,
                    "role": None, "roman": m.group(3).strip(), "uid": None, "box": box, "page": 1,
                    "source": "layoutx_dots_grid"})
    return correct_grid_names(doc, out, p0)


def correct_grid_names(doc, grid, p0):
    """dots 网格里单人名的 OCR 异读，用其它渠道同一罗马名的写法投票纠正。"""
    path = os.path.join(OLD, doc, "merged", "page_0.json")
    if not grid or not os.path.exists(path):
        return grid
    channels = load(path).get("channels") or {}
    for a in grid:
        roman = (a.get("roman") or "").strip()
        if not roman:
            continue
        original = fold(a["name"])
        votes = collections.Counter()
        variants = {}
        for ch in ("dots", "paddle", "mineru"):
            for b in channels.get(ch) or []:
                text = clean((b.get("text") or "").replace("<sup>", "").replace("</sup>", ""))
                if roman not in text:
                    continue
                for m in re.finditer(r"([가-힣]{2,4})\s*[,，]\s*" + re.escape(roman), text):
                    key = fold(m.group(1))
                    if key != original and difflib.SequenceMatcher(None, key, original).ratio() >= .65:
                        votes[key] += 1
                        variants.setdefault(key, m.group(1))
        if votes and votes.most_common(1)[0][1] >= 2:
            a["name"] = variants[votes.most_common(1)[0][0]]
    return grid


def raw_grid_english_title(doc, title):
    path = os.path.join(OLD, doc, "merged", "page_0.json")
    if not os.path.exists(path):
        return None, None
    raw = load(path).get("channels", {}).get("dots") or []
    cand = []
    for b in raw:
        text = strip_title(b.get("text"))
        box = b.get("box") or [0, 0, 0, 0]
        if title and not (title["box"][3] - 5 <= box[1] < title["box"][3] + 260):
            continue
        if len(re.findall(r"[A-Za-z]", text)) < 12 or re.search(r"[가-힣]", text) or len(text) > 320:
            continue
        cand.append((box[1], text, {"box": box, "uid": None}))
    return (min(cand, key=lambda z: z[0])[1], min(cand, key=lambda z: z[0])[2]) if cand else (None, None)


def dedup_authors(authors):
    out = []
    for a in authors:
        name = fold(a.get("name"))
        duplicate = False
        for i, b in enumerate(out):
            other = fold(b.get("name"))
            if name == other or (name.endswith(other) and 0 < len(name) - len(other) <= 2) or (other.endswith(name) and 0 < len(other) - len(name) <= 2):
                if len(name) < len(other):
                    out[i] = a
                duplicate = True
                break
            if len(name) == len(other) and name and other and difflib.SequenceMatcher(None, name, other).ratio() >= .65 and a.get("_source_line") != b.get("_source_line"):
                # 异读折叠时保留跨渠道票数更多的一边，而非偶然先出现的一边。
                if a.get("_channel_votes", 0) > b.get("_channel_votes", 0):
                    out[i] = a
                duplicate = True
                break
        if not duplicate:
            out.append(a)
    return out


def raw_authors_fallback(doc, p0, title):
    path = os.path.join(OLD, doc, "merged", "page_0.json")
    if not os.path.exists(path) or not title:
        return []
    channels = load(path).get("channels") or {}
    channel_results = []
    for ch in ("dots", "paddle", "mineru"):
        out, seen = [], set()
        for b in channels.get(ch) or []:
            box = b.get("box") or [0, 0, 0, 0]
            if not (title["box"][1] + 5 <= box[1] <= .62 * p0["h"]):
                continue
            u = {"uid": None, "box": box, "type": b.get("cat", "Text")}
            for line in (b.get("text") or "").splitlines():
                line = line.strip()
                if not line or re.search(r"^(?:[✚+●○•▪※■*\-~\s]*)(?:핵\s*심\s*주\s*제\s*어|주\s*제\s*어|키\s*워\s*드|핵\s*심\s*어|keywords?|key[\s-]*words?)\b", line, re.I):
                    break
                if not line or is_abstract(line) or re.match(r"^[#*\s]*(?:차\s*례|목\s*차|초록|요약)", line):
                    continue
                if not re.search(r"[가-힣一-鿿]", line):
                    continue
                if not author_zone_ok(u, title, p0, line) or heading_echo(dict(u, main_text=line), p0):
                    continue
                line = re.sub(r"[\w.%+-]+@[\w.-]+(?:\.[A-Za-z]{2,})?", "", line).strip()
                if nonauthor_context(line, title) or not byline_structure(line):
                    continue
                if re.match(r"^\S*(?:대학교|대학원|대학|연구소|연구원|학과|학부|센터)(?:\s+(?:교수|강사|연구원))?$", line):
                    continue
                if out and not re.search(r"[*†‡¹²³⁴⁵⁶⁷⁸⁹<]|\([A-Za-z]|주\s*저자|교신\s*저자|제\s*\d+\s*저자|공동저자|대학교|연구소|연구원|학과|학부", line):
                    continue
                cjk_name = re.fullmatch(r"([一-鿿]{2,4})\s*(?:[*†‡¹²³⁴⁵⁶⁷⁸⁹]+|\*+\d+\)?|\$\s*\^\s*\{\s*\*\s*\}\s*\$)", line)
                monk = re.search(r"스님\(([가-힣]{2,4})\)", line)
                transliterated = transliterated_authors(line)
                spaced_single = re.fullmatch(r"((?:[가-힣]\s*){2,4})[*¹²³⁴⁵⁶⁷⁸⁹]", line)
                simple_aff = re.fullmatch(r"([가-힣]{2,4})\s+(\S*(?:대학교|대학|연구원|연구소|학과|학부|센터|협회|부)(?:-wise)?)", line)
                name_aff = re.match(r"^([가-힣]{2,4})\s+([가-힣]{1,2})\s+(\S*(?:대학교|대학|연구원|연구소|학과|학부|센터|협회).*)$", line)
                roman_comma = re.match(r"^([가-힣]{2,4})\(([^)]*[A-Za-z][^)]*,[^)]*)\)", line)
                if cjk_name:
                    parsed = [{"name": cjk_name.group(1), "markers": [], "affiliation": None, "role": None, "roman": None}]
                elif roman_comma:
                    rest = re.split(r"[|｜┃]", line)
                    parsed = [{"name": roman_comma.group(1), "markers": [], "affiliation": rest[1].strip() if len(rest) > 1 else None, "role": None, "roman": roman_comma.group(2)}]
                elif transliterated:
                    parsed = transliterated
                elif spaced_single:
                    parsed = [{"name": re.sub(r"\s", "", spaced_single.group(1)), "markers": [], "affiliation": None, "role": None, "roman": None}]
                elif monk:
                    parsed = [{"name": monk.group(1), "markers": [], "affiliation": None, "role": None, "roman": None}]
                elif name_aff and front._looks_affil(name_aff.group(3)):
                    parsed = [{"name": name_aff.group(1) + " " + name_aff.group(2), "markers": [], "affiliation": name_aff.group(3), "role": None, "roman": None}]
                elif simple_aff:
                    parsed = [{"name": simple_aff.group(1), "markers": [], "affiliation": simple_aff.group(2), "role": None, "roman": None}]
                elif front._split_latin_ko_byline(line):
                    latin_name, latin_aff = front._split_latin_ko_byline(line)
                    parsed = [{"name": latin_name, "markers": [], "affiliation": latin_aff, "role": None, "roman": None}]
                else:
                    parsed = front._parse_author_line(line, u, {})
                for a in parsed or []:
                    name = re.sub(r"\s*\^\{[^}]*\}", "", (a.get("name") or "").strip())
                    name = re.sub(r"\*+\d+\)?$", "", name).strip()
                    military = re.match(r"^([가-힣]{2,4})(국방부|국방대)$", name)
                    if military:
                        name = military.group(1)
                        a["affiliation"] = (military.group(2) + " " + (a.get("affiliation") or "")).strip()
                    if (a.get("affiliation") or "").startswith(("대학교", ":", "：")) or len(a.get("affiliation") or "") > 80 or re.search(r"다\.|였다|이었다|점이다|있고$", a.get("affiliation") or ""):
                        continue
                    if not valid_author_name(name) or fold(name) in seen:
                        continue
                    seen.add(fold(name))
                    a.update({"name": name, "uid": None, "box": box, "page": 1, "source": "layoutx_channel_" + ch, "_source_line": line, "_source_type": b.get("cat")})
                    out.append(a)
        if out:
            channel_results.append(out)
    if not channel_results:
        return []
    votes = collections.Counter(fold(a["name"]) for result in channel_results for a in result)
    accepted = []
    seen = set()
    for result in channel_results:
        for a in result:
            key = fold(a["name"])
            if votes[key] >= 2 and key not in seen:
                a["_channel_votes"] = votes[key]
                accepted.append(a)
                seen.add(key)
    return accepted or channel_results[0]


def parse_authors(fp, template):
    doc, p0, title, bys = fp["doc"], load(os.path.join(OLD, fp["doc"], "unified", "page_0.json")), fp["title"], fp["bylines"]
    extra = authors_from_title_byline(title, p0)

    def with_extra(items):
        names = {fold(a["name"]) for a in items}
        merged = list(items)
        for a in extra:
            if fold(a["name"]) not in names:
                merged.append(a)
                names.add(fold(a["name"]))
        return dedup_authors(merged)

    bys = [u for u in bys if author_zone_ok(u, title, p0, u.get("main_text") or "") and not heading_echo(u, p0)]
    grid = raw_grid_authors(doc, p0, title)
    if len(grid) >= 2:
        return with_extra(grid)
    out = []
    seen = set()
    candidates = list(bys)
    known = {u.get("uid") for u in candidates}
    if title:
        for u in p0["unified"]:
            if u.get("uid") in known or margin(u) or is_abstract(u.get("main_text")) or is_keywords(u.get("main_text")):
                continue
            text = u.get("main_text") or ""
            if not (title["box"][1] + 5 <= u["box"][1] <= .68 * p0["h"]) or len(text) > 2400 or "http" in text:
                continue
            if not author_zone_ok(u, title, p0, text) or heading_echo(u, p0):
                continue
            if re.search(r"[*†‡<]|[가-힣]{2,4}\([A-Za-z]|[가-힣]{2,4}\s*,\s*(?:주\s*저자|제\s*\d+\s*저자|교신\s*저자)|^[가-힣]{2,4}(?:\s*[·•,，]\s*[가-힣]{2,4})*$", clean(text)):
                candidates.append(u)
                known.add(u.get("uid"))
    if title and re.search(r"[*†‡<]", title.get("main_text") or "") and len((title.get("main_text") or "").splitlines()) > 1:
        candidates.append(title)
    for u in candidates:
        for line in (u.get("main_text") or "").splitlines():
            line = line.strip()
            if re.match(r"^[#*\s]*(?:차\s*례|목\s*차|핵심어|주제어|키워드|keywords?|key[\s-]*words?|국문초록|국문요약|초록|abstract)\b", line, re.I):
                break
            if not line or is_abstract(line) or is_keywords(line):
                continue
            line = re.sub(r"[\w.%+-]+@[\w.-]+(?:\.[A-Za-z]{2,})?", "", line).strip()
            if nonauthor_context(line, title) or not byline_structure(line):
                continue
            parsed = []
            if not re.search(r"[가-힣]", line) and (len(line.split()) > 4 or re.search(r"\b(?:Strategies|Grammar|Understanding|Society|Education|Narrative|Study|Analysis)\b", line, re.I)):
                continue
            if out and not re.search(r"[*†‡¹²³⁴⁵⁶⁷⁸⁹<]|\([A-Za-z]|주\s*저자|교신\s*저자|제\s*\d+\s*저자|공동저자|대학교|연구소|연구원|학과|학부", line):
                continue
            if re.match(r"^\S*(?:대학교|대학원|대학|연구소|연구원|학과|학부|센터)(?:\s+(?:교수|강사|연구원))?$", line):
                continue
            cjk_name = re.fullmatch(r"([一-鿿]{2,4})\s*(?:[*†‡¹²³⁴⁵⁶⁷⁸⁹]+|\*+\d+\)?|\$\s*\^\s*\{\s*\*\s*\}\s*\$)", line)
            if cjk_name:
                parsed = [{"name": cjk_name.group(1), "markers": [], "affiliation": None, "role": None, "roman": None}]
            transliterated = transliterated_authors(line)
            if transliterated:
                parsed = transliterated
            body_join = re.match(r"^((?:[가-힣]{2,4}\*{1,4}(?:\s*,\s*)?){2,})\s+(.{60,})$", line)
            if body_join:
                line = body_join.group(1).strip(" ,")
            spaced_single = re.fullmatch(r"((?:[가-힣]\s*){2,4})[*¹²³⁴⁵⁶⁷⁸⁹]", line)
            if spaced_single:
                parsed = [{"name": re.sub(r"\s", "", spaced_single.group(1)), "markers": ["*"], "affiliation": None, "role": None, "roman": None}]
            if template["author_grammar"] == "spaced_names_markers_split_not_rescue" and re.search(r"[가-힣]\s*\*{1,4}\s*[가-힣]", line):
                parts = re.split(r"\s*(\*{1,4})\s*(?=[가-힣])", line)
                parsed = []
                for i in range(0, len(parts), 2):
                    name = re.sub(r"\s+", "", parts[i]).strip(" *")
                    if re.fullmatch(r"[가-힣]{2,4}", name):
                        parsed.append({"name": name, "markers": list(parts[i + 1]) if i + 1 < len(parts) else [], "affiliation": None, "role": None, "roman": None})
            # 人名+机构同行：先在机构词边界剥离，再交给纯作者语法。
            m = re.match(r"^([가-힣]{2,4})\s+([가-힣]{1,4})\s+((?:\S*(?:대학교|대학|연구원|연구소|학과|학부|센터|협회|부)).*)$", line)
            simple_aff = re.fullmatch(r"([가-힣]{2,4})\s+(\S*(?:대학교|대학|연구원|연구소|학과|학부|센터|협회|부)(?:-wise)?)", line)
            if simple_aff:
                parsed = [{"name": simple_aff.group(1), "markers": [], "affiliation": simple_aff.group(2), "role": None, "roman": None}]
            roman_comma = re.match(r"^([가-힣]{2,4})\(([^)]*[A-Za-z][^)]*,[^)]*)\)", line)
            if roman_comma:
                rest = re.split(r"[|｜┃]", line)
                parsed = [{"name": roman_comma.group(1), "markers": [], "affiliation": rest[1].strip() if len(rest) > 1 else None, "role": None, "roman": roman_comma.group(2)}]
            if parsed:
                pass
            elif m and front._looks_affil(m.group(3)):
                # 短第二令牌多为音译名的后半（'리우 커 대구가톨릭대학교'）：保留空格，
                # 既是正确作者写法，也让机构剥离能按全名去掉前缀。
                second = m.group(2)
                full = m.group(1) + (" " + second if len(second) <= 2 else second)
                parsed = [{"name": full, "markers": [], "affiliation": m.group(3), "role": None, "roman": None}]
            elif front._split_latin_ko_byline(line):
                latin_name, latin_aff = front._split_latin_ko_byline(line)
                parsed = [{"name": latin_name, "markers": [], "affiliation": latin_aff, "role": None, "roman": None}]
            else:
                parsed = front._parse_author_line(line, u, {})
            for a in parsed or []:
                name = (a.get("name") or "").strip()
                name = re.sub(r"\s*\^\{[^}]*\}", "", name)
                name = re.sub(r"\*+\d+\)?$", "", name).strip()
                a["name"] = name
                if (a.get("affiliation") or "").startswith(("대학교", ":", "：")) or len(a.get("affiliation") or "") > 80 or re.search(r"다\.|였다|이었다|점이다|있고$", a.get("affiliation") or ""):
                    continue
                military = re.match(r"^([가-힣]{2,4})(국방부|국방대)$", name)
                if military:
                    name = military.group(1)
                    a["name"] = name
                    a["affiliation"] = (military.group(2) + " " + (a.get("affiliation") or "")).strip()
                if not valid_author_name(name) or fold(name) in seen:
                    continue
                seen.add(fold(name))
                a.update({"uid": u.get("uid"), "box": u.get("box"), "page": 1, "source": "layoutx_template", "_source_line": line, "_source_type": u.get("type")})
                out.append(a)
    ps = []
    ufolder = os.path.join(OLD, doc, "unified")
    for name in os.listdir(ufolder):
        if re.fullmatch(r"page_\d+\.json", name):
            ps.append(load(os.path.join(ufolder, name)))
    own_keywords = set(fold(x) for x in keywords(ps) + keywords(ps, True))
    title_terms = {fold(x) for x in re.split(r"[\s:：;；,，·•\-–—<>《》『』「」()（）]+", strip_title(title.get("main_text") if title else "")) if len(fold(x)) >= 2}
    blocked = own_keywords | title_terms
    out = [a for a in out if fold(a["name"]) not in blocked]
    out = [a for a in out if not (a.get("_source_type") == "Section-header" and fold(a.get("_source_line")) != fold(a["name"]) and not (a.get("roman") or a.get("role") or a.get("affiliation")))]
    out = dedup_authors(out)
    raw = [a for a in raw_authors_fallback(doc, p0, title) if fold(a["name"]) not in blocked]
    out_names = {fold(a["name"]) for a in out}
    raw_names = {fold(a["name"]) for a in raw}
    if len(raw_names) == len(out_names) == 1 and raw and out and raw[0].get("_channel_votes", 1) >= 2 and difflib.SequenceMatcher(None, next(iter(raw_names)), next(iter(out_names))).ratio() >= .65:
        return with_extra(raw)
    if raw_names >= out_names and len(raw_names) > len(out_names):
        return with_extra(raw)
    return with_extra(out or raw)


def clean_keyword_values(vals):
    out = []
    for x in vals:
        x = re.sub(r"\s+ISSN\s*[\dXx-]+\s*\(?(?:Print|Online)?\)?$", "", x, flags=re.I)
        if re.search(r"<table>|<tr>|<td|</?td>|</?tr>|\"image_source\"", x) or x.startswith(("td>", "tr>")) or x.endswith("<"):
            continue
        x = re.sub(r"\s+[A-Z][a-z-]+(?:\s+[A-Z][a-z-]+)+\s+\([^)]*(?:University|College|Institute)[^)]*\)+$", "", x).strip(" .|")
        if not x or len(x) > 140 or re.search(r"본고는|본\s*연구는|분석\s*결과|하였다|판단된다|전망된다|보고되었다", x):
            continue
        if x not in out:
            out.append(x)
    return out


def keywords(ps, english=False):
    pat = r"keywords?|key[\s-]*words?" if english else r"핵\s*심\s*주\s*제\s*어|주\s*제\s*어|키\s*워\s*드|핵\s*심\s*어|중\s*심\s*어"
    for pi, p in enumerate(ps):
        for u in p["unified"]:
            lines = [re.sub(r"\*\*", "", x).strip() for x in (u.get("main_text") or "").splitlines()]
            for i, line in enumerate(lines):
                m = re.match(r"^[●○•▪※■✚+*\-~\s]*(?:" + pat + r")\s*[:：]?\s*(.*)$", line, re.I)
                if not m:
                    continue
                content = m.group(1)
                if not content and i + 1 < len(lines):
                    content = lines[i + 1]
                if i + 1 < len(lines) and re.fullmatch(r"[A-Z]+\)?", lines[i + 1].strip()):
                    content += ", " + lines[i + 1].strip()
                content = re.split(r"\n|\bDOI\s*:", content, flags=re.I)[0]
                vals = [clean(x).strip(" .") for x in re.split(r"[;；,，]", content)]
                vals = clean_keyword_values(vals)
                if vals:
                    return vals
    return []


def channel_keywords(doc, english=False):
    pat = r"keywords?|key[\s-]*words?" if english else r"핵\s*심\s*주\s*제\s*어|주\s*제\s*어|키\s*워\s*드|핵\s*심\s*어|중\s*심\s*어"
    folder = os.path.join(OLD, doc, "merged")
    candidates = []
    for name in sorted((x for x in os.listdir(folder) if re.fullmatch(r"page_\d+\.json", x)), key=lambda x: int(x[5:-5])):
        p = load(os.path.join(folder, name))
        channels = p.get("channels") or {}
        for ch in ("dots", "paddle", "mineru"):
            for b in channels.get(ch) or []:
                lines = [re.sub(r"\*\*", "", x).strip() for x in (b.get("text") or "").splitlines()]
                for i, line in enumerate(lines):
                    label_only = r"^[●○•▪※■✚+*\-~\s]*(?:" + pat + r")\s*[:：]?\s*$"
                    if i + 1 < len(lines) and re.match(label_only, lines[i + 1], re.I) and re.search(r"[,，;；]", line):
                        vals = clean_keyword_values(re.split(r"[;；,，]", line))
                        if vals:
                            candidates.append(vals)
                        continue
                    embedded = re.search(r"주제어", line) if not english else None
                    if embedded and re.search(r"[,，]", line[:embedded.start()]):
                        content = line[:embedded.start()].rstrip(" ,가")
                        vals = clean_keyword_values(re.split(r"[;；,，]", content))
                        if vals:
                            candidates.append(vals)
                        continue
                    m = re.match(r"^[●○•▪※■✚+*\-~\s]*(?:" + pat + r")\s*[:：]?\s*(.*)$", line, re.I)
                    if m:
                        content = m.group(1) or (lines[i + 1] if i + 1 < len(lines) else "")
                        if i + 1 < len(lines) and re.fullmatch(r"[A-Z]+\)?", lines[i + 1].strip()):
                            content += ", " + lines[i + 1].strip()
                        vals = [clean(x).strip(" .") for x in re.split(r"[;；,，]", content)]
                        vals = clean_keyword_values(vals)
                        if vals:
                            candidates.append(vals)
    def candidate_score(v):
        bad = sum(bool(len(x) > 45 or re.search(r"본고|본\s*연구|하였다|다\.|살았다$|수행한\s*결과|[가-힣][A-Za-z]+[가-힣]", x)) for x in v)
        votes = sum(sum(fold(x).lower() == fold(y).lower() for other in candidates for y in other) for x in v)
        return (-bad, votes, sum(x.count("-") for x in v), -max([len(x) for x in v] or [0]), -sum(len(x) for x in v))
    return max(candidates, key=candidate_score) if candidates else []


def bracket_keywords(ps):
    for p in ps[:1]:
        for u in p["unified"]:
            text = clean(u.get("main_text")).strip("[]")
            if "/" not in text or not re.search(r"[가-힣]", text) or not re.search(r"[A-Za-z]", text):
                continue
            groups = re.split(r",\s*(?=[^,/]*[가-힣])", text, maxsplit=1)
            if len(groups) == 1:
                groups = re.split(r"/(?=[^/]*[가-힣])", text, maxsplit=1)
            if len(groups) == 2:
                en = [clean(x) for x in groups[0].split("/") if re.search(r"[A-Za-z]", x)]
                ko = [clean(x) for x in groups[1].split("/") if re.search(r"[가-힣]", x)]
            else:
                en, ko = [], []
                for item in text.split(","):
                    pair = item.split("/")
                    if len(pair) == 2 and re.search(r"[A-Za-z]", pair[0]) and re.search(r"[가-힣]", pair[1]):
                        en.append(clean(pair[0]))
                        ko.append(clean(pair[1]))
            if len(en) >= 2 and len(ko) >= 2:
                return ko, en
    return [], []


def affil_lines(text):
    lines = (text or "").splitlines()
    out = []
    for line in lines:
        if out and re.search(r"\b(?:Korea|Chungnam|Gyeongsang)\s*$", out[-1]) and re.match(r"^(?:Military\s+Academy|National\s+University|University|Institute|College)\b", line):
            out[-1] += " " + line.strip()
        elif out and re.search(r"\b(?:Electric|Department)\s*$", out[-1]) and re.match(r"^(?:Engineering|Kyungpook|National|University)\b", line):
            out[-1] += " " + line.strip()
        else:
            out.append(line)
    return out


# 学位/学历残片：不是机构，不能和机构并列输出，更不能把多行署名拼成一条。
DEGREE_ONLY = re.compile(
    r"^(?:[가-힣A-Za-z.\s]{1,30}?)(?:석사|박사|학사|M\.?S\.?|M\.?Sc\.?|Ph\.?D\.?"
    r"|Master|Doctor|Bachelor)(?:\s*\([^)]*\))?$", re.I)
INST_WORD = re.compile(
    r"대학교|대학원|대학|연구소|연구원|학과|학부|센터|협회|국방부|본부|팀"
    r"|University|Institute|College|Department|Center", re.I)


def dedupe_degree_variants(items):
    """多名作者同一机构各带学位残片时，只保留裸机构（学位不是机构）。"""
    groups = {}
    for x in items:
        parts = [p.strip() for p in x.split(",")]
        if (len(parts) == 2 and DEGREE_ONLY.match(parts[1]) and not INST_WORD.search(parts[1])
                and not re.search(r"교수|강사|연구원|소장|박사과정|석사과정", parts[0])):
            groups.setdefault(fold(parts[0]), []).append(x)
    replace = {}
    for key, vals in groups.items():
        if len(vals) >= 2:
            bare = vals[0].split(",", 1)[0].strip()
            for v in vals:
                replace[v] = bare
    if not replace:
        return items
    out = []
    for x in items:
        target = replace.get(x, x)
        if target not in out:
            out.append(target)
    return out


def collapse_degree_parts(aff):
    """把同一机构重复 + 多个学位残片折叠为单一机构名（通用规则，不按 doc 特判）。"""
    parts = [x.strip() for x in (aff or "").split(",")]
    if len(parts) < 2:
        return aff
    inst = [p for p in parts if INST_WORD.search(p) and not DEGREE_ONLY.match(p)]
    if len(inst) < 2:
        return aff
    kept = []
    for p in parts:
        if not p or DEGREE_ONLY.match(p) and not INST_WORD.search(p):
            continue
        if not any(fold(p) == fold(k) for k in kept):
            kept.append(p)
    return ", ".join(kept) if kept else aff


def extract_affiliations(p0, title, authors, doc):
    out = list(dict.fromkeys(a["affiliation"] for a in authors if a.get("affiliation") and front._looks_affil(a["affiliation"])))
    if not title:
        return out
    role = re.compile(r"\s*(?:초빙교수|겸임교수|객원교수|명예교수|특임교수|전임교수|부교수|조교수|교수|강사|총장|부총장|학장|선임연구원|책임연구원|전문연구원|전임강사|박사과정|석사과정|대학원생|제\s*\d+\s*저자|교신저자)\s*$")

    def add_line(line):
        if re.search(r"\*\*\s*(?:교신저자|제\s*\d+\s*저자)", line):
            parts = re.split(r"\*\*\s*(?=교신저자|제\s*\d+\s*저자)", line)
            for part in parts:
                add_line(part)
            return
        if re.search(r"[|｜┃]", line):
            for part in re.split(r"[|｜┃]", line):
                add_line(part)
            return
        anchors = list(re.finditer(r"[가-힣]{2,4}\([A-Z][^)]*\)", line))
        if len(anchors) >= 2:
            for i, m in enumerate(anchors):
                add_line(line[m.start():anchors[i + 1].start() if i + 1 < len(anchors) else len(line)])
            return
        line = clean(re.sub(r"<[^>]+>", "", line)).lstrip("*∗†‡¹²³⁴⁵⁶⁷⁸⁹●-:： ")
        if not line or is_abstract(line) or is_keywords(line):
            return
        line = re.sub(r"\([^)]*@[^)]*\)", "", line)
        line = re.sub(r"(?:E?-?mail\s*:\s*)?[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}", "", line, flags=re.I).strip(" ,()")
        aff = role.sub("", line).strip(" ,·•")
        if not front._looks_affil(aff) and re.search(r"(?:교수|강사|연구원|연구소장|박사과정|석사과정|박사\s*수료)", aff):
            aff = re.split(r"(?:초빙교수|겸임교수|객원교수|명예교수|특임교수|전임교수|부교수|조교수|교수|전임강사|전임대우강의교수|강사|책임연구원|선임연구원|전문연구원|연구소장|박사과정|석사과정|박사\s*수료)", aff)[0].strip(" ,·•")
            if "," in aff and not front._looks_affil(aff.split(",")[0]):
                aff = aff.split(",", 1)[1].strip()
        aff = re.sub(r"^(?!한국\s|중국\s|일본\s)[가-힣]{2,4}(?<!대)[*†‡¹²³⁴⁵⁶⁷⁸⁹]*(?:\s*[·•]\s*[가-힣]{2,4}[*†‡¹²³⁴⁵⁶⁷⁸⁹]*)?\s+(?=.*(?:대학교|대학원|연구소|연구원|학과|학부|국방부))", "", aff)
        # 署名行残留在机构名前的人名/音译片段（'리우 커 대구가톨릭대학교' → '대구가톨릭대학교'）
        aff = re.sub(r"^(?!(?:한국|중국|일본|러시아|전)\s)(?:[가-힣]{1,4}(?<!대)\s+)+(?=[가-힣]{2,}(?:대학교|대학원|대학|연구소|연구원|학과|학부|센터)(?![가-힣]))", "", aff)
        if front._looks_affil(aff) and re.search(r"(?:박사|석사)\s*수료", aff):
            for part in re.split(r"(?<=\.)\s+", aff):
                part = role.sub("", part).strip(" ,·•")
                if front._looks_affil(part) and part not in out:
                    out.append(part)
            return
        if (front._looks_affil(aff) or re.search(r"협동과정$", aff)) and aff not in out:
            out.append(aff)

    for u in p0["unified"]:
        if u.get("type") in ("Page-header", "Page-number") or u["box"][1] < title["box"][3] - 3:
            continue
        for line in affil_lines(u.get("main_text")):
            add_line(line)
    path = os.path.join(OLD, doc, "merged", "page_0.json")
    if os.path.exists(path):
        channels = load(path).get("channels") or {}
        for ch in ("dots", "paddle", "mineru"):
            for b in channels.get(ch) or []:
                # 脚注常是作者机构的唯一来源（博士毕业生/교수署名），不能整块跳过；
                # 混入的纯英文对译串在后面的"已有韩文机构则丢弃 ASCII 机构"规则里清理。
                if b.get("cat") in ("Page-header", "Page-number") or (b.get("box") or [0, 0])[1] < title["box"][3] - 12:
                    continue
                for line in affil_lines(b.get("text")):
                    add_line(line)
    cleaned = []
    role_tail = re.compile(r"\s*(?:초빙교수|겸임교수|객원교수|명예교수|특임교수|전임교수|부교수|조교수|교수|전임강사|전임대우강의교수|강사|전임연구원|일반연구원|수석연구원|총장|부총장|학장|기획처장|처장|원장|실장|팀장|선임연구원|책임연구원|전문연구원|방문연구원|전임|초빙강의|박사과정|석사과정|대학원생|제\s*\d+\s*저자|교신저자|주저자)\s*$")
    for aff in out:
        aff = clean(aff).strip(" ,;|｜┃")
        aff = re.sub(r"^(?:X\s+|\$\s*\^\s*\{[^}]*\}\s*\$?\s*)", "", aff)
        aff = re.sub(r",\s*\.[A-Za-z]+$", "", aff)
        aff = re.sub(r"^(?:[가-힣]{2,4}[*†‡¹²³⁴⁵⁶⁷⁸⁹]+[·•]?){2,}\s+", "", aff)
        aff = re.sub(r"^(?:[*∗]+\s*)?(?:제\s*\d+\s*저자|교신저자|주저자)\s*[:：]?\s*", "", aff)
        for author in authors:
            for key in (author.get("name"), author.get("roman")):
                if key and aff.startswith(key) and len(aff) > len(key):
                    aff = aff[len(key):].lstrip(" *†‡¹²³⁴⁵⁶⁷⁸⁹,·•")
        aff = re.sub(r"^[A-Z][A-Za-z'-]+\s+[A-Z][A-Za-z'-]+\s+(?=[가-힣])", "", aff)
        aff = re.sub(r"^[\u3400-\u9fff\u2e80-\u2fff]{2,4}\s*,\s*", "", aff)
        m_paren = re.match(r"^[가-힣]{2,4}\(([^()]+)$", aff)
        if m_paren:
            aff = m_paren.group(1)
        if re.search(r"(?:대학교|대학원|학과|학부|연구소|센터).*\s+연구원$", aff):
            aff = re.sub(r"\s+연구원$", "", aff)
        aff = re.sub(r"(?<=과)\s+대학원$|(?:\s+박사)$", "", aff)
        aff = re.sub(r"[가-힣]{2,4}[*†‡¹²³⁴⁵⁶⁷⁸⁹]+$", "", aff).strip()
        aff = re.sub(r"\($", "", aff).strip()
        previous = None
        while previous != aff:
            previous = aff
            aff = role_tail.sub("", aff).strip(" ,;().")
        aff = re.sub(r"(?<=과)\s+대학원$|(?:\s+박사)$", "", aff)
        if re.search(r"(?:대학교|대학원|학과|학부|연구소|센터).*\s+연구원$", aff):
            aff = re.sub(r"\s+연구원$", "", aff)
        if not aff or len(aff) > 120 or re.match(r"^[#\s]*(?:\d+|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+|IV|V|VI|IX|X)[.)]\s*", aff):
            continue
        if re.search(r"본고는|본\s*논문은|이\s*연구는|이러한\s|연구\s*결과|제시한다|필요성|분석하고|제출됨", aff):
            continue
        aff = re.sub(r"\s*(?:Professor|Research Engineer|Chief Research Engineer)\s*,?\s*", "", aff, flags=re.I).strip(" ,;")
        aff = re.sub(r"(?:부교수|조교수|교수)\s*,\s*현\s*", ", ", aff)
        aff = collapse_degree_parts(aff)
        parts = [x.strip() for x in aff.split(",")]
        if len(parts) > 1 and not front._looks_affil(parts[-1]) and len(parts[-1].split()) <= 3:
            parts = parts[:-1]
        if len(parts) < 2 or not all(front._looks_affil(x) for x in parts):
            parts = [aff]
        for part in parts:
            if part and part not in cleaned:
                cleaned.append(part)
    # 已有韩文机构时，混入的纯英文对译串（含脚注/署名行里的旧英文串）不再是独立机构。
    if any(re.search(r"[가-힣]", x) for x in cleaned):
        cleaned = [x for x in cleaned if re.search(r"[가-힣]", x)
                   or not re.search(r"(?:University|Institute|Department|College|Center|Research|Engineering|Systems)", x, re.I)]
    cleaned = dedupe_degree_variants(cleaned)
    # 连续的"机构 + 部门/团队"合并为一项（경북대학교 + 전기공학과 等）。
    merged = []
    for aff in cleaned:
        joined = (merged[-1] + ", " + aff) if merged else ""
        if (merged and fold(aff) not in fold(merged[-1])
                and re.search(r"(?:대학교|대학|연구원|연구소|회사|시스템|넥스원|사|부|Co\.?|Systems)$", merged[-1])
                and re.match(r"^[가-힣]{2,12}(?:과|학과|학부|팀|센터|본부|부|실)(?:[,，]|$)", aff)
                and aff_support(joined, p0, doc) >= 1):
            # 拼接串必须在至少一个 OCR 渠道原文中真实出现，避免把多作者机构拼成假串。
            merged[-1] = joined
        else:
            merged.append(aff)
    cleaned = merged
    result = []
    for aff in cleaned:
        fa = fold(aff)
        if any(fa == fold(x) for x in result):
            continue
        if any(fa != fold(x) and fa in fold(x) and fa[:3] == fold(x)[:3] for x in result):
            continue
        near = next((x for x in result if fa != fold(x) and fa[:3] == fold(x)[:3]
                     and difflib.SequenceMatcher(None, fa, fold(x)).ratio() >= .8), None)
        if near:
            # OCR 异读折叠：保留跨渠道出现次数更多的写法，而不是先出现的一边。
            if aff_support(aff, p0, doc) > aff_support(near, p0, doc):
                result[result.index(near)] = aff
            continue
        result.append(aff)
    return result


def prefer_channel_keywords(primary, fallback):
    if not primary:
        return fallback
    if not fallback:
        return primary
    if len(fallback) > len(primary):
        return fallback
    dirty = any(len(x) > 45 or re.search(r"본고|본\s*연구|결과|하였다|다\.|살았다$|[가-힣][A-Za-z]+[가-힣]", x) for x in primary)
    if dirty and len(fallback) <= len(primary) + 2 and sum(len(x) for x in fallback) < sum(len(x) for x in primary):
        return fallback
    return primary


def extract(fp, clusters):
    key = tuple(fp["cluster_key"])
    cluster_id = next((c["cluster_id"] for c in clusters if tuple(c["key"]) == key), None)
    base = {"doc": fp["doc"], "layout": {"cluster_id": cluster_id, "fingerprint": list(key)},
            "title": value(None), "title_en": value(None), "authors": [], "affiliations": [],
            "keywords": [], "keywords_en": [], "abstain": True, "abstain_reason": "no_template"}
    path = os.path.join(HERE, "templates", (cluster_id or "none") + ".json")
    matched = cluster_id and os.path.exists(path)
    if matched:
        template = load(path)
    else:
        cluster_id = "C00"
        template = {"cluster_id": "C00", "author_grammar": "generic_byline", "channel_policy": "unified_best_reading", "min_confidence": 0.4}
    p0 = load(os.path.join(OLD, fp["doc"], "unified", "page_0.json"))
    folder = os.path.join(OLD, fp["doc"], "unified")
    ps = [load(os.path.join(folder, n)) for n in sorted(os.listdir(folder)) if re.fullmatch(r"page_\d+\.json", n)]
    ps.sort(key=lambda p: p["page"])
    fp = dict(fp, title=title_candidate(p0) or fp["title"])
    title, source = best_title(fp["title"]) if fp["title"] else (None, None)
    title = channel_title(fp["doc"], fp["title"], title)
    authors = parse_authors(fp, template)
    en, en_u = latin_title(p0, fp["title"], fp["bylines"])
    if not en and template["author_grammar"] == "korean_name_comma_roman_name":
        en, en_u = raw_grid_english_title(fp["doc"], fp["title"])
    en, en_u = global_english_title(ps, en, en_u, fp["doc"])
    if en:
        for a in authors:
            roman = (a.get("roman") or "").strip()
            if roman and en.endswith(roman):
                en = en[:-len(roman)].rstrip()
        en = re.sub(r"\s+[A-Z][A-Za-z-]+\s+[A-Z][A-Za-z-]+\s+\$\s*\^\s*\{[^}]*\}\s*\$?$", "", en)
        en = re.sub(r"\s+(?:Military\s+Science\s+and\s+Technology\s+Soldier|Consolidated\s+Maintenance\s+Depot).*$", "", en, flags=re.I)
    ko_kw, en_kw = keywords(ps), keywords(ps, True)
    ko_kw = prefer_channel_keywords(ko_kw, channel_keywords(fp["doc"]))
    en_kw = prefer_channel_keywords(en_kw, channel_keywords(fp["doc"], True))
    ko_dirty = any(len(x) > 45 or re.search(r"본고|본\s*연구|하였다|다\.", x) for x in ko_kw)
    if not ko_kw or not en_kw or ko_dirty:
        bracket_ko, bracket_en = bracket_keywords(ps)
        ko_kw = bracket_ko if ko_dirty and bracket_ko else (ko_kw or bracket_ko)
        en_kw = en_kw or bracket_en
    if en_kw and (not ko_kw or any(len(x) > 45 or re.search(r"본고|본\s*연구|하였다|다\.", x) for x in ko_kw)):
        ko_kw = en_kw
    blocked = set(fold(x) for x in ko_kw + en_kw)
    blocked |= {fold(x) for x in re.split(r"[\s:：;；,，·•\-–—<>《》『』「」()（）]+", title or "") if len(fold(x)) >= 2}
    authors = [a for a in authors if fold(a["name"]) not in blocked and fold(a["name"]) not in fold(title or "")]
    verified_authors = []
    coverage = collections.Counter()
    for a in authors:
        coverage[a.get("_source_line") or ""] += len(fold(a["name"]))
    for a in authors:
        line = a.get("_source_line") or ""
        marker = re.search(re.escape(a["name"]) + r"\s*(\*+|[†‡¹²³⁴⁵⁶⁷⁸⁹]+)", line)
        if marker and not a.get("markers"):
            a["markers"] = list(marker.group(1))
        structural = a.get("roman") or a.get("markers") or a.get("role") or a.get("affiliation")
        line = a.get("_source_line") or ""
        if not structural and a.get("_source_type") == "Section-header" and fold(line) != fold(a["name"]):
            continue
        if not structural and coverage[a.get("_source_line") or ""] < .2 * max(1, len(fold(line))):
            continue
        a.pop("_source_line", None)
        a.pop("_source_type", None)
        a.pop("_channel_votes", None)
        verified_authors.append(a)
    authors = verified_authors
    base.update({"layout": {"cluster_id": cluster_id, "observed_cluster_id": next((c["cluster_id"] for c in clusters if tuple(c["key"]) == key), None), "template": template, "confidence": 1.0 if matched else .45, "fingerprint": list(key), "title_channel": source},
                 "title": value(title, fp["title"]), "title_en": value(en, en_u, en_u.get("page", 1) if en_u else 1), "authors": authors,
                 "affiliations": extract_affiliations(p0, fp["title"], authors, fp["doc"]), "keywords": ko_kw, "keywords_en": en_kw,
                 "abstain": False, "abstain_reason": None})
    return base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", nargs="*")
    args = ap.parse_args()
    clusters = load(os.path.join(HERE, "clusters.json"))["cluster_list"]
    fps = [json.loads(x) for x in open(os.path.join(HERE, "fingerprints.jsonl"), encoding="utf-8")]
    wanted = set(args.docs or [])
    count = 0
    for fp in fps:
        if wanted and fp["doc"] not in wanted:
            continue
        meta = extract(fp, clusters)
        folder = os.path.join(NEW, fp["doc"])
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        count += 1
        if count % 100 == 0:
            print("extracted", count, file=sys.stderr)
    print(json.dumps({"written": count, "out": NEW}, ensure_ascii=False))


if __name__ == "__main__":
    main()
