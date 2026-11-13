# -*- coding: utf-8 -*-
"""桌面工作台(tkinter, 零依赖): 任务状态 + 运行历史 + 一键重跑。

动机(2026-11-12): webapp 每次调试要新开浏览器窗口, 且 JOBS 是内存态, 服务一重启全部归 0。
本 GUI 的两条硬约束:
  状态从文件系统派生 — out/<doc>/index.html + raw/<ch>{,_api} 就是真值, 重启/断电都不丢;
  历史落盘追加 — gui_runs.jsonl 每跑一篇记一行(文档/起止/结果/渠道状态/触发方式)。

用法:  python gui.py   (或双击 启动GUI.bat)
"""
import io
import json
import os
import queue
import re
import shutil
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import stages

ROOT = os.path.dirname(os.path.abspath(__file__))
UP = os.path.join(ROOT, "uploads")
OUT = os.path.join(ROOT, "out")
HIST_FP = os.path.join(ROOT, "gui_runs.jsonl")
SINGLE_PORT = 8790   # 单实例互斥端口: bind 由内核原子完成, 无文件锁的 check-then-act 竞态窗
CH = ("mineru", "dots", "paddle")
CH_ZH = {"mineru": "MinerU", "dots": "dots.ocr", "paddle": "PaddleOCR"}
NER_ROOT = os.path.join(ROOT, "out_llm_trial")   # ner_lite 产物, 仅用于"机构"的口径对齐

# 质检 = 必抽项是否齐全(2026-11-12 用户定)。缺任一项即报红, 不分级、不看置信度。
# "机构"必须与查看器同口径: metadata.affiliations / authors[].affiliation / ner_lite 的 org 实体,
# 三处任一有即算有(viewer.py 的实体面板正是这么显示的)。
QC_REQUIRED = [("title", "标题"), ("keywords", "关键词"), ("abstract", "摘要"),
               ("authors", "作者"), ("affiliations", "机构"), ("journal", "期刊"),
               ("year", "出版年"), ("volume", "卷"), ("issue", "期"),
               ("pages", "页码"), ("references", "参考文献")]
QC_ONE_OF = (("citation_spans", "引文句"), ("footnote_refs", "脚注"))   # 二存一即可
QC_OPTIONAL = ("dataset", "funding", "software")   # 实体三类可选, 不参与报红


def _acquire_instance_sock():
    """单实例: 独占本机端口即存活证明。返回需一直持有的 socket; 已被占返回 None。"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", SINGLE_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


_WEBAPP_CACHE = [0.0, False]


def webapp_alive():
    """旧 web 服务(8765)与 GUI 同时监视 uploads 会并发跑同一篇、写坏产物(13563526 实证)——检出即提醒。

    2026-11-12 卡顿修复: 原来是 urllib urlopen(timeout=1.5), 对端不响应时每次"刷新"都卡满
    1.5s(实测 1.574s, 而刷新其余部分合计仅 0.05s)。改成 0.15s 的 TCP 连接探测(不发 HTTP,
    不做请求往返), 结果再缓存 30s, 连续点刷新不再重复探测。
    """
    import socket
    now = time.time()
    if now - _WEBAPP_CACHE[0] < 30:
        return _WEBAPP_CACHE[1]
    s = socket.socket()
    s.settimeout(0.15)
    try:
        s.connect(("127.0.0.1", 8765))
        alive = True
    except OSError:
        alive = False
    finally:
        s.close()
    _WEBAPP_CACHE[:] = [now, alive]
    return alive


def doc_of(pdf_name):
    doc = re.sub(r"\.pdf$", "", pdf_name, flags=re.I)
    return re.sub(r"[\s]*[（(]\d+[)）]$", "", doc).strip()


def _dir_has_files(d):
    if not os.path.isdir(d):
        return False
    with os.scandir(d) as it:   # 迭代器必须关闭, 否则 Windows 拖住目录句柄
        return any(True for _ in it)


def _ch_done(doc, ch):
    return (_dir_has_files(os.path.join(OUT, doc, "raw", ch))
            or _dir_has_files(os.path.join(OUT, doc, "raw", ch + "_api")))


def scan_docs():
    """文件系统派生的文档状态(真值): 未跑/缺渠道/完成 + 渠道勾选 + 完成时间。"""
    docs = {}
    for f in sorted(os.listdir(UP)) if os.path.isdir(UP) else []:
        if not f.lower().endswith(".pdf"):
            continue
        doc = doc_of(f)
        dd = os.path.join(OUT, doc)
        idx = os.path.join(dd, "index.html")
        ch = {c: _ch_done(doc, c) for c in CH}
        done_at = os.path.getmtime(idx) if os.path.exists(idx) else None
        miss = [c for c in CH if not ch[c]]
        if done_at is None:
            state = "未跑"
        elif miss:
            state = "缺渠道"
        else:
            state = "完成"
        docs[doc] = {"pdf": os.path.join(UP, f), "state": state, "ch": ch,
                     "done_at": done_at, "miss": miss}
    # out/ 里有产物但 uploads 里 PDF 已没了的(清库残留), 也列出来只读展示
    if os.path.isdir(OUT):
        for d in sorted(os.listdir(OUT)):
            if d in docs or d.startswith("_") or not os.path.isdir(os.path.join(OUT, d)):
                continue
            idx = os.path.join(OUT, d, "index.html")
            if not os.path.exists(idx):
                continue
            docs[d] = {"pdf": None, "state": "无PDF",
                       "ch": {c: _ch_done(d, c) for c in CH},
                       "done_at": os.path.getmtime(idx), "miss": []}
    return docs


def load_history():
    """gui_runs.jsonl → {doc: [记录...]}(按时间序); 文件损坏时跳过坏行。"""
    hist = {}
    if not os.path.exists(HIST_FP):
        return hist
    for ln in io.open(HIST_FP, encoding="utf-8"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        hist.setdefault(r.get("doc") or "", []).append(r)
    return hist


def append_history(rec):
    with io.open(HIST_FP, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


_BODY_CACHE = {}     # doc -> (summary_mtime, 正文是否达标); unified 不常变, 别每次刷新重读几百个 page_*.json


def _body_ok(doc, dd):
    """与 viewer.py:1358 完全一致: unified 全篇 Text/Footnote 型块的 main_text 总字符 >= 500。"""
    sp = os.path.join(dd, "unified", "_unified_summary.json")
    mt = os.path.getmtime(sp) if os.path.isfile(sp) else 0
    hit = _BODY_CACHE.get(doc)
    if hit and hit[0] == mt:
        return hit[1]
    total, udir = 0, os.path.join(dd, "unified")
    if os.path.isdir(udir):
        for f in os.listdir(udir):
            if not re.match(r"page_\d+\.json$", f):
                continue
            try:
                j = json.load(io.open(os.path.join(udir, f), encoding="utf-8"))
            except Exception:
                continue
            for u in j.get("unified") or []:
                if u.get("type") in ("Text", "Footnote"):
                    total += len((u.get("main_text") or "").strip())
    ok = total >= 500
    _BODY_CACHE[doc] = (mt, ok)
    return ok


def qc_of(doc):
    """质检: 必抽项是否齐全(2026-11-12 用户定)。

    只看"该有的有没有", 不看置信度、不做分级、不看实体三类(科学数据集/基金/研究软件均为可选)。
    必抽: 标题/关键词/摘要/作者/机构/期刊/出版年/卷/期/页码/正文/参考文献;
    引文句与脚注二存一即可。缺任一项即报红, 缺几项就写几项, 名字直接进单元格
    (不再有"质检详情"弹窗, 单元格是唯一出口)。
    """
    dd = os.path.join(OUT, doc)
    miss = []
    mfp = os.path.join(dd, "metadata.json")
    m = {}
    if os.path.isfile(mfp):
        try:
            m = json.load(io.open(mfp, encoding="utf-8")) or {}
        except Exception:
            return {"issues": ["metadata.json 解析失败"], "level": "bad", "label": "\u2718 产物损坏"}
    else:
        return {"issues": ["无 metadata.json"], "level": "bad", "label": "\u2718 无元数据"}

    def _empty(v):
        if isinstance(v, dict):          # 单值字段形如 {"value":..., "confidence":...}
            v = v.get("value")
        return v is None or v == "" or v == [] or v == {}

    for key, zh in QC_REQUIRED:
        if _empty(m.get(key)):
            miss.append(zh)
    # 关键词有两条通路(keywords 解析结果 / keywords_lines 原始行), 任一有即算有
    if "关键词" in miss and not _empty(m.get("keywords_lines")):
        miss.remove("关键词")
    # 机构与 viewer_compare.html 同口径(viewer.py:1027 的质检清单 + :816 的题录行 + 实体面板):
    # ①metadata.affiliations ②authors[].affiliation ③ner_lite 的 org 实体, 任一有即算有。
    # 13563526 实证: 前两处都空, 但实体面板显示 "机构 Gyeongkuk National University",
    # 质检却报"机构"缺失——就是漏了第③条。
    if "机构" in miss:
        in_authors = any(isinstance(a, dict) and str(a.get("affiliation") or "").strip()
                         for a in (m.get("authors") or []))
        in_ner = False
        nfp = os.path.join(NER_ROOT, "fds_extract", "ner_lite", doc, "ner_lite.json")
        if os.path.isfile(nfp):
            try:
                recs = json.load(io.open(nfp, encoding="utf-8")).get("records") or []
                in_ner = any(r.get("kind") == "org" for r in recs if isinstance(r, dict))
            except Exception:
                in_ner = False
        if in_authors or in_ner:
            miss.remove("机构")
    # 正文: 与查看器同口径(见 _body_ok)
    if not _body_ok(doc, dd):
        miss.append("正文")
    # 引文句 / 脚注: 二存一
    if all(_empty(m.get(k)) for k, _zh in QC_ONE_OF):
        miss.append("引文句/脚注")

    if not miss:
        return {"issues": [], "level": "", "label": "\u2714"}
    head = "/".join(miss[:4]) + ("…" if len(miss) > 4 else "")
    return {"issues": miss, "level": "bad",
            "label": "\u2718 %d 项 %s" % (len(miss), head)}


class Runner(object):
    """串行跑阶段子集: 队列依次执行; 成功渠道走缓存, 只有 clear 指定的渠道重取。

    状态控制三原则(2026-11-12 全面检修):
      同 doc 去重 — 运行中/排队中的文档拒绝重复入队(连点防并发写同一产物);
      单任务异常隔离 — BaseException 级捕获 + finally task_done, 一个任务崩了工作线程照跑;
      忙→闲转换压后 — 查看器重建完成才置闲, 下一任务不会撞上上一任务 trailing 写盘,
      自动刷新也只会看到落定状态。

    阶段化(2026-11-12 晚): 入队单位从"整条链"变成"阶段子集", 由 stages.py 执行并按序
    依赖校验; 全链预设等价于原 pipeline.main, 后链子集秒级完成。
    """

    def __init__(self, log):
        self.log = log
        self.q = queue.Queue()          # (doc, pdf, ids, clear_tuple, trigger)
        self.cur = None                 # (doc, trigger, started)
        self.queued = set()             # 运行中+排队中的 doc(去重)
        self.lock = threading.Lock()
        self._mod_mtimes = {}           # 模块热更新基线(源文件 mtime)
        threading.Thread(target=self._work, daemon=True).start()

    _RELOADABLE = ("metapy.common", "metapy.refs", "metapy.front", "metapy.abs",
                   "metapy.issue", "metapy.cspan", "metapy.m3", "metapy.dual",
                   "metapy.llmrep", "metapy", "regress", "unify", "merge_out",
                   "viewer", "ner_lite", "stages", "pipeline")

    def _reload_if_changed(self):
        """进程常驻时改代码不必重启 GUI: 下轮任务前重载 mtime 变过的模块(长命 GUI 用旧
        abs 跑出新 bug 的教训, 2026-11-12)。子模块先于 metapy 包, pipeline 殿后。"""
        import importlib, sys
        for name in self._RELOADABLE:
            mod = sys.modules.get(name)
            fp = getattr(mod, "__file__", None) if mod else None
            if not fp:
                continue
            try:
                mt = os.path.getmtime(fp)
            except OSError:
                continue
            last = self._mod_mtimes.get(name)
            if last is None:
                self._mod_mtimes[name] = mt
            elif mt > last + 0.5:
                try:
                    importlib.reload(mod)
                    self._mod_mtimes[name] = mt
                    self.log("[热更新] %s 已重载" % name)
                except Exception as e:
                    self.log("[热更新] %s 重载失败(沿用旧版): %s" % (name, str(e)[:80]))

    def submit(self, doc, pdf, ids, trigger="重跑", clear=()):
        with self.lock:
            if doc in self.queued or (self.cur and self.cur[0] == doc):
                self.log("[队列] %s 已有任务在跑/排队, 跳过重复提交" % doc)
                return False
            self.queued.add(doc)
        self.q.put((doc, pdf, tuple(ids), tuple(clear), trigger))
        self.log("[队列] %s 已加入 (%s · %d 段)" % (doc, trigger, len(ids)))
        return True

    def _work(self):
        while True:
            doc, pdf, ids, clear, trigger = self.q.get()
            try:
                self._run_one(doc, pdf, ids, clear, trigger)
            except BaseException as e:   # 单任务异常不许杀死工作线程
                self.log("[异常] %s 任务抛出: %s" % (doc, e))
            finally:
                with self.lock:
                    self.queued.discard(doc)
                self.q.task_done()

    def _run_one(self, doc, pdf, ids, clear, trigger):
        self._reload_if_changed()   # 改了代码不用重启: 任务开跑前热更新
        for ch in clear:
            for sub in (ch, ch + "_api"):
                d = os.path.join(OUT, doc, "raw", sub)
                if os.path.isdir(d):
                    shutil.rmtree(d, ignore_errors=True)
            self.log("[重取] %s 渠道 %s 缓存已清" % (doc, ch))
        with self.lock:
            self.cur = (doc, trigger, time.time())
        import pipeline
        old = pipeline.log

        def _hook(*a):
            self.log("  " + " ".join(str(x) for x in a))

        pipeline.log = _hook
        t0 = time.time()
        res = {"ok": False, "results": {}, "error": ""}
        try:
            res = stages.run(doc, ids, pdf=pdf, log=_hook)
        except Exception as e:   # stages.run 内部已逐步兜异常; 这里兜它自己
            res = {"ok": False, "results": {}, "error": str(e)[:200]}
        finally:
            pipeline.log = old
        ok = bool(res.get("ok"))
        rec = {"doc": doc, "trigger": trigger, "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)),
               "elapsed_s": int(time.time() - t0), "ok": ok, "err": res.get("error") or None,
               "selected": list(ids),
               "stages": {k: ("ok" if v.get("ok") else ("skip" if v.get("skipped") else "fail"))
                          for k, v in (res.get("results") or {}).items()},
               "channels": {c: _ch_done(doc, c) for c in CH}}
        append_history(rec)
        # 不替用户补跑未勾选的阶段(2026-11-12 用户定: 耦合的地方不要一起重跑)。
        # 元数据/NER 变了而没勾 viewer, 只在日志里提醒一句, 由人决定要不要补一段。
        if "viewer" not in ids and any(i in ids for i in stages.META_IDS + ("ner",)):
            self.log("[提示] %s 元数据/实体已变但未勾选「对照查看器」段, HTML 仍是上一版" % doc)
        self.log("[完成] %s %s (耗时 %ds · %d 段)%s" % (
            doc, "成功" if ok else "失败", rec["elapsed_s"], len(ids),
            (" · " + rec["err"]) if rec["err"] else ""))
        with self.lock:
            self.cur = None

    def status(self):
        with self.lock:
            cur = self.cur
        n = self.q.qsize()
        if cur:
            return "正在跑: %s (%s) · 排队 %d" % (cur[0], cur[1], n)
        return "空闲 · 排队 %d" % n

    def is_busy(self):
        with self.lock:
            cur = self.cur is not None
        return cur or self.q.qsize() > 0

    def elapsed(self):
        with self.lock:
            cur = self.cur
        return int(time.time() - cur[2]) if cur else 0

    def cur_doc(self):
        with self.lock:
            cur = self.cur
        return cur[0] if cur else ""


class App(tk.Tk):
    def __init__(self):
        tk.Tk.__init__(self)
        self.title("KCI 解析工作台")
        self.geometry("980x640")
        self.q = queue.Queue()
        self.runner = Runner(self._log)
        self._was_busy = False
        self._webapp_on = False
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build()
        self.after(100, self._drain)
        self.after(500, self.refresh)
        self._log("就绪: 拖 PDF 到 uploads; Ctrl/Shift 多选可批量。「一键重跑」= 执行勾选的阶段(主区) / 右端箭头选阶段; "
                  "「快速重跑(跳NER)」不烧 LLM。质检列只查必抽项齐全, 缺项报红。")

    # ---------- 界面 ----------
    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=8)
        ttk.Button(top, text="刷新", command=self.refresh).pack(side="left")
        ttk.Button(top, text="打开 uploads 目录", command=lambda: self._open(UP)).pack(side="left", padx=6)
        ttk.Button(top, text="打开 out 目录", command=lambda: self._open(OUT)).pack(side="left")
        self.status_lbl = ttk.Label(top, text="")
        self.status_lbl.pack(side="right")

        mid = ttk.Panedwindow(self, orient="vertical")
        mid.pack(fill="both", expand=True, padx=10)

        # 文档表(多选: Ctrl/Shift 批量, 操作按钮对选中集生效)
        wrap = ttk.Frame(mid)
        cols = ("state", "qc", "mineru", "dots", "paddle", "done_at", "runs")
        self.tree = ttk.Treeview(wrap, columns=cols, show="tree headings", height=12,
                                 selectmode="extended")
        self.tree.heading("#0", text="文档")
        for c, w, t in (("state", 70, "状态"), ("qc", 214, "质检 · 必抽项"),
                        ("mineru", 58, "MinerU"), ("dots", 58, "dots"),
                        ("paddle", 62, "Paddle"), ("done_at", 118, "上次完成"),
                        ("runs", 86, "历史")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="center")
        self.tree.column("#0", width=180)
        self.tree.column("qc", anchor="w")   # 缺项名要能读, 左对齐; 其余列居中
        self.tree.tag_configure("qc_bad", foreground="#b03030")
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._sync_buttons())
        self.tree.bind("<Double-1>", lambda e: self._open_viewer())
        self.tree.bind("<Button-3>", self._context_menu)
        mid.add(wrap, weight=3)

        # 操作条(对选中文档)
        act = ttk.Frame(mid)
        self.stage_vars = {}
        for sid in stages.ORDER:
            self.stage_vars[sid] = tk.BooleanVar(value=(sid in stages.DEFAULT_IDS))
        # 一个键两个热区(2026-11-12 用户定):
        #   主区(左)   = 执行「阶段菜单」里当前勾选的阶段, 与菜单里的"执行选中阶段"完全等价;
        #   右端小箭头 = 只负责打开菜单改勾选。
        # 必须是同一个控件——拆成两个按钮会变成"两个键", 用 Menubutton 又会让点哪都弹菜单、
        # 逼用户再确认一次。所以用单个 Button + 按下坐标判断落在哪个热区。
        # 「快速重跑(跳NER)」仍是独立键: 它存在的目的就是不重复调 LLM。
        grp = ttk.Frame(act)
        grp.pack(side="left", padx=(4, 8))
        self.btn_rerun = ttk.Button(grp, text="一键重跑  ▾", command=self._on_rerun_click)
        self.btn_rerun.pack(side="left")
        self.btn_rerun.bind("<ButtonPress-1>", self._note_click_x)
        self.btn_quick = ttk.Button(grp, text="快速重跑(跳NER)", command=self._rerun_fast)
        self.btn_quick.pack(side="left", padx=(4, 0))
        self.stage_menu = tk.Menu(self, tearoff=0, postcommand=self._build_stage_menu)
        self.btn_retry = ttk.Button(act, text="重试缺失渠道", command=self._retry_missing)
        self.btn_view = ttk.Button(act, text="对照查看器", command=self._open_viewer)
        self.btn_index = ttk.Button(act, text="结果总览", command=self._open_index)
        for b in (self.btn_retry, self.btn_view, self.btn_index):
            b.pack(side="left", padx=4)
        self.sel_lbl = ttk.Label(act, text="未选中")
        self.sel_lbl.pack(side="left", padx=12)
        mid.add(act, weight=0)

        # 日志
        logw = ttk.Frame(mid)
        self.logtxt = scrolledtext.ScrolledText(logw, height=10, state="disabled", font=("Consolas", 9))
        self.logtxt.pack(fill="both", expand=True)
        mid.add(logw, weight=2)

        self.after(1000, self._tick_status)

    # ---------- 阶段菜单 ----------
    _ARROW_W = 20          # 键右侧箭头热区宽度(px): 落在这一段内=开菜单, 其余=执行

    def _note_click_x(self, ev):
        self._click_x = ev.x

    def _on_rerun_click(self):
        x = getattr(self, "_click_x", None)
        self._click_x = None            # 用完即清, 防键盘触发时读到上次的旧坐标
        if x is not None and x >= self.btn_rerun.winfo_width() - self._ARROW_W:
            self._post_stage_menu()
        else:
            self._run_selected()

    def _post_stage_menu(self):
        """菜单贴在这个键的右端下方(箭头正下方)。"""
        x = self.btn_rerun.winfo_rootx() + self.btn_rerun.winfo_width() - 6
        y = self.btn_rerun.winfo_rooty() + self.btn_rerun.winfo_height()
        try:
            self.stage_menu.tk_popup(x, y)
        finally:
            self.stage_menu.grab_release()

    def _build_stage_menu(self):
        """每次弹出前重建: 依赖状态随当前文档产物变化, 不能只建一次。

        缺依赖的条目置灰并在标签后写明缺什么——置灰只是提示, 真正的闸门在 stages.plan()。
        """
        m = self.stage_menu
        m.delete(0, "end")
        docs = self._selected() or ([self.tree.focus()] if self.tree.focus() else [])
        probe = docs[0] if docs else None
        avail = stages.availability(probe) if probe else {}

        pre = tk.Menu(m, tearoff=0)
        for name, ids in stages.PRESETS:
            pre.add_command(label=name, command=lambda i=list(ids), n=name: self._run_stages(i, n))
        m.add_cascade(label="预设", menu=pre)
        m.add_separator()

        groups = []
        for sid, group, label, hint, req, npdf in stages.STAGES:
            if group not in groups:
                groups.append(group)
        for g in groups:
            gm = tk.Menu(m, tearoff=0)
            for sid, group, label, hint, req, npdf in stages.STAGES:
                if group != g:
                    continue
                ok, miss, _ = avail.get(sid, (True, [], None))
                text = "%s — %s" % (label, hint)
                if not ok and probe:
                    text += "   (缺 %s)" % "、".join(miss)
                gm.add_checkbutton(label=text, variable=self.stage_vars[sid],
                                   state="normal" if (ok or not probe) else "disabled")
            m.add_cascade(label=g, menu=gm)
        m.add_separator()
        m.add_command(label="执行选中阶段", command=self._run_selected)
        m.add_separator()
        m.add_command(label="全选", command=lambda: self._set_all_stages(True))
        m.add_command(label="全不选", command=lambda: self._set_all_stages(False))
        m.add_command(label="只选后链(元数据+实体)",
                      command=lambda: self._set_stages(list(stages.META_IDS) + ["ner"]))

    def _set_all_stages(self, on):
        for sid in stages.ORDER:
            self.stage_vars[sid].set(bool(on))

    def _set_stages(self, ids):
        ids = set(ids)
        for sid in stages.ORDER:
            self.stage_vars[sid].set(sid in ids)

    def _checked_stages(self):
        return [sid for sid in stages.ORDER if self.stage_vars[sid].get()]

    # ---------- 数据 ----------
    def refresh(self):
        self.docs = scan_docs()
        self.hist = load_history()
        self._webapp_on = webapp_alive()
        sels = [d for d in self._selected() if d in self.docs]   # 多选保留
        self.tree.delete(*self.tree.get_children())
        for doc, d in sorted(self.docs.items()):
            h = self.hist.get(doc, [])
            runs = "%d 次%s" % (len(h), "(末次失败)" if (h and not h[-1]["ok"]) else "")
            qc = qc_of(doc) if d["state"] != "未跑" else {"label": "-", "level": "", "issues": []}
            tags = ("qc_bad",) if qc["level"] == "bad" else ()
            self.tree.insert("", "end", iid=doc, text=doc, tags=tags, values=(
                d["state"],
                qc["label"],
                "✔" if d["ch"]["mineru"] else "—",
                "✔" if d["ch"]["dots"] else "—",
                "✔" if d["ch"]["paddle"] else "—",
                time.strftime("%m-%d %H:%M", time.localtime(d["done_at"])) if d["done_at"] else "-",
                runs))
        if sels:
            self.tree.selection_set(sels)
        self._sync_buttons()

    def _selected(self):
        return list(self.tree.selection())

    def _sync_buttons(self):
        docs = self._selected()
        has = bool(docs)
        runnable = any(self.docs.get(d, {}).get("pdf") for d in docs)
        retryable = any(self.docs.get(d, {}).get("pdf") and self.docs[d]["miss"] for d in docs)
        self.btn_rerun.configure(state="normal" if runnable else "disabled")
        self.btn_quick.configure(state="normal" if runnable else "disabled")
        self.btn_retry.configure(state="normal" if retryable else "disabled")
        self.btn_view.configure(state="normal" if has else "disabled")
        self.btn_index.configure(state="normal" if has else "disabled")
        n = len(self._checked_stages()) if hasattr(self, "stage_vars") else 0
        if has:
            self.sel_lbl.configure(text="已选 %d 篇: %s ｜ 菜单勾选 %d 段" % (
                len(docs), ", ".join(docs[:3]) + (" …" if len(docs) > 3 else ""), n))
        else:
            self.sel_lbl.configure(text="未选中")

    # ---------- 动作(对选中集批量) ----------
    def _run_stages(self, ids, trigger=None):
        """按给定阶段子集批量提交(预设/自定义走同一入口)。"""
        ids = [i for i in stages.ORDER if i in set(ids)]   # 规范顺序 + 去重
        if not ids:
            self._log("[阶段] 未选择任何阶段")
            return
        n = 0
        skipped = []
        for doc in self._selected():
            pdf = self.docs.get(doc, {}).get("pdf")
            if not pdf:
                skipped.append(doc)
                continue
            if self.runner.submit(doc, pdf, ids, trigger or ("执行选中 %d 段" % len(ids))):
                n += 1
        if n:
            self._log("[批量] 已提交 %d 篇 · %d 段: %s" % (
                n, len(ids), "→".join(stages.BY_ID[i][2] for i in ids)))
        if skipped:
            self._log("[批量] 跳过 %d 篇(无 PDF 源文件): %s" % (len(skipped), ", ".join(skipped[:5])))

    def _run_selected(self):
        self._run_stages(self._checked_stages())


    def _rerun_fast(self):
        """快速重跑(跳NER): 全链减实体抽取——这个键存在的唯一目的就是不重复烧 LLM。

        不改动菜单里的勾选状态: 菜单勾选是另一条路(自定义子集), 替用户改勾选
        会让他下一次"执行选中阶段"跑出没预期的集合。
        """
        ids = [i for i in stages.ORDER if i != "ner"]
        self._run_stages(ids, "快速重跑(跳NER)")

    def _retry_missing(self):
        n = 0
        for doc in self._selected():
            d = self.docs.get(doc)
            if d and d["pdf"] and d["miss"]:
                # 清掉缺失渠道的 raw/ 缓存后跑全链: 已有渠道走缓存, 只补缺失那路
                if self.runner.submit(doc, d["pdf"], stages.ORDER,
                                      "重试缺失:" + "/".join(d["miss"]), clear=tuple(d["miss"])):
                    n += 1
        if n:
            self._log("[批量] 重试缺失渠道已提交 %d 篇" % n)


    def _context_menu(self, ev):
        iid = self.tree.identify_row(ev.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
        if not iid:
            return
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="打开对照查看器", command=self._open_viewer)
        m.add_command(label="打开结果总览", command=lambda: self._open(os.path.join(OUT, iid, "index.html"))
                      if os.path.exists(os.path.join(OUT, iid, "index.html")) else None)
        m.add_command(label="打开产物目录", command=lambda: self._open(os.path.join(OUT, iid)))
        m.add_separator()
        m.add_command(label="复制文档 ID", command=lambda: (self.clipboard_clear(),
                                                            self.clipboard_append(iid),
                                                            self._log("[复制] %s" % iid)))
        m.add_separator()
        sub = tk.Menu(m, tearoff=0)
        for name, ids in stages.PRESETS:
            sub.add_command(label=name, command=lambda i=list(ids), n=name: (
                self.tree.selection_set(iid), self._run_stages(i, n)))
        m.add_cascade(label="按预设重跑", menu=sub)
        try:
            m.tk_popup(ev.x_root, ev.y_root)
        finally:
            m.grab_release()

    def _open_viewer(self):
        doc = self.tree.focus() or (self._selected()[0] if self._selected() else None)
        if not doc:
            return
        fp = os.path.join(OUT, doc, "viewer_compare.html")
        if os.path.exists(fp):
            self._open(fp)

    def _open_index(self):
        for doc in self._selected():
            fp = os.path.join(OUT, doc, "index.html")
            if os.path.exists(fp):
                self._open(fp)

    def _on_close(self):
        if self.runner.is_busy():
            if not messagebox.askyesno("任务仍在运行",
                                       "关闭窗口会中断正在跑的解析, 可能留下写一半的产物;\n"
                                       "排队中的任务会丢失。\n\n确定要关闭吗?"):
                return
        self.destroy()

    @staticmethod
    def _open(path):
        if os.name == "nt":
            os.startfile(path)  # noqa: S606 - 本地工作台双击等价物
        else:
            import webbrowser
            webbrowser.open("file://" + path)

    # ---------- 日志/状态 ----------
    def _log(self, s):
        self.q.put(s)

    def _drain(self):
        try:
            n = 0
            while n < 300:   # 单次回调限量: 日志洪峰不许把 UI 线程钉死
                s = self.q.get_nowait()
                n += 1
                self.logtxt.configure(state="normal")
                self.logtxt.insert("end", time.strftime("%H:%M:%S ") + s + "\n")
                # 日志上限: 只保留最后 ~3000 行, 防长会话内存膨胀
                tail = self.logtxt.index("end-1c")
                if tail and int(tail.split(".")[0]) > 3000:
                    self.logtxt.delete("1.0", "2000.0")
                self.logtxt.see("end")
                self.logtxt.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def _tick_status(self):
        txt = self.runner.status()
        if self._webapp_on:
            txt = "⚠ 检测到 web 服务(8765)也在运行, 勿与 GUI 同时解析同一篇 · " + txt
        self.status_lbl.configure(text=txt)
        busy = self.runner.is_busy()
        if busy:
            el = self.runner.elapsed()
            # 任务心跳: 长跑阶段(OCR/LLM 网络等待)日志静默, 每 15s 报一次"还在跑", 防误判卡死
            if el // 15 != getattr(self, "_last_beat", -1):
                self._last_beat = el // 15
                self._log("[心跳] 正在跑 %s, 已 %ds" % (self.runner.cur_doc(), el))
        if self._was_busy and not busy:
            self._was_busy = busy   # 先翻转再刷新: 杜绝 refresh↔_tick_status 互相递归
            self.refresh()
        else:
            self._was_busy = busy
        self.after(1000, self._tick_status)


def main():
    os.makedirs(UP, exist_ok=True)
    sock = _acquire_instance_sock()
    if sock is None:
        print("GUI 已在运行(见任务栏另一个窗口), 请勿重复启动。")
        return
    app = App()
    app.mainloop()   # sock 随进程存活, 退出即释放端口


if __name__ == "__main__":
    main()
