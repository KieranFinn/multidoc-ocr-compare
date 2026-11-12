# -*- coding: utf-8 -*-
import os, re, io, json, datetime
import regress
from .common import *

# markdown 加粗 요약 头(13563525 '**국문요약** 본고는…' 同块, 无冒号——ABS_INLINE_KO 要求冒号,
# ABS_HEAD_KO 只认独立节头, 两不沾则整段丢失)
_ABS_BOLD_KO = re.compile(u"^\\*{1,2}\\s*(요\\s*약|국문\\s*요약|초\\s*록)\\s*\\*{1,2}\\s*[:：]?")


def _collect_abstract(pdata, pages, head_u, max_pages=3, skip_fold=None):
    """从头块之下收集摘要正文(跨页续接, 遇 Keywords 行/新节头/文献区 即止)。
    跨栏块按行取: 若块首在头上方, 只取 ABSTRACT_START 之后的行(13625974 作者行同块形态)。
    skip_fold: 折叠全等的节头视为英文题名的尾页复写(1892171/1632041 形态), 整块跳过不截停。
    双栏感知(2026-11-10, 13563525 尾页左文献/右英文摘要实证): 头页确为双栏时只收与头块
    同栏的块——左栏文献区头/小节头既不截停也不混入; REF_HEAD 停止加 y 守卫(只停头下方)。
    首内容行剥 '|' 分隔的作者/机构前缀(u9_7 'Shin, Seong-Hwan | Chung-Ang University |
    This study…' 实证): 前段为短罗马名(≤40 全字符)或含机构关键词才剥, 遇正文长句即停。"""
    out_lines = []
    uids = []
    start_p = head_u["page"] - 1
    H = pdata[start_p]["h"]
    limit_bottom = head_u["box"][3] + 0.62 * H
    boundary0 = _page_col_boundary(pdata[start_p])
    head_col = _col_of_b(head_u, boundary0, pdata[start_p]["w"])
    stripped0 = False
    for p in range(start_p, min(len(pages), start_p + max_pages)):
        j = pdata[p]
        boundary = boundary0 if p == start_p else _page_col_boundary(j)
        if boundary is not None and boundary0 is None:
            boundary = None   # 续页栏判定与头页不一致时保守按单栏(只首启用栏过滤)
        for u in j["unified"]:
            if u.get("cross_page", {}).get("role") == "head":
                continue
            if u["type"] in ("Page-header", "Page-footer", "Page-number", "Footnote"):
                continue
            if boundary is not None and u["type"] in ("Section-header", "Text") \
                    and _col_of_b(u, boundary, j["w"]) != head_col:
                continue   # 双栏: 非头块同栏的块整体跳过
            if u["type"] == "Section-header" and u["uid"] != head_u["uid"] \
                    and u["box"][1] >= head_u["box"][3] - 2:
                if skip_fold and _fold((u["main_text"] or "").split("\n")[0]) == skip_fold:
                    continue   # 英文题名复写头: 整块跳过
                return "\n".join(out_lines).strip(), uids   # 区内新节头=界(先判停, 其行不入摘要)
            if u["type"] == "Section-header" and regress.REF_HEAD.search(u["main_text"] or "") \
                    and u["box"][1] >= head_u["box"][3] - 2:
                return "\n".join(out_lines).strip(), uids
            y1, y3 = u["box"][1], u["box"][3]
            lines = (u["main_text"] or "").split("\n")
            take = []
            if p == start_p and y1 < head_u["box"][3] - 2 and y3 > head_u["box"][3]:
                started = False
                for ln in lines:
                    if ABSTRACT_START.match(ln.strip()):
                        started = True
                    if started:
                        take.append(ln)
            elif y1 >= head_u["box"][3] - 2 or p > start_p:
                if p == start_p and y1 >= limit_bottom:
                    continue
                take = list(lines)
            stop = False
            for ln in take:
                s = ln.strip()
                if not s:
                    continue
                if not stripped0 and "|" in s:
                    segs = [x.strip() for x in s.split("|")]
                    dropped = False
                    while segs:
                        sg = segs[0]
                        if not sg:
                            segs.pop(0)
                            continue
                        if (len(sg) <= 40 and re.match(r"^[A-Z][A-Za-z .\-',]+$", sg)) or \
                            re.search(r"University|Univ\b|Department|Institute|College|School|"
                                      r"Association|Academy|Research|Laboratory|Corporation", sg):
                            segs.pop(0)
                            dropped = True
                            continue
                        break
                    stripped0 = True
                    if dropped:
                        s = " | ".join(segs).strip()
                        if not s:
                            continue
                if re.match(r"(?i)^abstract\s*[:：]", s):
                    s = re.split(r"[:：]", s, maxsplit=1)[1].strip()   # 'Abstract: …' 标签剥离
                    if not s:
                        continue
                if skip_fold and _fold(s) == skip_fold:
                    continue   # 块内首行的题名复写
                if re.match(r"^-\s*[A-Z][A-Za-z .\-']{3,40}\s*-$", s):
                    continue   # 尾页罗马拼音作者行(如 '- Yoo Jae Choon -')
                if len(s) < 70 and re.match(r"^[A-Z]", s):
                    _parts = re.split(r"\s*·\s*|\s*,\s*|\s+and\s+", s)
                    if len(_parts) >= 2 and all(re.match(r"^[A-Z][A-Za-z .\-']+$", p.strip())
                                                for p in _parts if p.strip()):
                        continue   # 罗马化作者行('Kim, Jin-Kyoung · Cho, Young-Il')
                if KW_LINE.match(s):
                    stop = True
                    break
                if re.match(r"(?i)^abstract$", s):
                    stop = True   # 独行 Abstract 小标签(13625974 尾)
                    break
                if re.match(r"^\s*(참고\s*문헌|인용\s*문헌|参考文献|References|REFERENCES|Bibliography)\s*$",
                            s, re.I):
                    stop = True   # 文献区头(行级全等; 摘要内 '선행문헌' 类词不误触)
                    break
                out_lines.append(s)
            if take and u["uid"] not in uids:
                uids.append(u["uid"])
            if stop:
                return "\n".join(out_lines).strip(), uids
    return "\n".join(out_lines).strip(), uids

def abstract_doc(doc, out_root):
    """单篇③: 韩英双摘要定位(首页优先, 英文摘要缺则检索尾页文献区后残余区域——regress
    ref_region 遇 Abstract 截断已证明此形态存在)。尾页英文 matter 顺带供给 title_en 兜底与
    罗马化作者名。产出 metadata.json abstract/abstract_en/keywords。"""
    doc_dir = os.path.join(out_root, doc)
    udir = os.path.join(doc_dir, "unified")
    if not os.path.isdir(udir):
        print("跳过 %s: 无 unified 产物" % doc)
        return None
    pages = sorted(int(re.match(r"page_(\d+)\.json$", f).group(1))
                   for f in os.listdir(udir) if re.match(r"page_\d+\.json$", f))
    pdata = {p: json.load(io.open(os.path.join(udir, "page_%d.json" % p), encoding="utf-8"))
             for p in pages}
    umap = {u["uid"]: u for p in pages for u in pdata[p]["unified"]}

    def _find_head(pat, page_list):
        for p in page_list:
            for u in pdata[p]["unified"]:
                if u.get("cross_page", {}).get("role"):
                    continue
                t = (u["main_text"] or "").strip()
                if "\n" in t:
                    t = t.split("\n")[0].strip()
                if pat.match(t):
                    return u
        return None

    def _conf(u, uids):
        lv = "high"
        basis = []
        us = [umap[x] for x in (uids or []) if x in umap]
        if u is not None and u["review"]:
            lv = "mid" if u["confidence"]["level"] != "low" else "low"
            basis.append("头块级%s" % u["confidence"]["level"])
        rev = sum(1 for x in us if x["review"])
        if us and rev > len(us) / 2:
            lv = "mid" if lv == "high" else lv
            basis.append("正文块过半复检")
        return {"level": lv, "basis": "; ".join(basis) or "界限干净",
                "source": "regex", "uid_head": u and u["uid"]}

    res = {}
    # —— 韩摘要: 首页 ——
    h_ko = _find_head(ABS_HEAD_KO, [0])
    if h_ko is not None:
        # 韩摘限首页(首页摘要不跨页; 跨页续接会把正文卷进来——1892171 实况)
        txt, uids = _collect_abstract(pdata, [0], h_ko, max_pages=1)
        res["abstract"] = {"value": txt or None, "page": 1, "box": h_ko["box"],
                           "confidence": _conf(h_ko, uids)}
    else:
        # 内联头形态(895567 '요 앵 : …' 与 Key words 同块): 定位行后收集
        # 2026-11-10 增 markdown 加粗头形态(13563525 '**국문요약** 본고는…' 同块实证,
        # 无冒号): 剥头取余行; 该形态正文同块收全, 不跨页续接(防吞次页正文)。
        found = None
        bold = False
        for u in pdata[0]["unified"]:
            if u.get("cross_page", {}).get("role"):
                continue
            if u["type"] in ("Page-header", "Page-footer", "Page-number"):
                continue
            lns = (u["main_text"] or "").split("\n")
            for i, ln in enumerate(lns):
                if ABS_INLINE_KO.match(ln.strip()):
                    found = (u, i)
                    break
                if _ABS_BOLD_KO.match(ln.strip()):
                    found = (u, i)
                    bold = True
                    break
            if found:
                break
        if found:
            blk, li = found
            if bold:
                rest = _ABS_BOLD_KO.sub("", (blk["main_text"] or "").split("\n")[li]).strip()
                lines = [rest] if rest else []
                basis = u"markdown 加粗 요약 头同块"
            else:
                first_rest = re.split(r"[:：]", (blk["main_text"] or "").split("\n")[li], maxsplit=1)
                lines = [first_rest[1].strip()] if len(first_rest) > 1 and first_rest[1].strip() else []
                basis = u"内联 요약 头(与Key words同块)"
            for ln in (blk["main_text"] or "").split("\n")[li + 1:]:
                s = ln.strip()
                if not s:
                    continue
                if KW_LINE.match(s) or ABS_INLINE_KO.match(s) or _ABS_BOLD_KO.match(s):
                    break
                lines.append(s)
            if not bold:
                stop = False
                for p in range(1, min(3, len(pages))):
                    for u in pdata[p]["unified"]:
                        if u.get("cross_page", {}).get("role"):
                            continue
                        if u["type"] in ("Page-header", "Page-footer", "Page-number", "Footnote"):
                            continue
                        if u["type"] == "Section-header":
                            stop = True
                            break
                        for ln in (u["main_text"] or "").split("\n"):
                            if KW_LINE.match(ln.strip()) or ABS_INLINE_KO.match(ln.strip()):
                                stop = True
                                break
                            lines.append(ln.strip())
                        if stop:
                            break
                    if stop:
                        break
            res["abstract"] = {"value": "\n".join(l for l in lines if l).strip() or None,
                               "page": 1, "box": blk["box"],
                               "confidence": {"level": "mid", "basis": basis,
                                              "source": "regex", "uid_head": blk["uid"]}}
        else:
            res["abstract"] = {"value": None, "confidence": _conf(None, [])}
    # —— 英摘要: 首页 → 全文档倒序检索(英文摘要可能位于文末参考文献区之后/之内——
    #    regress ref_region 遇 Abstract 截断已证明此形态; 1892171 '< ABSTRACT >' 在文献区尾) ——
    h_en = _find_head(ABS_HEAD_EN, [0])
    tail = None
    if h_en is None:
        h_en = _find_head(ABS_HEAD_EN, list(reversed(pages[1:])))
        if h_en is not None:
            tail = h_en["page"]
    # —— 英摘要取值: 首页 Abstract 头 → 英题下无头形态(作者行下取至 Keywords) ——
    meta0 = json.load(io.open(os.path.join(doc_dir, "metadata.json"), encoding="utf-8"))         if os.path.exists(os.path.join(doc_dir, "metadata.json")) else {}
    ten_fold = _fold(((meta0.get("title_en") or {}).get("value") or ""))
    # 尾页英文题名预探(h_en 之下的首个英文节头/长英文行): 其折叠值供收集时跳过题名复写
    if h_en is not None and h_en["page"] - 1 > 0:
        hp = h_en["page"] - 1
        for u in sorted(pdata[hp]["unified"], key=lambda x: x["box"][1]):
            if u.get("cross_page", {}).get("role"):
                continue
            if u["box"][1] <= h_en["box"][3]:
                continue
            if u["type"] not in ("Section-header", "Text"):
                continue
            first = (u["main_text"] or "").split("\n")[0].strip()
            if len(_fold(first)) >= 15 and _hangul_ratio(first) < 0.2 \
               and not KW_LINE.match(first) and not ABS_HEAD_EN.match(first):
                ten_fold = ten_fold or _fold(first)
                break
    if h_en is not None:
        txt, uids = _collect_abstract(pdata, pages, h_en, skip_fold=ten_fold or None)
        res["abstract_en"] = {"value": txt or None, "page": h_en["page"], "box": h_en["box"],
                              "from_tail": bool(tail),
                              "confidence": _conf(h_en, uids)}
    else:
        ten = (meta0.get("title_en") or {})
        if (ten.get("page") or 1) == 1 and ten.get("uid") in umap:
            blk = umap[ten["uid"]]
            lines = []
            started = False
            stop = False
            for p in range(0, min(3, len(pages))):
                for u in pdata[p]["unified"]:
                    if u.get("cross_page", {}).get("role"):
                        continue
                    if u["type"] in ("Page-header", "Page-footer", "Page-number"):
                        continue
                    if p == 0 and u["box"][1] < blk["box"][3] - 4 and u["uid"] != blk["uid"]:
                        continue
                    seg = (u["main_text"] or "").split(chr(10))
                    for ln in seg:
                        s = ln.strip()
                        if not started:
                            if ABSTRACT_START.match(s):
                                started = True
                                if re.match(r"(?i)^abstract\s*[:：]", s):
                                    s = re.split(r"[:：]", s, maxsplit=1)[1].strip()
                                    if not s:
                                        continue
                            else:
                                continue
                        if KW_LINE.match(s):
                            stop = True
                            break
                        if re.match(r"(?i)^abstract$", s):
                            stop = True
                            break
                        if u["type"] != "Footnote":
                            lines.append(s)
                    if stop:
                        break
                if stop:
                    break
            txt = chr(10).join(l for l in lines if l).strip()
            if txt:
                res["abstract_en"] = {"value": txt, "page": 1, "box": blk["box"],
                                      "from_tail": False,
                                      "confidence": {"level": "mid",
                                                     "basis": "英题下无 Abstract 头, 作者行下取至 Keywords",
                                                     "source": "regex", "uid_head": blk["uid"]}}
    if "abstract_en" not in res:
        res["abstract_en"] = {"value": None, "from_tail": False, "confidence": _conf(None, [])}
    # —— 尾页英文 matter 顺带: title_en 兜底 + 罗马化作者 ——
    meta_fp = os.path.join(doc_dir, "metadata.json")
    meta = json.load(io.open(meta_fp, encoding="utf-8")) if os.path.exists(meta_fp) else {}
    tail_title_en = None
    tail_roman = []
    if h_en is not None and h_en["page"] - 1 > 0:
        hp = h_en["page"] - 1
        below = [u for u in pdata[hp]["unified"]
                 if not u.get("cross_page", {}).get("role")
                 and u["box"][1] > h_en["box"][3]
                 and u["type"] in ("Section-header", "Text")]
        below.sort(key=lambda u: u["box"][1])
        for u in below[:3]:
            t = (u["main_text"] or "").strip()
            if not t:
                continue
            first = t.split("\n")[0].strip()
            if tail_title_en is None and (u["type"] == "Section-header" or _hangul_ratio(first) < 0.2) \
               and len(_fold(first)) >= 15 and not KW_LINE.match(first) \
               and not ABS_HEAD_EN.match(first):
                tail_title_en = {"value": first, "page": hp + 1, "box": u["box"], "uid": u["uid"]}
            for ln in t.split("\n"):
                m = re.match(r"^-\s*([A-Z][A-Za-z .\-']{3,40})\s*-$", ln.strip())
                if m:
                    tail_roman.append(m.group(1).strip())
    # keywords 行(顺带)
    kws = []
    for p in pages:
        if p > 2 and not res["abstract_en"].get("from_tail"):
            break
        for u in pdata[p]["unified"]:
            for ln in (u["main_text"] or "").split("\n"):
                if KW_LINE.match(ln.strip()):
                    kws.append(ln.strip())
    res["keywords_lines"] = kws[:4]
    # 成品清洗: 剥粘入的 running 脚/URL 行(1892171 韩摘尾 'www.kci.go.kr' 实证)
    doc_heads = _doc_heads(pdata, pages)
    for k in ("abstract", "abstract_en"):
        v = (res[k] or {}).get("value")
        if v:
            cleaned, _ = _clean_lines(v, (), doc_heads)
            res[k]["value"] = cleaned.strip() or v
    # §38 分层: confidence.basis → audit; 产品 confidence 只留 {level, source, uid_head}
    abs_audit = {}
    for k in ("abstract", "abstract_en"):
        c = (res[k] or {}).get("confidence") or {}
        abs_audit[k] = {"basis": c.get("basis")}
        res[k]["confidence"] = {"level": c.get("level"), "source": c.get("source"),
                                "uid_head": c.get("uid_head")}
    meta.pop("tail_english_matter", None)   # 遗留诊断键清理(§38 已迁 audit)
    meta.update({"doc": doc,
                 "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                 "abstract": res["abstract"], "abstract_en": res["abstract_en"],
                 "keywords_lines": res["keywords_lines"]})
    if tail_title_en is not None and not (meta.get("title_en") or {}).get("value"):
        meta.setdefault("title_en", {})
        meta["title_en"].update({"value": tail_title_en["value"], "page": tail_title_en["page"],
                                 "box": tail_title_en["box"], "uid": tail_title_en["uid"],
                                 "confidence": {"level": "mid", "source": "tail_matter"}})
        if tail_roman and meta.get("authors"):
            ko_authors = [a for a in meta["authors"] if a.get("name")]
            if len(tail_roman) == len(ko_authors):
                for a, r in zip(ko_authors, tail_roman):
                    a["roman"] = a.get("roman") or r
        abs_audit["title_en"] = {"basis": "尾页英文 matter 供给"}
        abs_audit["tail_english_matter"] = {"title_en": tail_title_en, "roman_authors": tail_roman}
    io.open(meta_fp, "w", encoding="utf-8").write(json.dumps(meta, ensure_ascii=False, indent=1))
    audit = load_audit(doc_dir)
    audit.update(dict({"doc": doc}, **abs_audit))
    save_audit(doc_dir, audit)
    print("== ③ %s ==" % doc)
    for k in ("abstract", "abstract_en"):
        v = res[k]["value"]
        print("  %-11s: p%-3s from_tail=%s len=%-5s %s" % (
            k, res[k].get("page", "-"), res[k].get("from_tail", "-"),
            len(v or ""), (v or "(无)")[:60].replace("\n", " ")))
    if tail_title_en:
        print("  tail title_en: %s" % tail_title_en["value"][:64])
    if tail_roman:
        print("  tail roman   : %s" % "; ".join(tail_roman))
    return meta


def _selftest():
    """离线冒烟: 摘要边界与英题下无头路径; 只写临时目录。"""
    import hashlib
    import shutil
    import tempfile

    head = {"uid": "h", "page": 1, "type": "Section-header", "box": [0, 10, 100, 20],
            "main_text": "Abstract", "cross_page": {}}
    body = {"uid": "b", "page": 1, "type": "Text", "box": [0, 30, 100, 50],
            "main_text": "This study is complete.", "cross_page": {}}
    kw = {"uid": "k", "page": 1, "type": "Text", "box": [0, 60, 100, 80],
          "main_text": "Keywords: alpha, beta", "cross_page": {}}
    text, uids = _collect_abstract({0: {"w": 1000, "h": 1000, "unified": [head, body, kw]}}, [0], head,
                                   max_pages=1)
    ok1 = text == "This study is complete." and uids == ["b", "k"]

    tmp = tempfile.mkdtemp()
    try:
        doc_dir = os.path.join(tmp, "d")
        udir = os.path.join(doc_dir, "unified")
        os.makedirs(udir)
        title = {"uid": "t", "page": 1, "type": "Section-header", "box": [0, 100, 500, 140],
                 "main_text": "English Title", "cross_page": {}}
        block = {"uid": "b", "page": 1, "type": "Text", "box": [0, 160, 500, 220],
                 "main_text": "Abstract: This study answers.\nKeywords: alpha, beta",
                 "cross_page": {}}
        page = {"w": 1000, "h": 1000, "unified": [title, block]}
        page_fp = os.path.join(udir, "page_0.json")
        io.open(page_fp, "w", encoding="utf-8").write(json.dumps(page, ensure_ascii=False))
        before = hashlib.sha256(io.open(page_fp, "rb").read()).hexdigest()
        io.open(os.path.join(doc_dir, "metadata.json"), "w", encoding="utf-8").write(json.dumps(
            {"doc": "d", "title_en": {"value": "English Title", "page": 1, "uid": "t"}},
            ensure_ascii=False))
        meta = abstract_doc("d", tmp)
        audit = load_audit(doc_dir)
        ok2 = (meta["abstract_en"]["value"] == "This study answers."
               and meta["abstract_en"]["confidence"]["uid_head"] == "t"
               and meta["keywords_lines"] == ["Keywords: alpha, beta"]
               and audit["abstract_en"]["basis"] == "英题下无 Abstract 头, 作者行下取至 Keywords"
               and hashlib.sha256(io.open(page_fp, "rb").read()).hexdigest() == before)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # 3) 双栏尾页(13563525 实证): 左栏文献区头/小节头不截停不混入; 英文题名复写头整块跳过;
    #    首行 '|' 作者/机构前缀剥除
    head2 = {"uid": "h2", "page": 10, "type": "Section-header", "box": [700, 179, 1100, 209],
             "main_text": "Abstract", "cross_page": {}}
    refhead = {"uid": "r", "page": 10, "type": "Section-header", "box": [60, 161, 620, 191],
               "main_text": u"참고문헌", "cross_page": {}}
    subhead = {"uid": "s", "page": 10, "type": "Section-header", "box": [60, 214, 620, 244],
               "main_text": u"1. 단행본 및 논문", "cross_page": {}}
    ten = {"uid": "tn", "page": 10, "type": "Section-header", "box": [700, 216, 1100, 246],
           "main_text": "A Glocal Cultural History of Bakery", "cross_page": {}}
    abody = {"uid": "ab", "page": 10, "type": "Text", "box": [700, 314, 1100, 500],
             "main_text": "Shin, Seong-Hwan | Chung-Ang University | This study reinterprets "
                          "the cultural history.\nSecond line of abstract.",
             "cross_page": {}}
    refbody = {"uid": "rb", "page": 10, "type": "Text", "box": [60, 250, 620, 400],
               "main_text": u"김성수, 「두 개의」, 2013.", "cross_page": {}}
    refbody2 = {"uid": "rb2", "page": 10, "type": "Text", "box": [60, 410, 620, 500],
                "main_text": u"김은혜, 「새만금」, 2012.", "cross_page": {}}
    refbody3 = {"uid": "rb3", "page": 10, "type": "Text", "box": [60, 510, 620, 600],
                "main_text": u"문승렬, 「장수」, 2018.", "cross_page": {}}
    atext2 = {"uid": "at2", "page": 10, "type": "Text", "box": [700, 510, 1100, 620],
              "main_text": "Third line.", "cross_page": {}}
    pdata2 = {9: {"w": 1241, "h": 1754,
                  "unified": [refhead, head2, subhead, ten, abody, atext2, refbody, refbody2, refbody3]}}
    text2, uids2 = _collect_abstract(pdata2, list(range(10)), head2, skip_fold=_fold(
        "A Glocal Cultural History of Bakery"))
    ok3 = text2 == ("This study reinterprets the cultural history.\nSecond line of abstract."
                    "\nThird line.") and uids2 == ["ab", "at2"]
    # 4) 韩文 markdown 加粗头同块(13563525 '**국문요약** 본고는…'): 剥头取余行, 遇 핵심어 止
    mb = {"uid": "mk", "page": 1, "type": "Text", "box": [10, 300, 900, 500],
          "main_text": u"**국문요약** 본고는 군산 이성당의 80년을 통해 외래의 표준이 지역의 "
                       u"현실과 만나 정착하는 양상을 추적했다.\n핵심어\t 이성당, 군산",
          "cross_page": {}}
    found_line = _ABS_BOLD_KO.match(u"**국문요약** 본고는")
    ok4 = bool(found_line) and \
        _ABS_BOLD_KO.sub("", u"**국문요약** 본고는 군산").strip() == u"본고는 군산" and \
        not _ABS_BOLD_KO.match(u"**관련법령** 본문")
    for i, ok in enumerate((ok1, ok2, ok3, ok4), 1):
        if not ok:
            print("FAIL [abs case %d]" % i)
    print("abs selftest: %d/4 PASS" % (ok1 + ok2 + ok3 + ok4))
    return ok1 and ok2 and ok3 and ok4


if __name__ == "__main__":
    raise SystemExit(0 if _selftest() else 1)
