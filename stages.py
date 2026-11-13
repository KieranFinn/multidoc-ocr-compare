# -*- coding: utf-8 -*-
"""stages.py — 按阶段独立重跑(工作台二级菜单的执行端)。

为什么要有这一层(2026-11-12):
  原来"重跑"只有两种粒度: pipeline.main 全链, 或跳过 NER 的全链。改一行 refs.py 的判据
  也得重跑 OCR 三渠道(分钟级), 而 refs/NER/查看器 本来就只读 unified/, 秒级可完成。
  这里把链路拆成 17 个阶段, 每个阶段只依赖磁盘上已存在的产物, 因此可以任意勾选子集重跑。

设计约束:
  单一实现 — 前段(render/acquire/adapt/merge/unify/index)全部调用 pipeline / merge_out /
             unify 里的既有函数, 不复制逻辑; 后链复用 metapy.*_doc 与 ner_lite, 与 CLI 同源。
  依赖显式 — 每阶段声明 requires(产物键), 缺产物即拒跑并报"缺什么", 不做静默降级;
             GUI 置灰只是提示, 真正的闸门在 run() 里(防绕过)。
  按序执行 — 传入集合按 ORDER 排序执行, 与 pipeline.main 的原有顺序一致。

用法:
  python stages.py list                        # 阶段表 + 当前文档可用性
  python stages.py <doc> refs,front            # 只跑后链两段
  python stages.py <doc> all                   # 全链
  python stages.py <doc> all --pdf uploads/x.pdf
  python stages.py --batch a,b,c all --workers 5   # 5 篇并发(一篇一子进程)
"""
import io
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

OUT = os.path.join(HERE, "out")
UP = os.path.join(HERE, "uploads")
CH = ("mineru", "dots", "paddle")
CH_ZH = {"mineru": "MinerU", "dots": "dots.ocr", "paddle": "PaddleOCR"}


# ---------- 产物探测(依赖判据的唯一来源) ----------

def _has(d, pattern=None):
    """目录下有(符合 pattern 的)文件。用 scandir 并显式关闭, 防 Windows 拖住句柄。"""
    if not os.path.isdir(d):
        return False
    with os.scandir(d) as it:
        if pattern is None:
            return any(True for _ in it)
        return any(pattern.match(e.name) for e in it if e.is_file())


_PAGE_RE = re.compile(r"page_\d+\.jpg$")
_MERGED_RE = re.compile(r"page_\d+\.json$")
_NER_REL = os.path.join("out_llm_trial", "fds_extract", "ner_lite")

ARTIFACTS = {
    "pages":   lambda dd: _has(os.path.join(dd, "pages"), _PAGE_RE),
    "raw":     lambda dd: any(_has(os.path.join(dd, "raw", c)) or _has(os.path.join(dd, "raw", c + "_api"))
                              for c in CH),
    "merged":  lambda dd: _has(os.path.join(dd, "merged"), _MERGED_RE),
    "unified": lambda dd: _has(os.path.join(dd, "unified"), _MERGED_RE),
    "metadata": lambda dd: os.path.isfile(os.path.join(dd, "metadata.json")),
    "ner":     lambda dd: os.path.isfile(os.path.join(
                   HERE, _NER_REL, os.path.basename(dd), "ner_lite.json")),
}
ARTIFACT_ZH = {"pages": "页图 pages/*.jpg", "raw": "渠道原始产物 raw/<ch>",
               "merged": "合并产物 merged/", "unified": "归一化层 unified/",
               "metadata": "元数据 metadata.json", "ner": "实体产物 ner_lite.json"}


def _sizes(doc_dir):
    """页尺寸 [(w,h)]。按页号数值排序——merge_out 的磁盘兜底用字符串排序,
    page_10 会排到 page_2 前(11 页以上即错位), 这里显式给对的顺序。"""
    pages_dir = os.path.join(doc_dir, "pages")
    if not os.path.isdir(pages_dir):
        return []
    files = [f for f in os.listdir(pages_dir) if _PAGE_RE.match(f)]
    files.sort(key=lambda s: int(re.search(r"(\d+)", s).group(1)))
    from PIL import Image
    out = []
    for f in files:
        with Image.open(os.path.join(pages_dir, f)) as im:
            out.append(im.size)
    return out


def _read_json(fp):
    return json.load(io.open(fp, encoding="utf-8"))


# ---------- 各阶段实现 ----------

def _st_render(ctx):
    import pipeline
    sizes = pipeline.render_pages(ctx["pdf"], os.path.join(ctx["doc_dir"], "pages"))
    return "页图 %d 张" % len(sizes)


def _st_acquire(ctx):
    import pipeline
    pipeline.CFG = pipeline.ensure_cfg() if pipeline.CFG is None else pipeline.CFG
    chans, src = pipeline.fetch_channels(ctx["doc"], ctx["pdf"], ctx["doc_dir"],
                                         {"mineru": None, "dots": None, "paddle": None},
                                         echo=ctx["log"])
    got = [c for c in CH if chans.get(c)]
    ctx["chans"] = chans
    return "已获取: " + (", ".join("%s(%s)" % (c, src.get(c, "?")) for c in got) or "无")


def _st_adapt(ctx):
    """从 raw/ 重新适配并重建单渠道查看器(产物: viewer_<ch>.html)。"""
    import merge_out
    import pipeline
    sizes = ctx["sizes"] or _sizes(ctx["doc_dir"])
    if not sizes:
        raise RuntimeError("缺 pages/*.jpg, 无法确定页尺寸")
    out = ctx["doc_dir"]
    got = []
    for ch in CH:
        pp, md, src = merge_out.adapt_channel(out, ch, sizes)
        if pp is None and md is None:
            continue
        vw = os.path.join(out, "viewer_%s.html" % ch)
        title = "%s ｜ %s" % (ctx["doc"], {"mineru": "MinerU（云端 API·非OCR）",
                                          "dots": "dots.ocr（小红书）",
                                          "paddle": "PaddleOCR-VL-1.6（在线）"}[ch])
        if md is not None:
            pipeline.build_viewer(vw, title, sizes, md_text=md)
        else:
            pipeline.build_viewer(vw, title, sizes, per_page=pp,
                                  calib_note=("%s -> %s" % (ch, pipeline.mineru_calib_key(src)))
                                  if ch == "mineru" else None,
                                  img_prefix=pipeline._out_rel(src, out) or ("raw/%s/" % ch))
        got.append(ch)
    if not got:
        raise RuntimeError("raw/ 下无可用渠道产物")
    return "已适配: " + ", ".join(CH_ZH[c] for c in got)


def _st_merge(ctx):
    import merge_out
    sizes = ctx["sizes"] or _sizes(ctx["doc_dir"])
    if not sizes:
        raise RuntimeError("缺 pages/*.jpg, 无法确定页尺寸")
    info = merge_out.merge_doc(ctx["doc"], ctx["doc_dir"], sizes=sizes)
    ctx["sizes"] = sizes
    return "merged: %d 页 / %d 块" % (info["pages"], info["blocks"])


def _st_unify(ctx):
    import unify
    st = unify.unify_doc(ctx["doc"], ctx["out_root"])
    return str(st)[:160] if st else "unify 无返回"


def _meta_stage(name):
    fname = META_FN[name]

    def _run(ctx):
        import metapy
        fn = getattr(metapy, fname, None)
        if fn is None:                       # 注册表与包出口不同步时, 报名字而不是 AttributeError 原文
            raise RuntimeError("metapy 缺入口 %s(阶段 %s)" % (fname, name))
        fn(ctx["doc"], ctx["out_root"])
        msg = "%s 完成" % name
        if name == "refs":
            # 早退(无文献区)不会抛异常, 但产物是空的——把守卫状态带进阶段结果,
            # 否则"跑完了 0 条"和"跑出 46 条"在菜单里长得一样(13563525 教训)。
            fp = os.path.join(ctx["doc_dir"], "metadata_audit.json")
            if os.path.isfile(fp):
                g = (_read_json(fp).get("ref_yield_guard") or {})
                # 守卫是三态 PASS/RED/AMBER(见 metapy/refs.py:_yield_guard), PASS 是健康态。
                if g.get("state") in ("RED", "AMBER"):
                    ctx["warn"] = "ref_yield_guard=%s: %s" % (g["state"], g.get("reason") or "")
                    msg = u"%s 完成 ⚠ 守卫 %s" % (name, g["state"])
        return msg
    return _run


def _st_ner(ctx):
    import ner_lite
    import pipeline
    cfg = (pipeline.load_cfg().get("llm") or {})
    if not (os.environ.get("KCI_ZJLAB_KEY") or os.environ.get("KCI_LLM_KEY")):
        raise RuntimeError("缺 KCI_LLM_KEY / KCI_ZJLAB_KEY")
    model = ner_lite.make_model(cfg)
    product, audit = ner_lite.run_doc(ctx["doc"], model, echo=ctx["log"])
    ner_lite.write_outputs(ctx["doc"], product, audit)
    c = audit["counts"]
    ctx["ner_counts"] = c
    return "提名 %d 准入 %d 记录 %d (丢弃 anchor_miss %d)" % (
        c["nominations"], c["admitted"], c["records"], c["dropped_anchor_miss"])


def _st_viewer(ctx):
    from metapy.llmrep import rebuild_viewer
    rebuild_viewer(ctx["doc"], ctx["out_root"])
    return "viewer_compare.html 已重建"


def _st_index(ctx):
    """按磁盘产物重建总览: 渠道块数/韩文字符/FFFD 从 merged.json 统计, 不重跑适配。"""
    import pipeline
    doc, out = ctx["doc"], ctx["doc_dir"]
    mj_fp = os.path.join(out, "merged.json")
    per_ch = {c: {"n": 0, "hang": 0, "fffd": 0} for c in CH}
    if os.path.isfile(mj_fp):
        for pg in _read_json(mj_fp).get("page_data") or []:
            for ch, blks in (pg.get("channels") or {}).items():
                if ch not in per_ch:
                    continue
                t = "".join((b.get("text") or "") for b in blks)
                per_ch[ch]["n"] += len(blks)
                per_ch[ch]["hang"] += len(re.findall(r"[\uac00-\ud7a3]", t))
                per_ch[ch]["fffd"] += t.count("\ufffd")
    report = []
    titles = {"mineru": "MinerU（云端 API·非OCR）", "dots": "dots.ocr（小红书）",
              "paddle": "PaddleOCR-VL-1.6（在线）"}
    for ch in CH:
        d = per_ch[ch]
        nraw = 0
        for sub in (ch, ch + "_api"):
            p = os.path.join(out, "raw", sub)
            if os.path.isdir(p):
                nraw += sum(len(fs) for _, _, fs in os.walk(p))
        status = "OK" if d["n"] else "无产物"
        report.append((titles[ch], status, d["n"], d["hang"], d["fffd"], "viewer_%s.html" % ch, nraw))
    sizes = ctx["sizes"] or _sizes(out)
    pdf_name = os.path.basename(ctx["pdf"]) if ctx.get("pdf") else doc + ".pdf"
    pipeline.write_index(doc, out, report, os.path.join(out, "viewer_compare.html"), sizes, pdf_name)
    return "index.html 已重建 (%d 渠道)" % sum(1 for r in report if r[2])


# ---------- 阶段表 ----------

STAGES = [
    # id, 分组, 名称, 说明, 依赖产物, 需 PDF
    ("render",  "① 取数", "页图渲染",     "PDF → pages/page_N.jpg",        (), True),
    ("acquire", "① 取数", "渠道获取",     "API/缓存/inbox 取三渠道原始产物",  (), True),
    ("adapt",   "① 取数", "渠道适配",     "raw/ → 单渠道查看器",            ("pages", "raw"), False),
    ("merge",   "② 归一", "跨渠道合并",   "merged/ + merged.json + .md",   ("pages", "raw"), False),
    ("unify",   "② 归一", "归一化层",     "merged/ → unified/",            ("merged",), False),
    ("m3",      "③ 元数据", "跨页拼接",   "unified 跨页块拼接(会写回)",      ("unified",), False),
    ("refs",    "③ 元数据", "参考文献",   "references[] + 条目校验",        ("unified",), False),
    ("frefs",   "③ 元数据", "脚注引文",   "footnote_refs[]",               ("unified",), False),
    ("front",   "③ 元数据", "题名作者机构", "title/author/affiliations",     ("unified",), False),
    ("abs",     "③ 元数据", "摘要关键词", "abstract/keywords",             ("unified",), False),
    ("issue",   "③ 元数据", "期刊年卷期", "journal/year/volume/issue",     ("unified", "metadata"), False),
    ("cspan",   "③ 元数据", "引文句",     "citation_spans[]",              ("unified", "metadata"), False),
    ("ner",     "④ 实体/呈现", "实体抽取", "ner_lite.json(需 LLM key)",     ("unified",), False),
    ("viewer",  "④ 实体/呈现", "对照查看器", "viewer_compare.html 重建",     ("merged",), False),
    ("index",   "④ 实体/呈现", "结果总览", "index.html 重建",               ("pages",), False),
]

BY_ID = {s[0]: s for s in STAGES}
ORDER = [s[0] for s in STAGES]
META_IDS = ("m3", "refs", "frefs", "front", "abs", "issue", "cspan")


def _stage_missing(sid, dd, have, pdf=None):
    miss = [ARTIFACT_ZH[r] for r in BY_ID[sid][4] if not have.get(r)]
    if BY_ID[sid][5] and not pdf:
        miss.append("uploads/%s.pdf" % os.path.basename(dd))
    return miss

# 阶段 id -> metapy 入口函数名。不能靠 "<id>_doc" 拼: abs 的入口是 abstract_doc
# (2026-11-12 实跑踩过: 全链跑到 abs 抛 module 'metapy' has no attribute 'abs_doc')。
META_FN = {"m3": "m3_doc", "refs": "refs_doc", "frefs": "frefs_doc", "front": "front_doc",
           "abs": "abstract_doc", "issue": "issue_doc", "cspan": "cspan_doc"}

RUNNERS = {
    "render": _st_render, "acquire": _st_acquire, "adapt": _st_adapt,
    "merge": _st_merge, "unify": _st_unify, "ner": _st_ner,
    "viewer": _st_viewer, "index": _st_index,
}
for _m in META_IDS:
    RUNNERS[_m] = _meta_stage(_m)

PRESETS = [
    ("全链(等价一键重跑)", ORDER),
    ("只后链(元数据+实体)", list(META_IDS) + ["ner"]),
    ("只重建呈现(查看器+总览)", ["viewer", "index"]),
    ("只归一化及以下", ["merge", "unify"]),
]
# 菜单默认勾选: 全选。因为「一键重跑」主区的动作 = 执行当前勾选(= 菜单里的"执行选中阶段"),
# 默认若不勾全, 这个键就不再是"一键重跑"了。想少跑/不烧 LLM 有两条路:
# 「快速重跑(跳NER)」独立键, 或在箭头菜单里取消勾选后回来点主区。
DEFAULT_IDS = list(ORDER)


def _check_registry():
    """注册表自检: 每段都要有执行函数, 每个元数据阶段都要在 metapy 里有入口。

    这类错(如把 abstract_doc 拼成 abs_doc)原本只在跑到那一段时才炸, 而那时前 9 段
    已经改过磁盘了(2026-11-12 全链实跑实证)。开跑前先查, 出错点=0 次写盘。
    """
    miss = [s for s in ORDER if s not in RUNNERS]
    if miss:
        raise RuntimeError("阶段缺执行函数: %s" % ", ".join(miss))
    import metapy
    bad = ["%s->%s" % (k, v) for k, v in META_FN.items() if not hasattr(metapy, v)]
    if bad:
        raise RuntimeError("metapy 缺入口: %s" % ", ".join(bad))


def doc_dir_of(doc, out_root=None):
    return os.path.join(out_root or OUT, doc)


def availability(doc, out_root=None):
    """{id: (可跑, 缺什么)}——GUI 置灰与 CLI 拒跑共用同一判据。"""
    dd = doc_dir_of(doc, out_root)
    have = {k: bool(f(dd)) for k, f in ARTIFACTS.items()}
    res = {}
    for sid, _g, _l, _h, req, need_pdf in STAGES:
        pdf = _find_pdf(doc) if need_pdf else None
        miss = _stage_missing(sid, dd, have, pdf)
        res[sid] = (not miss, miss, pdf)
    return res


def _find_pdf(doc):
    if os.path.isdir(UP):
        for f in sorted(os.listdir(UP)):
            if f.lower().endswith(".pdf") and re.sub(r"\.pdf$", "", f, flags=re.I) == doc:
                return os.path.join(UP, f)
    return None


def plan(ids, doc, out_root=None):
    """当前产物快照下的 (按序 id 列表, 错误列表)，供菜单和命令行预览。"""
    avail = availability(doc, out_root)
    bad_meta = [i for i in ids if i not in BY_ID]
    ordered = [i for i in ORDER if i in set(ids)]
    errs, ok = [], []
    for i in ordered:
        miss = avail[i][1]
        if miss:
            errs.append("%s(%s): 缺 %s" % (i, BY_ID[i][2], "、".join(miss)))
        else:
            ok.append(i)
    errs.extend("未知阶段: %s" % i for i in bad_meta)
    return ok, errs


def run(doc, ids, pdf=None, out_root=None, log=print):
    """按序执行所选阶段。返回 {"doc","results","ok"}。

    results[stage] = {"ok":bool, "skipped":bool, "msg":str, "elapsed":int}
    每段执行前重新检查依赖，因此本阶段新生成的产物可供下一段立即使用。
    任一阶段缺依赖或失败即停(skipped 标余下)——链式阶段在下游跑等于用坏输入产出坏产物。
    """
    out_root = out_root or OUT
    dd = doc_dir_of(doc, out_root)
    _check_registry()
    ids = list(ids)
    todo = [i for i in ORDER if i in set(ids)]
    unknown = [i for i in ids if i not in BY_ID]
    results = {}
    if unknown:
        for sid in unknown:
            log("[阶段] 拒绝 未知阶段: " + sid)
        return {"doc": doc, "results": results, "ok": False,
                "error": "未知阶段: " + ", ".join(unknown)}
    if not todo:
        return {"doc": doc, "results": results, "ok": False, "error": "无可执行阶段: 未选择"}
    pdf = pdf or _find_pdf(doc)
    ctx = {"doc": doc, "doc_dir": dd, "out_root": out_root, "pdf": pdf,
           "log": log, "sizes": _sizes(dd) if ARTIFACTS["pages"](dd) else []}
    for pos, sid in enumerate(todo):
        have = {k: bool(f(dd)) for k, f in ARTIFACTS.items()}
        miss = _stage_missing(sid, dd, have, pdf)
        if miss:
            error = "%s(%s): 缺 %s" % (sid, BY_ID[sid][2], "、".join(miss))
            log("[阶段] 拒绝 " + error)
            results[sid] = {"ok": False, "skipped": False, "elapsed": 0, "msg": error}
            for rest in todo[pos + 1:]:
                results[rest] = {"ok": False, "skipped": True, "elapsed": 0,
                                 "msg": "上游 %s 缺依赖" % sid}
            return {"doc": doc, "results": results, "ok": False, "error": error}
        t0 = time.time()
        log("[阶段] %s %s …" % (sid, BY_ID[sid][2]))
        ctx.pop("warn", None)
        try:
            msg = RUNNERS[sid](ctx) or ""
            el = int(time.time() - t0)
            results[sid] = {"ok": True, "skipped": False, "msg": msg, "elapsed": el}
            if ctx.get("warn"):
                results[sid]["warn"] = ctx["warn"]
                log("[阶段] %s 警告: %s" % (sid, ctx["warn"]))
            log("[阶段] %s 完成 %ds %s" % (sid, el, msg))
        except Exception as e:
            el = int(time.time() - t0)
            results[sid] = {"ok": False, "skipped": False, "msg": str(e)[:200], "elapsed": el}
            log("[阶段] %s 失败 %ds: %s" % (sid, el, str(e)[:160]))
            for rest in todo[pos + 1:]:
                results[rest] = {"ok": False, "skipped": True, "elapsed": 0,
                                 "msg": "上游 %s 失败" % sid}
            return {"doc": doc, "results": results, "ok": False, "error": str(e)[:200]}
    return {"doc": doc, "results": results, "ok": True}


# ---------- 并发: 一篇一进程 ----------
#
# 为什么是进程而不是线程(2026-11-13):
#   阶段里 CPU 密集段(refs 正则、unify 的 dice 矩阵、md2html)在 GIL 下线程并发等于没并发;
#   更麻烦的是**全局污染**: pipeline.log 是模块级函数(被 GUI 猴补丁替换过), pipeline.CFG 是
#   模块级配置 —— 多线程跑不同文档时日志会串台、配置会互相看见。进程隔离一次解决两件事,
#   顺带让"改了抽取器代码不用重启 GUI"变成天然成立(每个子进程新 import)。
#
# 为什么必须带闸(见 crossproc.py):
#   并发的代价不在本机, 在 LLM/渠道 API 这些共享且失败静默的外部资源上。


def _child_entry(job, q, out_root):
    """子进程体: 跑一篇。所有日志(含第三方 print)逐行经 q 回主进程。

    必须模块顶层 —— Windows 用 spawn, 子进程要重新 import 本模块再反序列化入口。
    """
    import sys as _sys

    doc = job["doc"]

    def _emit(kind, payload):
        """往主进程送一条消息。父进程已经退出时**必须丢弃而不是阻塞**:
        队列底层是有界管道, 没人读时 put 会永远卡住 —— 那样 GUI 一关, 子进程就僵在这里,
        产物永远停在一半。丢日志是可接受的, 卡死不是。"""
        for _ in range(3):
            try:
                q.put((kind, doc, payload), timeout=5)
                return True
            except Exception:
                pass
        return False

    class _W(io.TextIOBase):
        def write(self, s):
            for line in str(s).splitlines():
                if line.strip():
                    _emit("log", line)
            return len(s)
        def flush(self):
            pass

    _sys.stdout = _sys.stderr = _W()
    try:
        import pipeline
        pipeline.log = lambda *a: _sys.stdout.write(
            "[pipeline] " + " ".join(str(x) for x in a) + "\n")
    except Exception:
        pass
    res = {"doc": doc, "results": {}, "ok": False, "error": ""}
    try:
        res = run(doc, job["ids"], pdf=job.get("pdf"), out_root=out_root,
                  log=lambda s: _sys.stdout.write(str(s) + "\n"))
    except BaseException as e:            # 子进程里任何异常都要变成结果, 不许静默消失
        res = {"doc": doc, "results": {}, "ok": False,
               "error": "%s: %s" % (type(e).__name__, str(e)[:200])}
    _emit("done", res)


def run_batch(jobs, workers=5, log=print, out_root=None):
    """并发跑多篇(一篇一进程), 返回 {doc: result}。

    jobs  = [{"doc":..., "ids":[...], "pdf":... or None}, ...]
    workers 上限受 len(jobs) 约束; 任一子进程崩溃只影响它自己, 其余照跑(失败隔离)。
    日志顺序即到达顺序(多篇交错), 每行带 [doc] 前缀 —— 并发下"哪篇在说什么"比"顺序好看"重要。
    """
    import multiprocessing as mp
    from queue import Empty

    out_root = out_root or OUT
    jobs = list(jobs)
    results = {}
    if not jobs:
        return results
    workers = max(1, min(int(workers), len(jobs)))
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    pending = list(jobs)
    running = {}                       # doc -> Process
    nlog = lambda d, s: log("[%s] %s" % (d, s) if workers > 1 else s)

    def _start(job):
        p = ctx.Process(target=_child_entry, args=(job, q, out_root), daemon=False)
        p.start()
        running[job["doc"]] = p

    try:
        while pending or running:
            while pending and len(running) < workers:
                _start(pending.pop(0))
            got = None
            try:
                got = q.get(timeout=0.3)
            except Empty:
                pass
            if got:
                kind, doc, payload = got
                if kind == "log":
                    nlog(doc, payload)
                else:
                    results[doc] = payload
            for doc, p in list(running.items()):
                if not p.is_alive():
                    p.join()
                    if doc not in results:
                        results[doc] = {"doc": doc, "results": {}, "ok": False,
                                        "error": "子进程异常退出(exitcode=%s)" % p.exitcode}
                    running.pop(doc)
    finally:
        for doc, p in running.items():   # Ctrl-C / 上层异常: 不留孤儿进程
            try:
                p.terminate()
            except Exception:
                pass
    # 子进程可能在被 terminate 前已写 done 但主循环没读到, 补齐顺序
    return {j["doc"]: results.get(j["doc"], {"doc": j["doc"], "results": {}, "ok": False,
                                             "error": "无结果返回"}) for j in jobs}


def run_one_child(job, log=print, out_root=None):
    """单篇也走子进程(与批量同一实现): GUI 用它做并发的工作单元。"""
    return run_batch([job], workers=1, log=log, out_root=out_root)[job["doc"]]


def find_pdf(doc):
    return _find_pdf(doc)


# ---------- CLI ----------

def _main(argv):
    """唯一的命令行入口(2026-11-13 收口)。

    历史上"跑一次"有 5 个命令: pipeline.py(全链) / stages.py(分段) / metadata.py(元数据) /
    unify.py / merge_out.py。它们跑的是同一条链的不同切片, 用户得先记住"这次该用哪个"。
    现在统一到本入口的子命令, 其余文件保留 CLI 只为向后兼容(且各自 import 本模块转发):

        python stages.py list [doc]                 阶段表 + 该文档哪些能跑
        python stages.py <doc> <stage[,stage...]>   跑阶段(旧语法, 保持不变)
        python stages.py --batch a,b,c <stage|all> [--workers N]   多篇并发
        python stages.py archive add|list|check|restore|export
        python stages.py audit [doc] [--html f]     字段值后验
        python stages.py serve                     起浏览器工作台
        python stages.py ui                        起桌面工作台
    """
    if not argv or argv[0] in ("list", "-l"):
        doc = argv[1] if len(argv) > 1 else None
        avail = availability(doc) if doc else {}
        for sid, group, label, hint, req, npdf in STAGES:
            flag = ""
            if doc:
                ok, miss, _ = avail[sid]
                flag = "  ✔" if ok else "  ✘ 缺 " + "、".join(miss)
            print("%-8s %-10s %-14s %s%s" % (sid, group, label, hint, flag))
        if not doc:
            print("\n用法: python stages.py <doc> <stage[,stage...]|all> [--pdf x.pdf]")
            print("      python stages.py --batch doc1,doc2,... <stage|all> [--workers 5]")
            print("      python stages.py archive <add|list|check|restore|export> ...")
            print("      python stages.py audit [doc] [--all] [--html 报告.html]")
            print("      python stages.py serve | ui")
            print("文档: 归档/后验/UI 都由本入口转派, 不必再记其它脚本名。")
            print("预设: " + "; ".join("%s=%s" % (n, ",".join(ids)) for n, ids in PRESETS))
        return 0

    workers = 1
    if "--workers" in argv:
        i = argv.index("--workers")
        try:
            workers = max(1, int(argv[i + 1]))
        except (IndexError, ValueError):
            print("--workers 需要一个整数"); return 2
        del argv[i:i + 2]

    # 并发批量: 篇与篇之间互不依赖, 各写自己的 out/<doc>/, 因此可以同时跑。
    # 单篇仍走原路径(同进程), 批量才起子进程 —— 不为一次跑一篇付进程启动开销。
    if argv and argv[0] in ("--batch", "-b"):
        if len(argv) < 2:
            print("用法: python stages.py --batch doc1,doc2,... [all|阶段列表] [--workers 5]")
            return 2
        docs = [x for x in re.split(r"[,\s]+", argv[1]) if x]
        spec = argv[2] if len(argv) > 2 and not str(argv[2]).startswith("-") else "all"
        ids = ORDER if spec == "all" else [x.strip() for x in spec.split(",") if x.strip()]
        import archive
        jobs, skipped = [], []
        for d in docs:
            if archive.is_archived(d):
                skipped.append("%s(已归档)" % d)
                continue
            jobs.append({"doc": d, "ids": ids, "pdf": _find_pdf(d)})
        for s in skipped:
            print("[批量] 跳过 %s" % s)
        if not jobs:
            print("[批量] 无可执行文档"); return 2
        print("[批量] %d 篇 × %d 段 · 并发 %d" % (len(jobs), len(ids), min(workers, len(jobs))))
        t0 = time.time()
        res = run_batch(jobs, workers=workers, log=lambda s: print(s))
        wall = time.time() - t0
        seq = sum(sum(int(v.get("elapsed") or 0) for v in ((r.get("results") or {}).values()))
                  for r in res.values())
        print("\n===== 汇总: 墙钟 %.0fs · 各篇阶段耗时合计 %ds · 并发 %d =====" % (
            wall, seq, min(workers, len(jobs))))
        for j in jobs:
            r = res.get(j["doc"]) or {}
            st = (r.get("results") or {})
            nok = sum(1 for v in st.values() if v.get("ok"))
            print("%-24s %s  %d/%d 段%s" % (
                j["doc"], "OK  " if r.get("ok") else "FAIL", nok, len(st),
                ("  " + str(r.get("error"))[:80]) if r.get("error") else ""))
        return 0 if all((res.get(j["doc"]) or {}).get("ok") for j in jobs) else 1

    app = argv[0]
    if app == "archive":
        import archive
        return archive._cli(argv[1:])
    if app == "audit":
        from metapy import audit_value
        return audit_value._main(argv[1:])
    if app in ("serve", "web"):
        import subprocess, sys as _sys
        return subprocess.call([_sys.executable,
                                os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp.py")])
    if app in ("ui", "gui"):
        import subprocess, sys as _sys
        return subprocess.call([_sys.executable,
                                os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui.py")])

    doc = argv[0]
    # 已归档的文档不该再从命令行重跑: 产物在 archive/ 里, 再跑一遍会在 out/ 分裂出一份同名产物,
    # 之后归档清单与工作台各有一份, 恢复时还会撞"目标已存在"。要重跑先 --restore。
    import archive
    if "--force" not in argv and archive.is_archived(doc):
        print("拒绝: %s 已在归档中 (archive/%s)。\n"
              "  恢复: python archive.py --restore %s\n"
              "  或加 --force 强行重跑(会在 out/ 另生一份产物)"
              % (doc, doc, doc))
        return 2
    spec = argv[1] if len(argv) > 1 else "all"
    ids = ORDER if spec == "all" else [x.strip() for x in spec.split(",") if x.strip()]
    pdf = None
    if "--pdf" in argv:
        pdf = argv[argv.index("--pdf") + 1]
    r = run(doc, ids, pdf=pdf, log=lambda s: print(s))
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv[1:]))
