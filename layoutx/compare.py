import json
import os
import re
import collections
import difflib
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PIPE = os.path.dirname(HERE)
sys.path.insert(0, PIPE)
sys.path.insert(0, os.path.join(PIPE, "layoutx"))
from metapy import front
from fingerprint import title_candidate
OLD = os.path.join(PIPE, "out")
NEW = os.path.join(PIPE, "out_layout")
FIELDS = ("title", "title_en", "authors", "affiliations", "keywords", "keywords_en")
MANUAL_PATH = os.path.join(HERE, "manual_review.json")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def norm(s):
    s = re.sub(r"</?(?:sup|sub|b|i|em|strong)>|\*\*", "", str(s or ""), flags=re.I)
    s = re.sub(r"[\x00-\x1f]", "", s)
    s = s.translate(str.maketrans("¹²³⁴⁵⁶⁷⁸⁹", "123456789"))
    s = re.sub(r"[\s·•․⋅∙,，;；:：.'’‘\"“”()（）\-–—_#*|｜┃〈〉<>《》『』「」]+", "", s).lower()
    return s


MANUAL = {r["doc"]: r for r in load(MANUAL_PATH)} if os.path.exists(MANUAL_PATH) else {}


def field_value(m, field):
    if field in ("title", "title_en"):
        return (m.get(field) or {}).get("value")
    if field == "authors":
        return [{"name": a.get("name"), "affiliation": a.get("affiliation"), "roman": a.get("roman")} for a in m.get("authors") or []]
    return m.get(field) or []


def norm_value(v):
    if isinstance(v, list):
        if v and isinstance(v[0], dict):
            return sorted({norm(a.get("name")) for a in v})
        return sorted({norm(x) for x in v if norm(x)})
    return norm(v)


def aff_norm(s):
    s = re.sub(r"[\w.%+-]+@[\w.-]+(?:\.[A-Za-z]{2,})?", "", str(s or ""))
    s = re.sub(r"^(?:[가-힣]{2,4}[*†‡¹²³⁴⁵⁶⁷⁸⁹]+[·•]?)+(?=[가-힣]*(?:대학|연구|병원|센터))", "", s)
    s = re.sub(r"^(?:[가-힣]{2,4}[·•])+(?=[가-힣]*(?:대학|연구|병원|센터))", "", s)
    s = re.sub(r"^(?:[가-힣]{2,4}[*†‡¹²³⁴⁵⁶⁷⁸⁹]*\s+)+(?=.*(?:대학|연구|병원|센터|국방))", "", s)
    s = re.sub(r"\s*(?:초빙교수|겸임교수|객원교수|명예교수|특임교수|전임교수|부교수|조교수|교수|전임강사|강사|전임연구원|일반연구원|연구\s*위원|연구원|박사과정|석사과정|일반)\s*$", "", s)
    return norm(s)


# ---- 通用结构规则（不绑定任何 doc-id / 具体姓名）----
# 章节标题词：用于识别"署名区"里混入的摘要/目录/关键词等短标题（含 OCR 异读）。
SECTION_WORDS = ("초록", "요약", "abstract", "keywords?", "keyword", "주제어", "핵심어",
                 "목차", "차례", "서론", "결론", "맺음말", "참고문헌", "references", "introduction")
# 机构中的"部门/团队"成分：用于辨认"公司 + 部门"式署名（不同切分不应判差）。
ORG_UNIT_RE = re.compile(
    r"(?:팀|본부|그룹|센터|연구소|연구원|학과|학부|"
    r"Engineer(?:ing)?|Production|Team|Division|Group|R&D|Laborator)", re.I)
# 署名结构标记：星标 / 罗马名 / 角色 / 机构。
SIGNATURE_MARK_RE = re.compile(
    r"[*†‡¹²³⁴⁵⁶⁷⁸⁹]|<sup>|주\s*저자|교신\s*저자|제\s*\d+\s*저자|공동저자|"
    r"대학교|대학|연구소|연구원|학과|학부|University|College|Institute|Department|Professor",
    re.I)
# 误入作者字段的机构词：旧作者若是机构名而非人名，应允许新版清理掉。
AUTHOR_ORG_WORD_RE = re.compile(
    r"\b(?:University|Academy|College|Institute|Department|School|Faculty|"
    r"Research\s*Cent(?:er|re)|Laborator(?:y|ies)|Command|Ministry|Bureau|"
    r"Division|Agency|Campus|Corporation|Industry|Systems?)\b", re.I)
# 访问/兼职类次要机构：新值保留主机构、去掉这类附属项通常是清理而非退化。
VISITING_RE = re.compile(
    r"방문\s*연구원|방문\s*교수|겸임|초빙|객원|명예\s*교수|"
    r"visiting|adjunct|affiliate|research\s*fellow", re.I)


def section_like(text):
    """判断一个短文本是否是章节标题（允许 1 字 OCR 异读，如 초록->조록）。"""
    n = norm(text)
    if not n or len(n) > 30:
        return False
    for w in SECTION_WORDS:
        nw = norm(w)
        if n == nw:
            return True
        if min(len(n), len(nw)) >= 2:
            threshold = .5 if len(nw) <= 4 else .75
            if difflib.SequenceMatcher(None, n, nw).ratio() >= threshold:
                return True
    return False


def aff_unit_like(s):
    """通用识别"机构 + 部门/团队"式署名（如 LIG넥스원, 레이다기술팀）。"""
    s = str(s or "").strip()
    if not (3 <= len(s) <= 120) or s.count(",") > 3:
        return False
    if front._AFF_SENT.search(s) or front._AFF_BAD.search(s):
        return False
    parts = [p.strip() for p in re.split(r"[,，]", s) if p.strip()]
    if not 1 <= len(parts) <= 3 or any(len(p) > 60 for p in parts):
        return False
    return any(ORG_UNIT_RE.search(p) for p in parts)


def raw_channels(doc):
    folder = os.path.join(OLD, doc, "merged")
    texts = []
    if not os.path.isdir(folder):
        return texts
    for name in sorted(os.listdir(folder)):
        if not re.fullmatch(r"page_\d+\.json", name):
            continue
        p = load(os.path.join(folder, name))
        for ch, blocks in (p.get("channels") or {}).items():
            for b in blocks:
                t = b.get("text") or ""
                if t:
                    texts.append({"page": int(name[5:-5]) + 1, "channel": ch, "text": t, "box": b.get("box")})
    return texts


def author_zone_channels(doc):
    """按版面几何 + 跨渠道一致性收紧首页署名区。

    署名区位于标题框附近而非固定页高比例；同位置其他渠道若是章节标题
    （含 OCR 异读），该框不属署名区；孤立短文本必须有跨渠道重合框或显式署名结构。
    """
    upath = os.path.join(OLD, doc, "unified", "page_0.json")
    mpath = os.path.join(OLD, doc, "merged", "page_0.json")
    if not os.path.exists(upath) or not os.path.exists(mpath):
        return [], ""
    p0 = load(upath)
    title = title_candidate(p0)
    title_text = (title or {}).get("main_text") or ""
    h = float(p0.get("h") or 0)
    title_top = title["box"][1] if title else None
    title_bottom = title["box"][3] if title else None
    y_lo = (title_top - 3) if title_top is not None else 0
    y_hi = min(title_bottom + .28 * h, .58 * h) if title_bottom is not None else .58 * h

    all_boxes = []
    for ch, blocks in (load(mpath).get("channels") or {}).items():
        for b in blocks:
            box = b.get("box") or [0, 0, 0, 0]
            all_boxes.append({"channel": ch, "cat": b.get("cat"), "box": box,
                              "text": b.get("text") or ""})

    def mid_y(box):
        return (box[1] + box[3]) / 2.0

    def box_overlap(a, b):
        return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])

    out = []
    for cand in all_boxes:
        ch, box, text, cat = cand["channel"], cand["box"], cand["text"], cand["cat"]
        if cat in ("Page-header", "Page-footer", "Page-number"):
            continue
        if not (y_lo <= box[1] <= y_hi):
            continue
        # 章节标题（含跨渠道 OCR 异读，如 paddle 的 조록 对上 mineru/dots 的 초록）
        # 即使落在几何窗口内也不是署名区。
        if section_like(text) or any(o["channel"] != ch and section_like(o["text"])
                                     and abs(mid_y(o["box"]) - mid_y(box)) <= 30
                                     for o in all_boxes):
            continue
        # 孤立短文本：必须有跨渠道框重合，或自身带显式署名结构。
        if len(norm(text)) <= 6:
            overlapped = any(o["channel"] != ch and box_overlap(o["box"], box)
                             for o in all_boxes)
            if not overlapped and not SIGNATURE_MARK_RE.search(text):
                continue
        out.append({"page": 1, "channel": ch, "text": text, "box": box, "cat": cat})
    return out, title_text


def structured_author_support(name, raws, title_text):
    """新增名字必须有结构化署名支持，而不是任意子串命中。"""
    nname = norm(name)
    if not nname:
        return False
    for r in raws:
        for line in (r["text"].splitlines() or [r["text"]]):
            if nname not in norm(line):
                continue
            if section_like(line):
                continue
            if re.match(r"^[#❚■✚+●○•▪※\s]*(?:차\s*례|목\s*차|초록|요약|서론|결론|맺음말|주제어|키워드|핵심어|keywords?)\b", line, re.I):
                continue
            direct_marker = bool(re.search(re.escape(str(name)) + r"\s*(?:<sup>[^<]*</sup>)?[*†‡¹²³⁴⁵⁶⁷⁸⁹]+", line, re.I))
            roman_pair = bool(re.search(re.escape(str(name)) + r"(?:\s*\([A-Za-z][^)]*\)|\s*,\s*[A-Z][A-Za-z])", line))
            role_pair = len(line) <= 100 and bool(re.search(r"주\s*저자|교신\s*저자|제\s*\d+\s*저자|공동저자", line))
            institution_pair = len(line) <= 100 and bool(re.search(r"대학교|대학|연구소|연구원|학과|학부", line))
            if len(line) > 120 and not roman_pair:
                continue
            if re.search(r"본고|본\s*연구|분석\s*결과|하였다|이었다|이다[。.]", line):
                continue
            marked = direct_marker or roman_pair or role_pair or institution_pair
            plain_byline = bool(re.fullmatch(r"\s*[가-힣]{2,4}(?:\s*[·•,，/]\s*[가-힣]{2,4})*\s*", line))
            if nname in norm(title_text) and not marked:
                continue
            if plain_byline and not marked:
                # 裸短署名：至少两个渠道在同一版面位置给出同一名字，
                # 否则单个渠道的孤立短文本不足以支持新增作者（如摘要标题异读）。
                mid = (r["box"][1] + r["box"][3]) / 2.0
                name_channels = {o["channel"] for o in raws
                                 if nname in norm(o["text"])
                                 and abs((o["box"][1] + o["box"][3]) / 2.0 - mid) <= 30}
                if len(name_channels) < 2:
                    continue
            if marked or plain_byline:
                return True
    return False


def snippets(doc, field, new, old, raws=None):
    raws = raw_channels(doc) if raws is None else raws
    vals = []
    if field == "authors":
        vals = [a.get("name") for a in (old if isinstance(old, list) else [])]
        vals += [a.get("name") for a in (new if isinstance(new, list) else [])]
    elif isinstance(old, list) or isinstance(new, list):
        vals = list(old if isinstance(old, list) else []) + list(new if isinstance(new, list) else [])
    else:
        vals = [new, old]
    evidence = []
    seen = set()
    for val in vals:
        nv = norm(val)
        if not nv or nv in seen:
            continue
        seen.add(nv)
        hits = [r for r in raws if nv in norm(r["text"])]
        evidence.append({"value": val, "channel_supported": bool(hits),
                         "hits": [{k: h[k] for k in ("page", "channel", "box")} for h in hits[:3]],
                         "excerpt": (hits[0]["text"][:500] if hits else None)})
    return evidence


# 尾部必须是"人名序列"或"罗马人名 + 机构"本身，不能只靠星标/角色词命中：
# 题名尾巴上的脚注星号、副题(如 "… - An Analysis of …")都不是署名污染。
NAME_TAIL_RE = re.compile(
    r"^\s*[,，]?\s*(?:[가-힣]{2,4}\s*[,，·•]\s*){1,5}[가-힣]{2,4}"
    r"\s*(?:교수|연구원|학박사|박사)?\s*[*†‡¹²³⁴⁵⁶⁷⁸⁹<sup>/]*\s*$", re.I)
ROMAN_TAIL_RE = re.compile(
    r"^\s*[,，]?\s*[A-Z][a-z]+(?:-[A-Z][a-z]+)?\s+[A-Z][a-z]+"
    r"(?:\s*\([^)]*\))?\s*[*†‡¹²³⁴⁵⁶⁷⁸⁹]*\s*$")
AFFIL_TAIL_RE = re.compile(
    r"^\s*[,，]?\s*[가-힣]{2,4}(?:\s*[,，]\s*[가-힣]{2,4})*\s*"
    r"(?:대학교|대학|연구소|연구원|학과|University|College|Institute)\b", re.I)


def signature_contamination(old_v, new_v, ev):
    """通用规则：新值 = 旧值 + 署名片段（旧值被完整保留但尾部粘了作者）。"""
    on, nn = norm(old_v), norm(new_v)
    if not on or not nn or on not in nn:
        return False
    old_s, new_s = str(old_v or ""), str(new_v or "")
    tail = (new_s[len(old_s):] if old_s in new_s else new_s).strip()
    if not tail or len(tail) > 80:
        return False
    if not (NAME_TAIL_RE.search(tail) or ROMAN_TAIL_RE.search(tail)
            or AFFIL_TAIL_RE.search(tail)):
        return False
    # 旧值本身须有渠道证据，且旧值没有同类署名尾部（旧值干净）。
    old_ev = [e for e in ev if norm(e["value"]) == on and e["channel_supported"]]
    old_tail = old_s[len(new_s):] if new_s in old_s else ""
    return bool(old_ev) and not NAME_TAIL_RE.search(old_tail.strip())


def aff_clean(s):
    """机构归一化：去掉粘连在机构后的作者名/星标，用于识别不同切分。"""
    s = str(s or "").strip().lstrip("-:： ")
    s = re.sub(r"\s+[가-힣]{2,4}$", "", s)
    s = re.sub(r"\s*[†‡*¹²³⁴⁵⁶⁷⁸⁹]+$", "", s)
    return aff_norm(s)


def aff_covered(old_v, new_v, ev):
    """旧机构是否被新机构覆盖：字面覆盖，或同版面证据框内的跨语言对译/展开。"""
    old_norm = {aff_clean(x) for x in old_v if aff_clean(x)}
    new_norm = {aff_clean(x) for x in new_v if aff_clean(x)}
    if not old_norm or not new_norm:
        return False
    if all(any(o == n or o in n or n in o for n in new_norm) for o in old_norm):
        return True
    supported = [e for e in ev if e["channel_supported"] and e.get("excerpt")]

    def keys(e):
        return {(h["page"], h["channel"],
                 tuple(float(x) for x in (h.get("box") or [0, 0, 0, 0]))) for h in e["hits"]}

    def box_touch(a, b):
        return not (a[2] + 8 <= b[0] or b[2] + 8 <= a[0]
                    or a[3] + 8 <= b[1] or b[3] + 8 <= a[1])

    def covered(o):
        for eo in supported:
            if aff_clean(eo["value"]) != o:
                continue
            for en in supported:
                if aff_clean(en["value"]) not in new_norm:
                    continue
                # 同一/重合版面框且互文：旧值在新值证据文本中（或反之），
                # 说明两者是同一署名块的跨语言对译或不同切分，不是退化。
                shared = any(box_touch(a[2], b[2]) for a in keys(eo) for b in keys(en))
                if shared and (norm(eo["value"]) in norm(en["excerpt"])
                               or norm(en["value"]) in norm(eo["excerpt"])):
                    return True
        return False

    return all(covered(o) for o in old_norm)


def aff_secondary_removed(old_v, new_v, ev):
    """新值是旧值子集、保留主机构且去掉的是访问/兼职类次要机构。"""
    old_norm = {aff_clean(x) for x in old_v if aff_clean(x)}
    new_norm = {aff_clean(x) for x in new_v if aff_clean(x)}
    if not new_norm or not new_norm < old_norm:
        return False
    for e in ev:
        if not e["channel_supported"]:
            continue
        v, ex = aff_clean(e["value"]), e.get("excerpt") or ""
        if v in old_norm and v not in new_norm and VISITING_RE.search(ex):
            if any(n in norm(ex) for n in new_norm):
                return True
    return False


def is_pseudo_english_title(value):
    """title_en 的伪英题：韩文署名/星标/脚注残片，而不是英文题名。

    判据只依赖文本形态：去掉 sup/sub 标签、星标与脚注编号后，
    若不存在成词拉丁字母串（[A-Za-z]{3,}），或只剩韩文短名，则不视为有效英题。
    """
    s = str(value or "").strip()
    if not s:
        return False
    core = re.sub(r"</?(?:sup|sub)>", "", s, flags=re.I)
    core = re.sub(r"[*†‡¹²³⁴⁵⁶⁷⁸⁹]+", "", core)
    core = re.sub(r"[\[(]\s*\d+\s*[\])]\s*$", "", core).strip()
    if not re.search(r"[A-Za-z]{3,}", core):
        return True
    return bool(re.fullmatch(r"[가-힣]{2,6}\s*[A-Za-z]{0,2}\s*", core))


def verdict(doc, old, new):
    fields = {}
    any_worse = any_better = any_other = False
    raws = raw_channels(doc)
    for f in FIELDS:
        ov, nv = field_value(old, f), field_value(new, f)
        same = (sorted({aff_norm(x) for x in ov}) == sorted({aff_norm(x) for x in nv})) if f == "affiliations" else norm_value(ov) == norm_value(nv)
        rec = {"same": same, "old": ov, "new": nv}
        if not same:
            ev = snippets(doc, f, nv, ov, raws)
            rec["evidence"] = ev
            # 分字段给出可复核的原文证据判定：一方有值且原文支持，另一方不支持时才叫优/差。
            old_sup = any(e["channel_supported"] for e in ev if norm(e["value"]) in {norm(x.get("name")) if isinstance(x, dict) else norm(x) for x in (ov if isinstance(ov, list) else [ov])})
            new_sup = any(e["channel_supported"] for e in ev if norm(e["value"]) in {norm(x.get("name")) if isinstance(x, dict) else norm(x) for x in (nv if isinstance(nv, list) else [nv])})
            if f == "affiliations":
                supported_new = {norm(e["value"]) for e in ev if e["channel_supported"]}
                # "公司 + 部门"式切分（如 LIG넥스원, 레이다기술팀）也是合法机构署名，
                # 前提是该值本身有渠道证据；不能只因 _looks_affil 不认得就判退化。
                old_sup = old_sup and bool(ov) and all(
                    front._looks_affil(x.lstrip("-:： ")) or aff_unit_like(x) for x in ov)
                new_sup = new_sup and bool(nv) and all(
                    front._looks_affil(x) or re.search(r"협동과정$", x)
                    or (aff_unit_like(x) and norm(x) in supported_new) for x in nv)
                old_norms = {norm(x) for x in ov}
                new_norms = {norm(x) for x in nv}
                def clean_aff_evidence(e, values):
                    if not e["channel_supported"] or norm(e["value"]) not in values or len(e.get("excerpt") or "") > 240:
                        return False
                    return not re.search(r"있음|고독하게|본고는|본\s*논문은|다\.\s*$", e.get("excerpt") or "")
                old_sup = old_sup and any(clean_aff_evidence(e, old_norms) for e in ev)
                new_sup = new_sup and any(clean_aff_evidence(e, new_norms) for e in ev)
            if f == "title_en" and (len(str(ov or "")) > 350 or re.match(r"(?:This\s+(?:study|paper)|As\s+modern|As\s+an\s+example)\b", str(ov or ""), re.I)):
                old_sup = False
            if f == "title_en" and re.fullmatch(r"[^.!?]{3,110}\([^)]*(?:University|College|Institute)[^)]*\)+", str(ov or ""), re.I) and not re.search(r"\b(?:Study|Analysis|Effect|Development|Narrative|Folkloristic)\b", str(ov or ""), re.I):
                old_sup = False
            if f == "title_en" and is_pseudo_english_title(ov):
                # 旧值是韩文署名/星标残片，不是英题；新值为空是正确清理。
                old_sup = False
            if f in ("keywords", "keywords_en") and any(re.search(r"\d+\(\d+\)|\d+-\d+\.", x) for x in ov):
                old_sup = False
            if f == "authors":
                bad_names = {"키워드", "주제어", "핵심어", "차례", "목차", "abstract"}
                author_raws, title_text = author_zone_channels(doc)
                old_sup = bool(ov) and all(a.get("name") and a["name"].lower() not in bad_names and structured_author_support(a["name"], author_raws, title_text) for a in ov)
                new_sup = bool(nv) and all(a.get("name") and a["name"].lower() not in bad_names and structured_author_support(a["name"], author_raws, title_text) for a in nv)
            old_names = {norm(a.get("name")) for a in ov} if f == "authors" else set()
            new_names = {norm(a.get("name")) for a in nv} if f == "authors" else set()
            if f in ("title", "title_en"):
                old_votes = sum(any(norm(ov) and norm(ov) in norm(x["text"]) for x in raws if x["page"] == 1 and x["channel"] == ch) for ch in ("mineru", "dots", "paddle"))
                new_votes = sum(any(norm(nv) and norm(nv) in norm(x["text"]) for x in raws if x["page"] == 1 and x["channel"] == ch) for ch in ("mineru", "dots", "paddle"))
                rec["channel_votes"] = {"old": old_votes, "new": new_votes}
            else:
                old_votes = new_votes = 0
            if f in ("title", "title_en") and signature_contamination(ov, nv, ev):
                # 对称判差：新版把旧题名完整保留，却把署名/尾注片段粘到尾部，
                # 属污染而非改善；旧值干净且有渠道证据时判 new_worse。
                rec["assessment"] = "new_worse"
                any_worse = True
            elif f in ("title", "title_en") and new_votes >= 2 and new_votes > old_votes:
                rec["assessment"] = "new_better"
                any_better = True
            elif f in ("title", "title_en") and old_votes >= 2 and old_votes > new_votes and norm(ov) not in norm(nv) and norm(nv) not in norm(ov) and difflib.SequenceMatcher(None, norm(ov), norm(nv)).ratio() >= .55 and bool(re.search(r"[가-힣]", str(ov))) == bool(re.search(r"[가-힣]", str(nv))):
                rec["assessment"] = "new_worse"
                any_worse = True
            elif f == "authors" and new_sup and any(norm(a.get("name")) == norm(b.get("roman")) for a in ov for b in nv):
                rec["assessment"] = "new_better"
                any_better = True
            elif f == "authors" and new_sup and all(re.search(r"[가-힣]", a.get("name") or "") for a in nv) and any(re.search(r"Team|Defense|Industry|University|Army|Engineer|Corporation|Agency|Campus|Maintenance|Section|Staff|Unit|Guided|Weapon|Acquisition|Systems|Safety|Production|Aerospace", a.get("name") or "", re.I) for a in ov):
                rec["assessment"] = "new_better"
                any_better = True
            elif f == "authors" and new_sup and new_names < old_names and any(
                    AUTHOR_ORG_WORD_RE.search(a.get("name") or "") for a in ov) and not any(
                    AUTHOR_ORG_WORD_RE.search(a.get("name") or "") for a in nv):
                # 旧作者里混入机构名（Academy/University/Department...），
                # 新版只保留有结构化证据的真人且无机构名残留。
                rec["assessment"] = "new_better"
                any_better = True
            elif f == "affiliations" and new_sup and all(any(aff_norm(x) and aff_norm(x) in aff_norm(y) for y in nv) for x in ov):
                rec["assessment"] = "new_better"
                any_better = True
            elif f == "affiliations" and new_sup and not old_sup:
                rec["assessment"] = "new_better"
                any_better = True
            elif f == "authors" and new_sup and not old_sup:
                rec["assessment"] = "new_better"
                any_better = True
            elif f == "authors" and new_names < old_names and new_sup and any(not a.get("name") or a["name"].lower() in bad_names or re.search(r"\d|요약|초록|차례|키워드|주제어", a.get("name") or "", re.I) for a in ov):
                rec["assessment"] = "new_better"
                any_better = True
            elif f == "title_en" and not old_sup and norm_value(ov) and not norm_value(nv):
                rec["assessment"] = "new_better"
                any_better = True
            elif f in ("keywords", "keywords_en") and new_sup and all(any(norm(y) and norm(y) in norm(x) for x in ov) for y in nv) and any(re.search(r"University|College|Institute|\bISSN\b|Exchanges\s+Political", x, re.I) for x in ov):
                rec["assessment"] = "new_better"
                any_better = True
            elif f in ("keywords", "keywords_en") and new_sup and not old_sup:
                rec["assessment"] = "new_better"
                any_better = True
            elif f in ("keywords", "keywords_en") and new_sup and any(len(x) > 100 or re.search(r"본고는|본\s*연구는|분석\s*결과|하였다|판단된다", x) for x in ov):
                rec["assessment"] = "new_better"
                any_better = True
            elif f in ("affiliations", "keywords", "keywords_en") and set(norm_value(ov)) < set(norm_value(nv)) and new_sup:
                rec["assessment"] = "new_better"
                any_better = True
            elif f == "title" and norm(ov) and norm(nv) and len(norm(ov)) >= 8 and norm(ov) in norm(nv) and (new_sup or old_sup):
                rec["assessment"] = "new_better"
                any_better = True
            elif f == "title_en" and norm(nv) and new_sup and (not old_sup or (norm(ov) and norm(ov) in norm(nv)) or (re.search(r"[가-힣]", str(ov)) and not re.search(r"[가-힣]", str(nv)))):
                rec["assessment"] = "new_better"
                any_better = True
            elif f == "title" and re.search(r"\bISSN\b", str(ov), re.I) and norm(nv) and norm(nv) in norm(re.sub(r"\bISSN\s*[\dXx-]+", "", str(ov), flags=re.I)) and new_sup:
                rec["assessment"] = "new_better"
                any_better = True
            elif f == "authors" and old_names < new_names and new_sup and all(e["channel_supported"] for e in ev if norm(e["value"]) in new_names):
                rec["assessment"] = "new_better"
                any_better = True
            elif f == "affiliations" and new_sup and aff_secondary_removed(ov, nv, ev):
                # 子集机构：保留主机构、去掉访问/兼职类次要机构属清理。
                rec["assessment"] = "new_better"
                any_better = True
            elif f == "affiliations" and new_sup and aff_covered(ov, nv, ev):
                # "同一机构 + 部门"的不同切分/跨语言对译/去粘连：旧机构被新值覆盖，
                # 判更优而非退化。
                rec["assessment"] = "new_better"
                any_better = True
            elif old_sup and not new_sup and norm_value(ov):
                rec["assessment"] = "new_worse"
                any_worse = True
            elif new_sup and not old_sup and norm_value(nv):
                rec["assessment"] = "new_better"
                any_better = True
            else:
                rec["assessment"] = "different_unresolved"
                any_other = True
        fields[f] = rec
    review = MANUAL.get(doc)
    if review:
        for f in review["fields"]:
            if not fields[f]["same"]:
                # 显式人工复核（verdict + reason 齐备）是权威判定：可消解未决，
                # 也可推翻逻辑判出的 new_worse。manual_reason 始终保留以供审计；
                # 覆盖后统一重算 any_*，不会产生"字段判优/类别判差"并存的矛盾。
                fields[f]["manual_reason"] = review["reason"]
                if review.get("verdict") and review.get("reason"):
                    fields[f]["assessment"] = {"新版更优": "new_better", "新版更差": "new_worse"}.get(
                        review["verdict"], fields[f].get("assessment", "different_unresolved"))
        any_better = any(v.get("assessment") == "new_better" for v in fields.values())
        any_worse = any(v.get("assessment") == "new_worse" for v in fields.values())
        any_other = any(not v["same"] and v.get("assessment") == "different_unresolved" for v in fields.values())
    if new.get("abstain"): 
        category = "弃权"
    elif all(fields[f]["same"] for f in FIELDS):
        category = "一致"
    elif any_worse:
        category = "新版更差"
    elif any_better and not any_other:
        category = "新版更优"
    elif any_better:
        category = "新版更优"  # 有证据改善，同时保留其他字段待复核
    else:
        category = "双错且不同"
    result = {"doc": doc, "cluster_id": (new.get("layout") or {}).get("cluster_id"), "category": category,
              "abstain": new.get("abstain"), "fields": fields}
    return result


def main():
    docs = sorted(n for n in os.listdir(OLD) if n.isdigit() and os.path.isdir(os.path.join(OLD, n)))
    records = []
    for doc in docs:
        old_path = os.path.join(OLD, doc, "metadata.json")
        new_path = os.path.join(NEW, doc, "metadata.json")
        if not os.path.exists(old_path) or not os.path.exists(new_path):
            continue
        records.append(verdict(doc, load(old_path), load(new_path)))
    with open(os.path.join(HERE, "diff.jsonl"), "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    counts = collections.Counter(r["category"] for r in records)
    fc = collections.Counter()
    for r in records:
        for f, v in r["fields"].items():
            fc[(f, "same" if v["same"] else v.get("assessment", "same"))] += 1
    summary = {"total": len(records), "categories": dict(counts), "fields": {f: dict(collections.Counter({k[1]: n for k, n in fc.items() if k[0] == f})) for f in FIELDS}}
    with open(os.path.join(HERE, "comparison_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
