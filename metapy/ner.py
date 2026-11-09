# -*- coding: utf-8 -*-
"""本文相关实体关系抽取(LLM 全文覆盖, 词典仅做归一/批注共享)。
- 遍历 unified/ 的非空 Text/Footnote 块，排除跨页 head、栏外竖条与 in_ref 文献表页；
  按块数+字符预算分批，长块不截断，正文只覆盖一次。
- funding/software/datasets 必须带合法 relation、evidence_span、needs_context；代码同时核对
  证据确在 uid 块内、实体名在证据内、relation 合法。仅名称出现不再够收。
- 仅 needs_context 候选携带前后块二次复核；仍不确定则 context_unresolved，不收。
- journals/standards/proceedings 保留既有字段与名称锚匹配口径。
- 人工标注: 实体的 verdict(ok/bad/doubt/null)/comment 就是 ner_llm.json 的两个字段, 审计页经
  webapp /api/ner_note 直接回写; 重跑抽取按 (kind, uid, 折叠主标识) 继承, 不丢;
- 全局批注词典(ner_glossary.json, 用户拍板): 键=(kind, name归一化[去全部空白+casefold]);
  给某条写批注时若词条不存在则新建(带来源 doc/uid), 已存在不更新(只进不改); 未单独批注的同名
  条目在审计页显示继承批注(整个装进蓝框), 不写实体; 个体批注只影响自己, 清空后
  回退到继承; verdict 不共享; 词典页 ner_glossary.html 只读+可删词条;
- 结果只写 out_llm_trial/, 不进 out/(out/ 只读); unify/m3 不动;
- 开关: channels.json llm 段 ner_enabled(默认 false), endpoint/model/key 沿用 llm 段(KCI_LLM_KEY)。
用法: python metadata.py <doc>... --stage ner;  python metadata.py <任意占位doc> --stage ner_audit
      (重建审计页+词典页);  自检: python -m metapy.ner(mock 不触网)。
"""
import os, re, io, json, datetime
from .common import _foldv, CONTENT_TYPES, _is_margin
from .llmrep import _llm_cfg, _json_objs
from .dual import trial_root

KINDS = ("funding", "software", "datasets", "journals", "standards", "proceedings")

# 核心字段：funding 至少 funder/grant_id 其一，其余类型要求 name；三类另带 relation/evidence_span。
# 2026-11-06: orgs 已删(用户拍板: 机构=作者署名机构, 归 front.affiliations, 正文机构不抽)。
KIND_FIELDS = {
    "funding": ("funder", "grant_id"),
    "software": ("name", "version"),
    "datasets": ("name", "url"),
    "journals": ("name", "url"),
    "standards": ("name", "code"),
    "proceedings": ("name", "url"),
}

RELATIONS = {
    "funding": {"funds_current_paper"},
    "software": {"used_in_current_paper", "developed_in_current_paper"},
    "datasets": {"used_in_current_paper", "created_in_current_paper"},
}

# system 规范每批一次；用户消息只含块头+原文。输出 {"blocks":[{uid,...}]} 保留 uid 绑定。
_SYS_PROMPT = (u"韩文/英文期刊实体关系抽取。只输出一个 JSON 对象 {\"blocks\":[...]}，不解释。\n"
               u"程序会覆盖全部 Text/Footnote 块，不能凭关键词自行缩小范围。Footnote 可含基金声明。\n"
               u"【三类核心任务】只收与当前论文有直接关系的实体，正文出现名称本身不构成收录理由：\n"
               u"- funding{funder,grant_id}：仅当前论文/当前研究受资助，relation=funds_current_paper。\n"
               u"- software{name,version}：当前研究实际使用或开发的软件/程序；relation=used_in_current_paper "
               u"或 developed_in_current_paper。量表、实验设备、算法、参考文献中的工具不收；办公软件只有实际承担"
               u"本研究数据处理才收。\n"
               u"- datasets{name,url}：当前研究实际使用或构建的数据集/数据库/语料库；"
               u"relation=used_in_current_paper 或 created_in_current_paper。引用图书、普通网页、页脚网址不收。\n"
               u"【其他既有类型】journals{name,url}=作为信息来源的期刊；standards{name,code}=正式法规标准；"
               u"proceedings{name,url}=会议/会议论文集。抽象概念和参考文献著录不收。\n"
               u"【三类证据契约】每项必须附 relation + evidence_span + needs_context。evidence_span 是包含实体名"
               u"并支持该关系的原文连续句/段，逐字摘自当前块，不翻译不改写。relation 必须取该 kind 的合法值。"
               u"关系证据不足但可能存在时 relation=null、needs_context=true，仍给原文证据；其余情况 needs_context=false。"
               u"没有直接关系的候选项不要输出。同块同实体只报一次。\n"
               u"【输出】按块给 uid 和非空类别数组；空块省略。三类示例：\n"
               u"原文『본 연구는 NRF의 지원(NRF-2023)을 받았다. 분석에는 R 4.2를 사용하였다.』\n"
               u"{\"blocks\":[{\"uid\":\"u1\",\"funding\":[{\"funder\":\"NRF\",\"grant_id\":\"NRF-2023\","
               u"\"relation\":\"funds_current_paper\",\"evidence_span\":\"본 연구는 NRF의 지원(NRF-2023)을 받았다.\","
               u"\"needs_context\":false}],\"software\":[{\"name\":\"R\",\"version\":\"4.2\","
               u"\"relation\":\"used_in_current_paper\",\"evidence_span\":\"분석에는 R 4.2를 사용하였다.\","
               u"\"needs_context\":false}]}]}\n"
               u"其他类型仍按各自字段输出，无需 relation/evidence_span。")

_REVIEW_PROMPT = _SYS_PROMPT + u"\n【二次上下文复核】本次输入含目标块前后块；重新判断候选与当前论文的关系，证据仍逐字取自实际所在块。"

# 用户消息每块只留块头+原文，规范只在 system 中每批一次。
_PROMPT_TMPL = (u"块 %s(第%s页) 类型=%s\n<<<\n%s\n>>>")


def collect_blocks(doc, out_root):
    """收集非空正文块: type∈{Text,Footnote}, main_text 非空, 排除跨页 head 角色块与栏外竖条,
    **排除 in_ref 文献表页**(该页块已被 refs 阶段解析进 metadata.references, 再送 LLM 纯属
    重复且引入出版者/刊名著录噪音)。返回 [{uid, page, type, text}], 按页码/文件顺序。"""
    udir = os.path.join(out_root, doc, "unified")
    blocks = []
    if not os.path.isdir(udir):
        return blocks
    pages = []
    for f in os.listdir(udir):
        mm = re.match(r"page_(\d+)\.json$", f)
        if mm:
            pages.append((int(mm.group(1)), f))
    for pno, f in sorted(pages):
        j = json.load(io.open(os.path.join(udir, f), encoding="utf-8"))
        if j.get("in_ref"):
            continue   # 文献表页: 已归 metadata.references, 不送 LLM
        W = j.get("w") or 0
        for u in j.get("unified") or []:
            if u.get("type") not in CONTENT_TYPES:
                continue
            if u.get("cross_page", {}).get("role"):
                continue
            if W and _is_margin(u, W):
                continue
            t = (u.get("main_text") or "").strip()
            if not t:
                continue
            blocks.append({"uid": u.get("uid"), "page": u.get("page") or pno,
                           "type": u.get("type"), "text": t})
    return blocks


def _norm_entity(kind, obj):
    if not isinstance(obj, dict):
        return None
    ent = {"kind": kind}
    n_core = 0
    for f in KIND_FIELDS[kind]:
        v = obj.get(f)
        if isinstance(v, str):
            v = v.strip() or None
        if v is not None:
            ent[f] = v
            n_core += 1
    if kind in RELATIONS:
        for f in ("relation", "evidence_span"):
            v = obj.get(f)
            ent[f] = v.strip() or None if isinstance(v, str) else None
        ent["needs_context"] = bool(obj.get("needs_context"))
    if kind == "funding":
        return ent if n_core >= 1 else None
    return ent if ent.get("name") else None


def _match_anchor(ent, folded_text):
    """journals/standards/proceedings 的名称锚匹配；三类核心任务改用 _match_evidence。"""
    for f in ("funder", "name", "grant_id", "code", "url", "version"):
        v = ent.get(f)
        if v and _foldv(str(v)) in folded_text:
            return str(v)
    return None


def _match_evidence(ent, text):
    evidence = ent.get("evidence_span")
    if not evidence:
        return None
    folded_evidence = _foldv(evidence)
    if not folded_evidence or folded_evidence not in _foldv(text):
        return None
    name = _primary_name(ent) or ent.get("grant_id") or ""
    if name and _foldv(name) not in folded_evidence:
        return None
    return evidence


def _primary_name(ent):
    """实体的主标识(funding 用 funder, 其余用 name)——标注继承键/词典键用。"""
    return ent.get("funder") or ent.get("name") or ""


# —— 全局批注词典(ner_glossary.json) ——
def norm_name(s):
    """词典 name 归一化(用户拍板): 去全部空白 + casefold(忽略大小写), kind 由键分开。"""
    return re.sub(r"\s+", "", s or "").casefold()


def glossary_key(kind, name):
    return u"%s%s" % (kind, norm_name(name))


def load_glossary(out_root):
    fp = os.path.join(trial_root(out_root), "ner_glossary.json")
    if os.path.exists(fp):
        try:
            return json.load(io.open(fp, encoding="utf-8"))
        except Exception:
            pass
    return {"entries": []}


def save_glossary(out_root, g):
    fp = os.path.join(trial_root(out_root), "ner_glossary.json")
    d = os.path.dirname(fp)
    if not os.path.isdir(d):
        os.makedirs(d)
    io.open(fp, "w", encoding="utf-8").write(json.dumps(g, ensure_ascii=False, indent=1))
    return fp


def glossary_add(out_root, kind, name, comment, doc=None, uid=None):
    """词条只进不改(用户拍板): 同 (kind, name_norm) 已存在→返回现有,不更新; 否则新建并落盘。
    返回 (词条dict, 是否新建)。"""
    if kind not in KINDS or not (name or "").strip() or not (comment or "").strip():
        return None, False
    g = load_glossary(out_root)
    nk = norm_name(name)
    for e in g.get("entries") or []:
        if e.get("kind") == kind and e.get("name_norm") == nk:
            return e, False
    ent = {"kind": kind, "name": name.strip(), "name_norm": nk, "comment": comment,
           "source": {"doc": doc, "uid": uid},
           "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
    g.setdefault("entries", []).append(ent)
    save_glossary(out_root, g)
    return ent, True


def _collect_objs(objs):
    """把模型返回的顶层对象展平成 [(六类对象, 模型自称uid|None)]。兼容两种形状:
    ① prompt v2 {"blocks":[{uid,funding:...,...}, ...]} → 每个 blocks 元素一项;
    ② 旧/兜底 平铺 {"funding":...,...} → (obj, None)(归属时全批按字段找块)。
    也容忍 blocks 里缺某类的对象(下面按 KIND 逐个 get 取, None 跳过)。
    uid 用于归属；错报或缺失时由代码按证据/名称在当前批重新定位。"""
    out = []
    for o in objs:
        if not isinstance(o, dict):
            continue
        bl = o.get("blocks")
        if isinstance(bl, list):
            for b in bl:
                if isinstance(b, dict):
                    out.append((b, (b.get("uid") or None)))
            continue
        if any(k in o for k in KINDS):
            out.append((o, (o.get("uid") or None)))
    return out


def _batch_blocks(blocks, cfg):
    per = max(1, int(cfg.get("max_fields_per_call") or 8))
    max_chars = max(1000, int(cfg.get("ner_max_chars_per_call") or 12000))
    batches, current, size = [], [], 0
    for block in blocks:
        cost = len(_PROMPT_TMPL % (block["uid"], block["page"], block["type"], block["text"]))
        if current and (len(current) >= per or size + cost > max_chars):
            batches.append(current)
            current, size = [], 0
        current.append(block)
        size += cost
    if current:
        batches.append(current)
    return batches


def _request_batch(batch, cfg, opener, review=False):
    import urllib.request
    body = json.dumps({"model": cfg.get("model"),
                       "temperature": float(cfg.get("temperature") or 0),
                       "messages": [{"role": "system",
                                     "content": _REVIEW_PROMPT if review else _SYS_PROMPT},
                                    {"role": "user",
                                     "content": "\n\n".join(
                                         _PROMPT_TMPL % (b["uid"], b["page"], b["type"], b["text"])
                                         for b in batch)}]}).encode("utf-8")
    req = urllib.request.Request(cfg["endpoint"], data=body, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + os.environ.get(cfg.get("api_key_env") or "KCI_LLM_KEY", "")})
    try:
        op = opener or urllib.request.urlopen
        with op(req, timeout=120) as resp:
            out = json.loads(resp.read().decode("utf-8"))
        choice = out["choices"][0]
        return _json_objs(choice["message"]["content"]), choice.get("finish_reason"), None
    except Exception as e:
        return [], None, str(e)[:120]


def ner_doc(doc, out_root, cfg=None, opener=None):
    """全文覆盖的关系抽取：分批不截块，三类须 relation+原文证据，仅不确定候选二次复核。"""
    if not os.path.isdir(os.path.join(out_root, doc, "unified")):
        print("跳过 ner %s: 无 unified/" % doc)
        return None
    cfg = cfg if cfg is not None else _llm_cfg()
    if not cfg.get("ner_enabled"):
        print("== ner %s == ner_enabled=false(跳过; 真调产物只许写 out_llm_trial/)" % doc)
        return None
    key = os.environ.get(cfg.get("api_key_env") or "KCI_LLM_KEY", "")
    if not key or not cfg.get("endpoint"):
        print("== ner %s == 缺 endpoint/api_key, 跳过" % doc)
        return None
    old_notes = {}
    fp = os.path.join(trial_root(out_root), doc, "ner_llm.json")
    if os.path.exists(fp):
        try:
            for e0 in json.load(io.open(fp, encoding="utf-8")).get("entities") or []:
                old_notes[(e0.get("kind"), e0.get("uid"),
                           _foldv(_primary_name(e0)))] = (e0.get("verdict"), e0.get("comment") or "")
        except Exception:
            pass
    blocks = collect_blocks(doc, out_root)
    entities, rejected, errors, pending = [], [], [], []
    n_acc = dict((k, 0) for k in KINDS)
    n_rej = dict((k, 0) for k in KINDS)
    called = 0

    def reject(kind, ent, reason, hit=None):
        n_rej[kind] += 1
        rejected.append({"kind": kind, "uid": (hit or {}).get("uid"),
                         "page": (hit or {}).get("page"), "reason": reason, "entity": ent})

    def process(objs, batch, allow_pending):
        bmap = dict((b["uid"], b) for b in batch)
        for obj, claimed in _collect_objs(objs):
            for kind in KINDS:
                lst = obj.get(kind)
                if lst is None:
                    continue
                if isinstance(lst, dict):
                    lst = [lst]
                if not isinstance(lst, list):
                    errors.append({"uid": None, "page": batch[0]["page"],
                                   "error": u"%s 返回类型非法(%s)" % (kind, type(lst).__name__)})
                    continue
                for raw in lst:
                    ent = _norm_entity(kind, raw)
                    if ent is None:
                        reject(kind, raw, "schema")
                        continue
                    hit, anchor = None, None
                    candidates = [bmap[claimed]] if claimed in bmap else batch
                    for b in candidates:
                        if kind in RELATIONS:
                            matched = _match_evidence(ent, b["text"])
                        else:
                            matched = _match_anchor(ent, _foldv(b["text"]))
                        if matched:
                            hit, anchor = b, matched
                            break
                    if hit is None:
                        reject(kind, ent, "evidence_not_found" if kind in RELATIONS else "name_not_found")
                        continue
                    if kind in RELATIONS and ent.get("needs_context"):
                        if allow_pending:
                            pending.append({"entity": ent, "uid": hit["uid"], "page": hit["page"]})
                        continue
                    if kind in RELATIONS and ent.get("relation") not in RELATIONS[kind]:
                        reject(kind, ent, "relation_invalid", hit)
                        continue
                    n_acc[kind] += 1
                    prev = old_notes.get((kind, hit["uid"], _foldv(_primary_name(ent))))
                    entities.append(dict(ent, uid=hit["uid"], page=hit["page"], matched=anchor,
                                         verdict=prev[0] if prev else None,
                                         comment=prev[1] if prev else ""))

    for batch in _batch_blocks(blocks, cfg):
        objs, fr, error = _request_batch(batch, cfg, opener)
        called += 1
        if error:
            for b in batch:
                errors.append({"uid": b["uid"], "page": b["page"], "error": error})
            continue
        if not objs:
            for b in batch:
                errors.append({"uid": b["uid"], "page": b["page"], "error": u"空响应"})
            continue
        if fr and fr != "stop":
            errors.append({"uid": None, "page": batch[0]["page"],
                           "error": u"finish_reason=%s, 本批 %d 块可能未答完" % (fr, len(batch))})
        process(objs, batch, True)
    index = dict((b["uid"], i) for i, b in enumerate(blocks))
    reviewed = set()
    for item in pending:
        i = index.get(item["uid"])
        if i is None or item["uid"] in reviewed:
            continue
        reviewed.add(item["uid"])
        batch = blocks[max(0, i - 1):i + 2]
        objs, fr, error = _request_batch(batch, cfg, opener, review=True)
        called += 1
        if error:
            errors.append({"uid": item["uid"], "page": item["page"], "error": error})
            continue
        if not objs:
            errors.append({"uid": item["uid"], "page": item["page"], "error": u"复核空响应"})
            continue
        if fr and fr != "stop":
            errors.append({"uid": item["uid"], "page": item["page"], "error": u"复核 finish_reason=%s" % fr})
        process(objs, batch, False)
    for item in pending:
        ent = item["entity"]
        found = any(e["kind"] == ent["kind"] and e.get("uid") == item["uid"]
                    and _foldv(_primary_name(e)) == _foldv(_primary_name(ent)) for e in entities)
        if not found:
            reject(ent["kind"], ent, "context_unresolved",
                   {"uid": item["uid"], "page": item["page"]})
    total = sum(n_acc.values()) + sum(n_rej.values())
    stats = {"blocks": len(blocks), "batches": called,
             "context_reviewed": len(reviewed),
             "accepted": dict(n_acc, total=sum(n_acc.values())),
             "rejected": dict(n_rej, total=sum(n_rej.values())),
             "errors": len(errors),
             "span_hit_rate": round(sum(n_acc.values()) / float(total), 4) if total else None}
    tdir = os.path.join(trial_root(out_root), doc)
    if not os.path.isdir(tdir):
        os.makedirs(tdir)
    payload = {"doc": doc,
               "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
               "model": cfg.get("model"), "endpoint": cfg.get("endpoint"),
               "stats": stats, "entities": entities,
               "rejected": rejected, "errors": errors}
    io.open(fp, "w", encoding="utf-8").write(json.dumps(payload, ensure_ascii=False, indent=1))
    print(u"== ner %s == 块 %d, 批 %d, 收 %d 条(%s), 拒 %d, 错 %d, "
          u"命中 %.1f%% -> %s" % (
              doc, len(blocks), called, sum(n_acc.values()),
              "/".join("%s%d" % (k[:4], n_acc[k]) for k in KINDS if n_acc[k]),
              sum(n_rej.values()), len(errors),
              (stats["span_hit_rate"] or 0) * 100, fp))
    return payload


# —— 人工审计页(ner_audit.html, 单文件零 fetch) ——
def _wsfold(s):
    return "".join(ch for ch in (s or "") if not ch.isspace())


def _locate_span(span, text):
    """空白不敏感定位 evidence_span 在源块原文中的位置, 返回 (start, end) 半开下标; 找不到给 None。
    纯去空白折叠(1:1 保位), 高亮用途——闸门判定以 _foldv 为准, 两者独立。"""
    fs, ft = _wsfold(span), _wsfold(text)
    if not fs or fs not in ft:
        return None
    i = ft.find(fs)
    start, n = None, -1
    for idx, ch in enumerate(text):
        if not ch.isspace():
            n += 1
            if n == i:
                start = idx
                break
    need, cnt, end = len(fs), 0, len(text)
    for idx in range(start, len(text)):
        if not text[idx].isspace():
            cnt += 1
            if cnt == need:
                end = idx + 1
                break
    return (start, end)


def build_audit_viewer(out_root, docs=None):
    """汇总 out_llm_trial/<doc>/ner_llm.json -> out_llm_trial/ner_audit.html。
    每篇一个抽屉; 实体卡=字段+命中锚+源块高亮+uid/page+对照查看器; 判定/个体批注回写 ner_llm.json
    (webapp /api/ner_note), 个体批注与内容不同的词典批注并显(均不写回对方);
    header 有词典页入口 ner_glossary.html。"""
    trial = trial_root(out_root)
    if docs is None:
        docs = sorted(os.listdir(trial)) if os.path.isdir(trial) else []
    rows, metas = [], []
    for doc in docs:
        fp = os.path.join(trial, doc, "ner_llm.json")
        if not os.path.exists(fp):
            continue
        d = json.load(io.open(fp, encoding="utf-8"))
        st = d.get("stats") or {}
        bmap = dict((b["uid"], b["text"]) for b in collect_blocks(doc, out_root))

        def _row(kind, e, idx=None, rejected=False, reason=None):
            e = e or {}
            src = bmap.get(e.get("uid") or "", "") if not rejected else ""
            anchor = e.get("matched") or e.get("funder") or e.get("name") or ""
            loc = _locate_span(anchor, src) if (src and anchor) else None
            parts = [src[:loc[0]], src[loc[0]:loc[1]], src[loc[1]:]] if loc else [src, "", ""]
            name = e.get("funder") or e.get("name") or e.get("grant_id") or ""
            fields = [[f, e[f]] for f in KIND_FIELDS.get(kind, ()) if e.get(f)]
            if e.get("relation"):
                fields.append(["relation", e["relation"]])
            rows.append({"doc": doc, "kind": kind, "name": name, "nk": norm_name(name),
                         "fields": fields,
                         "anchor": anchor,
                         "uid": e.get("uid"), "page": e.get("page"),
                         "parts": parts, "idx": idx,
                         "v": e.get("verdict") or "", "c": e.get("comment") or "",
                         "rejected": rejected, "reason": reason})

        for i, e in enumerate(d.get("entities") or []):
            _row(e.get("kind"), e, idx=i)
        for r in d.get("rejected") or []:
            _row(r.get("kind"), r.get("entity"), rejected=True, reason=r.get("reason"))
        metas.append({"doc": doc, "model": d.get("model"),
                      "acc": (st.get("accepted") or {}).get("total", 0),
                      "rej": (st.get("rejected") or {}).get("total", 0),
                      "hit": st.get("span_hit_rate")})
    if not metas:
        print("跳过 ner_audit: out_llm_trial 下无 ner_llm.json(先跑 --stage ner)")
        return None
    data = {"generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "metas": metas, "rows": rows,
            "glossary": (load_glossary(out_root).get("entries") or [])}
    html = _AUDIT_TMPL.replace("@@DATA@@", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    ofp = os.path.join(trial, "ner_audit.html")
    io.open(ofp, "w", encoding="utf-8").write(html)
    print(u"审计页 -> %s (实体 %d, 闸门拒收 %d)" % (
        ofp, sum(1 for r in rows if not r["rejected"]), sum(1 for r in rows if r["rejected"])))
    return ofp


def build_glossary_viewer(out_root):
    """全局批注词典页 -> out_llm_trial/ner_glossary.html(单文件, 内联快照+live fetch,
    经 webapp 打开可删词条; 双击文件打开只读)。"""
    trial = trial_root(out_root)
    data = {"generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "entries": (load_glossary(out_root).get("entries") or [])}
    html = (_GLOSSARY_TMPL
            .replace("@@DATA@@", json.dumps(data, ensure_ascii=False).replace("</", "<\\/")))
    ofp = os.path.join(trial, "ner_glossary.html")
    io.open(ofp, "w", encoding="utf-8").write(html)
    print(u"词典页 -> %s (词条 %d)" % (ofp, len(data["entries"])))
    return ofp


_AUDIT_TMPL = """<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>NER 人工审计</title>
<style>
body{font-family:"Microsoft YaHei",sans-serif;margin:0 0 60px;background:#f8fafc;color:#0f172a}
#topbar{position:sticky;top:0;z-index:9}
header{background:#0f172a;color:#fff;padding:10px 14px;font-size:13px;display:flex;flex-wrap:wrap;align-items:center;gap:4px 12px;justify-content:space-between}
header b{font-size:15px}.meta{color:#94a3b8;margin-left:10px}
.hright{display:flex;align-items:center;gap:10px;white-space:nowrap}
.glink{color:#fff;background:#2563eb;padding:2px 10px;border-radius:4px;text-decoration:none;font-size:12px;white-space:nowrap}
#bar{background:#e2e8f0;padding:8px 14px;font-size:13px;border-bottom:1px solid #cbd5e1}
#bar button,#bar select{font-size:13px;margin-right:6px;padding:2px 8px;border:1px solid #94a3b8;background:#fff;border-radius:4px;cursor:pointer}
#bar button.on{background:#2563eb;color:#fff;border-color:#2563eb}
#bar input{padding:2px 8px;font-size:13px;border:1px solid #94a3b8;border-radius:4px;width:220px}
.drawer{background:#fff;border:1px solid #cbd5e1;border-radius:8px;margin:10px 14px;overflow:hidden}
.dhead{padding:9px 12px;cursor:pointer;font-weight:bold;font-size:14px;user-select:none;background:#eef2f7}
.drawer.open .dhead{border-bottom:1px solid #e2e8f0}
.dhead:hover{background:#e2e8f0}
.dhead .dm{color:#64748b;font-weight:normal;font-size:12px;margin-left:10px}
.dhead .arrow{float:right;color:#64748b;font-size:13px}
.dbody{padding:4px 4px 8px}
.dbody .eb{margin:8px 10px}
.eb{background:#fff;border:1px solid #cbd5e1;border-left:5px solid #94a3b8;border-radius:6px;margin:8px 14px;padding:8px 12px;font-size:13px}
.eb.k-funding{border-left-color:#2563eb}.eb.k-software{border-left-color:#d97706}.eb.k-datasets{border-left-color:#16a34a}
.eb.k-journals{border-left-color:#7c3aed}.eb.k-orgs{border-left-color:#0891b2}.eb.k-standards{border-left-color:#be185d}
.eb.k-proceedings{border-left-color:#65a30d}
.eb.v-ok{border-left-color:#16a34a;background:#f0fdf4}
.eb.v-bad{border-left-color:#dc2626;background:#fef2f2}
.eb.v-doubt{border-left-color:#d97706;background:#fffbeb}
.nm{font-weight:bold;font-size:14px}
.badge{display:inline-block;font-size:11px;padding:1px 7px;border-radius:9px;color:#fff;vertical-align:2px;margin-right:8px}
.b-funding{background:#2563eb}.b-software{background:#d97706}.b-datasets{background:#16a34a}
.b-journals{background:#7c3aed}.b-orgs{background:#0891b2}.b-standards{background:#be185d}.b-proceedings{background:#65a30d}
.rejtag{background:#dc2626}
.fld{color:#475569;margin-left:12px;font-size:12px}.fld b{color:#0f172a}
.vbtns{float:right}
.vbtns button{font-size:12px;margin-left:5px;padding:1px 8px;border:1px solid #94a3b8;background:#fff;border-radius:4px;cursor:pointer}
.vbtns button.on-ok{background:#16a34a;color:#fff;border-color:#16a34a}
.vbtns button.on-bad{background:#dc2626;color:#fff;border-color:#dc2626}
.vbtns button.on-doubt{background:#d97706;color:#fff;border-color:#d97706}
.src{background:#f1f5f9;border:1px dashed #cbd5e1;border-radius:5px;padding:6px 9px;margin-top:6px;font-size:12px;color:#334155;white-space:pre-wrap;word-break:break-all}
.src mark{background:#fde047;padding:0 1px}
.srcline{color:#64748b;font-size:11px;margin-top:4px}.srcline a{color:#2563eb}
.cmtbox{margin-top:6px}
.cmtbox textarea{width:100%;box-sizing:border-box;font-size:12px;font-family:inherit;padding:4px 6px;border:1px solid #94a3b8;border-radius:4px;min-height:44px}
.cmtbox .cb{margin:4px 6px 0 0;font-size:12px;padding:1px 10px;border:1px solid #94a3b8;background:#fff;border-radius:4px;cursor:pointer}
.cmtview{margin-top:6px;font-size:12px;color:#7c2d12;background:#fff7ed;border-left:3px solid #fb923c;padding:4px 8px;border-radius:0 4px 4px 0;white-space:pre-wrap;word-break:break-all}
.gsrcbox{margin-top:6px;font-size:12px;color:#1e40af;background:#eff6ff;border-left:3px solid #3b82f6;padding:4px 8px;border-radius:0 4px 4px 0;word-break:break-all}
.gsrcbox .gtxt{margin-top:3px;white-space:pre-wrap}
.grp{background:#fff;border:1px solid #cbd5e1;border-left:5px solid #94a3b8;border-radius:6px;margin:8px 10px;overflow:hidden}
.grp.k-funding{border-left-color:#2563eb}.grp.k-software{border-left-color:#d97706}.grp.k-datasets{border-left-color:#16a34a}
.grp.k-journals{border-left-color:#7c3aed}.grp.k-orgs{border-left-color:#0891b2}.grp.k-standards{border-left-color:#be185d}.grp.k-proceedings{border-left-color:#65a30d}
.grph{padding:7px 10px;background:#f8fafc;border-bottom:1px solid #eef2f7}
.grp .eb{margin:8px;border-left-width:3px}
.occ + .occ{border-top:1px dashed #e2e8f0}
.occline{padding:6px 10px;font-size:12px;color:#475569;cursor:pointer}
.occline:hover{background:#f1f5f9}
.vdot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;background:#cbd5e1}
.vdot-ok{background:#16a34a}.vdot-bad{background:#dc2626}.vdot-doubt{background:#d97706}
.gsum{font-size:11px;margin-left:8px;padding:0 5px;border-radius:8px}
.gsum-ok{background:#dcfce7;color:#166534}.gsum-bad{background:#fee2e2;color:#991b1b}.gsum-doubt{background:#fef3c7;color:#92400e}
.gcount{color:#64748b;font-weight:normal;font-size:12px;margin-left:10px}
#foot{position:fixed;bottom:0;left:0;right:0;background:#1e293b;color:#e2e8f0;padding:7px 14px;font-size:12px}
</style></head><body>
<div id="topbar">
<header><b>NER 人工审计</b><span class="meta" id="hmeta"></span><span class="meta" id="svstat"></span>
<span class="hright">已审 <b id="vd">0</b>/<span id="tt">0</span>
<button onclick="exportV()">导出标注 JSON</button>
<a class="glink" href="ner_glossary.html" target="_blank">&#128218; 词典</a></span></header>
<div id="bar">
<select id="fdoc" onchange="render()"><option value="">全部文档</option></select>
<button class="fk on" data-k="" onclick="setK(this)">全部类型</button>
<button class="fk" data-k="funding" onclick="setK(this)">基金</button>
<button class="fk" data-k="software" onclick="setK(this)">软件</button>
<button class="fk" data-k="datasets" onclick="setK(this)">数据集</button>
<button class="fk" data-k="journals" onclick="setK(this)">期刊</button>
<button class="fk" data-k="standards" onclick="setK(this)">法规标准</button>
<button class="fk" data-k="proceedings" onclick="setK(this)">会议集</button>
<button class="fv on" data-v="" onclick="setV(this)">全部状态</button>
<button class="fv" data-v="none" onclick="setV(this)">未审</button>
<button class="fv" data-v="ok" onclick="setV(this)">✓正确</button>
<button class="fv" data-v="bad" onclick="setV(this)">✗误抽</button>
<button class="fv" data-v="doubt" onclick="setV(this)">?存疑</button>
<input id="q" placeholder="搜名称/证据/源块/批注…" oninput="render()">
<button onclick="expandAll()">展开全部</button>
<button onclick="collapseAll()">收起全部</button>
</div>
</div>
<div id="list"></div>
<div id="foot">判定/批注直接写入 out_llm_trial/&lt;doc&gt;/ner_llm.json 实体的 verdict/comment 字段(经 webapp 打开 http://127.0.0.1:8765/trial/ner_audit.html); 首次批注的同名词条自动收录进 ner_glossary.json, 未单独批注的同名条目显示词典继承批注(不写实体)。双击文件打开则本次有效, 请点"导出标注 JSON"留档。对照查看器链接按 unified 块跳 1:3 原图版面。</div>
<script>
var D = @@DATA@@;
var FK="", FV="", Q="";
var verd = {};
var GLOS = D.glossary || [];
var openCmt = {};
var KINDL = {funding:"基金", software:"软件", datasets:"数据集", journals:"期刊",
             standards:"法规标准", proceedings:"会议集"};
function esc(s){ return (s==null?"":String(s)).replace(/[&<>"]/g, function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]}) }
function keyOf(i){ return D.rows[i].doc + "#" + i; }
function getV(i){ var d = verd[keyOf(i)]; return (d && d.v) || ""; }
function getC(i){ var d = verd[keyOf(i)]; return (d && d.c) || ""; }
function setStat(t){ var el = document.getElementById("svstat"); if (el) el.textContent = t; }
function glosFind(kind, nk){
  for (var i = 0; i < GLOS.length; i++){
    var e = GLOS[i];
    if (e.kind === kind && e.name_norm === nk) return e;
  }
  return null;
}
try {   // 经 webapp 打开: 拉最新词典(批注后可能已新建词条), 失败则用内联快照
  fetch("/api/ner_glossary").then(function(r){ return r.json(); }).then(function(j){
    if (j && j.entries){ GLOS = j.entries; render(); }
  }).catch(function(){});
} catch(e) {}
try {   // 标注 live 覆盖: 服务端 ner_llm.json 是真相源, 内联快照可能过期(保存后未重建页)
  fetch("/api/ner_annotations").then(function(r){ return r.json(); }).then(function(j){
    var ann = (j && j.ann) || {};
    D.rows.forEach(function(r, i){
      if (r.idx == null) return;
      var k = keyOf(i);
      var a = (ann[r.doc] || {})[String(r.idx)];
      if (a) verd[k] = {v: a.verdict || "", c: a.comment || ""};
      else if (verd[k]) delete verd[k];   // 服务端无此标注(已清空), 内联快照视为过期
    });
    render();
  }).catch(function(){});
} catch(e) {}
D.rows.forEach(function(r, i){ if (r.v || r.c) verd[keyOf(i)] = {v: r.v || "", c: r.c || ""}; });
document.getElementById("hmeta").textContent =
  "生成 " + D.generated + " ｜ 模型 " + ((D.metas[0]||{}).model||"-") +
  " ｜ 实体 " + D.rows.filter(function(r){return !r.rejected}).length +
  " ｜ 闸门拒收 " + D.rows.filter(function(r){return r.rejected}).length +
  " ｜ 词条 " + GLOS.length;
var sel = document.getElementById("fdoc");
D.metas.forEach(function(m){
  var n = D.rows.filter(function(r){return r.doc===m.doc && !r.rejected}).length;
  var o = document.createElement("option"); o.value = m.doc; o.textContent = m.doc + " (" + n + ")";
  sel.appendChild(o);
});
function setK(b){ FK=b.getAttribute("data-k");
  Array.prototype.forEach.call(document.querySelectorAll(".fk"), function(x){x.classList.toggle("on",x===b)}); render(); }
function setV(b){ FV=b.getAttribute("data-v");
  Array.prototype.forEach.call(document.querySelectorAll(".fv"), function(x){x.classList.toggle("on",x===b)}); render(); }
function setVerdict(i, v){
  var k = keyOf(i), cur = verd[k] || {v:"", c:""};
  if (cur.v === v) cur.v = ""; else cur.v = v;
  verd[k] = cur; persist(); render();
}
function toggleCmt(i){
  var k = keyOf(i);
  if (openCmt[k]) delete openCmt[k]; else openCmt[k] = true;
  render();
  if (openCmt[k]) { var t = document.getElementById("cmt-" + i); if (t) t.focus(); }
}
function saveCmt(i){
  var k = keyOf(i), t = document.getElementById("cmt-" + i);
  var cur = verd[k] || {v:"", c:""};
  cur.c = t ? t.value : "";
  verd[k] = cur; delete openCmt[k]; persist(); render();
}
function collectNotes(){
  var out = [];
  D.rows.forEach(function(r, i){
    if (r.idx == null) return;
    var k = keyOf(i), d = verd[k];
    var has = d && (d.v || d.c), had = r.v || r.c;
    if (!has && !had) return;   // 有值或曾有值才回写(后者用于清空)
    out.push({doc: r.doc, idx: r.idx, kind: r.kind, name: r.name, uid: r.uid,
              verdict: has ? (d.v || null) : null,
              comment: has ? (d.c || "") : ""});
  });
  return out;
}
function persist(){
  try {
    fetch("/api/ner_note", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({notes: collectNotes()})})
      .then(function(r){ return r.json(); })
      .then(function(j){
        if (j && j.ok){
          var t = "已写入 ner_llm.json (" + (j.docs != null ? j.docs : "?") + " 篇)";
          if (j.glossary_new) t += ", 新收录词条 " + j.glossary_new;
          setStat(t);
        } else setStat("写入失败");
      })
      .catch(function(){ setStat("未连服务, 标注仅本次有效, 请导出留档"); });
  } catch(e) { setStat("未连服务, 标注仅本次有效, 请导出留档"); }
}
var openDocs = {};
if (D.metas.length) openDocs[D.metas[0].doc] = true;   // 首个抽屉默认展开
function isOpenDoc(doc){ return openDocs[doc] || FK || FV || Q || sel.value; }  // 有过滤条件时自动展开
function toggleDoc(doc){ openDocs[doc] = !openDocs[doc]; render(); }
function expandAll(){ D.metas.forEach(function(m){ openDocs[m.doc] = true; }); render(); }
function collapseAll(){ openDocs = {}; render(); }
function cardHtml(i){
  var r = D.rows[i], v = getV(i);
  var h = '<div class="eb k-' + r.kind + (v ? ' v-' + v : '') + '">'
      + '<span class="vbtns">'
      + '<button class="'+(v==='ok'?'on-ok':'')+'" onclick="setVerdict('+i+',&#39;ok&#39;)">&#10003;正确</button>'
      + '<button class="'+(v==='bad'?'on-bad':'')+'" onclick="setVerdict('+i+',&#39;bad&#39;)">&#10007;误抽</button>'
      + '<button class="'+(v==='doubt'?'on-doubt':'')+'" onclick="setVerdict('+i+',&#39;doubt&#39;)">?存疑</button>'
      + '<button onclick="toggleCmt('+i+')">'+(getC(i)?'&#9998;改批注':'&#9998;批注')+'</button></span>'
      + '<span class="badge b-' + r.kind + (r.rejected?' rejtag':'') + '">'
      + (r.rejected ? '闸门拒收:' + esc(r.reason||'') : (KINDL[r.kind] || esc(r.kind))) + '</span>'
      + '<span class="nm">' + esc(r.name) + '</span>';
    (r.fields||[]).forEach(function(f){ h += '<span class="fld">' + esc(f[0]) + ': <b>' + esc(f[1]) + '</b></span>'; });
    if (r.anchor) h += '<div class="fld" style="margin:6px 0 0">命中: ' + esc(r.anchor) + '</div>';
    if (r.parts[0] || r.parts[1])
      h += '<div class="src">' + esc(r.parts[0]) + (r.parts[1] ? '<mark>' + esc(r.parts[1]) + '</mark>' : '') + esc(r.parts[2]) + '</div>';
    h += '<div class="srcline">uid ' + esc(r.uid) + ' · page ' + esc(r.page)
       + (r.page ? ' · <a href="../out/' + encodeURIComponent(r.doc) + '/viewer_compare.html#p' + (r.page-1) + '">对照查看器</a>' : '')
       + '</div>';
    var own = getC(i);
    var g = r.nk ? glosFind(r.kind, r.nk) : null;
    if (own && !openCmt[keyOf(i)])
      h += '<div class="cmtview">批注: ' + esc(own) + '</div>';
    if (g && g.comment !== own)   // 词典继承整个装进蓝框(来源标注+正文一体), 与个体橙条区分; 内容相同不重复
      h += '<div class="gsrcbox">&#128218; 词典继承<div class="gtxt">' + esc(g.comment) + '</div></div>';
    if (openCmt[keyOf(i)])
      h += '<div class="cmtbox"><textarea id="cmt-' + i + '" placeholder="写点备注, 如: 应归为 software / 版本号文中是 4.0 / 课题号另见脚注…">'
         + esc(own) + '</textarea>'
         + '<div><button class="cb" onclick="saveCmt('+i+')">保存批注</button>'
         + '<button class="cb" onclick="toggleCmt('+i+')">取消</button></div></div>';
    h += '</div>';
  return h;
}
var openOcc = {};
function glossOf(i){
  var r = D.rows[i];
  return (!getC(i) && r.nk) ? glosFind(r.kind, r.nk) : null;
}
function rowHay(i){
  var r = D.rows[i], g = glossOf(i);
  return (r.name + " " + r.anchor + " " + r.parts.join(" ") + " " + getC(i)
          + " " + (g ? g.comment : "")).toLowerCase();
}
function buildGroups(){
  // 组键=(篇,kind,归一名): 同篇同词合并为一张组卡展示; 判定/批注仍逐处个别(用户拍板), 组头只汇总
  var groups = [], gmap = {};
  D.rows.forEach(function(r, i){
    if (r.rejected){ groups.push({rejected: true, members: [i]}); return; }
    var key = r.doc + "|" + r.kind + "|" + r.nk;
    var g = gmap[key];
    if (!g){
      g = {doc: r.doc, kind: r.kind, name: r.name, nk: r.nk, fmap: {}, members: []};
      gmap[key] = g; groups.push(g);
    }
    g.members.push(i);
    (r.fields || []).forEach(function(f){ g.fmap[f[0]] = f[1]; });
  });
  return groups;
}
function groupMatches(g){
  var r0 = D.rows[g.members[0]];
  if (sel.value && r0.doc !== sel.value) return false;
  if (FK && r0.kind !== FK) return false;
  var has = {"ok": 0, "bad": 0, "doubt": 0}, allNone = true;
  g.members.forEach(function(i){
    var v = getV(i);
    if (v === "ok" || v === "bad" || v === "doubt") has[v] += 1;
    if (v || getC(i)) allNone = false;
  });
  if (FV === "none" && !allNone) return false;
  if (FV && FV !== "none" && !has[FV]) return false;
  if (Q){
    var hit = false;
    g.members.forEach(function(i){ if (rowHay(i).indexOf(Q) >= 0) hit = true; });
    if (!hit) return false;
  }
  return true;
}
function toggleOcc(i){
  if (openOcc[i]) delete openOcc[i]; else openOcc[i] = true;
  render();
}
function occLine(i, k){
  var r = D.rows[i], v = getV(i), c = getC(i);
  return '<div class="occline" onclick="toggleOcc(' + i + ')">'
       + '<span class="vdot vdot-' + (v || "none") + '"></span>第 ' + (k + 1) + ' 处 · page '
       + esc(r.page) + ' · ' + esc(r.uid)
       + (c ? ' · <span style="color:#9a3412">批注: ' + esc(c.slice(0, 40)) + (c.length > 40 ? "…" : "") + '</span>' : '')
       + '<span style="float:right;color:#94a3b8">' + (openOcc[i] ? '&#9662;' : '&#9656;') + '</span></div>';
}
function groupHtml(g){
  var counts = {"ok": 0, "bad": 0, "doubt": 0}, annotated = 0, primary = g.members[0];
  g.members.forEach(function(i){
    var v = getV(i);
    if (counts[v] !== undefined) counts[v] += 1;
    if (v || getC(i)){ annotated += 1; if (primary === g.members[0]) primary = i; }
  });
  var fl = [];
  Object.keys(g.fmap).forEach(function(f){
    fl.push('<span class="fld">' + esc(f) + ': <b>' + esc(g.fmap[f]) + '</b></span>');
  });
  var gsum = "";
  ["ok", "bad", "doubt"].forEach(function(v){
    if (counts[v]) gsum += '<span class="gsum gsum-' + v + '">' + (v === "ok" ? "&#10003;" : v === "bad" ? "&#10007;" : "?") + counts[v] + '</span>';
  });
  var pages = g.members.map(function(i){ return D.rows[i].page; }).join(" / ");
  var h = '<div class="grp k-' + g.kind + '"><div class="grph">'
        + '<span class="badge b-' + g.kind + '">' + (KINDL[g.kind] || esc(g.kind)) + '</span>'
        + '<span class="nm">' + esc(g.name) + '</span>' + gsum
        + '<span class="gcount">本篇 ' + g.members.length + ' 次 · 页 ' + esc(pages) + '</span>'
        + (annotated && annotated < g.members.length
           ? '<span class="gcount">个别标注 ' + annotated + '/' + g.members.length + '</span>' : '')
        + '<div style="margin-top:3px">' + fl.join("") + '</div></div>';
  g.members.forEach(function(i, k){
    h += '<div class="occ">';
    if (i === primary || openOcc[i]) h += cardHtml(i);
    else h += occLine(i, k);
    h += '</div>';
  });
  return h + '</div>';
}
function render(){
  Q = document.getElementById("q").value.trim().toLowerCase();
  var vd = 0, perDoc = {}, groups = buildGroups();
  document.getElementById("tt").textContent = D.rows.length;
  groups.forEach(function(g){
    var doc = g.rejected ? D.rows[g.members[0]].doc : g.doc;
    var d = perDoc[doc] = perDoc[doc] || {groups: [], vd: 0, tot: 0};
    d.tot++;
    var annotated = g.members.some(function(i){ return getV(i) || getC(i); });
    if (annotated){ d.vd++; vd++; }
    if (groupMatches(g)) d.groups.push(g);
  });
  var h = "";
  D.metas.forEach(function(m){
    var d = perDoc[m.doc];
    if (!d || !d.groups.length) return;
    var open = isOpenDoc(m.doc);
    h += '<div class="drawer' + (open ? ' open' : '') + '">'
       + '<div class="dhead" onclick="toggleDoc(&#39;' + esc(m.doc) + '&#39;)">'
       + '<span class="arrow">' + (open ? '&#9662;' : '&#9656;') + '</span>'
       + esc(m.doc)
       + '<span class="dm">收 ' + (m.acc!=null?m.acc:'-') + ' ｜ 拒 ' + (m.rej!=null?m.rej:'-')
       + ' ｜ 命中 ' + (m.hit!=null ? (m.hit*100).toFixed(1)+'%' : '-')
       + ' ｜ 已审 ' + d.vd + '/' + d.tot + ' 组</span></div>';
    if (open){
      h += '<div class="dbody">';
      d.groups.forEach(function(g){
        h += g.rejected ? cardHtml(g.members[0]) : groupHtml(g);
      });
      h += '</div>';
    }
    h += '</div>';
  });
  document.getElementById("list").innerHTML = h || "<p style='margin:14px'>无匹配条目。</p>";
  document.getElementById("vd").textContent = vd;
}
function exportV(){
  var blob = new Blob([JSON.stringify({generated:new Date().toISOString(), source:"ner_audit",
    notes: collectNotes()}, null, 1)], {type:"application/json"});
  var a = document.createElement("a"); a.href = URL.createObjectURL(blob);
  a.download = "ner_notes_export.json"; a.click();
}
render();
</script></body></html>"""


_GLOSSARY_TMPL = """<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>NER 全局批注词典</title>
<style>
body{font-family:"Microsoft YaHei",sans-serif;margin:0 0 50px;background:#f8fafc;color:#0f172a}
header{position:sticky;top:0;background:#0f172a;color:#fff;padding:10px 14px;z-index:9;font-size:13px}
header b{font-size:15px}.meta{color:#94a3b8;margin-left:10px}
.glink{float:right;margin:0;color:#fff;background:#2563eb;padding:2px 10px;border-radius:4px;text-decoration:none;font-size:12px}
table{border-collapse:collapse;width:calc(100% - 28px);margin:14px;font-size:13px;background:#fff}
th,td{border:1px solid #e2e8f0;padding:6px 9px;text-align:left;vertical-align:top}
th{background:#eef2ff}
.badge{display:inline-block;font-size:11px;padding:1px 7px;border-radius:9px;color:#fff}
.b-funding{background:#2563eb}.b-software{background:#d97706}.b-datasets{background:#16a34a}
.b-journals{background:#7c3aed}.b-orgs{background:#0891b2}.b-standards{background:#be185d}.b-proceedings{background:#65a30d}
.cmt{color:#7c2d12;background:#fff7ed;border-left:3px solid #fb923c;padding:3px 8px;border-radius:0 4px 4px 0;white-space:pre-wrap;max-width:460px}
.del{font-size:12px;padding:1px 9px;border:1px solid #94a3b8;background:#fff;border-radius:4px;cursor:pointer}
.del:hover{background:#fee2e2;border-color:#dc2626}
.src{color:#64748b;font-size:11px}
#foot{position:fixed;bottom:0;left:0;right:0;background:#1e293b;color:#e2e8f0;padding:7px 14px;font-size:12px}
</style></head><body>
<header><b>NER 全局批注词典</b><span class="meta" id="hmeta"></span>
<a class="glink" href="ner_audit.html">&#8592; 返回审计页</a></header>
<table><thead><tr><th>类型</th><th>名称</th><th>批注</th><th>首次批注来源</th><th>收录时间</th><th></th></tr></thead>
<tbody id="tb"></tbody></table>
<div id="foot">词典只进不改: 审计页首次批注同名条目时自动收录, 已有词条不更新。经 webapp 打开可删除词条; 双击文件打开只读。删除后其他同名条目不再显示继承批注。</div>
<script>
var D = @@DATA@@;
var GLOS = D.entries || [];
var serverOk = false;
var KINDL = {funding:"基金", software:"软件", datasets:"数据集", journals:"期刊",
             standards:"法规标准", proceedings:"会议集"};
function esc(s){ return (s==null?"":String(s)).replace(/[&<>"]/g, function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]}) }
function keyOf(e){ return e.kind + "" + e.name_norm; }
function render(){
  document.getElementById("hmeta").textContent =
    "生成 " + D.generated + " ｜ 词条 " + GLOS.length + (serverOk ? " ｜ 已连服务" : " ｜ 未连服务(只读)");
  var h = "";
  GLOS.forEach(function(e, i){
    h += "<tr><td><span class='badge b-" + esc(e.kind) + "'>" + (KINDL[e.kind] || esc(e.kind)) + "</span></td>"
      + "<td><b>" + esc(e.name) + "</b></td>"
      + "<td><div class='cmt'>" + esc(e.comment) + "</div></td>"
      + "<td class='src'>" + esc((e.source && e.source.doc) || "-") + " · " + esc((e.source && e.source.uid) || "-") + "</td>"
      + "<td class='src'>" + esc(e.created || "-") + "</td>"
      + "<td>" + (serverOk ? "<button class='del' onclick='delE(" + i + ")'>删除</button>" : "") + "</td></tr>";
  });
  document.getElementById("tb").innerHTML = h || "<tr><td colspan=6>词典暂无词条(去审计页写批注自动收录)</td></tr>";
}
function delE(i){
  var e = GLOS[i];
  if (!confirm("删除词条 [" + e.name + "] ?\\n删除后同名条目不再显示继承批注。")) return;
  fetch("/api/ner_glossary", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({delete: [keyOf(e)]})})
    .then(function(r){ return r.json(); })
    .then(function(j){ if (j && j.ok){ GLOS.splice(i, 1); render(); } })
    .catch(function(){ alert("未连服务, 无法删除"); });
}
try {
  fetch("/api/ner_glossary").then(function(r){ return r.json(); }).then(function(j){
    if (j && j.entries){ GLOS = j.entries; serverOk = true; render(); }
  }).catch(function(){ render(); });
} catch(e) { render(); }
render();
</script></body></html>"""


# —— 契约 selftest(python -m metapy.ner; mock opener 不触网, tmp 目录不碰 out/) ——
def _selftest():
    import hashlib, shutil, tempfile
    from .llmrep import _MockResp
    os.environ["KCI_LLM_KEY"] = "t"
    n_ok = 0
    tmp = tempfile.mkdtemp()
    try:
        out_root = os.path.join(tmp, "out")
        dd = os.path.join(out_root, "d")
        udir = os.path.join(dd, "unified")
        os.makedirs(udir)
        b1 = u"본 연구는 한국연구재단(NRF)의 지원(NRF-2023R1A2C1001234)을 받아 수행하였다."
        b2 = u"통계 분석은 SPSS 26.0을 사용하였다."
        b3 = u"자료는 국가통계포털(KOSIS)에서 수집하였다."
        b4 = u"결론적으로 본 연구의 함의를 논하였다."
        page = {"doc": "d", "page": 1, "w": 1000, "h": 1500, "in_ref": False,
                "unified": [
                    {"uid": "u1_1", "page": 1, "box": [10, 10, 500, 40], "type": "Text",
                     "cross_page": {"spans": False}, "readings": [], "main_text": b1},
                    {"uid": "u1_2", "page": 1, "box": [10, 50, 500, 80], "type": "Text",
                     "cross_page": {"spans": False}, "readings": [], "main_text": b2},
                    {"uid": "u1_3", "page": 1, "box": [10, 90, 500, 120], "type": "Footnote",
                     "cross_page": {"spans": False}, "readings": [], "main_text": b3},
                    {"uid": "u1_4", "page": 1, "box": [10, 130, 500, 160], "type": "Text",
                     "cross_page": {"spans": False}, "readings": [], "main_text": b4},
                    {"uid": "u1_5", "page": 1, "box": [10, 170, 500, 190], "type": "Page-header",
                     "cross_page": {"spans": False}, "readings": [], "main_text": u"헤더"},
                    {"uid": "u1_6", "page": 1, "box": [10, 200, 500, 230], "type": "Text",
                     "cross_page": {"role": "head"}, "readings": [], "main_text": u"吞并块"}]}
        pfp = os.path.join(udir, "page_1.json")
        io.open(pfp, "w", encoding="utf-8").write(json.dumps(page, ensure_ascii=False))
        # 文献表页(in_ref): 必须被 collect_blocks 整页排除, 不参与抽取
        io.open(os.path.join(udir, "page_2.json"), "w", encoding="utf-8").write(json.dumps(
            {"doc": "d", "page": 2, "w": 1000, "h": 1500, "in_ref": True,
             "unified": [{"uid": "u2_1", "page": 2, "box": [10, 10, 500, 40], "type": "Text",
                          "cross_page": {"spans": False}, "readings": [],
                          "main_text": u"References 김철수(2001) …"}]}, ensure_ascii=False))
        h0 = hashlib.md5(io.open(pfp, "rb").read()).hexdigest()
        cfg = {"enabled": True, "ner_enabled": True, "endpoint": "mock://x", "model": "m",
               "api_key_env": "KCI_LLM_KEY", "temperature": 0, "max_fields_per_call": 8}
        # i) 关闸: 不写文件, 返回 None
        r0 = ner_doc("d", out_root, cfg=dict(cfg, ner_enabled=False))
        tdir = os.path.join(tmp, "out_llm_trial", "d")
        ok0 = (r0 is None and not os.path.exists(os.path.join(tdir, "ner_llm.json")))
        if ok0:
            n_ok += 1
        else:
            print("FAIL [ner-disabled]")
        objs = [
            {"blocks": [
                {"uid": "u1_1",
                 "funding": [
                     {"funder": u"한국연구재단", "grant_id": "NRF-2023R1A2C1001234",
                      "relation": "funds_current_paper", "evidence_span": b1, "needs_context": False},
                     {"funder": "NASA", "grant_id": None, "relation": "funds_current_paper",
                      "evidence_span": "NASA", "needs_context": False},
                     {"funder": None, "grant_id": None}]},
                {"uid": "u1_2", "software": [{"name": "SPSS", "version": "26.0",
                                              "relation": "used_in_current_paper",
                                              "evidence_span": b2, "needs_context": False}]}]},
            {"funding": None, "software": None, "journals": None, "standards": None,
             "datasets": [{"name": "KOSIS", "url": "https://kosis.kr", "relation": None,
                           "evidence_span": b3, "needs_context": True}],
             "proceedings": None},
        ]
        review_objs = [{"blocks": [{"uid": "u1_3", "datasets": [
            {"name": "KOSIS", "url": "https://kosis.kr", "relation": "used_in_current_paper",
             "evidence_span": b3, "needs_context": False}]}]}]
        def op(req, timeout=120):
            payload = json.loads(req.data.decode("utf-8"))
            review = u"二次上下文复核" in payload["messages"][0]["content"]
            use = review_objs if review else objs
            return _MockResp({"choices": [{"message": {"content": "\n".join(
                json.dumps(o, ensure_ascii=False) for o in use)}}]})
        res = ner_doc("d", out_root, cfg=cfg, opener=op)
        st = res and res["stats"]
        ok1 = (res and st["blocks"] == 4 and st["batches"] == 2 and st["context_reviewed"] == 1
               and st["accepted"]["funding"] == 1 and st["accepted"]["software"] == 1
               and st["accepted"]["datasets"] == 1
               and st["accepted"]["total"] == 3
               and st["rejected"]["funding"] == 2 and st["rejected"]["total"] == 2
               and st["errors"] == 0
               and st["span_hit_rate"] == round(3 / 5.0, 4)
               and all("verdict" in e and "comment" in e and "matched" in e
                       for e in res["entities"]))
        if ok1:
            n_ok += 1
        else:
            print("FAIL [ner-counts] %s" % json.dumps(st, ensure_ascii=False)[:200])
        reasons = [r["reason"] for r in (res and res["rejected"] or [])]
        kinds_got = sorted(e["kind"] for e in res["entities"])
        ok2 = (sorted(reasons) == ["evidence_not_found", "schema"]
               and kinds_got == ["datasets", "funding", "software"]
               and dict((e["uid"], e["kind"]) for e in res["entities"])
                   == {"u1_1": "funding", "u1_2": "software", "u1_3": "datasets"}
               and all(e["matched"] for e in res["entities"]))   # 每条都带代码命中锚
        if ok2:
            n_ok += 1
        else:
            print("FAIL [ner-gate] reasons=%s kinds=%s" % (reasons, kinds_got))
        # iii) 词典: 归一化建词(空格/大小写不敏感) + 只进不改 + 词典页产物
        e_new, created1 = glossary_add(out_root, "datasets", u" 디지털 군산문화대전 ",
                                       u"批注A", doc="d", uid="u1_3")
        e_old, created2 = glossary_add(out_root, "datasets", u"디지털군산문화대전", u"批注B", doc="d")
        e_knil, created3 = glossary_add(out_root, "bogus", "X", "c")
        gfp = build_glossary_viewer(out_root)
        ghtml = io.open(gfp, encoding="utf-8").read() if gfp else ""
        ok3 = (e_new and created1 and not created2
               and e_old["comment"] == u"批注A"      # 已存在不更新(只进不改), 返回现有词条
               and e_old["kind"] == "datasets" and e_old["name_norm"] == e_new["name_norm"]
               and e_knil is None and not created3   # 非法 kind 不收
               and norm_name(" A b\tC ") == "abc"
               and gfp and u"디지털 군산문화대전" in ghtml and "/api/ner_glossary" in ghtml)
        if ok3:
            n_ok += 1
        else:
            print("FAIL [ner-glossary]")
        # iv) 审计页: 数据内联(含标注/词典/nk) + 高亮 + 抽屉 + 回写接口 + 词典入口
        afp = build_audit_viewer(out_root)
        html = io.open(afp, encoding="utf-8").read() if afp else ""
        ok4 = (afp and afp.endswith(os.path.join("out_llm_trial", "ner_audit.html"))
               and (u'"parts": ["", "' + b2 + u'", ""]') in html
               and "used_in_current_paper" in html
               and "NASA" in html and '"doc": "d"' in html and "命中" in html
               and "闸门拒收" in html and "saveCmt" in html and "批注" in html
               and "&#39;" in html and "\\'" not in html
               and "/api/ner_note" in html and "collectNotes" in html
               and "toggleDoc" in html and "drawer" in html
               and "localStorage" not in html
               and "/api/ner_glossary" in html and "glosFind" in html
               and "ner_glossary.html" in html
               and "/api/ner_annotations" in html
               and "本篇" in html and "occline" in html and "buildGroups" in html
               and "个别标注" in html)
        if ok4:
            n_ok += 1
        else:
            print("FAIL [ner-audit]")
        # v) 标注继承: 改标注后重跑, verdict/comment 不丢(按 kind+uid+折叠主标识)
        fp0 = os.path.join(tdir, "ner_llm.json")
        d0 = json.load(io.open(fp0, encoding="utf-8"))
        e_spss = [e for e in d0["entities"] if e["kind"] == "software"][0]
        e_spss["verdict"] = "ok"
        e_spss["comment"] = u"人工批注X"
        io.open(fp0, "w", encoding="utf-8").write(json.dumps(d0, ensure_ascii=False))
        res2 = ner_doc("d", out_root, cfg=cfg, opener=op)
        e1 = res2 and [e for e in res2["entities"] if e["uid"] == "u1_2"]
        ok5 = (res2 and e1 and e1[0]["verdict"] == "ok" and e1[0]["comment"] == u"人工批注X"
               and all("verdict" in e and "comment" in e for e in res2["entities"]))
        if ok5:
            n_ok += 1
        else:
            print("FAIL [ner-note-carry]")
        # vi) 落盘位置 out_llm_trial/, out/ 只读(hash 不变); 空响应→整块记 error
        ok6a = (os.path.exists(os.path.join(tdir, "ner_llm.json"))
                and hashlib.md5(io.open(pfp, "rb").read()).hexdigest() == h0
                and res["stats"]["accepted"]["total"] == 3)
        r_empty = ner_doc("d", out_root, cfg=cfg,
                          opener=lambda req, timeout=120: _MockResp(
                              {"choices": [{"message": {"content": "无json"}}]}))
        ok6 = (ok6a and r_empty and r_empty["stats"]["accepted"]["total"] == 0
               and r_empty["stats"]["errors"] == 4)
        if ok6:
            n_ok += 1
        else:
            print("FAIL [ner-isolation]")
        packed = [{"uid": "u%d" % i, "page": 1, "type": "Text", "text": u"가" * 600}
                  for i in range(5)]
        batches = _batch_blocks(packed, {"max_fields_per_call": 3, "ner_max_chars_per_call": 1400})
        evidence_ok = _norm_entity("software", {"name": "SPSS", "version": "26.0",
                                                "relation": "used_in_current_paper",
                                                "evidence_span": b2})
        evidence_bad = _norm_entity("software", {"name": "MSCEIT", "relation": "mentioned_only",
                                                 "evidence_span": b2})
        if (len(batches) == 3
                and [b["uid"] for batch in batches for b in batch] == [b["uid"] for b in packed]
                and _match_evidence(evidence_ok, b2) == b2
                and _match_evidence(evidence_bad, b2) is None
                and evidence_bad["relation"] not in RELATIONS["software"]
                and u"程序会覆盖全部" in _SYS_PROMPT):
            n_ok += 1
        else:
            print("FAIL [relation-evidence-batching]")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("ner selftest: %d/8 PASS" % n_ok)
    return n_ok == 8

if __name__ == "__main__":
    raise SystemExit(0 if _selftest() else 1)
