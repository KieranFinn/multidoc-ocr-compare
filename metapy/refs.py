# -*- coding: utf-8 -*-
import os, re, io, json, datetime, collections, html
import pipeline
from pipeline import _gdice, _fold_sym
import regress
import unify
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
        out, last = [], None
        pos = [m.start() for m in marks]
        head, tail0 = t[:pos[0]], t[pos[0]:]
        segs = re.split(r"(?m)(?=^)", tail0)
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

def _extract_fields(entry):
    """L3 正则抽取: 作者/年/题名/来源/卷期页/DOI/语种。正则优先, 失败留 None 送 ④ LLM。"""
    e = entry.strip()
    f = {}
    mdoi = DOI_RE.search(e)
    f["doi"] = mdoi.group(0).rstrip(".,;") if mdoi else None
    myear = None
    for m in YEAR_RE.finditer(e):
        if int(m.group(1)) in YEAR_OK:
            myear = m
            break
    f["year"] = myear.group(1) if myear else None
    mq = QUOTE_TITLE.search(e)
    mi = ITALIC_TITLE.search(e)
    f["title"] = mq.group(1).strip() if mq else (mi.group(1).strip() if mi else None)
    rem = None
    if mq is None and mi is None:
        # 缺开引号的题名(段内含 ” 而无 “, 如 '…science textbook”, DongA…'): 按闭引号段回收
        rem = re.search(r"(?:^|,\s*|․\s*|·\s*)([^,“\"『「«]{4,90}?[”」』])", e)
        if rem:
            f["title"] = rem.group(1).strip(" ”」』")
    # 作者: 题名/年份前的一段
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
    f["authors_raw"] = auth or None
    f["authors"] = _split_authors(auth) if auth else []
    # 来源: 题名后(或年份后)至卷/期/页/年份前的首段
    rest = e[mq.end():] if mq else (e[rem.end(1):] if rem else (e[myear.end():] if myear else ""))
    src = None
    if rest.strip():
        mcut = re.search(r"(?:vol\.?|V\.?|no\.?|제\s*\d+\s*[권호]|pp?\.\s*\d|,\s*(?:19|20)\d{2})", rest, re.I)
        seg = rest[:mcut.start()] if mcut else rest
        seg = seg.strip().strip(",. ")
        if seg and not YEAR_RE.fullmatch(seg):
            src = _norm_ws(seg)
    if f["title"] is None and not mq and myear:
        # 无引号条目(韩式 '저자(년), 제목, 출처…'): 年后首段=题名, 次段=来源
        rest2 = e[myear.end():].strip().lstrip(",. ").lstrip(")").strip()
        segs2 = [s.strip() for s in rest2.split(",") if s.strip()]
        if segs2:
            f["title"] = _norm_ws(segs2[0].strip(" ,.()"))
            src = _norm_ws(segs2[1].strip(" ,.()")) if len(segs2) > 1 else None
    f["source"] = src
    mv = VOL_RE.search(e) or KW_VOL.search(e)
    f["volume"] = mv.group(1) if mv else None
    mn = NO_RE.search(e) or KW_NO.search(e)
    f["issue"] = mn.group(1) if mn else None
    mpp = PP_RE.search(e)
    pages = None
    if mpp:
        pages = "%s-%s" % (mpp.group(1), mpp.group(2))
    else:
        mpg = PG_RANGE.search(e)
        if mpg:
            pages = "%s-%s" % (mpg.group(1), mpg.group(2))
    f["pages"] = pages
    h = len(re.findall(u"[가-힣]", e))
    el = len(re.findall(r"[A-Za-z]", e))
    f["lang"] = "ko" if h > el else ("en" if el > h else "?")
    return f

def _split_authors(auth):
    """L2 作者切分(韩语特性): ·/․ 与逗号分层, 空白只在无分隔符时用; 英文 首字母+姓 配对
    ("Y. Kwak" 不拆); and/& 连接拆分; 机构/出版社段不入作者(多段时, 人名锚 김철수(…) 除外);
    缺开引号的书名残留(段内含 ” 而无 “)截到引号前; 剥职称词表(교수/연구원/정회원/우수회원…);
    括号内罗马拼音是作者名硬锚, 保留。"""
    if not auth:
        return []
    a = _norm_ws(auth).strip().strip(",․·. ")
    if not a:
        return []
    if "·" in a or "․" in a:
        parts = [p.strip() for p in re.split(r"[·․]", a) if p.strip()]
        sep_mode = True
    elif "," in a:
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
        parts = a.split()
        sep_mode = False   # 空白切分(机构名内部片段): 不套机构剔除, 免误杀 '충북대 중원문화연구소'
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
            if (re.match(r"^[A-Z][A-Za-z\-']+$", p) and re.match(r"^([A-Z]\.?\s*){1,3}$", q)) or \
               (re.match(r"^([A-Z]\.?\s*){1,3}$", p) and re.match(r"^[A-Z][A-Za-z\-']+$", q)):
                merged.append(p + " " + q)
                i += 2
                continue
        merged.append(p)
        i += 1
    out = []
    org = re.compile(r"(Co\.?|Ltd\.?|LLC|INC|사|출판|Press|Publishing|Publisher|University|"
                     r"학\s*회|회\s*지|연구\s*소|연구\s*원|학\s*교|공\s*사|청$|부$|처$|위원회)$")
    for p in merged:
        if p in ("and", "&", u"및"):
            continue
        p = HONORIFIC.sub("", p).strip(" ,․·.")
        if "”" in p and "“" not in p:
            p = p.split("”")[0].strip(" ,․·.")
            if not p:
                continue
        if not p:
            continue
        if sep_mode and len(merged) > 1 and org.search(p) and not re.search(u"[가-힣]{2,4}\\(", p):
            continue
        out.append(p)
    return out

def _field_agree(entry_fields, block_u):
    """L2-lite 三渠道互证: 年/题名/首作者 在各家读法中的出现/一致性。返回 {field: agree_0..1}。"""
    agree = {}
    readings = [r for r in block_u["readings"] if (r.get("text") or "").strip()]
    n = max(1, len(readings))

    def _hit(pred):
        return sum(1 for r in readings if pred(r.get("text") or "")) / float(n)

    if entry_fields["year"]:
        agree["year"] = _hit(lambda t: entry_fields["year"] in t)
    if entry_fields["title"]:
        tf = _fold(entry_fields["title"])[:30]
        agree["title"] = _hit(lambda t: tf in _fold(t))
    if entry_fields["authors"]:
        head = _fold(entry_fields["authors"][0])[:6]
        agree["authors"] = _hit(lambda t: head in _fold(t) if head else False)
    return agree

def refs_doc(doc, out_root):
    """单篇①: 文献区定位(页级 in_ref + 条目模式过滤) → 条目切分(跨块缝合) → 字段抽取 → 校验。
    产出 out\<doc>\metadata.json 的 references 段 + 校验摘要。"""
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
        for num, etext in _split_entries_block(b["t"], bstyle):
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
    # —— L3: 字段抽取 + 置信 ——
    umap = {}
    for p in pages:
        for u in pdata[p]["unified"]:
            umap[u["uid"]] = u
    refs = []
    for e in entries:
        f = _extract_fields(e["text"])
        agree = _field_agree(f, umap[e["uid"]])
        level = "high"
        basis = []
        if e["block_review"]:
            level = "mid" if e["block_level"] != "low" else "low"
            basis.append("块级%s继承" % e["block_level"])
        low_agree = [k for k, v in agree.items() if v < 0.67]
        if low_agree:
            level = "low"
            basis.append("渠道互证不足:%s" % ",".join(low_agree))
        if not f["authors"] or not f["year"]:
            level = "low" if level == "high" else level
            basis.append("作者/年缺")
        if not f["title"]:
            if level == "high":
                level = "mid"
            basis.append("题名未抽出(④LLM)")
        refs.append({
            "num": e["num"], "style": style, "raw": e["text"], "page": e["page"],
            "box": e["box"], "uid": e["uid"], "block_review": e["block_review"],
            "authors": f["authors"], "authors_raw": f["authors_raw"],
            "year": f["year"], "title": f["title"], "source": f["source"],
            "volume": f["volume"], "issue": f["issue"], "pages": f["pages"],
            "doi": f["doi"], "lang": f["lang"],
            "confidence": {"level": level, "basis": "; ".join(basis) or "三渠道一致·块级无分歧",
                           "source": "regex", "ch_agree": agree}})
    # —— L1 机械校验: 编号连续/年份合法/页码起止 ——
    val = {"year_bad": [], "pages_bad": [], "entry_count": len(refs)}
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
    for r in refs:
        if r["year"] and int(r["year"]) not in YEAR_OK:
            val["year_bad"].append(r["num"])
        if r["pages"]:
            a, _, b0 = r["pages"].partition("-")
            if b0 and int(a) >= int(b0):
                val["pages_bad"].append(r["num"])
    meta_fp = os.path.join(doc_dir, "metadata.json")
    meta = {}
    if os.path.exists(meta_fp):
        meta = json.load(io.open(meta_fp, encoding="utf-8"))
    meta.update({"doc": doc, "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                 "references": refs, "ref_validation": val,
                 "ref_style": style})
    io.open(meta_fp, "w", encoding="utf-8").write(json.dumps(meta, ensure_ascii=False, indent=1))
    lv = collections.Counter(r["confidence"]["level"] for r in refs)
    print("== ① %s == 风格=%s 条目=%d 置信=%s 编号=%s" % (
        doc, style, len(refs), dict(lv),
        val.get("num_sequence", "无编号")))
    return meta
