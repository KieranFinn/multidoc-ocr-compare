# -*- coding: utf-8 -*-
import os, re, io, json, datetime, collections, html
import pipeline
from pipeline import _gdice, _fold_sym
import regress
import unify
from .common import *



def _pick_title(j, want_hangul):
    """打分制选标题(禁单取 max 字号): Section-header 权重最高 + 垂直位置 + 宽度占比 + 排除词典
    (차례/요약/Keywords…) + running 头(顶部带且命中 doc_heads 者)排除。"""
    H = float(j["h"])
    best, bs = None, -1.0
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
        t = t.split("\n")[0].strip()   # 多行块取首行(英题名+韩作者行同块的 1892171 形态)
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
            best, bs = u, score
    return best

def _parse_author_line(line, u, foot_affil):
    """作者行解析: 上标集合化(*/**/***/†/‡/¹²/LaTeX $^{1)}$ 全归一) + 机构剥离 + 职称/角色词剥离
    + 括号内罗马拼音硬锚 + OCR 粘连名(홍영식서울교육대학교)按机构词表切开。
    返回 [(name, markers, affiliation, role, roman)]。"""
    line = (line or "").strip()
    if not line:
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
        tk = SUP_LATEX.sub("", tk)
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
    doc_heads = _doc_heads({0: j0}, [0]) if False else None
    # 标题
    t_ko = _pick_title(j0, True)
    t_en = _pick_title(j0, False)
    H = float(j0["h"])
    # 作者区: 标题(韩/英)之下, 摘要/차례/키워드头 之上; 区块行级扫描(两个区: 韩题下 + 英题下)
    stops = []
    for u in j0["unified"]:
        t = (u["main_text"] or "").strip()
        if t_ko and u["uid"] == t_ko["uid"] or t_en and u["uid"] == t_en["uid"]:
            continue
        if HEAD_EXCLUDE.match(t.split("\n")[0].strip()) or \
           re.match(u"^(요\\s*약|·국문요약|Keywords?|중심어|차\\s*례)", t.strip()):
            stops.append(u["box"][1])

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
                if ABSTRACT_START.match(s):
                    limit = min(limit, y1)
                    break
                out.append((s, u))
        return out

    zone_lines = _zone(t_ko)
    if t_en is not None:
        zone_lines += [z for z in _zone(t_en) if z not in zone_lines]
    affil_notes = {}      # marker -> affiliation(来自脚注)
    for u in j0["unified"]:
        if u.get("cross_page", {}).get("role"):
            continue
        if u["type"] != "Footnote":
            continue
        for ln in (u["main_text"] or "").split("\n"):
            m = MARKER_LINE.match(ln.strip())
            if m and (AFFIL_HEAD.search(m.group(2)) or ROLE_WORDS.search(m.group(2))):
                affil_notes[m.group(1).strip()] = m.group(2).strip()
    # 解析作者行(行内有 2-4 音节韩文名/罗马锚/上标残留 才算)
    authors = []
    affils_zone = []
    for s, u in zone_lines:
        if (re.fullmatch(u"[\\(（].+[\\)）]", s) and AFFIL_HEAD.search(s)) or \
           (_hangul_ratio(s) < 0.2 and AFFIL_HEAD.search(s) and "," not in s):
            affils_zone.append(re.sub(ROLE_WORDS, "", s).strip("() ").strip())
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
            affils_zone.append(re.sub(ROLE_WORDS, "", s).strip("() ").strip())
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
                    a["affiliation"] = a["affiliation"] or ROLE_WORDS.sub("", nv).strip(" ,·•")
                    a["role"] = a["role"] or (mrole.group(1) if mrole else None)
        if a["affiliation"] is None and affils_zone:
            a["affiliation"] = affils_zone[0]
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
        if a["affiliation"] and a["affiliation"] not in affils:
            affils.append(a["affiliation"])
    for v in affils_zone:
        if v and v not in affils:
            affils.append(v)

    def _best_text(u):
        """题名取值: 主读法之外, 若各渠道 dice 与主读法≥0.8, 取断行/空格最多者(dots 断行='\n' 版
        本即带空格的正确形态); 多行块按主读法首行的折叠长度截前缀(英题名+韩作者行同块形态)。"""
        mt = u["main_text"] or ""
        cands = [r["text"] for r in u["readings"]
                 if (r.get("text") or "").strip() and _gdice(r["text"], mt) >= 0.8] or [mt]
        best = max(cands, key=lambda t: t.count(" ") + t.count("\n") * 2)
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
            ag = sum(1 for r in rs if _gdice(r["text"], mt) >= 0.8) / float(len(rs))
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

    meta_fp = os.path.join(doc_dir, "metadata.json")
    meta = json.load(io.open(meta_fp, encoding="utf-8")) if os.path.exists(meta_fp) else {}
    meta.update({
        "doc": doc,
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "title": dict({"value": _clean_title(_best_text(t_ko)) if t_ko else None},
                      **_conf(t_ko)),
        "title_en": dict({"value": _clean_title(_best_text(t_en)) if t_en else None},
                         **_conf(t_en)),
        "authors": authors,
        "affiliations": affils,
        "front_notes": {"author_zone_lines": [s for s, _ in zone_lines],
                        "affil_footnotes": affil_notes},
    })
    io.open(meta_fp, "w", encoding="utf-8").write(json.dumps(meta, ensure_ascii=False, indent=1))
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
