# -*- coding: utf-8 -*-
import os, re, io, json, datetime
import regress
from .common import *
from . import langid as _langid

# markdown 加粗 요약 头(13563525 '**국문요약** 본고는…' 同块, 无冒号——ABS_INLINE_KO 要求冒号,
# ABS_HEAD_KO 只认独立节头, 两不沾则整段丢失)
_ABS_BOLD_KO = re.compile(u"^\\*{1,2}\\s*(요\\s*약|국문\\s*요약|초\\s*록)\\s*\\*{1,2}\\s*[:：]?")
# plain 요약 头(13563528 '국문요약  본 연구는…' 同块: 无星号无冒号——主读法选中不带星号的渠道
# 读法时 BOLD 形态够不着; main_text 选边翻转即丢摘要, 2026-11-12 实证)
_ABS_PLAIN_KO = re.compile(u"^(?:·?국문\\s*요약|요\\s*약|초\\s*록)\\s+(?=\\S)")
# 括号 요약 头(FI2983265 '【한글요약】' 独立行后接正文)
_ABS_BRACKET_KO = re.compile(u"^【\\s*(?:한글\\s*요약|국문\\s*요약|요\\s*약|초\\s*록)\\s*】\\s*[:：]?")
# 全篇/三渠道摘要头(2026-11-12 体例全覆盖): 老式韩文期刊把 <국문초록> / (국문초록) 放在
# 文末(FI1632041 p31), SpringerOpen 把 Abstract 单独成块, unified 归并时这些低频块会被
# 并入邻块或整块丢弃, 而 merged.json 的三渠道原文仍在。只在首页/既有形态都没抓到时启用。
_ABS_ANY = re.compile(
    u"^\\s*(?:[*#■◆]{0,2}\\s*)?[<(【〈\\[]?\\s*"
    u"(?:국문\\s*초록|국문\\s*요약|한글\\s*요약|요\\s*약|초\\s*록|摘\\s*要|Abstract|ABSTRACT|Summary|SUMMARY)"
    u"\\s*[>)】〉\\]]?\\s*[:：.]?\\s*(.*)$", re.I)
_ABS_STOP = re.compile(u"^(?:참고\\s*문헌|Reference|References|Bibliography|목\\s*차|차\\s*례|"
                       u"I{1,3}\\.|^\\d+\\.\\s)", re.I)


_LATIN_ABS = re.compile(u"(?i)^[*#\\s]*(Résumé|Resumo|Аннотация|Abstract)\\b[.\\-—:：*]*\\s*(.*)$")
_LATIN_KW = re.compile(u"(?i)^[*#\\s]*(Mots[- ]cl[ée]s|Palavras[-–]chave|Ключевые\\s+слова|"
                       u"Key[-\\s]*words?|Descritores)\\b[.\\-—:：]*\\s*(.*)$")


def _abs_trim(rest):
    rest = re.sub(r"(?i)^Revue\s+[^,\n]+,\s*[\d/-]+,\s*(?:19|20)\d{2},\s*\d+\s+", "", rest.strip())
    rest = re.split(u"(?im)^\\s*(?:Bibliographie|Bibliográficas|Referências|References|Список\\s+литературы)\\b", rest)[0]
    rest = re.split(u"(?im)^\\s*(?:Abstract|Résumé|Resumo|Аннотация)\\b", rest)[0]
    rest = re.split(u"(?m)(?<=\n)\\s*(?:Introduction|Introdução|Notas introdutórias|Введение|КОНТЕКСТ)\\b", rest)[0]
    return re.sub(r"^[:：]\s*", "", rest.strip())


def _latin_abs_res(pdata, pages):
    res = {"abstract": {"value": None, "confidence": {"level": "low", "source": "regex"}},
           "abstract_en": {"value": None, "from_tail": False,
                           "confidence": {"level": "low", "source": "regex"}},
           "keywords": [], "keywords_en": [], "keywords_lines": []}
    scan_pages = list(dict.fromkeys(list(pages[:3]) + list(pages[-5:])))
    flat = [(p, u) for p in scan_pages for u in pdata[p]["unified"]]
    for bi, (p, u) in enumerate(flat):
        if u.get("type") not in ("Text", "Section-header"):
            continue
        lines = (u.get("main_text") or "").splitlines()
        for i, ln in enumerate(lines):
            m = _LATIN_ABS.match(ln.strip())
            if not m:
                continue
            label, rest = m.group(1).lower(), m.group(2).strip()
            if not rest and i + 1 < len(lines):
                rest = "\n".join(lines[i + 1:]).strip()
            elif rest and len(rest) < 50 and i + 1 < len(lines):
                rest = "\n".join([rest] + lines[i + 1:]).strip()
            if not rest and i > 0:
                rest = max((x.strip() for x in lines[:i]), key=len, default="")
            if len(rest) < 60:
                for _p2, u2 in flat[bi + 1:]:
                    if u2.get("type") != "Text":
                        continue
                    t2 = (u2.get("main_text") or "").strip()
                    if not t2 or _LATIN_ABS.match(t2.splitlines()[0].strip()):
                        break
                    if _LATIN_KW.match(t2.splitlines()[0].strip()) \
                            or (t2.upper() == t2 and re.search(r"\s+[–—]\s+", t2)):
                        continue
                    rest = t2
                    break
            rest = _abs_trim(rest)
            if len(rest) < 60:
                continue
            key = "abstract_en" if label == "abstract" else "abstract"
            if res[key]["value"]:
                continue
            res[key] = {"value": rest, "page": p + 1, "box": u["box"],
                        "from_tail": p not in pages[:3],
                        "confidence": {"level": "mid", "source": "regex", "uid_head": u["uid"]}}
    if not res["abstract"]["value"]:
        for u in pdata[0]["unified"]:
            for ln in (u.get("main_text") or "").splitlines():
                if re.match(u"(?i)(?:В\\s+статье|Статья\\s+посвящена)\\b", ln.strip()):
                    rest = re.split(u"(?i)Ключевые\\s+слова", ln.strip())[0].strip()
                    if len(rest) >= 60:
                        res["abstract"] = {"value": rest, "page": 1, "box": u["box"],
                                           "confidence": {"level": "mid", "source": "regex",
                                                          "uid_head": u["uid"]}}
                        break
            if res["abstract"]["value"]:
                break
    for bi, (p, u) in enumerate(flat):
        lines = (u.get("main_text") or "").splitlines()
        for i, ln in enumerate(lines):
                m = _LATIN_KW.match(ln.strip())
                if not m:
                    continue
                label, rest = m.group(1).lower(), m.group(2).strip()
                if not rest and i + 1 < len(lines):
                    rest = lines[i + 1].strip()
                if not rest:
                    for _p2, u2 in flat[bi + 1:]:
                        t2 = (u2.get("main_text") or "").strip()
                        if not t2 or _LATIN_ABS.match(t2.splitlines()[0].strip()):
                            break
                        if _LATIN_KW.match(t2.splitlines()[0].strip()):
                            continue
                        rest = t2.splitlines()[0].strip()
                        break
                raw = label + ": " + rest
                rest = SUP_LATEX.sub("", rest).lstrip(" :")
                items = [re.sub(r"^[\d\s$.]+|[\s$.]+$", "", x).strip(" .-")
                         for x in re.split(r"[;,]\s*|\s+[-–—]\s+|\.\s+", rest) if x.strip(" .-")]
                items = [re.sub(r"(?i)([a-zà-ÿ]{6,})-(tion|sion|ment|alization|alisation)", r"\1\2", x)
                         for x in items]
                items = [x for x in items if 1 < len(x) < 60 and not x.lower().startswith(("key", "palavras", "mots", "descritores"))]
                if not items:
                    continue
                if label.startswith("key"):
                    if not res["keywords_en"]:
                        res["keywords_en"] = items
                elif not res["keywords"]:
                    res["keywords"] = items
                if len(res["keywords_lines"]) < 4:
                    res["keywords_lines"].append(raw)
    if not res["keywords"] and res["abstract"]["value"]:
        for p in pages[:3]:
            for u in pdata[p]["unified"]:
                for ln in (u.get("main_text") or "").splitlines():
                    s = ln.strip().strip(" .")
                    if len(s) > 180 or s.count(",") < 2 or s.upper() != s or not re.search(r"[A-ZÀ-Ý]", s):
                        continue
                    items = [x.strip(" .-") for x in s.split(",") if x.strip(" .-")]
                    if all(1 < len(x) < 60 for x in items):
                        res["keywords"] = items
                        res["keywords_lines"].append(s)
                        break
                if res["keywords"]:
                    break
            if res["keywords"]:
                break
    return res


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

    def _fin():
        # 尾页形态(13563529): 题名复写/题名续段带作者行可能压在 Abstract 头下, 正文从
        # ABSTRACT_START 行才开始——裁掉起始非正文行; 无起始行形态时原样保留(不回归旧形态)
        lines = out_lines
        if skip_fold and lines:
            for i, s in enumerate(lines):
                if ABSTRACT_START.match(s):
                    if i:
                        lines = lines[i:]
                    break
        return "\n".join(lines).strip(), uids

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
                f0 = _fold((u["main_text"] or "").split("\n")[0])
                if skip_fold and len(f0) >= 15 and \
                        (skip_fold.startswith(f0) or f0.startswith(skip_fold)
                         or _gdice(f0, skip_fold) >= 0.9):   # dice 容差: 渠道大小写差(Seon/seon)曾致误停
                    continue   # 英文题名复写头(可能断行只带前段, 13563529 u9_5 实证): 整块跳过
                return _fin()   # 区内新节头=界(先判停, 其行不入摘要)
            if u["type"] == "Section-header" and regress.REF_HEAD.search(u["main_text"] or "") \
                    and u["box"][1] >= head_u["box"][3] - 2:
                return _fin()
            y1, y3 = u["box"][1], u["box"][3]
            lines = (u["main_text"] or "").split("\n")
            take = []
            if p == start_p and u["uid"] == head_u["uid"]:
                take = lines[1:]   # 头在块内首行(13563529 u0_6 '국문요약\n본 연구는…'): 取头行之后
            elif p == start_p and y1 < head_u["box"][3] - 2 and y3 > head_u["box"][3]:
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
                if skip_fold and (_fold(s) == skip_fold
                                 or (len(_fold(s)) >= 15 and _gdice(_fold(s), skip_fold) >= 0.9)):
                    continue   # 块内首行的题名复写(含渠道读法大小写差)
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
                return _fin()
    return _fin()

def abstract_doc(doc, out_root):
    """单篇③: 韩英双摘要定位(首页优先, 英文摘要缺则检索尾页文献区后残余区域——regress
    ref_region 遇 Abstract 截断已证明此形态存在)。尾页英文 matter 顺带供给 title_en 兜底与
    罗马化作者名。产出 metadata.json abstract/abstract_en/keywords。"""
    doc_dir = os.path.join(out_root, doc)
    udir = os.path.join(doc_dir, "unified")
    if not os.path.isdir(udir):
        print("跳过 %s: 无 unified 产物" % doc)
        return None
    pages, pdata = read_unified(udir)
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
    meta_fp = os.path.join(doc_dir, "metadata.json")
    meta = json.load(io.open(meta_fp, encoding="utf-8"))
    _lang = _langid.classify_page(pdata[0])
    if _lang in ("ru", "pt", "fr"):
        res = _latin_abs_res(pdata, pages)
        _full_text = "\n".join((u.get("main_text") or "") for p in pages for u in pdata[p]["unified"])
        for _key in ("abstract", "abstract_en"):
            meta.setdefault(_key, dict(res[_key]))
            _old = (meta.get(_key) or {}).get("value") or ""
            if re.search(r"(?i)A\s+propos\s+de\s*:", _full_text) \
                    or re.match(r"(?i)Key[- ]words?|##\s*Keywords|Sociologias", _old) \
                    or len(re.findall(r"(?:19|20)\d{2}", _old)) >= 5:
                meta[_key]["value"] = None
            elif re.search(r"(?i)\bAbstract\.", _old) and not re.match(r"(?i)^Abstract\.", _old):
                meta[_key]["value"] = re.split(r"(?i)\bAbstract\.", _old)[-1].strip()
        for _key in ("abstract", "abstract_en"): 
            if res[_key]["value"]:
                meta[_key] = res[_key]
        for _key in ("keywords", "keywords_en", "keywords_lines"):
            meta.setdefault(_key, [])
            if res[_key]:
                meta[_key] = res[_key]
        # 拉丁文种分支同样适用"摘要不分语种"口径: 只有英文摘要时主 abstract 采用英文摘要
        # (13582983/13582999 实证; 与非拉丁分支下方的主摘要回退同语义)。
        if not ((meta.get("abstract") or {}).get("value")) \
                and ((meta.get("abstract_en") or {}).get("value")):
            _a = dict(meta["abstract_en"])
            _c = dict(_a.get("confidence") or {})
            _c["fallback_from"] = "abstract_en"
            _a["confidence"] = _c
            meta["abstract"] = _a
        meta["generated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        print("== ③ %s ==" % doc)
        print("  abstract   : %s" % ((meta["abstract"]["value"] or "")[:70]))
        print("  abstract_en: %s" % ((meta["abstract_en"]["value"] or "")[:70]))
        print("  keywords   : %d / %d" % (len(meta["keywords"]), len(meta["keywords_en"])))
        io.open(meta_fp, "w", encoding="utf-8").write(json.dumps(meta, ensure_ascii=False, indent=1))
        return meta
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
        # 2026-11-12 增 plain 头(13563528 主读法无星号)与括号头(FI2983265 '【한글요약】')两形态。
        found, kind = None, None
        for u in pdata[0]["unified"]:
            if u.get("cross_page", {}).get("role"):
                continue
            if u["type"] in ("Page-header", "Page-footer", "Page-number"):
                continue
            lns = (u["main_text"] or "").split("\n")
            for i, ln in enumerate(lns):
                s = ln.strip()
                if ABS_INLINE_KO.match(s):
                    found, kind = (u, i), "inline"
                    break
                if _ABS_BOLD_KO.match(s):
                    found, kind = (u, i), "bold"
                    break
                if _ABS_BRACKET_KO.match(s):
                    found, kind = (u, i), "bracket"
                    break
                if _ABS_PLAIN_KO.match(s) and \
                        len(_ABS_PLAIN_KO.sub("", s).strip()) >= 10:
                    found, kind = (u, i), "plain"
                    break
            if found:
                break
        if found:
            blk, li = found
            line0 = (blk["main_text"] or "").split("\n")[li]
            if kind in ("bold", "bracket", "plain"):
                pat = {"bold": _ABS_BOLD_KO, "bracket": _ABS_BRACKET_KO,
                       "plain": _ABS_PLAIN_KO}[kind]
                rest = pat.sub("", line0).strip()
                lines = [rest] if rest else []
                basis = {"bold": u"markdown 加粗 요약 头同块",
                         "bracket": u"括号 요약 头(【한글요약】)",
                         "plain": u"plain 요약 头同块(无星号无冒号)"}[kind]
            else:
                first_rest = re.split(r"[:：]", line0, maxsplit=1)
                lines = [first_rest[1].strip()] if len(first_rest) > 1 and first_rest[1].strip() else []
                basis = u"内联 요약 头(与Key words同块)"
            for ln in (blk["main_text"] or "").split("\n")[li + 1:]:
                s = ln.strip()
                if not s:
                    continue
                if KW_LINE.match(s) or ABS_INLINE_KO.match(s) or _ABS_BOLD_KO.match(s) \
                        or _ABS_BRACKET_KO.match(s):
                    break
                lines.append(s)
            if kind == "inline":   # 仅内联形态跨页续接; 同块形态收全即止(防吞次页正文)
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
            # 无头摘要(13563530 '본고는…' 紧跟作者区无任何标记): 取首个内容起始行块, 遇关键词/节头 止
            _op = re.compile(u"^(?:본\\s*연구|본고는|본\\s*논문|이\\s*연구|이\\s*논문)")
            lines, started, done, box0 = [], False, False, None
            for u in pdata[0]["unified"]:
                if done:
                    break
                if u.get("cross_page", {}).get("role"):
                    continue
                if u["type"] in ("Page-header", "Page-footer", "Page-number", "Footnote"):
                    continue
                if started and u["type"] == "Section-header":
                    break
                for ln in (u["main_text"] or "").split("\n"):
                    s = ln.strip()
                    if not s:
                        continue
                    if not started:
                        if _op.match(s):
                            started, box0 = True, u["box"]
                        else:
                            continue
                    if KW_LINE.match(s):
                        done = True
                        break
                    lines.append(s)
            res["abstract"] = {"value": "\n".join(lines).strip() or None,
                               "page": 1, "box": box0,
                               "confidence": {"level": "low", "basis": u"无头 요약(内容起始行)",
                                              "source": "regex", "uid_head": None}}
    # —— 体例兜底: 摘要头在渠道原文/末页。按渠道保序检索, 同块余行 + 后续块收正文,
    #     遇关键词/文献/节头即止; 只在本轮所有既有形态都未产出摘要时启用。
    if not (res.get("abstract") or {}).get("value"):
        streams = {}
        for p, ch, t in channel_lines(doc_dir):
            streams.setdefault(ch, []).append((p, t))
        best = None
        for ch, seq in streams.items():
            for idx, (p, t) in enumerate(seq):
                lns = t.split("\n")
                for li, ln in enumerate(lns):
                    mm = _ABS_ANY.match(ln.strip())
                    if not mm or len(ln.strip()) > 45:
                        continue
                    parts = []
                    body = mm.group(1).strip()
                    if len(body) >= 60:
                        parts.append(body)
                    for s2 in lns[li + 1:]:
                        s2 = s2.strip()
                        if not s2:
                            continue
                        if KW_LINE.match(s2) or _ABS_ANY.match(s2) or _ABS_STOP.match(s2):
                            break
                        parts.append(s2)
                    if sum(len(x) for x in parts) < 60:
                        for _p2, t2 in seq[idx + 1:idx + 7]:
                            s2 = t2.strip()
                            if not s2:
                                continue
                            head = s2.split("\n")[0].strip()
                            if KW_LINE.match(head) or _ABS_ANY.match(head) or _ABS_STOP.match(head):
                                break
                            parts.append(s2)
                            if sum(len(x) for x in parts) > 3000:
                                break
                    txt = "\n".join(parts).strip()
                    if len(txt) >= 60:
                        best = (txt, p, ch)
                        break
                if best:
                    break
            if best:
                break
        if best:
            res["abstract"] = {"value": best[0], "page": best[1], "box": None,
                               "confidence": {"level": "mid",
                                              "basis": u"三渠道摘要头(全篇检索:%s)" % best[2],
                                              "source": "channel_fallback", "uid_head": None}}
            res["_channel_fallback_abstract"] = {"channel": best[2], "page": best[1],
                                                 "head": best[0][:60]}
    # —— 英摘要: 首页 → 全文档倒序检索(英文摘要可能位于文末参考文献区之后/之内——
    #    regress ref_region 遇 Abstract 截断已证明此形态; 1892171 '< ABSTRACT >' 在文献区尾) ——
    h_en = _find_head(ABS_HEAD_EN, [0])
    tail = None
    if h_en is None:
        h_en = _find_head(ABS_HEAD_EN, list(reversed(pages[1:])))
        if h_en is not None:
            tail = h_en["page"]
    # —— 英摘要取值: 首页 Abstract 头 → 英题下无头形态(作者行下取至 Keywords) ——
    _meta_fp0 = os.path.join(doc_dir, "metadata.json")
    meta0 = json.load(io.open(_meta_fp0, encoding="utf-8")) if os.path.exists(_meta_fp0) else {}
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
    # —— 末页英文 matter 的另一种形态: 有英文题名节头但没有 'Abstract' 头 ——
    #    2309666_2017 实证: p34 'The Roles of …' + 'Kim, Ji Hwan | The Chinese …' 跨两页, 末页 Key Words。
    #    只在英文摘要仍为空时启用。
    if not (res.get("abstract_en") or {}).get("value"):
        for p in range(len(pages) - 1, max(len(pages) - 5, -1), -1):
            cand = [u for u in pdata[p]["unified"]
                    if u["type"] == "Section-header"
                    and not u.get("cross_page", {}).get("role")
                    and len(_fold(u["main_text"] or "")) >= 15
                    and _hangul_ratio(u["main_text"] or "") < 0.2
                    and not HEAD_EXCLUDE.match((u["main_text"] or "").strip())
                    and not KW_LINE.match((u["main_text"] or "").strip())]
            got = None
            for hb in cand:
                lines, uids, started, stop = [], [hb["uid"]], False, False
                for q in (p, p + 1):
                    if q >= len(pages):
                        break
                    for u in sorted(pdata[q]["unified"], key=lambda x: x["box"][1]):
                        if u.get("cross_page", {}).get("role"):
                            continue
                        if u["type"] in ("Page-header", "Page-footer", "Page-number"):
                            continue
                        if q == p and u["box"][1] < hb["box"][3] - 4:
                            continue
                        for ln in (u["main_text"] or "").split("\n"):
                            s = ln.strip()
                            if not s:
                                continue
                            if KW_LINE.match(s):
                                stop = True
                                break
                            if not started:
                                if _hangul_ratio(s) > 0.2 or len(_fold(s)) < 25:
                                    continue     # 跳过英文作者行/短行
                                if _fold(s) == _fold((hb["main_text"] or "").strip()):
                                    continue
                                started = True
                            if u["type"] != "Footnote":
                                lines.append(s)
                                uids.append(u["uid"])
                        if stop:
                            break
                    if stop:
                        break
                for _i0, _s0 in enumerate(lines):   # 起始行裁剪: 作者/题名行不进摘要(13563526 兜底路径实证)
                    if ABSTRACT_START.match(_s0):
                        if _i0:
                            lines = lines[_i0:]
                        break
                txt = "\n".join(lines).strip()
                if started and len(_fold(txt)) >= 120:
                    got = {"value": txt, "page": p + 1, "box": hb["box"], "from_tail": True,
                           "confidence": {"level": "mid",
                                          "basis": "末页英文题名节头 + 英文正文(至 Key Words)",
                                          "source": "regex", "uid_head": hb["uid"]}}
                    break
            if got:
                res["abstract_en"] = got
                break
    if not (res.get("abstract_en") or {}).get("value"):
        blocks = pdata[0]["unified"]
        for i, u in enumerate(blocks):
            t = (u.get("main_text") or "").strip()
            if not (t.startswith("[") and t.endswith("]") and "/" in t) or i == 0:
                continue
            prev = blocks[i - 1]
            txt = (prev.get("main_text") or "").strip()
            if len(txt) >= 200 and _hangul_ratio(txt) < 0.2:
                res["abstract_en"] = {"value": txt, "page": 1, "box": prev["box"],
                                      "from_tail": False,
                                      "confidence": {"level": "mid", "basis": "首页英文摘要+方括号双语关键词",
                                                     "source": "regex", "uid_head": prev["uid"]}}
                break
    # 主摘要回退: 只有英文摘要的论文(英文期刊/末页英文 matter), abstract 直接采用英文摘要——
    # 「摘要」这一必抽项的语义是"本文有无摘要", 不分语种。
    if not (res.get("abstract") or {}).get("value") \
            and (res.get("abstract_en") or {}).get("value"):
        _a = dict(res["abstract_en"])
        _c = dict(_a.get("confidence") or {})
        _c["fallback_from"] = "abstract_en"
        _a["confidence"] = _c
        res["abstract"] = _a
    if "abstract_en" not in res:
        res["abstract_en"] = {"value": None, "from_tail": False, "confidence": _conf(None, [])}
    # —— 尾页英文 matter 顺带: title_en 兜底 + 罗马化作者 ——
    meta_fp = _meta_fp0
    meta = meta0   # 2026-11-18: 与 362 行同函数重复读合并(中段无写盘, 两次读同值)
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
            if tail_title_en is None and u["type"] == "Section-header" \
               and len(_fold(first)) >= 15 and not KW_LINE.match(first) \
               and not ABS_HEAD_EN.match(first) \
               and not re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", _fold(first)):
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
            cleaned = _strip_supsub(cleaned)   # HTML 上下标剥壳(KCI HTML 系 PDF 自带)
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
                 "main_text": "Abstract: This study<sup>*</sup> answers.\nKeywords: alpha, beta",
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
        ok2 = (meta["abstract_en"]["value"] == "This study* answers."
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
    # 5) plain 요약 头(13563528 '국문요약  본 연구는…' 无星号无冒号): 剥头取余行;
    #    '요약 하자'类短内容误伤由调用侧 ≥10 字闸拦(正则层不拦)
    ok5 = bool(_ABS_PLAIN_KO.match(u"국문요약  본 연구는 대전")) and \
        _ABS_PLAIN_KO.sub("", u"국문요약  본 연구는 대전").strip() == u"본 연구는 대전"
    for i, ok in enumerate((ok1, ok2, ok3, ok4, ok5), 1):
        if not ok:
            print("FAIL [abs case %d]" % i)
    print("abs selftest: %d/5 PASS" % (ok1 + ok2 + ok3 + ok4 + ok5))
    return ok1 and ok2 and ok3 and ok4 and ok5


if __name__ == "__main__":
    raise SystemExit(0 if _selftest() else 1)
