# -*- coding: utf-8 -*-
import os, re, io, json, datetime, collections, html
import pipeline
from pipeline import _gdice, _fold_sym
import regress
import unify
from .common import *


def _llm_cfg():
    fp = os.path.join(ROOT, "channels.json")
    try:
        c = json.load(io.open(fp, encoding="utf-8"))
    except Exception:
        return {}
    return c.get("llm") or {}

def llm_pending_fields(meta):
    """收集待 LLM 兜底的字段(值为空 或 置信 low 且 basis 含 '④LLM'/'题名未抽出')。
    返回 [{kind, index, field, raw, uid, page}]。"""
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
    return tasks

def llm_apply(doc, out_root):
    """LLM 兜底(路线A纪律: 正则优先, LLM 只处理规则失败/低置信字段)。
    配置 channels.json llm 段(enabled=false 时只落待办清单不调用, 留接口)。
    调用=每篇打包 1-3 次(OpenAI 兼容 chat.completions, temperature=0, 固定 JSON schema)。"""
    doc_dir = os.path.join(out_root, doc)
    meta_fp = os.path.join(doc_dir, "metadata.json")
    if not os.path.exists(meta_fp):
        return None
    meta = json.load(io.open(meta_fp, encoding="utf-8"))
    tasks = llm_pending_fields(meta)
    cfg = _llm_cfg()
    result = {"doc": doc, "pending": tasks, "called": 0, "applied": 0,
              "enabled": bool(cfg.get("enabled"))}
    if not tasks or not cfg.get("enabled"):
        meta["llm"] = result
        io.open(meta_fp, "w", encoding="utf-8").write(json.dumps(meta, ensure_ascii=False, indent=1))
        print("== ④LLM %s == 待办 %d 条, enabled=%s(未调用, 接口留待)" % (
            doc, len(tasks), cfg.get("enabled")))
        return result
    # —— 调用路径(有配置才走): 按 max_fields_per_call 打包 ——
    import urllib.request
    key = os.environ.get(cfg.get("api_key_env") or "KCI_LLM_KEY", "")
    if not key or not cfg.get("endpoint"):
        result["error"] = "缺 endpoint/api_key(环境变量 %s)" % cfg.get("api_key_env")
        meta["llm"] = result
        io.open(meta_fp, "w", encoding="utf-8").write(json.dumps(meta, ensure_ascii=False, indent=1))
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
        prompts = []
        for t in batch:
            u = umap.get(t.get("uid") or "")
            readings = ""
            if u:
                readings = "\n".join("- %s: %s" % (r["ch"], (r.get("text") or "")[:600])
                                     for r in u["readings"])
            prompts.append(u"字段[%s#%s] 块uid=%s page=%s\n主读法: %s\n三家读法:\n%s\n请从原文抽取该字段, "
                           u"输出固定 JSON: {\"value\": ...}" % (
                               t["kind"], t["field"], t.get("uid"), t.get("page"),
                               (t.get("raw") or "")[:800], readings))
        body = json.dumps({"model": cfg.get("model"),
                           "temperature": float(cfg.get("temperature") or 0),
                           "messages": [{"role": "system",
                                         "content": u"你是韩文期刊元数据抽取助手。只输出 JSON。"},
                                        {"role": "user", "content": "\n\n".join(prompts)}]}).encode("utf-8")
        req = urllib.request.Request(cfg["endpoint"], data=body, headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                out = json.loads(resp.read().decode("utf-8"))
            txt = out["choices"][0]["message"]["content"]
            vals = re.findall(r"\{[^{}]*\"value\"[^{}]*\}", txt)
            for t, vj in zip(batch, vals):
                try:
                    t["value"] = json.loads(vj).get("value")
                except Exception:
                    t["value"] = None
            result["called"] += 1
        except Exception as e:
            for t in batch:
                t["error"] = str(e)[:120]
    # 回写 metadata
    for t in tasks:
        if t.get("value") in (None, ""):
            continue
        if t["kind"] in ("title", "title_en", "abstract", "abstract_en"):
            meta.setdefault(t["kind"], {})["value"] = t["value"]
            meta[t["kind"]].setdefault("confidence", {})
            meta[t["kind"]]["confidence"]["source"] = "llm"
        elif t["kind"] == "ref":
            meta["references"][t["index"]][t["field"]] = t["value"]
            meta["references"][t["index"]]["confidence"]["source"] = "regex+llm"
        result["applied"] += 1
    meta["llm"] = result
    io.open(meta_fp, "w", encoding="utf-8").write(json.dumps(meta, ensure_ascii=False, indent=1))
    print("== ④LLM %s == 待办 %d, 调用 %d 批, 回填 %d" % (
        doc, len(tasks), result["called"], result["applied"]))
    return result

def _esc(s):
    return html.escape(str(s if s is not None else ""), quote=False)

def report_meta(docs, out_root):
    """全量 _metadata_report.html: 字段级红绿表(置信着色, 点击跳 1:3 查看器对应页) +
    四层验证指标(L1 机械校验/L2 三渠道互证/L3 数据库对照[留接口]/L4 人工 GT[待标注])。"""
    rows, val_rows, agree_rows = [], [], []
    for doc in docs:
        meta_fp = os.path.join(out_root, doc, "metadata.json")
        if not os.path.exists(meta_fp):
            continue
        m = json.load(io.open(meta_fp, encoding="utf-8"))

        def row(field, label, value, conf, page, extra=""):
            lvl = (conf or {}).get("level", "low")
            src = (conf or {}).get("source", "-")
            basis = (conf or {}).get("basis", "")
            link = "<a href='%s/viewer_compare.html#p%d'>p%d</a>" % (doc, (page - 1) if page else 0, page) if page else "-"
            rows.append(
                "<tr><td>%s</td><td>%s</td><td class=v>%s</td>"
                "<td style='color:%s;font-weight:bold'>%s</td><td>%s</td><td>%s</td><td class=f>%s</td></tr>"
                % (_esc(doc), label, _esc(value)[:180], LV_COLOR.get(lvl, "#333"), lvl,
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
        for r in m.get("references") or []:
            conf = r.get("confidence") or {}
            lv = conf.get("level", "low")
            au = "; ".join(r.get("authors") or [])[:60]
            val = "[%s] %s (%s) %s. %s" % (
                r.get("num") if r.get("num") is not None else "-", au, r.get("year") or "?",
                (r.get("title") or "（题名未抽出）")[:70], r.get("source") or "")
            link = "<a href='%s/viewer_compare.html#p%d'>p%d</a>" % (doc, (r.get("page") or 1) - 1, r.get("page", 0))
            rows.append("<tr><td>%s</td><td>文献#%s</td><td class=v>%s</td>"
                        "<td style='color:%s;font-weight:bold'>%s</td><td>%s</td><td>%s</td><td class=f>%s</td></tr>"
                        % (_esc(doc), r.get("num") if r.get("num") is not None else "-", _esc(val),
                           LV_COLOR.get(lv, "#333"), lv, link, conf.get("source", "-"),
                           _esc(conf.get("basis", ""))))
            ca = conf.get("ch_agree") or {}
            if ca:
                agree_rows.append((doc, r.get("num"), ca))
        # L1/L2 指标
        val = m.get("ref_validation") or {}
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
        for doc0, num, ca in [x for x in agree_rows if x[0] == doc]:
            pass
    # L2 表
    l2_rows = []
    for doc, num, ca in agree_rows:
        cells = "".join("<td>%.0f%%</td>" % (v * 100) for v in ca.values()) or "<td>-</td>"
        l2_rows.append("<tr><td>%s</td><td>#%s</td>%s</tr>" % (_esc(doc), num, cells))
    page = u"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>元数据字段层报告(M5④)</title>
<style>body{font-family:"Microsoft YaHei";max-width:1400px;margin:24px auto;background:#f4f5f7;padding:0 16px}
table{border-collapse:collapse;background:#fff;width:100%%;font-size:12px;margin-bottom:22px}
td,th{border:1px solid #e5e7eb;padding:5px 8px;vertical-align:top}
th{background:#eef2ff;text-align:left}.f{color:#9ca3af;font-size:11px}.v{max-width:640px}
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
</body></html>""" % (
        "\n".join(val_rows),
        "/".join(_esc(k) for k in (agree_rows[0][2].keys() if agree_rows else ["互证"])),
        "\n".join(l2_rows) or "<tr><td colspan=3>无</td></tr>",
        "\n".join(rows))
    fp = os.path.join(out_root, "_metadata_report.html")
    io.open(fp, "w", encoding="utf-8").write(page)
    print("报告 -> %s (%d 字段行, %d L1 校验, %d L2 行)" % (fp, len(rows), len(val_rows), len(l2_rows)))
    return fp

def rebuild_viewer(doc, out_root):
    """按磁盘现有 merged+unified 重建 1:3 对照查看器(不重跑 unify/adapt, M3 标记保留)。
    src_dirs 口径同 pipeline.main: out 内 API 源=raw\<ch\>_api 目录名, out 外归档源=raw\<ch>。"""
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
    fp = os.path.join(doc_dir, "viewer_compare.html")
    nc = pipeline.build_compare_viewer(fp, "%s ｜ 1:3 渠道对照" % doc, sizes, per_ch,
                                       src_dirs, unified_pages=upages or None,
                                       unified_driven=bool(upages), meta=meta)
    print("查看器重建 -> %s (%d 块, unified驱动=%s)" % (fp, nc, bool(upages)))
