# -*- coding: utf-8 -*-
import os, re, io, json, datetime, collections, html
import pipeline
from pipeline import _gdice, _fold_sym
import regress
import unify
from .common import *


def _annotate_join(tail_u, head_u, W, H, in_ref, line_h, how, stexts=(), doc_heads=(),
                   head_stexts=()):
    """执行拼接: 以尾成员重建组跑 make_unified(尾侧共识/置信/缺陷检测), 主读法=尾主读法+sep+页首主读法
    (页首块不入组, 避免 make_unified 换行拼接重复); 尾成员先逐渠道去重(渠道内重复识读缺陷)+剔页码页脚
    running 行+剥粘入段; 页首主读法同样过 running 行清洗; 拼后 dice 重算, 文献区重过 T1。"""
    head_main, _ = _clean_lines(head_u["main_text"], head_stexts, doc_heads)
    if not head_main.strip():
        return None, ""   # 页首块清洗后为空(running 脚误标等) → 不拼
    head_fold = _fold(head_main)
    g = {}
    for ch, units in tail_u["members"].items():
        seen, kept = set(), []
        for m in units:
            mm = dict(m)
            t, _ = _strip_glued(mm.get("text") or "", head_fold)
            t, _ = _clean_lines(t, stexts, doc_heads)
            mm["text"] = t
            f = _fold(t)
            if f and f in seen:
                continue
            seen.add(f)
            kept.append(mm)
        g[ch] = kept
    new_u = _rebuild(g, tail_u["page"], W, H, in_ref, tail_u["uid"], line_h)
    sep = _join_sep(new_u["main_text"], head_main)
    joined = (new_u["main_text"] or "").rstrip() + sep + head_main.lstrip()
    new_u["main_text"] = joined
    new_u["members"] = {ch: [dict(m) for m in units] for ch, units in g.items()}
    for ch, units in head_u["members"].items():
        new_u["members"].setdefault(ch, []).extend(dict(m) for m in units)
    new_u["box"] = [min(new_u["box"][0], head_u["box"][0]), min(new_u["box"][1], head_u["box"][1]),
                    max(new_u["box"][2], head_u["box"][2]), max(new_u["box"][3], head_u["box"][3])]
    for r in new_u["readings"]:
        ht = next((rr.get("text") or "" for rr in head_u["readings"] if rr["ch"] == r["ch"]), "")
        ht, _ = _clean_lines(ht, head_stexts, doc_heads)
        r["text"] = (r.get("text") or "").rstrip() + _join_sep(r.get("text") or "", ht) + ht.lstrip()
        r["dice"] = round(_gdice(r["text"], joined), 3) if r["text"].strip() else (1.0 if not joined.strip() else 0.0)
        r.setdefault("flags", []).append("跨页拼接(M3)")
    new_u["cross_page"] = {"spans": True, "pages": [tail_u["page"], head_u["page"]],
                           "role": "tail", "joined": [tail_u["uid"], head_u["uid"]],
                           "how": how, "last_col": _col_of(head_u, W)}
    if in_ref:
        ents = regress.split_entries(joined)
        new_u["cross_page"]["entry_flags"] = regress.check_entry(ents[-1], new_u["box"], H) if ents else []
    head_u["cross_page"] = {"spans": True, "pages": [tail_u["page"], head_u["page"]],
                            "role": "head", "merged_into": tail_u["uid"]}
    head_u["divergence"] = {"kind": "none", "detail": "跨页块首段, 已并入 %s" % tail_u["uid"]}
    head_u["confidence"] = {"level": new_u["confidence"]["level"],
                            "basis": "跨页成员, 置信随 %s" % tail_u["uid"]}
    head_u["review"] = False
    return new_u, sep

def _channel_tail_states(tail_u, stexts, doc_heads=(), glue_fold=None):
    out = {}
    for r in tail_u["readings"]:
        t = r.get("text") or ""
        if glue_fold:
            t, _ = _strip_glued(t, glue_fold)
        t, _ = _clean_lines(t, stexts, doc_heads)
        out[r["ch"]] = t
    return out

def _terminal_ok(ch_tails):
    n = len(ch_tails)
    inc = sum(1 for t in ch_tails.values() if t and not _complete(t))
    return inc >= max(2, (n + 1) // 2) or (n == 1 and inc == 1)

def _signals(tail_clean, head_u, ch_tails, glued=None):
    """返回 (terminal_ok, continuity_ok, detail)。continuity= 粘入证据|边界dice|片段头渠道一致。"""
    terminal_ok = _terminal_ok(ch_tails)
    head = (head_u["main_text"] or "").strip()
    hf = _fold(head)
    if glued and len(hf) >= 6 and hf[:48] in _fold(glued):
        return terminal_ok, True, "粘入证据"
    bd = _gdice(tail_clean[-24:], head[:24]) if (tail_clean.strip() and head) else 0.0
    if bd >= BOUND_DICE:
        return terminal_ok, True, "边界dice=%.2f" % bd
    ok = _frag_ok(tail_clean, head_u)
    return terminal_ok, terminal_ok and ok, "片段头(一致%d/NEW=%s/语位=%s)" % (
        _head_agree(head_u), bool(NEW_START.match(head)), _lang_mismatch(tail_clean, head))

def _try_join(tail_u, head_u, t_cleaned, p_ctx, how_kind, glued=None):
    """信号全过才拼; 返回 (new_u|None, detail)。"""
    ch_tails = _channel_tail_states(tail_u, p_ctx["stexts"], p_ctx["doc_heads"],
                                    _fold(glued) if glued else None)
    t_ok, c_ok, detail = _signals(t_cleaned, head_u, ch_tails, glued)
    if not (t_ok and c_ok):
        return None, detail
    if FOOTNOTE_CITE.match(t_cleaned) and not p_ctx["in_ref"] and not CITE_HINT.search(head_u["main_text"] or ""):
        return None, "脚注尾注不并散文头(非文献区)"
    how = "%s %s" % (how_kind, detail)
    return _annotate_join(tail_u, head_u, p_ctx["W"], p_ctx["H"], p_ctx["in_ref"],
                          p_ctx["line_h"], how, p_ctx["stexts"], p_ctx["doc_heads"],
                          p_ctx.get("head_stexts", ())) + (detail,)

def _tail_info(u, stexts, doc_heads=()):
    cleaned, dropped = _clean_lines(u["main_text"], stexts, doc_heads)
    if _standalone_note(cleaned):
        cleaned = cleaned + "." if not _complete(cleaned) else cleaned  # 独立注记视为完整
        complete = True
    else:
        complete = _complete(cleaned)
    return cleaned, complete, dropped

def m3_doc(doc, out_root):
    """单篇 M3: 写回 unified/page_N.json + _m3_xpage.json 日志; 返回验证指标。"""
    doc_dir = os.path.join(out_root, doc)
    udir = os.path.join(doc_dir, "unified")
    if not os.path.isdir(udir):
        print("跳过 %s: 无 unified 产物" % doc)
        return None
    pages = sorted(int(re.match(r"page_(\d+)\.json$", f).group(1))
                   for f in os.listdir(udir) if re.match(r"page_\d+\.json$", f))
    pdata = {p: json.load(io.open(os.path.join(udir, "page_%d.json" % p), encoding="utf-8"))
             for p in pages}

    def metrics():
        xp = rev = 0
        ref_t1 = 0
        for p in pages:
            j = pdata[p]
            for u in j["unified"]:
                if u.get("cross_page", {}).get("role") == "head":
                    continue
                if u["divergence"]["kind"] == "xpage" and u["review"]:
                    xp += 1
                if u["review"]:
                    rev += 1
                if j.get("in_ref") and u["type"] in CONTENT_TYPES and (u["main_text"] or "").strip():
                    for e in regress.split_entries(u["main_text"]):
                        if "T1" in regress.check_entry(e, u["box"], j["h"]):
                            ref_t1 += 1
        return {"xpage_eyes": xp, "review_eyes": rev, "ref_T1_entries": ref_t1}

    before = metrics()
    actions = []
    doc_heads = _doc_heads(pdata, pages)

    def act(**kw):
        actions.append(kw)

    for p in pages:
        j = pdata[p]
        W, H = j["w"], j["h"]
        line_h = _line_h(j["unified"])
        stexts = _struct_texts(j["unified"])
        in_ref = bool(j.get("in_ref"))
        cols = _col_blocks(j["unified"], W)
        cmax = max(cols.keys()) if cols else -1
        ctx = {"W": W, "H": H, "line_h": line_h, "stexts": stexts, "in_ref": in_ref,
               "doc_heads": doc_heads}
        consumed = set()

        # ---- 本页所有内容块的 xpage 状态盘点 ----
        infos = {}
        for u in _content_blocks(j["unified"], W):
            cleaned, complete, dropped = _tail_info(u, stexts, doc_heads)
            was_xpage = u["divergence"]["kind"] == "xpage" or any(
                "跨页并入" in r.get("flags", []) for r in u["readings"])
            infos[u["uid"]] = {"u": u, "cleaned": cleaned, "complete": complete,
                               "xpage": was_xpage, "dropped": dropped}

        # ---- 粘入段发现: 页脚/页码型块里的正文残余(正文型长度、无终止标点)。
        # 只认 Page-footer/Page-number: Page-header 是 running 头(完整名词短语, 无句点, 会误判为粘入段) ----
        glued = glued_from = None
        for u in j["unified"]:
            if u["type"] in ("Page-footer", "Page-number"):
                ct, _ = _clean_lines(u["main_text"], [], doc_heads)
                # 只保留正文性行(逐行非 running 头/脚): 页脚品牌行('• Police Science Institute'
                # 带页码/URL 粘连)整行剔除, 防止把页脚当"粘入的下页段首"
                body = [ln for ln in ct.split("\n") if ln.strip()
                        and not _is_running(ln, doc_heads)]
                ct = "\n".join(body)
                if len(_fold(ct)) >= MIN_GLUE_LEN and not _complete(ct) \
                        and not _is_running(ct, doc_heads):
                    glued, glued_from = ct, u["uid"]

        # ---- 1) 栏底无终止标点块: 先查栏内/本页次栏续接, 再跨页 ----
        for c in sorted(cols):
            us = sorted(cols[c], key=lambda x: x["box"][3])
            bottom = us[-1]
            info = infos[bottom["uid"]]
            if info["complete"] or bottom["uid"] in consumed:
                continue
            if not info["cleaned"] or _is_running(info["cleaned"], doc_heads):
                continue  # 清洗后空块/整段是 running 头脚(误标Text) → 非文本流尾
            col_bottom = bottom["box"][3]
            zone_soft = col_bottom >= max(x["box"][3] for x in us) - TAIL_SOFT_LINES * line_h
            if not zone_soft:
                continue
            paired = False
            if c < cmax and (c + 1) in cols:      # 本页次栏头(栏间断点)
                nxt = min(cols[c + 1], key=lambda x: x["box"][1])
                new_u = None
                if not _is_running(nxt["main_text"], doc_heads):
                    ch_t = _channel_tail_states(bottom, stexts, doc_heads)
                    if _terminal_ok(ch_t) and _frag_ok(info["cleaned"], nxt) and not (
                            FOOTNOTE_CITE.match(info["cleaned"]) and not in_ref
                            and not CITE_HINT.search(nxt["main_text"] or "")):
                        new_u, sep = _annotate_join(bottom, nxt, W, H, in_ref, line_h, "join_intra",
                                                    stexts, doc_heads, stexts)
                    if new_u:
                        j["unified"][j["unified"].index(bottom)] = new_u
                        consumed.add(nxt["uid"])
                        paired = True
                        act(type="join_intra", pages=[p + 1, p + 1], tail=bottom["uid"],
                            head=nxt["uid"], joined=new_u["main_text"][-80:])
            if not paired:                           # 跨页: 末栏尾→下页首栏头; 非末栏尾→下页同栏头
                if p + 1 not in pdata:
                    continue
                q = pdata[p + 1]
                q_cols = _col_blocks(q["unified"], q["w"])
                if not q_cols:
                    continue
                q_line = _line_h(q["unified"])
                ctx["head_stexts"] = _struct_texts(q["unified"])
                pref = [0] if c == cmax else [c]
                for d in pref:
                    if d not in q_cols:
                        continue
                    top = min(q_cols[d], key=lambda x: x["box"][1])
                    col_top = min(x["box"][1] for x in q_cols[d])
                    if top["box"][1] > col_top + HEAD_ZONE_LINES * q_line:
                        continue
                    if top["uid"] in consumed:
                        continue
                    if _is_running(top["main_text"], doc_heads):
                        detail = "页首块是running头/脚(误标Text), 不作续接头"
                        continue
                    r = _try_join(bottom, top, info["cleaned"], ctx, "join")
                    if r[0] is None:
                        detail = r[1]
                        continue
                    new_u, sep, detail = r
                    j["unified"][j["unified"].index(bottom)] = new_u
                    consumed.add(top["uid"])
                    paired = True
                    act(type="join", pages=[p + 1, p + 2], tail=bottom["uid"], head=top["uid"],
                        how=detail, tail_clean=info["cleaned"][-50:], head_txt=(top["main_text"] or "")[:50],
                        joined=new_u["main_text"][-90:],
                        entry_flags=new_u["cross_page"].get("entry_flags"))
                    break

        # ---- 2) 栏内中部无终止标点块(xpage 标记的): 试同栏下一近邻块 ----
        for u in list(_content_blocks(j["unified"], W)):
            if u["uid"] in consumed:
                continue
            info = infos[u["uid"]]
            if info["complete"] or not info["xpage"]:
                continue
            if not info["cleaned"] or _is_running(info["cleaned"], doc_heads):
                continue
            c = _col_of(u, W)
            below = [x for x in cols.get(c, []) if x["box"][1] >= u["box"][3] - 2
                     and x["uid"] not in consumed
                     and not _is_running(x["main_text"], doc_heads)]
            if not below:
                continue
            nxt = min(below, key=lambda x: x["box"][1])
            if nxt["box"][1] - u["box"][3] > CONT_GAP * line_h:
                continue
            ch_t = _channel_tail_states(u, stexts, doc_heads)
            if _terminal_ok(ch_t) and _frag_ok(info["cleaned"], nxt) and not (
                    FOOTNOTE_CITE.match(info["cleaned"]) and not in_ref
                    and not CITE_HINT.search(nxt["main_text"] or "")):
                new_u, sep = _annotate_join(u, nxt, W, H, in_ref, line_h, "join_intra",
                                            stexts, doc_heads, stexts)
                if new_u is None:
                    continue
                j["unified"][j["unified"].index(u)] = new_u
                consumed.add(nxt["uid"])
                act(type="join_intra", pages=[p + 1, p + 1], tail=u["uid"], head=nxt["uid"],
                    joined=new_u["main_text"][-80:])

        # ---- 3) 链式续接: 拼后仍无终止标点的跨页块, 按末段栏位再续一程(如 u11_5→u11_6→u12_2) ----
        if p + 1 in pdata:
            q = pdata[p + 1]
            q_cols = _col_blocks(q["unified"], q["w"])
            if q_cols:
                q_line = _line_h(q["unified"])
                ctx["head_stexts"] = _struct_texts(q["unified"])
                cmax_q = max(_col_blocks(j["unified"], W).keys() or [0])
                for u in list(j["unified"]):
                    cp = u.get("cross_page") or {}
                    if cp.get("role") != "tail" or cp.get("chained"):
                        continue
                    cp["chained"] = True
                    cleaned, complete, _ = _tail_info(u, stexts, doc_heads)
                    if complete or _is_running(cleaned, doc_heads):
                        continue
                    lc = cp.get("last_col", cmax_q)
                    pref = [0] if lc >= cmax_q else [lc]
                    for d in pref:
                        if d not in q_cols:
                            continue
                        top = min(q_cols[d], key=lambda x: x["box"][1])
                        col_top = min(x["box"][1] for x in q_cols[d])
                        if top["box"][1] > col_top + HEAD_ZONE_LINES * q_line:
                            continue
                        if top["uid"] in consumed or _is_running(top["main_text"], doc_heads):
                            continue
                        r = _try_join(u, top, cleaned, ctx, "join_chain")
                        if r[0] is None:
                            continue
                        new_u, sep, detail = r
                        new_u["cross_page"]["pages"] = [u["page"], top["page"]]
                        new_u["cross_page"]["chained"] = True
                        new_u["cross_page"]["how"] += "; chain " + detail
                        j["unified"][j["unified"].index(u)] = new_u
                        consumed.add(top["uid"])
                        act(type="join_chain", pages=[p + 1, p + 2], tail=u["uid"],
                            head=top["uid"], how=detail, joined=new_u["main_text"][-90:],
                            entry_flags=new_u["cross_page"].get("entry_flags"))
                        break

        # ---- 3) 粘入段处置: 回填下页空块, 或并入下页片段头 ----
        if glued and glued_from and p + 1 in pdata:
            q = pdata[p + 1]
            q_cols = _col_blocks(q["unified"], q["w"])
            tgt = None
            if q_cols:
                d0 = min(q_cols)
                for x in sorted(q_cols[d0], key=lambda x: x["box"][1]):
                    if not (x["main_text"] or "").strip() and x["uid"] not in consumed:
                        tgt = x
                        break
                    if (x["main_text"] or "").strip():
                        break
            mode = None
            if tgt is not None:
                tgt["main_text"] = glued
                for r in tgt["readings"]:
                    r["text"] = glued + (("\n" + r["text"]) if (r.get("text") or "").strip() else "")
                    r["dice"] = 1.0
                    r.setdefault("flags", []).append("跨页回填(M3)")
                tgt["cross_page"] = {"spans": True, "pages": [p + 1, p + 2], "role": "head",
                                     "backfilled": True, "from": glued_from}
                tgt["divergence"] = {"kind": "none", "detail": "空块按上页粘入段首回填"}
                tgt["review"] = False
                consumed.add(tgt["uid"])
                mode = "backfill"
                act(type="backfill", pages=[p + 1, p + 2], src=glued_from, head=tgt["uid"], glued=glued[:70])
            else:
                cands = []
                if q_cols:
                    d0 = min(q_cols)
                    col_top = min(x["box"][1] for x in q_cols[d0])
                    for x in q_cols[d0]:
                        if x["uid"] in consumed or not (x["main_text"] or "").strip():
                            continue
                        if x["box"][1] <= col_top + HEAD_ZONE_LINES * _line_h(q["unified"]):
                            cands.append(x)
                for h in cands:
                    if _is_running(h["main_text"], doc_heads):
                        continue
                    if _frag_ok(glued, h):
                        h["main_text"] = glued.rstrip() + _join_sep(glued, h["main_text"]) + (h["main_text"] or "").lstrip()
                        for r in h["readings"]:
                            r["text"] = glued.rstrip() + _join_sep(glued, r.get("text")) + (r.get("text") or "").lstrip()
                            r["dice"] = round(_gdice(r["text"], h["main_text"]), 3) if r["text"].strip() else 0.0
                            r.setdefault("flags", []).append("跨页回填(M3)")
                        if q.get("in_ref"):
                            ents = regress.split_entries(h["main_text"])
                            _eflags = regress.check_entry(ents[-1], h["box"], q["h"]) if ents else []
                        else:
                            _eflags = None
                        h["cross_page"] = {"spans": True, "pages": [p + 1, p + 2], "role": "head",
                                           "backfilled": True, "from": glued_from}
                        if _eflags:
                            h["cross_page"]["entry_flags"] = _eflags
                        h["divergence"] = {"kind": "none",
                                           "detail": "粘入段首已前置并入本块(来自 %s)" % glued_from}
                        h["review"] = False
                        consumed.add(h["uid"])
                        mode = "prepend"
                        act(type="prepend", pages=[p + 1, p + 2], src=glued_from, head=h["uid"],
                            joined=h["main_text"][:90])
                        break
            if mode is not None:
                src = next(x for x in j["unified"] if x["uid"] == glued_from)
                keep = [l for l in src["main_text"].split("\n")
                        if _fold(l) in stexts or PGNO_LINE.match(l.strip()) or URL_LINE.match(l.strip())]
                src["main_text"] = "\n".join(keep)
                src["divergence"] = {"kind": "none", "detail": "粘入的下页段首已剥离至下页"}
                src["review"] = False
                src["cross_page"] = {"spans": False, "stripped": True}

        # ---- 4) verify: 经查证完整的 xpage 块(任意位置)消误标 ----
        for uid, info in infos.items():
            u = info["u"]
            if u["uid"] in consumed or not info["xpage"] or not info["complete"]:
                continue
            cur = next((x for x in j["unified"] if x["uid"] == uid), None)
            if cur is None or cur.get("cross_page", {}).get("role"):
                continue
            g = _group_from_members(cur, None)
            new_u = _rebuild(g, cur["page"], W, H, in_ref, uid, line_h)
            cur_kind = new_u["divergence"]["kind"]
            if cur_kind != "xpage":
                j["unified"][j["unified"].index(cur)] = new_u
                new_u["cross_page"] = {"spans": False, "verified_complete": True}
                act(type="verify", pages=[p + 1], uid=uid, level=new_u["confidence"]["level"])

    # 未处置留档
    for p in pages:
        for u in pdata[p]["unified"]:
            if u["type"] in CONTENT_TYPES and not u.get("cross_page", {}).get("role") \
                    and u["divergence"]["kind"] == "xpage" and u["review"]:
                act(type="left", pages=[p + 1], uid=u["uid"], tail=(u["main_text"] or "")[-50:])
    after = metrics()
    for p in pages:
        io.open(os.path.join(udir, "page_%d.json" % p), "w", encoding="utf-8").write(
            json.dumps(pdata[p], ensure_ascii=False))
    sfp = os.path.join(udir, "_unified_summary.json")
    if os.path.exists(sfp):
        s = json.load(io.open(sfp, encoding="utf-8"))
        levels = collections.Counter()
        kinds = collections.Counter()
        review = 0
        for p in pages:
            for u in pdata[p]["unified"]:
                levels[u["confidence"]["level"]] += 1
                kinds[u["divergence"]["kind"]] += 1
                review += 1 if u["review"] else 0
        s["levels"] = dict(levels)
        s["kinds"] = dict(kinds)
        s["review_eyes"] = review
        s["m3"] = {"before": before, "after": after,
                   "actions": dict(collections.Counter(a["type"] for a in actions)),
                   "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
        io.open(sfp, "w", encoding="utf-8").write(json.dumps(s, ensure_ascii=False, indent=1))
    log = {"doc": doc, "before": before, "after": after, "actions": actions}
    io.open(os.path.join(udir, "_m3_xpage.json"), "w", encoding="utf-8").write(
        json.dumps(log, ensure_ascii=False, indent=1))
    print("== M3 %s ==\n  xpage 红眼 %d->%d  复检眼 %d->%d  文献区T1条目 %d->%d\n  动作: %s" % (
        doc, before["xpage_eyes"], after["xpage_eyes"], before["review_eyes"],
        after["review_eyes"], before["ref_T1_entries"], after["ref_T1_entries"],
        ", ".join("%s×%d" % kv for kv in collections.Counter(a["type"] for a in actions).most_common())))
    return log
