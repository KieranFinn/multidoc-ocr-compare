# -*- coding: utf-8 -*-
import os, re, io, json, html
import pipeline
from .common import *


def _llm_cfg():
    fp = os.path.join(ROOT, "channels.json")
    try:
        c = json.load(io.open(fp, encoding="utf-8"))
    except Exception:
        return {}
    return c.get("llm") or {}

# 批次4b 合并 verdict(用户 4b 前置①): pos_alarm 与族触发同 uid 命中时合并为单任务有序 verdict——
# 形如 ["参见注", "pos_alarm:题名含页标记"], 不再两平级字段各说一份; pos_alarm 消费统一于此(任意 level,
# 不再限 high——§56 归因修正: 0 生效的只是 high+pos_alarm 旧通路本身)。
def _hybrid_families(r, au=None):
    """批次4 四族触发(混合层 dry-run 队列; 验收=pending 覆盖 P0 错值名单, 非调通)。
    判据全部来自 REVIEW_METADATA_ANOMALIES P0 实测形态; 返回族标列表(可多族同中):
    参见注=引导语散文形态(句尾 참조/자세하다/서술함/하였다 或 이하…내용은 句型; 作者段引导语=批次3 guard 的全等级版);
    一条多文献=≥2 年份锚(_split_glued 同款 '年, 后随逗号') 或题名内 '에 대하여' 无逗号粘连第二题名(u36_6 型)——
      CJK 双容器判据实测弃用(章载于书合法形态 u8_6/#38 不可分, 且相关 P0 均有别族兜底);
    串烧残余=作者段内嵌引文/卷期页词(含单引号)、拉丁机构切碎(≥2 连写大写单词)、source 混入正文句(长+陈述尾)、
      source 首段非载体地名(Cornwall 型, 全语料唯一命中);
    layout=unknown=体例未识别且有缺口(题名缺/位置报警)——全绿正确条不入队(混合纪律: 正则优先, LLM 只兜规则失败);
    互证分歧(验收补充触发, 非四族)=有值题名但渠道互证分歧且 level=low(STAXI 판/관 2:1 实证)。"""
    raw = (r.get("raw") or "").strip()
    t = (r.get("title") or "").strip()
    s = (r.get("source") or "").strip()
    aus = r.get("authors") or []
    fam = []
    if re.search(u"(이하|대한 상세|관련된 주요|개요에 대한)[^,.]{0,30}(내용은|것은|연구는)", raw) \
       or re.search(u"(참조|자세하다|서술함|참고하여|하였다)[. ]*$", raw):
        fam.append(u"参见注")
    for a0 in aus:
        seg = (a0 or u"").strip()
        if len(seg.split()) >= 2 and re.search(u"[가-힣]", seg) and \
           seg.rstrip(u".,").endswith((u"는", u"은", u"이다", u"임")) and \
           any(w in seg for w in (u"참조", u"내용", u"자세하")):
            fam.append(u"参见注")
            break
    if len(re.findall(r",\s*(?:19|20)\d{2}[a-z]?(?=,)", raw)) >= 2 or \
       re.search(r"에 대하여\s+\S.{3,}", t):
        fam.append(u"一条多文献")
    for a0 in aus:
        if re.search(r"[『「“\"'‘]|제\s*\d+\s*[권호집]|pp?\.\s*\d", a0 or u""):
            fam.append(u"串烧残余")
            break
    if len([a for a in aus if re.fullmatch(r"[A-Z][a-z]{3,}", a or u"")]) >= 2:
        fam.append(u"串烧残余")
    if len(s) > 40 and re.search(r"(위와|하였다|한다|이다)[,.\s]", s) and _hangul_ratio(s) > 0.5:
        fam.append(u"串烧残余")
    if re.match(r"^[A-Z][a-z]{2,},\s*[A-Z]", s):
        fam.append(u"串烧残余")
    if not r.get("layout") and (not t or ((au or {}).get("pos_alarm"))):
        fam.append(u"layout=unknown")
    tinfo = ((au or {}).get("ch_agree") or {}).get("title") or {}
    tagree = tinfo.get("agree") if isinstance(tinfo, dict) else 1
    if t and isinstance(tagree, (int, float)) and tagree < 1 and \
       (r.get("confidence") or {}).get("level") == "low":
        fam.append(u"互证分歧")
    return fam

def llm_pending_fields(meta, audit=None):
    """收集待 LLM 兜底的字段(值为空 或 置信 low 且 basis 含 '④LLM'/'题名未抽出')。
    批次4 四族触发 + 互证分歧补充(验收=P0 错值名单全覆盖, 非调通); 批次4b 合并 verdict——
    pos_alarm 与族触发同 uid 命中合并为单任务有序 verdict(任意 level 统一消费, 取代批次3 的
    high+pos_alarm 并列通路, §56 归因修正); 族任务 field="entry" 整条目复核, verdict 只落 audit 不直写产品。
    返回 [{kind, index, field, raw, uid, page, verdict?}]。"""
    tasks = []
    for k in ("title", "title_en"):
        f = meta.get(k) or {}
        if not f.get("value"):
            tasks.append({"kind": k, "field": "value", "raw": "", "uid": f.get("uid"),
                          "page": f.get("page")})
    for k in ("abstract", "abstract_en"):
        f = meta.get(k) or {}
        if not f.get("value"):
            tasks.append({"kind": k, "field": "value", "raw": "", "uid": (f.get("confidence") or {}).get("uid_head"),
                          "page": f.get("page")})
    for i, r in enumerate(meta.get("references") or []):
        for fld in ("authors", "year", "title", "source"):
            if not r.get(fld):
                tasks.append({"kind": "ref", "index": i, "field": fld, "raw": r.get("raw", ""),
                              "uid": r.get("uid"), "page": r.get("page")})
    # 批次4/4b 触发(合并 verdict, 单任务/条目): 族触发 + pos_alarm(任意 level, 统一消费; audit 按序 join, 同 report_meta)
    aud = audit or {}
    for key, kind in (("references", "ref"), ("footnote_refs", "fref")):
        refs = meta.get(key) or []
        aud_list = aud.get(key) or []
        for i, r in enumerate(refs):
            au = aud_list[i] if i < len(aud_list) else {}
            verdict = list(_hybrid_families(r, au))
            verdict += [u"pos_alarm:%s" % al for al in (au.get("pos_alarm") or [])]
            if verdict:
                tasks.append({"kind": kind, "index": i, "field": "entry",
                              "raw": r.get("raw", ""), "uid": r.get("uid"), "page": r.get("page"),
                              "verdict": verdict})
    return tasks

def _json_objs(txt):
    """从模型输出中稳健抽取顶层 JSON 对象(raw_decode 顺序扫描, 支持 fields 嵌套)。"""
    dec = json.JSONDecoder()
    objs, pos = [], 0
    while True:
        i = (txt or "").find("{", pos)
        if i < 0:
            break
        try:
            obj, end = dec.raw_decode(txt, i)
            objs.append(obj)
            pos = end
        except Exception:
            pos = i + 1
    return objs

def _prompt_for(t, umap):
    """批次4c-2 prompt 契约: field=entry 整条目复核(带 verdict list+raw+三读法, 结构化 action 输出);
    其余=空值抽取(原 {\"value\":...} 形态)。fref 与 ref 同构。"""
    u = umap.get(t.get("uid") or "")
    readings = ""
    if u:
        readings = "\n".join("- %s: %s" % (r["ch"], (r.get("text") or "")[:600])
                             for r in u["readings"])
    if t.get("field") == "entry":
        return (u"条目复核[%s#%d] 块uid=%s page=%s\n触发信号(verdict): %s\n主读法(raw): %s\n三家读法:\n%s\n"
                u"请复核该条文献条目并裁决: action=keep(维持现抽, 可给修正 fields)/split(一条拆多, 必须给 "
                u"n_docs 与每条 raw_span, fields 必须为空)/null(置空, 必须给 reason)。"
                u"输出固定 JSON: {\"action\":...,\"fields\":{...},\"reason\":...,\"n_docs\":...,\"raw_span\":[...]}" % (
                    t["kind"], t.get("index") or 0, t.get("uid"), t.get("page"),
                    json.dumps(t.get("verdict") or [], ensure_ascii=False),
                    (t.get("raw") or "")[:800], readings))
    return (u"字段[%s#%s] 块uid=%s page=%s\n主读法: %s\n三家读法:\n%s\n请从原文抽取该字段, "
            u"输出固定 JSON: {\"value\": ...}" % (
                t["kind"], t["field"], t.get("uid"), t.get("page"),
                (t.get("raw") or "")[:800], readings))

def _suggest(t, obj):
    """4c-2 契约校验 → suggestion 单元 {kind,index,field,uid,action,value,fields,reason,...};
    非法(action 未知/互斥违规/必填缺)返回 None(计 rejected, 不抛)。"""
    if not isinstance(obj, dict):
        return None
    if t.get("field") == "entry":
        act = obj.get("action")
        if act not in ("keep", "split", "null"):
            return None
        fields = obj.get("fields") or {}
        if act == "split" and (fields or not obj.get("n_docs") or not obj.get("raw_span")):
            return None   # split 与 fields 互斥, 且必须给 n_docs+raw_span
        if act == "null" and not (obj.get("reason") or "").strip():
            return None   # null 必须给理由
        return {"kind": t["kind"], "index": t.get("index"), "field": "entry", "uid": t.get("uid"),
                "action": act, "value": None, "fields": fields or None, "reason": obj.get("reason"),
                "n_docs": obj.get("n_docs"), "raw_span": obj.get("raw_span")}
    val = obj.get("value")
    if val in (None, ""):
        return None
    return {"kind": t["kind"], "index": t.get("index"), "field": t["field"], "uid": t.get("uid"),
            "action": "fill", "value": val, "fields": None, "reason": obj.get("reason")}

def llm_apply(doc, out_root, cfg=None, opener=None):
    """LLM 兜底(路线A纪律: 正则优先, LLM 只处理规则失败/低置信字段)。
    批次4c 出口重建: **产品 metadata.json 只读不写**(原直写暗门已拆, 4c-1), 结果只落 audit:
      audit["llm"] = {doc, pending, called, applied(**恒 0**=§50-§56 历史对账锚"硬闸未破", 4c 起直写已拆),
                      suggested, suggested_kinds, rejected, enabled, suggestion:[...]}
    suggestion 单元见 _suggest; kind=fref 与 ref 同构落盘(4c-2)。cfg/opener 可注入(契约 selftest 用
    mock 不触网; 真调产物只许写 out_llm_trial/, 不回灌 out/——4c-3 硬约束)。"""
    doc_dir = os.path.join(out_root, doc)
    meta_fp = os.path.join(doc_dir, "metadata.json")
    if not os.path.exists(meta_fp):
        return None
    meta = json.load(io.open(meta_fp, encoding="utf-8"))   # 只读: pending 判定的输入
    audit = load_audit(doc_dir)
    tasks = llm_pending_fields(meta, audit)
    cfg = cfg if cfg is not None else _llm_cfg()
    result = {"doc": doc, "pending": tasks, "called": 0, "applied": 0,
              "suggested": 0, "suggested_kinds": {}, "rejected": 0,
              "suggestion": [], "enabled": bool(cfg.get("enabled"))}

    def _stash(res):
        au = load_audit(doc_dir)
        au["llm"] = res
        save_audit(doc_dir, au)

    if not tasks or not cfg.get("enabled"):
        _stash(result)
        print("== ④LLM %s == 待办 %d 条, enabled=%s(未调用, 建议只落 audit 不碰产品)" % (
            doc, len(tasks), cfg.get("enabled")))
        return result
    import urllib.request
    key = os.environ.get(cfg.get("api_key_env") or "KCI_LLM_KEY", "")
    if not key or not cfg.get("endpoint"):
        result["error"] = "缺 endpoint/api_key(环境变量 %s)" % cfg.get("api_key_env")
        _stash(result)
        return result
    udir = os.path.join(doc_dir, "unified")
    umap = {}
    if os.path.isdir(udir):
        for f in os.listdir(udir):
            mm = re.match(r"page_(\d+)\.json$", f)
            if mm:
                jj = json.load(io.open(os.path.join(udir, f), encoding="utf-8"))
                for u in jj["unified"]:
                    umap[u["uid"]] = u
    per = max(1, int(cfg.get("max_fields_per_call") or 8))
    for gi in range(0, len(tasks), per):
        batch = tasks[gi:gi + per]
        prompts = [_prompt_for(t, umap) for t in batch]
        body = json.dumps({"model": cfg.get("model"),
                           "temperature": float(cfg.get("temperature") or 0),
                           "messages": [{"role": "system",
                                         "content": u"你是韩文期刊元数据抽取助手。只输出 JSON。"},
                                        {"role": "user", "content": "\n\n".join(prompts)}]}).encode("utf-8")
        req = urllib.request.Request(cfg["endpoint"], data=body, headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key})
        try:
            op = opener or urllib.request.urlopen
            with op(req, timeout=120) as resp:
                out = json.loads(resp.read().decode("utf-8"))
            txt = out["choices"][0]["message"]["content"]
            result["called"] += 1
        except Exception as e:
            for t in batch:
                t["error"] = str(e)[:120]
            result["rejected"] += len(batch)
            continue
        objs = _json_objs(txt)
        for t, obj in zip(batch, objs):
            sg = _suggest(t, obj)
            if sg:
                result["suggestion"].append(sg)
                result["suggested"] += 1
                result["suggested_kinds"][sg["kind"]] = result["suggested_kinds"].get(sg["kind"], 0) + 1
            else:
                result["rejected"] += 1   # 解析失败/动作非法
        result["rejected"] += abs(len(objs) - len(batch))   # 多返/少返一条也计 rejected
    _stash(result)
    print("== ④LLM %s == 待办 %d, 调用 %d 批, 建议 %d, 拒绝 %d, applied=0(不碰产品)" % (
        doc, len(tasks), result["called"], result["suggested"], result["rejected"]))
    return result

def _esc(s):
    return html.escape(str(s if s is not None else ""), quote=False)

def report_meta(docs, out_root):
    """全量 _metadata_report.html: 字段级红绿表(置信着色, 点击跳 1:3 查看器对应页) +
    四层验证指标(L1 机械校验/L2 三渠道互证/L3 数据库对照[留接口]/L4 人工 GT[待标注])。"""
    rows, val_rows, agree_rows, fn_rows = [], [], [], []
    for doc in docs:
        meta_fp = os.path.join(out_root, doc, "metadata.json")
        if not os.path.exists(meta_fp):
            continue
        m = json.load(io.open(meta_fp, encoding="utf-8"))
        aud = load_audit(os.path.join(out_root, doc))   # §38 双读: 诊断层(basis/review_comment/ch_agree/L1)在 sidecar

        def row(field, label, value, conf, page, extra=""):
            lvl = (conf or {}).get("level", "low")
            src = (conf or {}).get("source", "-")
            basis = (conf or {}).get("basis", "")
            link = "<a href='%s/viewer_compare.html#p%d'>p%d</a>" % (doc, (page - 1) if page else 0, page) if page else "-"
            vraw = str(value if value is not None else "")
            vshow = _esc(value)[:180]
            if len(vraw) > 180:   # 长值(摘要/题名)悬浮看全文, 不再硬截
                vshow = "<span title='%s'>%s</span>" % (html.escape(vraw, quote=True), vshow)
            rows.append(
                "<tr><td>%s</td><td>%s</td><td class=v>%s</td>"
                "<td style='color:%s;font-weight:bold'>%s</td><td>%s</td><td>%s</td><td class=f>%s</td></tr>"
                % (_esc(doc), label, vshow, LV_COLOR.get(lvl, "#333"), lvl,
                   link, src, _esc(basis)))
        t = m.get("title") or {}
        row("title", "标题(韩)", t.get("value"), t.get("confidence"), t.get("page") or t.get("confidence", {}).get("page"))
        te = m.get("title_en") or {}
        row("title_en", "标题(英)", te.get("value"), te.get("confidence"),
            te.get("page") or te.get("confidence", {}).get("page"))
        for i, a in enumerate(m.get("authors") or []):
            row("author", "作者%d" % (i + 1),
                "%s%s%s" % (a.get("name"), " (%s)" % a["roman"] if a.get("roman") else "",
                            " [%s]" % a["role"] if a.get("role") else ""),
                {"level": "mid" if a.get("uid") else "low", "source": "regex", "basis": ""},
                a.get("page"), a.get("affiliation") or "")
        for i, v in enumerate(m.get("affiliations") or []):
            row("affil", "机构%d" % (i + 1), v, {"level": "mid", "source": "regex", "basis": ""}, 1)
        for k in ("abstract", "abstract_en"):
            f = m.get(k) or {}
            row(k, "摘要" + ("(英)" if k.endswith("en") else "(韩)"), (f.get("value") or "")[:120] + ("…" if f.get("value") else "（无）"),
                f.get("confidence"), f.get("page"), "from_tail=%s" % f.get("from_tail", "-"))
        aca_list = aud.get("references") or []
        for i, r in enumerate(m.get("references") or []):
            aca = aca_list[i] if i < len(aca_list) else {}
            conf = r.get("confidence") or {}
            lv = conf.get("level", "low")
            au_full = "; ".join(r.get("authors") or [])
            au = _esc(au_full[:60]) + ("…" if len(au_full) > 60 else "")
            if len(au_full) > 60:
                au = "<span title='%s'>%s</span>" % (html.escape(au_full, quote=True), au)
            ti_full = r.get("title") or ""
            ti = _esc(ti_full[:70]) + ("…" if len(ti_full) > 70 else "")
            if not ti_full:
                ti = "<span style='color:#dc2626'>（题名未抽出）</span>"
            elif len(ti_full) > 70:
                ti = "<span title='%s'>%s</span>" % (html.escape(ti_full, quote=True), ti)
            badges = []
            if r.get("grammar"):
                badges.append(_esc(r["grammar"]))
            if r.get("et_al"):
                badges.append("et_al")
            if r.get("notice_no"):
                badges.append("Notice No.%s" % _esc(r["notice_no"]))
            badge_html = ("<div class=mb>%s</div>" % " · ".join(badges)) if badges else ""
            val = "[%s] %s (%s) %s. %s%s" % (
                r.get("num") if r.get("num") is not None else "-", au, r.get("year") or "?",
                ti, _esc(r.get("source") or ""), badge_html)
            link = "<a href='%s/viewer_compare.html#p%d'>p%d</a>" % (doc, (r.get("page") or 1) - 1, r.get("page", 0))
            basis_html = _esc(aca.get("basis", ""))
            rc = aca.get("review_comment")
            if rc:
                sep = " ｜ " if basis_html else ""
                if len(rc) > 60:   # 复核备注全量, 折叠展开(差异核是排查载荷, 不再截 60)
                    basis_html += sep + ("<details class=rcd><summary>%s…</summary><div>%s</div></details>"
                                         % (_esc(rc[:60]), _esc(rc)))
                else:
                    basis_html += sep + _esc(rc)
            rows.append("<tr><td>%s</td><td>文献#%s</td><td class=v>%s</td>"
                        "<td style='color:%s;font-weight:bold'>%s</td><td>%s</td><td>%s</td><td class=f>%s</td></tr>"
                        % (_esc(doc), r.get("num") if r.get("num") is not None else "-", val,
                           LV_COLOR.get(lv, "#333"), lv, link, conf.get("source", "-"),
                           basis_html))
            ca = aca.get("ch_agree") or {}
            if ca:
                agree_rows.append((doc, r.get("num"), ca))
        # —— 正文脚注引文(--stage frefs 独立键, audit 同名键取 basis/review_comment) ——
        fn_aud = aud.get("footnote_refs") or []
        for i, r in enumerate(m.get("footnote_refs") or []):
            fa = fn_aud[i] if i < len(fn_aud) else {}
            conf = r.get("confidence") or {}
            lv = conf.get("level", "low")
            au = "; ".join(r.get("authors") or [])[:60]
            val = "[%s] %s (%s) %s. %s" % (
                r.get("num") if r.get("num") is not None else "-", au, r.get("year") or "?",
                (r.get("title") or "（题名未抽出）")[:70], r.get("source") or "")
            link = "<a href='%s/viewer_compare.html#p%d'>p%d</a>" % (doc, (r.get("page") or 1) - 1, r.get("page", 0))
            basis_show = _esc(fa.get("basis", ""))
            rc = fa.get("review_comment")
            if rc:
                sep = " ｜ " if basis_show else ""
                if len(rc) > 60:
                    basis_show += sep + ("<details class=rcd><summary>%s…</summary><div>%s</div></details>"
                                         % (_esc(rc[:60]), _esc(rc)))
                else:
                    basis_show += sep + _esc(rc)
            fn_rows.append("<tr><td>%s</td><td>脚注#%s</td><td class=v>%s</td>"
                           "<td style='color:%s;font-weight:bold'>%s</td><td>%s</td><td>%s</td><td class=f>%s</td></tr>"
                           % (_esc(doc), r.get("num") if r.get("num") is not None else "-", _esc(val),
                              LV_COLOR.get(lv, "#333"), lv, link, conf.get("source", "-"), basis_show))
        # L1/L2 指标
        val = aud.get("ref_validation") or {}
        seq = val.get("num_sequence") or {}
        l1 = []
        l1.append(("编号连续", "PASS" if seq.get("ok") else "FAIL: %s" % seq, seq.get("ok")))
        l1.append(("年份合法(1900-2026)", "PASS" if not val.get("year_bad") else "BAD:%s" % val["year_bad"],
                   not val.get("year_bad")))
        l1.append(("页码起止合法", "PASS" if not val.get("pages_bad") else "BAD:%s" % val["pages_bad"],
                   not val.get("pages_bad")))
        l1.append(("条目数", str(val.get("entry_count", "-")), True))
        for name, st, ok in l1:
            val_rows.append("<tr><td>%s</td><td>%s</td><td style='color:%s;font-weight:bold'>%s</td></tr>" % (
                _esc(doc), name, "#16a34a" if ok else "#dc2626", _esc(st)))
    # L2 表(ch_agree {field: {agree,...}} 取 agree 值; 批次6 后 13 篇 audit 全为新结构, 旧扁平兼容已删)
    l2_rows = []
    for doc, num, ca in agree_rows:
        tds = []
        for v in ca.values():
            agg = v.get("agree")
            cell = "%.0f%%" % (agg * 100)
            if isinstance(v, dict) and v.get("missing"):
                miss0 = v["missing"][0]
                snip = ((v.get("variant") or {}).get(miss0) or "")[:90]
                cell += "<div class=f>%s读法: %s</div>" % (_esc(miss0), _esc(snip))   # 差异片段上屏(排查载荷)
            tds.append("<td>%s</td>" % cell)
        l2_rows.append("<tr><td>%s</td><td>#%s</td>%s</tr>" % (_esc(doc), num, "".join(tds) or "<td>-</td>"))
    page = u"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>元数据字段层报告(M5④)</title>
<style>body{font-family:"Microsoft YaHei";max-width:1400px;margin:24px auto;background:#f4f5f7;padding:0 16px}
table{border-collapse:collapse;background:#fff;width:100%%;font-size:12px;margin-bottom:22px}
td,th{border:1px solid #e5e7eb;padding:5px 8px;vertical-align:top}
th{background:#eef2ff;text-align:left}.f{color:#9ca3af;font-size:11px}.v{max-width:640px}
.mb{color:#6b7280;font-size:10px;margin-top:2px}
.rcd summary{cursor:pointer;color:#9ca3af}
.rcd div{white-space:pre-wrap;color:#374151;margin:2px 0 4px}
a{color:#2563eb}h1{font-size:19px}h2{font-size:15px;margin:18px 0 8px}p{color:#6b7280;font-size:13px}</style></head><body>
<h1>元数据字段层报告(M5 路线A: ①文献条目 ②标题作者机构 ③摘要 ④LLM兜底/验证)</h1>
<p>绿=high 琥珀=mid 红=low。点页码跳 1:3 对照查看器对应页(悬停块可与原图框联动核对)。
四层验证: L1 机械校验(硬门槛) / L2 三渠道互证 / L3 数据库对照(只测量不改写, 待 KCI OpenAPI key) / L4 人工 GT(待标注)。</p>
<h2>L1 机械校验(参考文献)</h2>
<table><tr><th>文档</th><th>校验项</th><th>结果</th></tr>%s</table>
<h2>L2 三渠道字段互证(文献条目; 命中率&lt;67%% 即 low)</h2>
<table><tr><th>文档</th><th>条目</th><th>%s</th></tr>%s</table>
<h2>字段级明细(红绿表)</h2>
<table><tr><th>文档</th><th>字段</th><th>值</th><th>置信</th><th>页</th><th>来源</th><th>依据</th></tr>%s</table>
<h2>正文脚注引文(--stage frefs 独立键 footnote_refs, 不混 references[])</h2>
<table><tr><th>文档</th><th>字段</th><th>值</th><th>置信</th><th>页</th><th>来源</th><th>依据</th></tr>%s</table>
</body></html>""" % (
        "\n".join(val_rows),
        "/".join(_esc(k) for k in (agree_rows[0][2].keys() if agree_rows else ["互证"])),
        "\n".join(l2_rows) or "<tr><td colspan=3>无</td></tr>",
        "\n".join(rows),
        "\n".join(fn_rows) or "<tr><td colspan=7>无(该批文档无脚注引文或非 in_ref 页无 Footnote 块)</td></tr>")
    fp = os.path.join(out_root, "_metadata_report.html")
    io.open(fp, "w", encoding="utf-8").write(page)
    print("报告 -> %s (%d 字段行, %d L1 校验, %d L2 行)" % (fp, len(rows), len(val_rows), len(l2_rows)))
    return fp

def rebuild_viewer(doc, out_root):
    """按磁盘现有 merged+unified 重建 1:3 对照查看器(不重跑 unify/adapt, M3 标记保留)。
    src_dirs 口径同 pipeline.main: out 内 API 源=raw 下渠道 _api 目录名, out 外归档源=raw 下渠道目录。"""
    doc_dir = os.path.join(out_root, doc)
    mj = json.load(io.open(os.path.join(doc_dir, "merged.json"), encoding="utf-8"))
    sizes = [(pg["w"], pg["h"]) for pg in mj["page_data"]]
    per_ch = {}
    src_dirs = {}
    for pg in mj["page_data"]:
        for ch, blks in (pg.get("channels") or {}).items():
            out_blks = []
            for b in blks:
                bb = dict(b)
                img = bb.get("img")
                if img and img.startswith("raw/"):
                    # merged.json 的 img 已带全前缀(raw/<dir>/images/...); 查看器模板还会再拼
                    # raw/<src_dir>/, 这里剥回引擎目录内相对路径, 与 pipeline.main 传参形态一致
                    parts = img.split("/", 2)
                    if len(parts) == 3:
                        src_dirs.setdefault(ch, parts[1])
                        bb["img"] = parts[2]
                out_blks.append(bb)
            per_ch.setdefault(ch, {})[pg["page"] - 1] = out_blks   # merged.json 页号 1 基, 查看器 0 基
    udir = os.path.join(doc_dir, "unified")
    upages = {}
    if os.path.isdir(udir):
        for f in os.listdir(udir):
            m = re.match(r"page_(\d+)\.json$", f)
            if m:
                upages[int(m.group(1))] = json.load(
                    io.open(os.path.join(udir, f), encoding="utf-8")).get("unified", [])
    for ch in per_ch:
        if ch not in src_dirs:
            api = os.path.join(doc_dir, "raw", "%s_api" % ch)
            src_dirs[ch] = os.path.basename(api) if os.path.isdir(api) else ch
    meta = None
    mfp = os.path.join(doc_dir, "metadata.json")
    if os.path.exists(mfp):
        try:
            meta = json.load(io.open(mfp, encoding="utf-8"))
        except Exception:
            meta = None
    audit = load_audit(doc_dir)   # §38 双读: 面板 tooltip(basis/review_comment)与编号徽标(L1)取自 sidecar
    choices = None   # 批次4d: 双渠道人工选择(out_llm_trial/_choice.json, 可缺省)
    trial_fp = os.path.join(os.path.dirname(os.path.abspath(out_root)), "out_llm_trial",
                            doc, "_choice.json")
    if os.path.exists(trial_fp):
        try:
            choices = (json.load(io.open(trial_fp, encoding="utf-8")) or {}).get("choices") or []
        except Exception:
            choices = None
    fp = os.path.join(doc_dir, "viewer_compare.html")
    nc = pipeline.build_compare_viewer(fp, "%s ｜ 1:3 渠道对照" % doc, sizes, per_ch,
                                       src_dirs, unified_pages=upages or None,
                                       unified_driven=bool(upages), meta=meta, audit=audit,
                                       choices=choices)
    print("查看器重建 -> %s (%d 块, unified驱动=%s)" % (fp, nc, bool(upages)))


# —— 批次4 四族触发冻结冒烟(python -m metapy.llmrep; 语料取 P0 实测形态) ——
_HYBRID_SELFTEST = [
    ({"raw": u"이하 왜구침입과 관계된 주요 내용은 『江原道史』(江原道, 1995, 609~612쪽)와 『高麗史』를 참고하여 서술함.",
      "title": None, "source": u"609~612쪽)와 『高麗史』를 참고하여 서술함", "authors": [u"이하 왜구침입과 관계된 주요 내용은 『江原道史』(江原道"],
      "layout": None, "confidence": {"level": "mid"}},
     {"ch_agree": {}}, {u"参见注", u"串烧残余", u"layout=unknown"}, "frefs#46"),
    ({"raw": u"10. 한종섭(2005), 貊國 왕도 유적 조사에 대하여 春川貊國의 歷史的 硏구, 춘천시강원향토문화연구회",
      "title": u"貊國 왕도 유적 조사에 대하여 春川貊國의 歷史的 硏구", "source": u"춘천시강원향토문화연구회",
      "authors": [u"한종섭"], "layout": "ko_agency", "confidence": {"level": "low"}},
     {"ch_agree": {"title": {"agree": 0.3333, "missing": ["mineru", "dots"], "variant": {}}}},
     {u"一条多文献", u"互证分歧"}, "u36_6"),
    ({"raw": u"American Psychiatric Association, DSM-4, APA, 1994.",
      "title": u"Diagnostic and statistical manual of mental diso", "source": "",
      "authors": ["American", "Psychiatric", "Association"], "layout": "en_journal",
      "confidence": {"level": "low"}},
     {"ch_agree": {}}, {u"串烧残余"}, "DSM 机构切碎"),
    ({"raw": u"우두산성에 대한 상세한 내용은 유재춘의 「春川 牛頭山城考」(『江原文化史研究』9집, 강원향토문화연구회, 2004) 참조.",
      "title": None, "source": u") 참조", "authors": [u"우두산성에 대한 상세한 내용은 유재춘의 「春川 牛頭山城考」(『江原文化史研究』9집", u"강원향토문화연구회"],
      "layout": None, "confidence": {"level": "mid"}},
     {"ch_agree": {}}, {u"参见注", u"串烧残余", u"layout=unknown"}, "frefs#22"),
    ({"raw": u"심경호, 茶山과 春川 , 강원대학교 출판부, 1995, pp.61～62․p.171 참조",
      "title": u"pp.61～62․p.171 참조", "source": "", "authors": [u"심경호", u"茶山과 春川"],
      "layout": None, "confidence": {"level": "low"}},
     {"ch_agree": {}}, {u"参见注"}, "frefs#65"),
    ({"raw": u"Bowell, R. J. and Bruce, I., 1995, \"Geochemistry...Levant Mine,\", Cornwall, Applied Geochemistry, v. 10",
      "title": u"Geochemistry of iron ochres and mine waters from Levant Mine", "source": u"Cornwall, Applied Geochemistry",
      "authors": [u"Bowell R. J", u"Bruce I"], "layout": "en_journal", "confidence": {"level": "mid"}},
     {"ch_agree": {}}, {u"串烧残余"}, "Cornwall"),
    ({"raw": u"7. 춘천공립보통학교 편(1915년경 발간), 春川郡郷土史料",
      "title": u"春川郡郷土史料", "source": "", "authors": [u"춘천공립보통학교"],
      "layout": None, "confidence": {"level": "high"}},
     {"ch_agree": {"title": {"agree": 1.0, "missing": [], "variant": {}}}}, set(), "全绿正确条不入队"),
]

class _MockResp(object):
    """契约 selftest 的 mock HTTP 响应(上下文管理器协议, 同 urllib 响应)。"""
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")
    def read(self):
        return self._b
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False

def _selftest():
    n_ok = 0
    for r, au, want, tag in _HYBRID_SELFTEST:
        got = set(_hybrid_families(r, au))
        if got != want:
            print("FAIL [%s] got=%s want=%s" % (tag, sorted(got), sorted(want)))
        else:
            n_ok += 1
    # 批次4b 合并 verdict 冒烟: 族触发 + pos_alarm 同 uid → 单任务有序 verdict(非两平级字段)
    _m = {"references": [{"raw": u"심경호, 茶山과 春川 , 강원대학교 출판부, 1995, pp.61～62․p.171 참조",
                          "title": None, "source": "", "authors": [u"심경호", u"茶山과 春川"],
                          "layout": None, "uid": "u21_7", "page": 21,
                          "confidence": {"level": "low"}}]}
    _a = {"references": [{"pos_alarm": [u"题名含页标记"], "ch_agree": {}}]}
    _t = [x for x in llm_pending_fields(_m, _a) if x.get("field") == "entry"]
    if len(_t) != 1 or _t[0]["verdict"] != [u"参见注", u"layout=unknown", u"pos_alarm:题名含页标记"]:
        print("FAIL [merge-verdict] got=%s" % (_t and _t[0]["verdict"]))
    else:
        n_ok += 1
    # —— 4c-4 契约级 selftest(mock endpoint, tmp 目录: 不触网/不碰 out/, 产品零写入为验收真理) ——
    import hashlib, shutil, tempfile
    os.environ["KCI_LLM_KEY"] = "t"
    _cfg = {"enabled": True, "endpoint": "mock://x", "model": "m", "api_key_env": "KCI_LLM_KEY",
            "temperature": 0, "max_fields_per_call": 8}

    def _hash(fp):
        return hashlib.md5(io.open(fp, "rb").read()).hexdigest()

    def _mk_doc(root, with_fref=False):
        dd = os.path.join(root, "d")
        os.makedirs(os.path.join(dd, "unified"))
        refs = [{"raw": u"김철수(2001), 제목A, 학술지 3", "title": None, "source": u"학술지 3",
                 "authors": [u"김철수"], "year": "2001", "layout": None, "uid": "u1", "page": 1,
                 "confidence": {"level": "low"}}]
        frefs = [{"raw": u"이영희, 「제목B」, 2005.", "title": None, "source": "",
                  "authors": [u"이영희"], "year": "2005", "layout": None, "uid": "u2", "page": 2,
                  "confidence": {"level": "mid"}}] if with_fref else []
        io.open(os.path.join(dd, "metadata.json"), "w", encoding="utf-8").write(
            json.dumps({"doc": "d", "title": {"value": "T"}, "title_en": {"value": "E"},
                        "abstract": {"value": "A"}, "abstract_en": {"value": "B"},
                        "references": refs, "footnote_refs": frefs}, ensure_ascii=False))
        audits = [{"num": None, "uid": "u1", "page": 1, "block_review": False, "basis": "x",
                   "ch_agree": {}, "pos_alarm": None, "review_comment": None}]
        faudits = [{"num": "1", "uid": "u2", "page": 2, "block_review": False, "basis": "x",
                    "ch_agree": {}, "pos_alarm": None, "review_comment": None}] if with_fref else []
        save_audit(dd, {"doc": "d", "references": audits, "footnote_refs": faudits})
        page = {"w": 1000, "in_ref": True,
                "unified": [{"uid": "u1", "readings": [{"ch": "m", "text": "R1"}, {"ch": "d", "text": "R2"}]},
                             {"uid": "u2", "readings": [{"ch": "m", "text": "R3"}]}]}
        io.open(os.path.join(dd, "unified", "page_1.json"), "w", encoding="utf-8").write(
            json.dumps(page, ensure_ascii=False))
        return dd

    def _mock(txt):
        def _op(req, timeout=120):
            return _MockResp({"choices": [{"message": {"content": txt}}]})
        return _op

    tmp = tempfile.mkdtemp()
    try:
        dd = _mk_doc(tmp)   # i) enabled=false: called/applied=0, suggestion=[], 产品 hash 不变
        h0 = _hash(os.path.join(dd, "metadata.json"))
        r1 = llm_apply("d", tmp, cfg=dict(_cfg, enabled=False))
        ok1 = (r1["called"] == 0 and r1["applied"] == 0 and r1["suggestion"] == []
               and _hash(os.path.join(dd, "metadata.json")) == h0)
        shutil.rmtree(dd)
        dd = _mk_doc(tmp)   # ii) 合法 split: 落 suggestion, applied=0, 产品 hash 不变
        h0 = _hash(os.path.join(dd, "metadata.json"))
        r2 = llm_apply("d", tmp, cfg=_cfg,
                       opener=_mock(u'{"value":"제목A"}\n{"action":"split","n_docs":2,'
                                    u'"raw_span":["sA","sB"],"fields":{},"reason":"two"}'))
        sg = [s for s in r2["suggestion"] if s["action"] == "split"]
        ok2 = (len(sg) == 1 and sg[0]["kind"] == "ref" and r2["applied"] == 0
               and _hash(os.path.join(dd, "metadata.json")) == h0)
        shutil.rmtree(dd)
        dd = _mk_doc(tmp)   # iii) 非法 JSON/动作: rejected 计数, 不抛异常
        r3 = llm_apply("d", tmp, cfg=_cfg, opener=_mock(u'{"action":"fly"} 乱码'))
        ok3 = (r3["suggested"] == 0 and r3["rejected"] >= 1 and r3["suggestion"] == [])
        shutil.rmtree(dd)
        dd = _mk_doc(tmp, with_fref=True)   # iv) fref suggestion 落 audit(不因无分支丢失)
        r4 = llm_apply("d", tmp, cfg=_cfg,
                       opener=_mock(u'{"value":"제목A"}\n{"action":"null","reason":"参见注"}\n'
                                    u'{"action":"keep","fields":{},"reason":"ok"}'))
        kinds = [s["kind"] for s in r4["suggestion"]]
        ok4 = ("fref" in kinds and "ref" in kinds)
        for ok_i, tag_i in ((ok1, "mock-disabled"), (ok2, "mock-split"),
                            (ok3, "mock-rejected"), (ok4, "mock-fref")):
            if ok_i:
                n_ok += 1
            else:
                print("FAIL [llm-contract:%s]" % tag_i)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("hybrid selftest: %d/%d PASS" % (n_ok, len(_HYBRID_SELFTEST) + 5))
    return n_ok == len(_HYBRID_SELFTEST) + 5

if __name__ == "__main__":
    raise SystemExit(0 if _selftest() else 1)
