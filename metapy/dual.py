# -*- coding: utf-8 -*-
"""批次4d 双渠道对比(正则 vs LLM, selection-only, 绝不回写产品)。
路径隔离(用户拍板①): LLM 数据(llm_refs.json/_dual.json/_choice.json)全落 out_llm_trial/<doc>/;
视图(viewer_dual.html/回显后的 viewer_compare.html)重建进 out/<doc>/——数据基线不可污染, 视图是派生物。
- llm_run_all: 全量条目走 LLM 抽取(8 字段固定 schema), 独立开关 dual_enabled(默认 false, 与 llm_apply 无关)
- compare: 逐字段四态(same/diff/only_regex/only_llm)+ llm_error 第四态; diff 给 difflib ratio
  比对口径: _foldv 折叠(批次3.5 异体表, 防郷/鄕 伪差异刷屏); year 剥著录后缀; pages 归一 ~～–—
- write_choice/load_choices: 人工选择落 _choice.json, 每条带 raw_head=_foldv(raw)[:48] 抗漂移锚
- build_dual_viewer: _dual.json 内联的单文件对比页(双击可开, 零 fetch 依赖); 选择实时 POST
  /api/choice(webapp.py), 失败退 localStorage+导出
不做: 汇总统计(用户明确不要)、不回写 metadata.json、不改 llm_apply/llm_pending_fields。
"""
import os, re, io, json, difflib, datetime
from .common import *
from .llmrep import _llm_cfg, _json_objs

FIELDS = ("title", "container_title", "publisher", "volume", "issue", "pages", "authors", "year")

def trial_root(out_root):
    return os.path.join(os.path.dirname(os.path.abspath(out_root)), "out_llm_trial")

def raw_head(raw):
    return _foldv(raw or "")[:RAW_HEAD_LEN]

def _norm_pages(v):
    return re.sub(r"[\s~～\u2013\u2014]+", "-", str(v or "").strip())

def _norm_year(v):
    return re.sub(r"[a-z]\s*$", "", str(v or "").strip(), flags=re.I)

def _fv_field(f, v):
    if v is None:
        return ""
    if f == "authors":
        return _foldv("; ".join(str(a) for a in v) if isinstance(v, list) else str(v))
    if f == "year":
        return _foldv(_norm_year(v))
    if f == "pages":
        return _foldv(_norm_pages(v))
    return _foldv(str(v))

def _cmp_field(f, rv, lv):
    """四态+ratio: same(折叠后相等, 双空也算) / diff(ratio) / only_regex / only_llm。"""
    rf, lf = _fv_field(f, rv), _fv_field(f, lv)
    if rf and lf:
        if rf == lf:
            return {"regex": rv, "llm": lv, "verdict": "same", "ratio": None}
        return {"regex": rv, "llm": lv, "verdict": "diff",
                "ratio": round(difflib.SequenceMatcher(None, rf, lf).ratio(), 4)}
    if rf:
        return {"regex": rv, "llm": lv, "verdict": "only_regex", "ratio": None}
    if lf:
        return {"regex": rv, "llm": lv, "verdict": "only_llm", "ratio": None}
    return {"regex": rv, "llm": lv, "verdict": "same", "ratio": None}

def _dual_prompt(t, umap):
    u = umap.get(t.get("uid") or "")
    readings = ""
    if u:
        readings = "\n".join("- %s: %s" % (r["ch"], (r.get("text") or "")[:600])
                             for r in u.get("readings") or [])
    return (u"条目抽取[%s#%d] 块uid=%s page=%s\n主读法(raw): %s\n三家读法:\n%s\n"
            u"请从原文抽取以下字段(值未知给 null, 不要编造; authors 为字符串数组, year 纯年份): "
            u"{\"title\":...,\"container_title\":...,\"publisher\":...,\"volume\":...,\"issue\":...,"
            u"\"pages\":...,\"authors\":[...],\"year\":...}" % (
                t["kind"], t.get("index") or 0, t.get("uid"), t.get("page"),
                (t.get("raw") or "")[:800], readings))

def llm_run_all(doc, out_root, cfg=None, opener=None):
    """全量条目 LLM 抽取(不受 pending 限制; 两渠道条目集相等才可比)。只读 metadata.json, 写 trial。"""
    doc_dir = os.path.join(out_root, doc)
    meta_fp = os.path.join(doc_dir, "metadata.json")
    if not os.path.exists(meta_fp):
        print("跳过 dual %s: 无 metadata.json" % doc)
        return None
    cfg = cfg if cfg is not None else _llm_cfg()
    if not cfg.get("dual_enabled"):
        print("== dual %s == dual_enabled=false(跳过; 真调产物只许写 out_llm_trial/)" % doc)
        return None
    key = os.environ.get(cfg.get("api_key_env") or "KCI_LLM_KEY", "")
    if not key or not cfg.get("endpoint"):
        print("== dual %s == 缺 endpoint/api_key, 跳过" % doc)
        return None
    meta = json.load(io.open(meta_fp, encoding="utf-8"))
    entries = []
    for kind, key0 in (("ref", "references"), ("fref", "footnote_refs")):
        for i, r in enumerate(meta.get(key0) or []):
            entries.append({"kind": kind, "index": i, "uid": r.get("uid"), "page": r.get("page"),
                            "num": r.get("num"), "raw": r.get("raw") or ""})
    umap = {}
    udir = os.path.join(doc_dir, "unified")
    if os.path.isdir(udir):
        for f in os.listdir(udir):
            mm = re.match(r"page_(\d+)\.json$", f)
            if mm:
                for u in json.load(io.open(os.path.join(udir, f), encoding="utf-8"))["unified"]:
                    umap[u["uid"]] = u
    import urllib.request
    per = max(1, int(cfg.get("max_fields_per_call") or 8))
    for gi in range(0, len(entries), per):
        batch = entries[gi:gi + per]
        body = json.dumps({"model": cfg.get("model"),
                           "temperature": float(cfg.get("temperature") or 0),
                           "messages": [{"role": "system",
                                         "content": u"你是韩文期刊元数据抽取助手。只输出 JSON。"},
                                        {"role": "user",
                                         "content": "\n\n".join(_dual_prompt(t, umap) for t in batch)}]}
                          ).encode("utf-8")
        req = urllib.request.Request(cfg["endpoint"], data=body, headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + key})
        try:
            op = opener or urllib.request.urlopen
            with op(req, timeout=120) as resp:
                out = json.loads(resp.read().decode("utf-8"))
            objs = _json_objs(out["choices"][0]["message"]["content"])
        except Exception as e:
            for t in batch:
                t["fields"], t["error"] = None, str(e)[:120]
            continue
        for k, t in enumerate(batch):
            t["error"] = None
            if k < len(objs) and isinstance(objs[k], dict):
                t["fields"] = {f: objs[k].get(f) for f in FIELDS}
            else:
                t["fields"], t["error"] = None, u"返回数与任务数不符(%d<%d)" % (len(objs), len(batch))
        if len(objs) > len(batch):
            for t in batch:   # 多返一条: 不致命, 记 error 提示
                t["error"] = (t.get("error") or "") + u"; 多返%d条" % (len(objs) - len(batch))
    tdir = os.path.join(trial_root(out_root), doc)
    if not os.path.isdir(tdir):
        os.makedirs(tdir)
    fp = os.path.join(tdir, "llm_refs.json")
    payload = {"doc": doc,
               "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
               "model": cfg.get("model"), "endpoint": cfg.get("endpoint"),
               "entries": entries}
    io.open(fp, "w", encoding="utf-8").write(json.dumps(payload, ensure_ascii=False, indent=1))
    print("== dual %s == LLM 全量 %d 条 -> %s" % (doc, len(entries), fp))
    return payload

def _llm_maps(llm_entries):
    by_rh, by_uid, by_idx = {}, {}, {}
    for e in llm_entries or []:
        by_rh[(e.get("kind"), raw_head(e.get("raw")))] = e
        by_uid.setdefault((e.get("kind"), e.get("uid")), e)
        by_idx.setdefault((e.get("kind"), e.get("index")), e)
    return by_rh, by_uid, by_idx

def compare(doc, out_root):
    """逐字段四态对比 → out_llm_trial/<doc>/_dual.json(正则侧顺序; 无汇总统计)。"""
    doc_dir = os.path.join(out_root, doc)
    meta = json.load(io.open(os.path.join(doc_dir, "metadata.json"), encoding="utf-8"))
    fp = os.path.join(trial_root(out_root), doc, "llm_refs.json")
    if not os.path.exists(fp):
        print("跳过 compare %s: 无 llm_refs.json(先跑 --stage dual 或 llm_run_all)" % doc)
        return None
    llm = json.load(io.open(fp, encoding="utf-8"))
    by_rh, by_uid, by_idx = _llm_maps(llm.get("entries"))
    out = []
    for kind, key0 in (("ref", "references"), ("fref", "footnote_refs")):
        for i, r in enumerate(meta.get(key0) or []):
            le = by_rh.get((kind, raw_head(r.get("raw")))) \
                 or by_uid.get((kind, r.get("uid"))) \
                 or by_idx.get((kind, i))
            fields, lerr = {}, None
            if le is None:
                for f in FIELDS:
                    fields[f] = {"regex": r.get(f), "llm": None, "verdict": "only_regex", "ratio": None}
            elif le.get("error"):
                lerr = le["error"]
                for f in FIELDS:
                    fields[f] = {"regex": r.get(f), "llm": None, "verdict": "llm_error", "ratio": None}
            else:
                lf = le.get("fields") or {}
                for f in FIELDS:
                    fields[f] = _cmp_field(f, r.get(f), lf.get(f))
            out.append({"kind": kind, "index": i, "num": r.get("num"), "uid": r.get("uid"),
                        "page": r.get("page"), "raw": r.get("raw") or "",
                        "raw_head": raw_head(r.get("raw")), "llm_error": lerr, "fields": fields})
    payload = {"doc": doc, "entries": out}
    tdir = os.path.join(trial_root(out_root), doc)
    if not os.path.isdir(tdir):
        os.makedirs(tdir)
    cfp = os.path.join(tdir, "_dual.json")
    io.open(cfp, "w", encoding="utf-8").write(json.dumps(payload, ensure_ascii=False, indent=1))
    print("== dual %s == 对比 %d 条 -> %s" % (doc, len(out), cfp))
    return payload

def write_choice(doc, out_root, choices):
    tdir = os.path.join(trial_root(out_root), doc)
    if not os.path.isdir(tdir):
        os.makedirs(tdir)
    payload = {"doc": doc, "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
               "choices": choices or []}
    fp = os.path.join(tdir, "_choice.json")
    io.open(fp, "w", encoding="utf-8").write(json.dumps(payload, ensure_ascii=False, indent=1))
    return fp

def load_choices(doc, out_root):
    fp = os.path.join(trial_root(out_root), doc, "_choice.json")
    if not os.path.exists(fp):
        return None
    try:
        return (json.load(io.open(fp, encoding="utf-8")) or {}).get("choices") or []
    except Exception:
        return None

_DUAL_TMPL = u"""<!doctype html><html><head><meta charset="utf-8"><title>双渠道对比 @@DOC@@</title>
<style>
body{font-family:sans-serif;margin:0 0 48px;background:#f8fafc}
header{position:sticky;top:0;background:#0f172a;color:#fff;padding:8px 14px;z-index:5;font-size:13px}
header span{color:#93c5fd;margin-left:12px}
.eb{background:#fff;border:1px solid #cbd5e1;border-radius:6px;margin:12px;padding:10px 14px}
.eb h3{margin:0 0 6px;font-size:13px}.eb h3 .btns{float:right}
.eb table{border-collapse:collapse;width:100%;font-size:12px}
.eb td,.eb th{border:1px solid #e2e8f0;padding:3px 6px;text-align:left}
tr.vd-diff{background:#fef9c3}tr.vd-only{background:#f1f5f9}tr.vd-err{background:#fee2e2}
.btns button{margin-left:6px;font-size:12px}button.on{outline:2px solid #2563eb}
#bar{position:fixed;bottom:0;left:0;right:0;background:#1e293b;color:#e2e8f0;padding:8px 14px;font-size:12px}
</style></head><body>
<header><b>双渠道对比 · @@DOC@@</b><span>正则=metadata.json ｜ LLM=llm_refs.json(out_llm_trial) ｜ 黄=diff 灰=单方 红=LLM错误</span>
<span style="float:right">条目 @@N@@ ｜ 已选 <b id="nch">0</b> 条 <button onclick="exportChoices()">导出选择</button></span></header>
<div id="list"></div>
<div id="bar">点击块右上角按钮选择渠道；经 webapp.py 打开时实时 POST 保存到 out_llm_trial/_choice.json；直连文件打开时存 localStorage 并可导出。</div>
<script>
var D = @@DATA@@;
var FIELDS = @@FIELDS@@;
var choices = {};
try { choices = JSON.parse(localStorage.getItem("dual_choice:"+D.doc) || "{}"); } catch(e) {}
function esc(s){ return (s==null?"":String(s)).replace(/[&<>"]/g, function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]}) }
function render(){
  var h = "";
  D.entries.forEach(function(e, i){
    var key = e.kind + ":" + e.index;
    var cur = choices[key] ? choices[key].pick : null;
    h += '<div class="eb"><h3>[' + e.kind + '#' + (e.num!=null?e.num:e.index) + '] ' + esc((e.raw||"").slice(0,80))
      + (e.llm_error ? ' <span style="color:#dc2626">LLM错误: ' + esc(e.llm_error) + '</span>' : '')
      + '<span class="btns"><button class="'+(cur=="regex"?"on":"")+'" onclick="pick('+i+',\'regex\')">采用正则</button>'
      + '<button class="'+(cur=="llm"?"on":"")+'" onclick="pick('+i+',\'llm\')">采用LLM</button></span></h3>';
    h += '<table><tr><th>字段</th><th>正则值</th><th>LLM值</th><th>相似度</th></tr>';
    FIELDS.forEach(function(f){
      var fd = (e.fields||{})[f] || {};
      var cls = fd.verdict=="diff"?"vd-diff":(fd.verdict=="llm_error"?"vd-err":((fd.verdict=="only_regex"||fd.verdict=="only_llm")?"vd-only":""));
      h += '<tr class="'+cls+'"><td>'+f+'</td><td>'+esc(fd.regex)+'</td><td>'+esc(fd.llm)+'</td><td>'
        + (fd.ratio!=null ? fd.ratio.toFixed(2) : (fd.verdict=="llm_error"?"ERR":"-")) + '</td></tr>';
    });
    h += '</table></div>';
  });
  document.getElementById("list").innerHTML = h;
  document.getElementById("nch").textContent = Object.keys(choices).length;
}
function pick(i, pick_){
  var e = D.entries[i]; var key = e.kind + ":" + e.index;
  var re_obj = {}, ll_obj = {};
  FIELDS.forEach(function(f){ var fd=(e.fields||{})[f]||{}; re_obj[f]=fd.regex!=null?fd.regex:null; ll_obj[f]=fd.llm!=null?fd.llm:null; });
  choices[key] = {kind:e.kind, index:e.index, uid:e.uid, raw_head:e.raw_head, pick:pick_,
                  regex_value:re_obj, llm_value:ll_obj};
  render(); save();
}
function save(){
  localStorage.setItem("dual_choice:"+D.doc, JSON.stringify(choices));
  fetch("/api/choice?doc=" + encodeURIComponent(D.doc), {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({choices: Object.keys(choices).map(function(k){ return choices[k]; })})})
   .then(function(r){ return r.json(); })
   .then(function(j){ setBar(j.ok ? ("已保存 " + Object.keys(choices).length + " 条选择到 out_llm_trial/"+D.doc+"/_choice.json") : ("保存失败: " + (j.err||""))); })
   .catch(function(){ setBar("服务未连(直连文件模式), 选择已存本地; 可导出后放回 out_llm_trial/"+D.doc+"/"); });
}
function setBar(t){ document.getElementById("bar").childNodes[0].textContent = t + "　"; }
function exportChoices(){
  var blob = new Blob([JSON.stringify({doc:D.doc, updated:new Date().toISOString(),
    choices:Object.keys(choices).map(function(k){return choices[k];})}, null, 1)], {type:"application/json"});
  var a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "_choice.json"; a.click();
}
render();
</script></body></html>"""

def build_dual_viewer(doc, out_root):
    """单文件对比页(_dual.json 内联, 双击可开零 fetch) → out/<doc>/viewer_dual.html。"""
    fp = os.path.join(trial_root(out_root), doc, "_dual.json")
    if not os.path.exists(fp):
        print("跳过 viewer_dual %s: 无 _dual.json" % doc)
        return None
    dual = json.load(io.open(fp, encoding="utf-8"))
    data = json.dumps(dual, ensure_ascii=False).replace("</", "<\\/")
    html = (_DUAL_TMPL.replace("@@DOC@@", doc)
            .replace("@@N@@", str(len(dual.get("entries") or [])))
            .replace("@@DATA@@", data)
            .replace("@@FIELDS@@", json.dumps(list(FIELDS))))
    out_fp = os.path.join(out_root, doc, "viewer_dual.html")
    io.open(out_fp, "w", encoding="utf-8").write(html)
    print("对比页 -> %s (%d 条)" % (out_fp, len(dual.get("entries") or [])))
    return out_fp

def run(doc, out_root):
    """--stage dual: 全量 LLM 抽取(dual_enabled 门) → 对比 → 对比页。"""
    llm_run_all(doc, out_root)
    if compare(doc, out_root):
        build_dual_viewer(doc, out_root)

# —— 契约 selftest(python -m metapy.dual; mock opener 不触网, tmp 目录不碰 out/) ——
def _selftest():
    import hashlib, shutil, tempfile
    from .llmrep import _MockResp
    os.environ["KCI_LLM_KEY"] = "t"
    n_ok = 0
    tmp = tempfile.mkdtemp()
    try:
        out_root = os.path.join(tmp, "out")
        dd = os.path.join(out_root, "d")
        os.makedirs(os.path.join(dd, "unified"))
        refs = [
            {"raw": u"A(2001), Same, 학술지 3", "title": u"Same", "container_title": None,
             "publisher": None, "volume": None, "issue": None, "pages": u"5~6",
             "authors": [u"A"], "year": "2001", "uid": "u1", "page": 1, "num": 1,
             "confidence": {"level": "high"}},
            {"raw": u"B(1982), Diff Title, Src", "title": u"Diff Title", "container_title": None,
             "publisher": u"Pub", "volume": None, "issue": None, "pages": None,
             "authors": [u"B"], "year": "1982a", "uid": "u2", "page": 1, "num": 2,
             "confidence": {"level": "mid"}},
        ]
        frefs = [{"raw": u"C, 「T」, 2005.", "title": None, "container_title": None, "publisher": None,
                  "volume": None, "issue": None, "pages": None, "authors": [u"C"], "year": "2005",
                  "uid": "u3", "page": 2, "num": "3", "confidence": {"level": "mid"}}]
        meta = {"doc": "d", "references": refs, "footnote_refs": frefs}
        mfp = os.path.join(dd, "metadata.json")
        io.open(mfp, "w", encoding="utf-8").write(json.dumps(meta, ensure_ascii=False))
        io.open(os.path.join(dd, "unified", "page_1.json"), "w", encoding="utf-8").write(json.dumps(
            {"unified": [{"uid": "u1", "readings": [{"ch": "m", "text": "R1"}]},
                         {"uid": "u2", "readings": [{"ch": "m", "text": "R2"}]},
                         {"uid": "u3", "readings": [{"ch": "m", "text": "R3"}]}]}, ensure_ascii=False))
        h0 = hashlib.md5(io.open(mfp, "rb").read()).hexdigest()
        cfg = {"enabled": True, "dual_enabled": True, "endpoint": "mock://x", "model": "m",
               "api_key_env": "KCI_LLM_KEY", "temperature": 0, "max_fields_per_call": 8}
        # mock: ref0 全等(含 pages ~归一/year 后缀剥离), ref1 diff+only_*, fref 少返一条→error
        objs = [
            {"title": u"Same", "container_title": None, "publisher": None, "volume": None,
             "issue": None, "pages": "5-6", "authors": [u"A"], "year": "2001"},
            {"title": u"Diff Title!", "container_title": u"Src", "publisher": None, "volume": None,
             "issue": None, "pages": None, "authors": [u"B"], "year": "1982"},
        ]
        op = lambda req, timeout=120: _MockResp(
            {"choices": [{"message": {"content": "\n".join(json.dumps(o, ensure_ascii=False) for o in objs)}}]})
        res = llm_run_all("d", out_root, cfg=cfg, opener=op)
        ok1 = (res and len(res["entries"]) == 3
               and hashlib.md5(io.open(mfp, "rb").read()).hexdigest() == h0
               and res["entries"][2].get("error"))
        if ok1:
            n_ok += 1
        else:
            print("FAIL [dual-run-all]")
        dual = compare("d", out_root)
        f = lambda k, i: dual["entries"][i]["fields"][k]
        ok2 = (dual and len(dual["entries"]) == 3
               and f("title", 0)["verdict"] == "same"
               and f("pages", 0)["verdict"] == "same"          # 5~6 vs 5-6 归一
               and f("year", 1)["verdict"] == "same"           # 1982a vs 1982 后缀剥离
               and f("title", 1)["verdict"] == "diff" and f("title", 1)["ratio"] < 1
               and f("publisher", 1)["verdict"] == "only_regex"
               and f("container_title", 1)["verdict"] == "only_llm"
               and all(f(k, 2)["verdict"] == "llm_error" for k in FIELDS)
               and dual["entries"][2]["llm_error"])
        if ok2:
            n_ok += 1
        else:
            print("FAIL [dual-compare] %s" % json.dumps(
                dual["entries"][1]["fields"], ensure_ascii=False)[:200] if dual else "no dual")
        cfp = write_choice("d", out_root, [{"kind": "ref", "index": 1, "uid": "u2",
                                            "raw_head": raw_head(refs[1]["raw"]),
                                            "pick": "llm", "regex_value": {}, "llm_value": {}}])
        ok3 = cfp and load_choices("d", out_root) and load_choices("d", out_root)[0]["pick"] == "llm"
        if ok3:
            n_ok += 1
        else:
            print("FAIL [dual-choice]")
        vfp = build_dual_viewer("d", out_root)
        html = io.open(vfp, encoding="utf-8").read() if vfp else ""
        ok4 = (vfp and "vd-diff" in html and "Diff Title" in html and '"doc": "d"' in html
               and "out_llm_trial" in html)
        if ok4:
            n_ok += 1
        else:
            print("FAIL [dual-viewer]")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("dual selftest: %d/4 PASS" % n_ok)
    return n_ok == 4

if __name__ == "__main__":
    raise SystemExit(0 if _selftest() else 1)
