# -*- coding: utf-8 -*-
import os, re, io, json, datetime, collections, html
import pipeline
from pipeline import _gdice, _fold_sym
import regress
import unify
from .common import *


def _collect_abstract(pdata, pages, head_u, max_pages=3, skip_fold=None):
    """从头块之下收集摘要正文(跨页续接, 遇 Keywords 行/新节头/文献区 即止)。
    跨栏块按行取: 若块首在头上方, 只取 ABSTRACT_START 之后的行(13625974 作者行同块形态)。
    skip_fold: 折叠全等的节头视为英文题名的尾页复写(1892171/1632041 形态), 跳过不停。"""
    out_lines = []
    uids = []
    start_p = head_u["page"] - 1
    H = pdata[start_p]["h"]
    limit_bottom = head_u["box"][3] + 0.62 * H
    for p in range(start_p, min(len(pages), start_p + max_pages)):
        j = pdata[p]
        for u in j["unified"]:
            if u.get("cross_page", {}).get("role") == "head":
                continue
            if u["type"] in ("Page-header", "Page-footer", "Page-number", "Footnote"):
                continue
            if u["type"] == "Section-header" and u["uid"] != head_u["uid"] \
                    and u["box"][1] >= head_u["box"][3] - 2 \
                    and not (skip_fold and _fold((u["main_text"] or "").split("\n")[0]) == skip_fold):
                return "\n".join(out_lines).strip(), uids   # 区内新节头=界(先判停, 其行不入摘要)
            if u["type"] == "Section-header" and regress.REF_HEAD.search(u["main_text"] or ""):
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
                if re.match(r"(?i)^abstract\s*[:：]", s):
                    s = re.split(r"[:：]", s, 1)[1].strip()   # 'Abstract: …' 标签剥离
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
                if re.match(u"^\\s*(참고\\s*문헌|인용\\s*문헌|参考文献|References|REFERENCES|Bibliography)\\s*$",
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
        found = None
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
            if found:
                break
        if found:
            blk, li = found
            first_rest = re.split(u"[:：]", (blk["main_text"] or "").split("\n")[li], 1)
            lines = [first_rest[1].strip()] if len(first_rest) > 1 and first_rest[1].strip() else []
            stop = False
            for ln in (blk["main_text"] or "").split("\n")[li + 1:]:
                s = ln.strip()
                if not s:
                    continue
                if KW_LINE.match(s) or ABS_INLINE_KO.match(s):
                    stop = True
                    break
                lines.append(s)
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
                               "confidence": {"level": "mid", "basis": "内联 요약 头(与Key words同块)",
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
                    if p == 0 and u["uid"] == blk["uid"]:
                        seg = (u["main_text"] or "").split(chr(10))
                    else:
                        seg = (u["main_text"] or "").split(chr(10))
                    for ln in seg:
                        s = ln.strip()
                        if not started:
                            if ABSTRACT_START.match(s):
                                started = True
                                if re.match(r"(?i)^abstract\s*[:：]", s):
                                    s = re.split(r"[:：]", s, 1)[1].strip()
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
    meta.update({"doc": doc,
                 "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                 "abstract": res["abstract"], "abstract_en": res["abstract_en"],
                 "keywords_lines": res["keywords_lines"]})
    if tail_title_en is not None and not (meta.get("title_en") or {}).get("value"):
        meta.setdefault("title_en", {})
        meta["title_en"].update({"value": tail_title_en["value"], "page": tail_title_en["page"],
                                 "box": tail_title_en["box"], "uid": tail_title_en["uid"],
                                 "confidence": {"level": "mid", "basis": "尾页英文 matter 供给", "source": "tail_matter"}})
        meta["tail_english_matter"] = {"title_en": tail_title_en, "roman_authors": tail_roman}
        if tail_roman and meta.get("authors"):
            ko_authors = [a for a in meta["authors"] if a.get("name")]
            if len(tail_roman) == len(ko_authors):
                for a, r in zip(ko_authors, tail_roman):
                    a["roman"] = a.get("roman") or r
    io.open(meta_fp, "w", encoding="utf-8").write(json.dumps(meta, ensure_ascii=False, indent=1))
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
