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
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

import archive
import qc
import stages
import workspaces
import entview
from docid import doc_of

ROOT = os.path.dirname(os.path.abspath(__file__))
UP = os.path.join(ROOT, "uploads")
OUT = os.path.join(ROOT, "out")
HIST_FP = os.path.join(ROOT, "gui_runs.jsonl")
NOTES_FP = os.path.join(ROOT, "gui_notes.json")
SINGLE_PORT = 8790   # 单实例互斥端口: bind 由内核原子完成, 无文件锁的 check-then-act 竞态窗
CH = ("mineru", "dots", "paddle")
CH_ZH = {"mineru": "MinerU", "dots": "dots.ocr", "paddle": "PaddleOCR"}
NER_ROOT = os.path.join(ROOT, "out_llm_trial")   # ner_lite 产物, 仅用于"机构"的口径对齐

# 质检口径已抽到 qc.py(2026-11-13): 归档要在**搬走产物之前**把质检状态快照进索引,
# 而归档是 CLI 也能跑的路径, 不能为此把 tkinter 拖进依赖链。
# 判定内容未变: 必抽项齐全性, 缺任一即红, 不看置信度, 三类实体可选。
qc_of = qc.qc_of
QC_REQUIRED, QC_ONE_OF = qc.QC_REQUIRED, qc.QC_ONE_OF
QC_OPTIONAL = ("dataset", "funding", "software")   # 实体三类可选, 不参与报红


def qc_display(qr):
    return qr["label"]


def load_notes(fp=None):
    fp = fp or NOTES_FP
    try:
        with io.open(fp, encoding="utf-8") as fh:
            data = json.load(fh)
        return {str(k): str(v) for k, v in data.items() if str(v).strip()} if isinstance(data, dict) else {}
    except Exception:
        return {}




def load_webfill_flags(out_root):
    """{doc: [字段]} — metadata 顶层 webfill 标记 = 含联网回填值(web_record)的文档,
    2026-11-18: 题录值三种来源(原文/web_record回填/注册表·账本)在界面上可一眼区分。
    备注列加 \U0001F310 前缀; 实体值来源以 confidence.source 为准(web_record 即联网)。"""
    flags = {}
    if not os.path.isdir(out_root):
        return flags
    for doc in os.listdir(out_root):
        mp = os.path.join(out_root, doc, "metadata.json")
        if not os.path.isfile(mp):
            continue
        try:
            with io.open(mp, encoding="utf-8") as fh:
                v = (json.load(fh) or {}).get("webfill")
            if v:
                flags[doc] = v
        except Exception:
            pass
    return flags
def save_notes(notes, fp=None):
    fp = fp or NOTES_FP
    tmp = fp + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(notes, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, fp)


def auto_note(qr):
    issues = set((qr or {}).get("issues") or [])
    notes = []
    if issues & {"期刊", "出版年", "卷", "期"}:
        notes.append("原文未见年卷期/DOI证据")
    if "引文句/脚注" in issues:
        notes.append("原文未见文内引文/脚注证据")
    return "；".join(notes)


def fmt_duration(seconds):
    if seconds is None:
        return "-"
    seconds = int(seconds)
    if seconds < 60:
        return "%ds" % seconds
    if seconds < 3600:
        return "%dm%02ds" % (seconds // 60, seconds % 60)
    return "%dh%02dm" % (seconds // 3600, (seconds % 3600) // 60)


def channel_display(d):
    return " ".join("✔" if d["ch"][c] else "—" for c in CH)


def timing_display(recs):
    """时间分析列 = 该篇累计: OCR 累计(acquire 各次实跑之和, 缓存命中贡献~0) : LLM 累计(ner+webfill)"""
    recs = [r for r in (recs or []) if isinstance(r, dict)]
    if not recs:
        return "-"
    ocr = sum(int(r.get("ocr_s") or 0) for r in recs)
    llm = sum(int(r.get("llm_s") or 0) for r in recs)
    return "%s : %s" % (fmt_duration(ocr), fmt_duration(llm))
def _acquire_instance_sock(attempts=15, wait=0.3):
    """单实例: 独占本机端口即存活证明。bind 由内核原子完成, 无文件锁的 check-then-act 竞态窗。
    attempts>1: 一键重启时新老进程交接端口有空窗, 新进程重试等待(老进程退出即释放)。"""
    import socket
    for i in range(attempts):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", SINGLE_PORT))
            s.listen(1)
            return s
        except OSError:
            s.close()
            if i < attempts - 1:
                time.sleep(wait)
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
    # webapp 绑定 8765 起、被占顺延至 8774(webapp.py 端口段), 只探 8765 会被绕过:
    # 服务落在 8766+ 时 GUI 不告警且再次拉起第二个 watch_uploads, 并发写坏同一篇(13563526 实证)。
    hint = None
    try:
        hint = int(io.open(os.path.join(ROOT, "_server.port"), encoding="ascii").read().strip())
    except (OSError, ValueError):
        pass
    order = ([hint] if hint else []) + [p for p in range(8765, 8775) if p != hint]
    port = None
    for p in order:
        s = socket.socket()
        s.settimeout(0.15)
        try:
            s.connect(("127.0.0.1", p))
            port = p
            break
        except OSError:
            pass
        finally:
            s.close()
    _WEBAPP_CACHE[:] = [now, port]
    return port




def _dir_has_files(d):
    try:
        with os.scandir(d) as it:   # 迭代器必须关闭, 否则 Windows 拖住目录句柄
            return any(True for _ in it)
    except (FileNotFoundError, NotADirectoryError):
        return False


def _ch_done(doc, ch, out_dir=None):
    out_dir = out_dir or OUT
    return (_dir_has_files(os.path.join(out_dir, doc, "raw", ch + "_api"))
            or _dir_has_files(os.path.join(out_dir, doc, "raw", ch)))


def _ner_done(dd, ner_root=None):
    return os.path.isfile(os.path.join(ner_root or NER_ROOT, "fds_extract", "ner_lite",
                                      os.path.basename(dd), "ner_lite.json"))


def scan_docs(up=None, out_dir=None, ner_root=None, include_archived=True, arch_root=None):
    """文件系统派生的文档状态(真值): 未跑/未完成/缺渠道/完成 + 渠道勾选 + 完成时间。


    已归档的文档不出现在工作台(2026-11-13): 归档把 PDF 与 out/ 都移走了, 但索引是权威凭据,
    所以先按索引过滤一遍——否则手工把产物挪回来、或只归档了 PDF, 就会漏成一条"无PDF"的僵尸行。
    """
    up, out_dir, ner_root = up or UP, out_dir or OUT, ner_root or NER_ROOT
    try:
        done_docs = archive.archived_set(arch_root) if include_archived else set()
    except ValueError:
        done_docs = set()      # 索引损坏: 宁可多显示几条, 也不能让所有文档凭空消失
    docs = {}
    for f in sorted(os.listdir(up)) if os.path.isdir(up) else []:
        if not f.lower().endswith(".pdf"):
            continue
        doc = doc_of(f)
        if doc in done_docs:
            continue
        dd = os.path.join(out_dir, doc)
        idx = os.path.join(dd, "index.html")
        ch = {c: _ch_done(doc, c, out_dir) for c in CH}
        done_at = os.path.getmtime(idx) if os.path.exists(idx) else None
        miss = [c for c in CH if not ch[c]]
        if done_at is None:
            state = "未完成" if _dir_has_files(dd) else "未跑"
        elif miss:
            state = "缺渠道"
        elif not os.path.isfile(os.path.join(dd, "metadata.json")):
            state = "未完成"
        else:
            state = "完成"
        docs[doc] = {"pdf": os.path.join(up, f), "state": state, "ch": ch,
                     "done_at": done_at, "miss": miss, "ner": _ner_done(dd, ner_root)}
    # out/ 里有产物但 uploads 里 PDF 已没了的(清库残留), 也列出来只读展示
    if os.path.isdir(out_dir):
        for d in sorted(os.listdir(out_dir)):
            if d in docs or d.startswith("_") or d in done_docs:
                continue
            if not os.path.isdir(os.path.join(out_dir, d)):
                continue
            idx = os.path.join(out_dir, d, "index.html")
            if not os.path.exists(idx):
                continue
            docs[d] = {"pdf": None, "state": "无PDF",
                       "ch": {c: _ch_done(d, c, out_dir) for c in CH},
                       "done_at": os.path.getmtime(idx), "miss": [],
                       "ner": _ner_done(os.path.join(out_dir, d), ner_root)}
    return docs


def load_history(fp=None):
    """gui_runs.jsonl → {doc: [记录...]}(按时间序); 文件损坏时跳过坏行。"""
    fp = fp or HIST_FP
    hist = {}
    if not os.path.exists(fp):
        return hist
    for ln in io.open(fp, encoding="utf-8"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        hist.setdefault(r.get("doc") or "", []).append(r)
    return hist


# 并发: 篇与篇互不依赖(各写自己的 out/<doc>/), 所以能同时跑。
# 上限不是 CPU 决定的, 是三类共享资源决定的(见 crossproc.py): LLM 默认 2 路、
# 渠道 API 默认 3 路、LLM 默认 2 路。并发数调大到 8 也不会让这些资源变多,
# 只会让排队发生在更里面一层 —— 所以给 8 就够了, 再大只是把等待换个地方排。
DEFAULT_PARALLEL = 5
MAX_PARALLEL = 8
PREFS_FP = os.path.join(ROOT, ".gui_prefs.json")


def load_prefs():
    try:
        with io.open(PREFS_FP, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def save_prefs(d):
    try:
        with io.open(PREFS_FP, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1)
    except Exception:
        pass


def append_history(rec, fp=None):
    with io.open(fp or HIST_FP, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


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

    def __init__(self, log, parallel=None, out_root=None, history_fp=None):
        self.log = log
        self.out_root = out_root or OUT
        self.history_fp = history_fp or HIST_FP
        self.q = queue.Queue()          # (doc, pdf, ids, clear_tuple, trigger, generation)
        self.running = {}               # doc -> (trigger, started): 并发下"当前在跑"是一个集合
        self.queued = set()             # 运行中+排队中的 doc(去重)
        self.parallel = int(parallel or DEFAULT_PARALLEL)   # 同时跑几篇(可在界面调)
        self.lock = threading.Lock()
        self._generation = 0
        self._stop_requested = False
        self._stop_cancelled = 0
        self._timing_ocr = 0
        self._timing_llm = 0
        self._hist_lock = threading.Lock()   # gui_runs.jsonl 追加: 并发下多线程同时写会交错
        threading.Thread(target=self._loop, daemon=True).start()

    # 热更新(2026-11-12 加的模块级 importlib.reload)已随"一篇一子进程"取消:
    # 每个任务都在新进程里跑, 天然 import 到磁盘上的最新代码, 不必再靠 mtime 猜。
    # 这里只留一条提醒 —— 代码改动在**下次任务**生效, 不影响正在跑的进程。

    def submit(self, doc, pdf, ids, trigger="重跑", clear=()):
        with self.lock:
            if self._stop_requested:
                self.log("[停止] %s 未提交：正在等待当前任务跑完" % doc)
                return False
            if doc in self.queued:
                self.log("[队列] %s 已有任务在跑/排队, 跳过重复提交" % doc)
                return False
            self.queued.add(doc)
            self.q.put((doc, pdf, tuple(ids), tuple(clear), trigger, self._generation,
                        self.out_root, self.history_fp))
        self.log("[队列] %s 已加入 (%s · %d 段)" % (doc, trigger, len(ids)))
        return True

    def stop_after_current(self):
        """取消未启动任务；已启动任务正常跑完。返回 (运行中篇数, 取消排队篇数)。"""
        with self.lock:
            if self._stop_requested:
                return len(self.running), self._stop_cancelled
            self._stop_requested = True
            self._generation += 1
            self._stop_cancelled = 0
        while True:
            try:
                doc, _pdf, _ids, _clear, _trigger, _generation, _out, _hist = self.q.get_nowait()
            except queue.Empty:
                break
            with self.lock:
                self.queued.discard(doc)
                self._stop_cancelled += 1
            self.q.task_done()
        with self.lock:
            running = len(self.running)
            cancelled = self._stop_cancelled
            if not running:
                self._stop_requested = False
        self.log("[停止] 已取消排队 %d 篇；等待当前 %d 篇跑完" % (cancelled, running))
        return running, cancelled

    def is_stopping(self):
        with self.lock:
            return self._stop_requested

    def timing_totals(self):
        with self.lock:
            return self._timing_ocr, self._timing_llm

    def set_parallel(self, n):
        """改并发数。只影响**之后**的调度: 已在跑的不动, 排队里的下一拍按新值放行。"""
        n = max(1, min(int(n), MAX_PARALLEL))
        old = self.parallel
        self.parallel = n
        if n != old:
            self.log("[并发] %d -> %d 篇同时跑" % (old, n))
        return n

    def _loop(self):
        """调度线程: 只负责起任务线程, 不管任务内容。

        为什么不让每个 worker 线程自己去 q.get(): 那样并发数就绑死在"开了几个线程"上,
        运行期改不了。这里由 _active 计数决定是否放行 —— 并发数变成可调参数而不是线程数。
        """
        while True:
            while self._active_count() >= self.parallel:
                time.sleep(0.15)
            doc, pdf, ids, clear, trigger, generation, out_root, history_fp = self.q.get()
            with self.lock:
                if generation != self._generation or self._stop_requested:
                    self.queued.discard(doc)
                    self._stop_cancelled += 1
                    cancel = True
                else:
                    self.running[doc] = (trigger, time.time())
                    cancel = False
            if cancel:
                self.q.task_done()
                continue
            t = threading.Thread(target=self._task,
                                 args=(doc, pdf, ids, clear, trigger, out_root, history_fp), daemon=True)
            t.start()

    def _active_count(self):
        with self.lock:
            return len(self.running)

    def _task(self, doc, pdf, ids, clear, trigger, out_root, history_fp):
        try:
            self._run_one(doc, pdf, ids, clear, trigger, out_root, history_fp)
        except BaseException as e:   # 单任务异常不许杀死调度线程
            self.log("[异常] %s 任务抛出: %s" % (doc, e))
        finally:
            with self.lock:
                self.queued.discard(doc)
                self.running.pop(doc, None)
                stopped = self._stop_requested and not self.running and self.q.qsize() == 0
                if stopped:
                    self._stop_requested = False
            self.q.task_done()
            if stopped:
                self.log("[停止] 当前任务已跑完，可以重新提交")

    def _run_one(self, doc, pdf, ids, clear, trigger, out_root=None, history_fp=None):
        out_root, history_fp = out_root or self.out_root, history_fp or self.history_fp
        for ch in clear:
            for sub in (ch, ch + "_api"):
                d = os.path.join(out_root, doc, "raw", sub)
                if os.path.isdir(d):
                    shutil.rmtree(d, ignore_errors=True)
            self.log("[重取] %s 渠道 %s 缓存已清" % (doc, ch))
        t0 = time.time()

        # 一篇一子进程: 子进程里 stdout/stderr 全部经 queue 回流, 所以这里只需给日志加篇名,
        # 并发时才知道哪一行属于哪篇。pipeline.log 的猴补丁不再需要(全局状态不能跨进程)。
        def _hook(s):
            self.log("[%s] %s" % (doc, str(s)))

        res = {"ok": False, "results": {}, "error": ""}
        try:
            res = stages.run_one_child({"doc": doc, "ids": list(ids), "pdf": pdf},
                                       log=_hook, out_root=out_root)
        except Exception as e:   # stages.run_one_child 内部已兜异常; 这里兜它自己(如进程起不来)
            res = {"ok": False, "results": {}, "error": str(e)[:200]}
        ok = bool(res.get("ok"))
        stage_elapsed = {k: int(v.get("elapsed") or 0)
                         for k, v in (res.get("results") or {}).items()}
        ocr_s = stage_elapsed.get("acquire", 0)
        llm_s = stage_elapsed.get("ner", 0) + stage_elapsed.get("webfill", 0)
        with self.lock:
            self._timing_ocr += ocr_s
            self._timing_llm += llm_s
        rec = {"doc": doc, "trigger": trigger, "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)),
               "elapsed_s": int(time.time() - t0), "ok": ok, "err": res.get("error") or None,
               "selected": list(ids), "parallel": self.parallel,
               "ocr_s": ocr_s, "llm_s": llm_s, "stage_elapsed": stage_elapsed,
               "stages": {k: ("ok" if v.get("ok") else ("skip" if v.get("skipped") else "fail"))
                          for k, v in (res.get("results") or {}).items()},
               "channels": {c: _ch_done(doc, c, out_root) for c in CH}}
        with self._hist_lock:
            append_history(rec, history_fp)
        # 不替用户补跑未勾选的阶段(2026-11-12 用户定: 耦合的地方不要一起重跑)。
        # 元数据/NER 变了而没勾 viewer, 只在日志里提醒一句, 由人决定要不要补一段。
        if "viewer" not in ids and any(i in ids for i in stages.META_IDS + ("ner",)):
            self.log("[提示] %s 元数据/实体已变但未勾选「对照查看器」段, HTML 仍是上一版" % doc)
        self.log("[完成] %s %s (总耗时 %ds · OCR %ds · LLM %ds · %d 段)%s" % (
            doc, "成功" if ok else "失败", rec["elapsed_s"], ocr_s, llm_s, len(ids),
            (" · " + rec["err"]) if rec["err"] else ""))

    def status(self):
        with self.lock:
            docs = sorted(self.running)
            stopping = self._stop_requested
            cancelled = self._stop_cancelled
        n = self.q.qsize()
        if stopping:
            return "正在停止：等待当前 %d 篇跑完 · 已取消排队 %d 篇" % (len(docs), cancelled)
        if docs:
            head = ", ".join(docs[:3]) + (" …" if len(docs) > 3 else "")
            return "正在跑 %d/%d 篇 (并发 %d): %s · 排队 %d" % (
                len(docs), self.parallel, self.parallel, head, n)
        return "空闲 · 并发 %d · 排队 %d" % (self.parallel, n)

    def is_busy(self):
        with self.lock:
            busy = bool(self.running)
        return busy or self.q.qsize() > 0

    def elapsed(self):
        """最久那篇已跑几秒(心跳用它: 并发下每篇起点不同, 报最大值才知道有没有真卡住)。"""
        with self.lock:
            ts = [v[1] for v in self.running.values()]
        return int(time.time() - min(ts)) if ts else 0

    def cur_doc(self):
        with self.lock:
            docs = sorted(self.running)
        return (", ".join(docs[:3]) + (" …" if len(docs) > 3 else "")) if docs else ""


API_CONFIG_SCHEMA = [
    ("mineru_api", "MinerU 云端 API", [
        ("token", "Token", "text"), ("model_version", "模型版本", ("pipeline", "vlm", "MinerU-HTML")),
        ("language", "语言", "text"), ("is_ocr", "启用 OCR（默认关=非OCR）", "bool"),
        ("enable_formula", "公式识别", "bool"), ("enable_table", "表格识别", "bool"),
        ("timeout", "超时（秒）", "int"),
    ]),
    ("paddle_api", "PaddleOCR 云端 API", [
        ("token", "Token", "text"), ("model", "模型", "text"),
        ("base", "API 地址", "text"), ("timeout", "超时（秒）", "int"),
    ]),
    ("dots_api", "dots.ocr 云端 API", [
        ("base", "实例地址", "text"), ("model", "模型", ("dots.mocr", "dots.mocr-svg")),
        ("prompt", "Prompt 键", "text"), ("timeout", "超时（秒）", "int"),
    ]),
]


class ApiConfigWindow(tk.Toplevel):
    def __init__(self, app):
        tk.Toplevel.__init__(self, app)
        self.app = app
        self.title("渠道 API 配置")
        self.geometry("620x680")
        self.transient(app)
        import pipeline
        cfg = pipeline.load_cfg()
        self.vars = {}
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        for sec, title, fields in API_CONFIG_SCHEMA:
            self.vars[sec] = {}
            box = ttk.LabelFrame(body, text=title, padding=8)
            box.pack(fill="x", pady=6)
            for row, (key, label, kind) in enumerate(fields):
                ttk.Label(box, text=label, width=24).grid(row=row, column=0, sticky="w", pady=3)
                value = (cfg.get(sec) or {}).get(key)
                if kind == "bool":
                    var = tk.BooleanVar(value=bool(value))
                    ttk.Checkbutton(box, variable=var).grid(row=row, column=1, sticky="w")
                else:
                    var = tk.StringVar(value="" if value is None else str(value))
                    if isinstance(kind, tuple):
                        widget = ttk.Combobox(box, textvariable=var, values=kind, state="readonly", width=28)
                    else:
                        widget = ttk.Entry(box, textvariable=var, width=31,
                                           show="•" if key == "token" else "")
                    widget.grid(row=row, column=1, sticky="ew", pady=3)
                self.vars[sec][key] = var
            box.columnconfigure(1, weight=1)
        ttk.Label(body, text="保存至 channels.local.json；下一篇新任务生效，正在运行的文档不改变配置。",
                  foreground="#4b5563", wraplength=570).pack(fill="x", pady=8)
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=8)
        ttk.Button(actions, text="取消", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(actions, text="保存", command=self._save).pack(side="right", padx=4)

    def _save(self):
        updates = {sec: {key: var.get() for key, var in self.vars[sec].items()}
                   for sec, _title, _fields in API_CONFIG_SCHEMA}
        try:
            import webapp
            webapp._save_cfg(updates)
        except Exception as e:
            messagebox.showerror("保存失败", str(e), parent=self)
            return
        messagebox.showinfo("渠道 API 配置", "已保存，下一篇新任务生效。", parent=self)
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        tk.Tk.__init__(self)
        self.title("KCI 解析工作台")
        self.geometry("980x640")
        self.q = queue.Queue()
        self._qc_queue = queue.Queue()
        self._qc_results = {}
        self._qc_generation = 0
        self._qc_pending = []
        self._qc_thread = None
        self._running_view_key = None
        self._selection_anchor = None
        self._prefs = load_prefs()
        self._workspace_paths = self._prefs.get("workspace_paths", {})
        current = self._prefs.get("workspace", "main")
        self.workspace = workspaces.custom_workspace(self._workspace_paths[current]) \
            if current in self._workspace_paths else workspaces.get_workspace(ROOT, current)
        self._notes = load_notes(self.workspace.notes)
        self._webfilled = load_webfill_flags(self.workspace.out)
        self._runners = {}
        self._log_views = {}
        self.runner = self._runner_for(self.workspace)
        self._was_busy = False
        self._webapp_on = False
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build()
        self.after(100, self._drain)
        self.after(50, self._drain_qc)
        self.after(50, self.refresh)
        self._log("就绪: 拖 PDF 到 uploads; Ctrl/Shift 多选可批量(默认 %d 篇并发, 顶栏可调)。"
                  "「一键重跑」= 执行勾选的阶段(主区) / 右端箭头选阶段; "
                  "「快速重跑(收敛链)」不取数/不跑NER。质检列只查必抽项齐全, 缺项报红。" % self.runner.parallel)

    def _runner_for(self, workspace):
        key = workspace.root
        if key not in self._runners:
            self._runners[key] = Runner(
                lambda msg, root=key: self._log(msg, workspace_root=root),
                parallel=self._prefs.get("parallel"), out_root=workspace.out,
                history_fp=workspace.history)
        return self._runners[key]

    # ---------- 界面 ----------
    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=8)
        ttk.Label(top, text="工作区").pack(side="left")
        self.workspace_var = tk.StringVar(value=self.workspace.name)
        self.workspace_box = ttk.Combobox(top, textvariable=self.workspace_var, width=10,
                                          state="readonly", values=workspaces.list_workspaces(ROOT, self._workspace_paths))
        self.workspace_box.pack(side="left", padx=(4, 2))
        self.workspace_box.bind("<<ComboboxSelected>>", self._on_workspace)
        ttk.Button(top, text="新建", command=self._new_workspace).pack(side="left", padx=(0, 6))
        ttk.Button(top, text="刷新", command=self.refresh).pack(side="left")
        ttk.Button(top, text="重启", command=self._restart).pack(side="left", padx=6)
        ttk.Button(top, text="打开 uploads 目录", command=lambda: self._open(self.workspace.uploads)).pack(side="left", padx=6)
        ttk.Button(top, text="打开 out 目录", command=lambda: self._open(self.workspace.out)).pack(side="left")
        tools = ttk.Frame(self)
        tools.pack(fill="x", padx=10, pady=(0, 6))
        # 归档清单是全局视图(不依赖选中), 所以入口在顶栏而不是操作条
        ttk.Button(tools, text="已归档…", command=self._open_archive_list).pack(side="left", padx=6)
        # 浏览器工作台(拖拽上传 + 三渠道并排调参)也收进这里: 用户只记"打开这个工作台"一个动作,
        # 唯一的双击入口就是 启动GUI.bat / start.sh(2026-11-13 收口, 11-25 删掉了重复的 start.bat)。
        ttk.Button(tools, text="浏览器工作台", command=self._open_webapp).pack(side="left")
        ttk.Button(tools, text="渠道 API 配置", command=self._open_api_config).pack(side="left", padx=(8, 0))
        # 并发数: 这是"同时跑几篇", 不是"一篇跑多快"。调大只影响排队位置, 见 crossproc.py。
        ttk.Label(tools, text="并发").pack(side="left", padx=(12, 2))
        self.par_var = tk.StringVar(value=str(self.runner.parallel))
        self.par_box = ttk.Combobox(tools, textvariable=self.par_var, width=3, state="readonly",
                                    values=[str(i) for i in range(1, MAX_PARALLEL + 1)])
        self.par_box.pack(side="left")
        self.par_box.bind("<<ComboboxSelected>>", self._on_parallel)

        # 状态栏单独占窗口底部一行: 并发跑 5 篇时它要报"哪几篇在跑", 挤在顶栏右侧会跟
        # 6 个按钮抢宽度(实测被压到 0 宽, 整条状态直接消失)。
        self.status_lbl = ttk.Label(self, text="", anchor="w")
        self.status_lbl.pack(side="bottom", fill="x", padx=12, pady=(0, 6))
        self.conflict_lbl = tk.Label(self, text="", anchor="w", padx=8, pady=5,
                                     bg="#fff4e5", fg="#b45309")
        self.conflict_lbl.pack(fill="x", padx=10, pady=(0, 4))
        self.conflict_lbl.pack_forget()

        mid = ttk.Panedwindow(self, orient="vertical")
        mid.pack(fill="both", expand=True, padx=10)

        # 文档表(多选: Ctrl/Shift 批量, 操作按钮对选中集生效)
        wrap = ttk.Frame(mid)
        viewbar = ttk.Frame(wrap)
        viewbar.pack(fill="x", pady=(0, 4))
        ttk.Label(viewbar, text="文档 ID").pack(side="left")
        self.search_var = tk.StringVar()
        search = ttk.Entry(viewbar, textvariable=self.search_var, width=12)
        search.pack(side="left", padx=(4, 12))
        self.search_var.trace_add("write", lambda *_args: self._apply_view())
        ttk.Label(viewbar, text="状态").pack(side="left")
        self.filter_var = tk.StringVar(value="全部")
        filter_box = ttk.Combobox(viewbar, textvariable=self.filter_var, width=8, state="readonly",
                                  values=("全部", "运行中", "异常", "未通过", "非正规", "特殊", "未跑", "未跑NER", "完成"))
        filter_box.pack(side="left", padx=4)
        filter_box.bind("<<ComboboxSelected>>", lambda _ev: self._apply_view())
        ttk.Label(viewbar, text="语言").pack(side="left", padx=(8, 0))
        self.lang_filter_var = tk.StringVar(value="全部")
        lang_box = ttk.Combobox(viewbar, textvariable=self.lang_filter_var, width=7, state="readonly",
                                values=("全部", "韩文", "中文", "英文", "混合", "未知"))
        lang_box.pack(side="left", padx=4)
        lang_box.bind("<<ComboboxSelected>>", lambda _ev: self._apply_view())
        self.entity_view = tk.BooleanVar(value=False)
        tk.Checkbutton(lang_box.master, text="实体视图", variable=self.entity_view,
                       command=self._switch_view).pack(side="left", padx=(10, 0))
        # 实体专属按钮只在实体视图下挂出(平时收起, 不挤"全选"按钮)
        self._btn_ent_refresh = ttk.Button(lang_box.master, text="更新实体",
                                           command=self._refresh_entities)
        self._btn_ent_export = ttk.Button(lang_box.master, text="导出Excel",
                                          command=self._export_entity_xlsx)
        # 右侧打包次序 = 被裁时的牺牲次序: 先包者最右且最后被裁。
        # 全选/已选 是功能件优先保; 文档计数纯展示, 顶栏溢出时先裁它(不再"全选被挤掉")。
        selection_box = ttk.Frame(viewbar)
        selection_box.pack(side="right", padx=8)
        self.sel_lbl = ttk.Label(selection_box, text="未选中")
        self.sel_lbl.pack(side="left", padx=(0, 4))
        self.btn_clear_sel = ttk.Button(selection_box, text="全选", command=self._toggle_select_all)
        self.btn_clear_sel.pack(side="left")
        self.count_lbl = ttk.Label(viewbar, text="文档 0 篇", foreground="#4b5563")
        self.count_lbl.pack(side="right", padx=4)
        table = ttk.Frame(wrap)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=(), show="tree headings", height=12,
                                 selectmode="extended")
        # 列规格(列名, 基准宽, 表头, 对齐)——文档视图 9 列; 实体视图 4 列(entview.KINDS 顺序)
        self._doc_colspec = (("state", 64, "状态", "center"), ("qc", 190, "质检 · 必抽项", "w"),
                             ("language", 58, "语言", "center"), ("channels", 58, "渠道", "center"),
                             ("ner", 54, "NER", "center"), ("timing", 104, "时间分析(OCR:LLM)", "center"),
                             ("done_at", 100, "上次完成", "center"), ("runs", 64, "历史", "center"),
                             ("note", 220, "备注", "w"))
        # 实体视图列 = 14 必抽项字段 + 4 实体 kind(entview.COLUMNS 驱动, 字段在前实体在后)
        self._ent_colspec = tuple((k, w, zh, "w") for k, zh, w in entview.COLUMNS)
        self._set_columns(self._doc_colspec)
        self.tree.tag_configure("qc_bad", foreground="#b03030")
        self.tree.tag_configure("qc_nonregular", foreground="#2563eb")
        self.tree.tag_configure("qc_novol", foreground="#7c3aed")   # 免卷期刊: 紫
        self.tree.tag_configure("state_special", foreground="#0e7490")  # 联网回填: 青蓝
        self.tree.tag_configure("state_complete", foreground="#000000")
        self.tree.tag_configure("state_running", foreground="#1d4ed8")
        self.tree.tag_configure("state_unrun", foreground="#6b7280")
        self.tree.tag_configure("state_warn", foreground="#b45309")
        style = ttk.Style(self)
        style.configure("Treeview", rowheight=28)
        style.map("Treeview", background=[("selected", "#dbeafe")],
                  foreground=[("selected", "#111827")])
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree_vscroll = sb
        self.tree.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky="ns")
        hsb = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree_hscroll = hsb
        self.tree.configure(xscrollcommand=hsb.set)
        hsb.grid(row=1, column=0, sticky="ew")
        self._column_base = {"#0": 150, "state": 64, "qc": 190, "language": 58,
                             "channels": 58, "ner": 54, "timing": 104,
                             "done_at": 100, "runs": 64, "note": 220}
        wrap.bind("<Configure>", lambda _ev: self._fit_tree_columns())
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._sync_buttons())
        self.tree.bind("<Button-1>", self._remember_selection_anchor, add=True)
        self.tree.bind("<Shift-Button-1>", self._select_shift_range)
        self.tree.bind("<Double-1>", self._on_double_click)
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
        # 「快速重跑(收敛链)」仍是独立键: 只跑元数据+呈现, 不重取数、不跑 LLM。
        grp = ttk.Frame(act)
        grp.pack(side="left", padx=(4, 8))
        self.btn_rerun = ttk.Button(grp, text="一键重跑  ▾", command=self._on_rerun_click)
        self.btn_rerun.pack(side="left")
        self.btn_rerun.bind("<ButtonPress-1>", self._note_click_x)
        self.btn_quick = ttk.Button(grp, text="快速重跑(收敛链)", command=self._rerun_fast)
        self.btn_quick.pack(side="left", padx=(4, 0))
        self.stage_menu = tk.Menu(self, tearoff=0, postcommand=self._build_stage_menu)
        self.btn_stop = ttk.Button(act, text="停止", command=self._stop_batch, state="disabled")
        self.btn_stop.pack(side="left", padx=(0, 4))
        self.btn_retry = ttk.Button(act, text="重试全部缺失渠道", command=self._retry_missing)
        self.btn_view = ttk.Button(act, text="对照查看器", command=self._open_viewer)
        self.btn_index = ttk.Button(act, text="结果总览", command=self._open_index)
        # 归档与"查看已归档"是两个独立键: 前者对选中集生效, 后者是全局清单(顶栏), 不合并
        self.btn_archive = ttk.Button(act, text="归档选中", command=self._archive_selected)
        for b in (self.btn_retry, self.btn_view, self.btn_index, self.btn_archive):
            b.pack(side="left", padx=4)
        mid.add(act, weight=0)

        # 日志
        logw = ttk.Frame(mid)
        logbar = ttk.Frame(logw)
        logbar.pack(fill="x")
        self.log_follow_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(logbar, text="自动滚动", variable=self.log_follow_var).pack(side="left")
        ttk.Button(logbar, text="清空视图", command=self._clear_log_view).pack(side="left", padx=8)
        self.logtxt = scrolledtext.ScrolledText(logw, height=10, state="disabled", font=("Consolas", 9))
        self.logtxt.pack(fill="both", expand=True)
        self.logtxt.bind("<MouseWheel>", self._on_log_scroll)
        self.logtxt.bind("<Button-4>", self._on_log_scroll)
        self.logtxt.bind("<Button-5>", self._on_log_scroll)
        mid.add(logw, weight=2)

        self.after(1000, self._tick_status)

    def _save_log_view(self):
        self._log_views[self.workspace.root] = (
            self.logtxt.get("1.0", "end-1c"), self.logtxt.yview()[0])

    def _restore_log_view(self):
        text, top = self._log_views.get(self.workspace.root, ("", 0.0))
        self.logtxt.configure(state="normal")
        self.logtxt.delete("1.0", "end")
        if text:
            self.logtxt.insert("1.0", text)
        self.logtxt.configure(state="disabled")
        self.logtxt.yview_moveto(top)

    def _on_workspace(self, _ev=None):
        name = self.workspace_var.get()
        if name == self.workspace.name:
            return
        self._save_log_view()
        self.workspace = workspaces.custom_workspace(self._workspace_paths[name]).ensure() \
            if name in self._workspace_paths else workspaces.get_workspace(ROOT, name).ensure()
        self.runner = self._runner_for(self.workspace)
        self._restore_log_view()
        self._notes = load_notes(self.workspace.notes)
        self._webfilled = load_webfill_flags(self.workspace.out)
        self._qc_results = {}
        self._prefs["workspace"] = name
        save_prefs(self._prefs)
        self.refresh()
        self._log("[工作区] 已切换到 %s" % name)

    def _new_workspace(self):
        path = filedialog.askdirectory(title="选择工作区文件夹", parent=self, mustexist=True)
        if not path:
            return
        ws = workspaces.custom_workspace(path).ensure()
        name = ws.name
        if name == "main" or (name in workspaces.list_workspaces(ROOT)
                              and name not in self._workspace_paths):
            name = "workspace-" + str(len(self._workspace_paths) + 1)
            ws.name = name
        self._workspace_paths[name] = ws.root
        self._prefs["workspace_paths"] = self._workspace_paths
        save_prefs(self._prefs)
        self.workspace_box.configure(values=workspaces.list_workspaces(ROOT, self._workspace_paths))
        self.workspace_var.set(name)
        self._on_workspace()

    def _on_parallel(self, _ev=None):
        try:
            n = self.runner.set_parallel(int(self.par_var.get()))
        except (TypeError, ValueError):
            return
        self.par_var.set(str(n))
        self._prefs["parallel"] = n
        save_prefs(self._prefs)

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

        缺依赖的条目置灰并在标签后写明缺什么——置灰只是提示, 真正的闸门在 stages.run()。
        """
        m = self.stage_menu
        m.delete(0, "end")
        docs = self._selected() or ([self.tree.focus()] if self.tree.focus() else [])
        probe = docs[0] if docs else None
        avail = stages.availability(probe, self.workspace.out) if probe else {}

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
        self._webfilled = load_webfill_flags(self.workspace.out)
        self.docs = scan_docs(self.workspace.uploads, self.workspace.out,
                              self.workspace.ner_root, include_archived=True,
                              arch_root=self.workspace.archive)
        self.hist = load_history(self.workspace.history)
        self._webapp_on = webapp_alive()
        if self._webapp_on:
            self.conflict_lbl.configure(
                text="警告：检测到 web 服务(端口%d)同时运行，请勿在两端解析同一篇文档" % self._webapp_on)
            self.conflict_lbl.pack(fill="x", padx=10, pady=(0, 4))
        else:
            self.conflict_lbl.pack_forget()
        self._qc_generation += 1
        self._qc_pending = sorted(self.docs)
        self._apply_view()
        self._start_qc_loader()

    def _display_state(self, doc):
        if doc in self.runner.running:
            return "运行中"
        if doc in self.runner.queued:
            return "排队"
        h = self.hist.get(doc) or []
        if h and not h[-1].get("ok"):
            return "失败"
        return self.docs[doc]["state"]

    def _row_tag(self, doc, qr=None):
        if qr and qr.get("level") == "bad":
            return "qc_bad"
        if doc in self._webfilled:
            return "state_special"
        if qr and qr.get("level") == "novol":
            return "qc_novol"
        if qr and qr.get("level") == "nonregular":
            return "qc_nonregular"
        state = self._display_state(doc)
        if state in ("运行中", "排队"):
            return "state_running"
        if state == "完成":
            return "state_complete"
        if state == "未跑":
            return "state_unrun"
        return "state_warn"

    def _set_columns(self, colspec):
        """列重配(实体视图切换): 重设列集合/表头/宽度, 同步 _column_base。"""
        self.tree.configure(columns=tuple(c for c, _w, _t, _a in colspec))
        self.tree.heading("#0", text="文档")
        for c, w, t, a in colspec:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=a)
        self.tree.column("#0", width=150)
        self._column_base = dict([("#0", 150)] + [(c, w) for c, w, _t, _a in colspec])

    def _switch_view(self):
        """一键切换: 文档视图(状态/质检/…) ↔ 实体视图(机构/基金/软件/数据集)。
        实体专属按钮(更新实体/导出Excel)随视图挂出收起, 文档视图下顶栏保持原样。"""
        if self.entity_view.get():
            self._btn_ent_refresh.pack(side="left", padx=(4, 0))
            self._btn_ent_export.pack(side="left", padx=(4, 0))
            self._set_columns(self._ent_colspec)
            # 切到实体视图时全量取一次实体矩阵; 之后 refresh 新增的篇目暂显示空格,
            # 再切一次即刷新(NER 产物是 LLM 阶段产出, 不值得每次 refresh 全库重读)。
            # 首次切换全库扫一次正文字数并落增量缓存(out/.view_body_chars.json), 之后秒开
            self._ent_cache = entview.matrix(sorted(self.docs), self.workspace.out,
                                             self.workspace.ner_root)
        else:
            self._btn_ent_refresh.pack_forget()
            self._btn_ent_export.pack_forget()
            self._set_columns(self._doc_colspec)
        self._fit_tree_columns()
        self._apply_view()

    def _refresh_entities(self):
        """更新实体视图: 重读题录+NER 产物并重渲(NER 是 LLM 阶段产物,
        默认只在切换视图时全量取一次; 此键手动强制刷新, 比如 ner 阶段补跑完之后)。
        全程反馈: 状态栏提示+忙光标, 完成后日志报篇数/实体数/耗时。"""
        t0 = time.time()
        self.status_lbl.configure(text="正在更新实体视图…")
        self.configure(cursor="watch")
        self.update_idletasks()
        try:
            self._ent_cache = entview.matrix(sorted(self.docs), self.workspace.out,
                                             self.workspace.ner_root)
        finally:
            self.configure(cursor="")
        dt = time.time() - t0
        n_with_ent = sum(1 for c in self._ent_cache.values()
                         if any(c.get(k) for k, _h, _w in entview.KINDS))
        note = "" if self.entity_view.get() else "(未开实体视图, 仅刷新缓存)"
        self._log("[实体视图] 已重读 %d 篇, 含实体 %d 篇, 用时 %.1fs%s"
                  % (len(self._ent_cache), n_with_ent, dt, note))
        if self.entity_view.get():
            self._fit_tree_columns()
            self._apply_view()

    def _export_entity_xlsx(self):
        """实体矩阵导出 Excel: 与实体视图同一份数据(entview.export_xlsx),
        落盘位置用户自选, 完成后日志留路径。"""
        name = "实体视图_%s.xlsx" % time.strftime("%Y%m%d_%H%M%S")
        fp = filedialog.asksaveasfilename(
            parent=self, title="导出实体视图", initialdir=self.workspace.root,
            initialfile=name, defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")])
        if not fp:
            return
        self.status_lbl.configure(text="正在导出 Excel…")
        self.configure(cursor="watch")
        self.update_idletasks()
        try:
            n = entview.export_xlsx(fp, sorted(self.docs), self.workspace.out,
                                    self.workspace.ner_root)
        except Exception as e:
            messagebox.showerror("导出失败", str(e), parent=self)
            return
        finally:
            self.configure(cursor="")
        self._log("[实体视图] 已导出 %d 篇 -> %s" % (n, fp))

    def _fit_tree_columns(self):
        available = max(240, self.tree.master.winfo_width() - self.tree_vscroll.winfo_reqwidth() - 4)
        # 2026-11-18: 列总宽超出窗口时不再按比例硬挤(字看不清), 保持基准宽交给横向滚动条; 放得下时才拉伸铺满
        ratio = 1.0 if sum(self._column_base.values()) > available else available / float(sum(self._column_base.values()))
        for col, width in self._column_base.items():
            self.tree.column(col, width=max(30, int(width * ratio)))

    def _apply_view(self):
        sels = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        query = self.search_var.get().strip().lower()
        mode = self.filter_var.get()
        lang_mode = self.lang_filter_var.get()
        for doc in sorted(self.docs):
            if query and query not in doc.lower():
                continue
            state = self._display_state(doc)
            qr = self._qc_results.get(doc)
            if lang_mode != "全部" and (qr or {}).get("language", "未知") != lang_mode:
                continue
            if mode == "运行中" and state not in ("运行中", "排队"):
                continue
            if mode == "异常" and not (state in ("失败", "缺渠道", "未完成", "无PDF")
                                       or (qr and qr.get("level") == "bad")):
                continue
            if mode == "未通过" and not (qr and qr.get("level") == "bad"):
                continue
            if mode == "非正规" and not (qr and qr.get("level") == "nonregular"):
                continue
            special = doc in self._webfilled and not (qr and qr.get("level") == "bad")
            if mode == "特殊" and not special:
                continue
            if mode == "未跑" and state != "未跑":
                continue
            if mode == "未跑NER" and self.docs[doc].get("ner"):
                continue
            if mode == "完成" and (state != "完成" or special):
                continue
            d = self.docs[doc]
            h = self.hist.get(doc, [])
            runs = "%d 次%s" % (len(h), "(末次失败)" if (h and not h[-1]["ok"]) else "")
            if d["state"] == "未跑":
                qr = {"label": "-", "level": "", "issues": []}
            if self.entity_view.get():
                _cells = (self._ent_cache or {}).get(doc) or {}
                _vals = tuple(_cells.get(k, "") for k, _zh, _w in entview.COLUMNS)
            else:
                state_disp = "特殊" if special else state
                _vals = (
                    state_disp,
                    qc_display(qr) if qr else "质检中",
                    "-" if d["state"] == "未跑" else (qr or {}).get("language", "检查中"),
                    channel_display(d),
                    "已跑" if d.get("ner") else "未跑",
                    timing_display(h),
                    time.strftime("%m-%d %H:%M", time.localtime(d["done_at"])) if d["done_at"] else "-",
                    runs,
                    (("\U0001F310 " + self._notes.get(doc, "")) if doc in self._webfilled else self._notes.get(doc, "")))
            self.tree.insert("", "end", iid=doc, text=doc, tags=(self._row_tag(doc, qr),), values=_vals)
        visible = [d for d in sels if self.tree.exists(d)]
        if visible:
            self.tree.selection_set(visible)
        self.count_lbl.configure(text="文档 %d / %d 篇" % (len(self.tree.get_children()), len(self.docs)))
        self._sync_buttons()

    def _start_qc_loader(self):
        if not self._qc_pending or (self._qc_thread and self._qc_thread.is_alive()):
            return
        generation = self._qc_generation
        pending = list(self._qc_pending)
        self._qc_thread = threading.Thread(target=self._qc_load, args=(generation, pending), daemon=True)
        self._qc_thread.start()

    def _qc_load(self, generation, pending):
        for doc in pending:
            if generation != self._qc_generation:
                self._qc_queue.put((generation, None, None))
                return
            state = (self.docs.get(doc) or {}).get("state")
            qr = qc_of(doc, out_dir=self.workspace.out, ner_root=self.workspace.ner_root) \
                if state != "未跑" else {"label": "-", "level": "", "issues": []}
            self._qc_queue.put((generation, doc, qr))
        self._qc_queue.put((generation, None, None))

    def _drain_qc(self):
        restart = False
        got = False   # dirty 门闩(2026-11-18): 本轮确实收到新质检结果才重绘视图——
                      # 此前挂在筛选档时每 50ms 重建近千行表格 20 次/秒, 多数轮次空转。
        try:
            for _ in range(30):
                generation, doc, qr = self._qc_queue.get_nowait()
                if generation != self._qc_generation:
                    if doc is None:
                        self._qc_thread = None
                        restart = True
                    continue
                if doc is None:
                    self._qc_pending = []
                    continue
                self._qc_results[doc] = qr
                got = True
                if doc in self.docs and self.tree.exists(doc):
                    values = list(self.tree.item(doc, "values"))
                    if not self.entity_view.get():
                        values[1] = qc_display(qr)
                        values[2] = qr.get("language", "未知")
                    if len(values) >= 9:
                        values[8] = self._notes.get(doc, "")
                    self.tree.item(doc, values=values, tags=(self._row_tag(doc, qr),))
        except queue.Empty:
            pass
        if got and (self.filter_var.get() in ("异常", "未通过", "非正规", "特殊")
                    or self.lang_filter_var.get() != "全部"):
            self._apply_view()
        if restart:
            self._start_qc_loader()
        self.after(50, self._drain_qc)

    def _remember_selection_anchor(self, ev):
        doc = self.tree.identify_row(ev.y)
        if doc:
            self._selection_anchor = doc

    def _select_shift_range(self, ev):
        target = self.tree.identify_row(ev.y)
        if not target:
            return "break"
        docs = list(self.tree.get_children())
        anchor = self._selection_anchor or self.tree.focus()
        if anchor not in docs:
            anchor = self._selected()[0] if self._selected() else docs[0]
        i, j = docs.index(anchor), docs.index(target)
        self.tree.selection_set(docs[min(i, j):max(i, j) + 1])
        self.tree.focus(target)
        return "break"

    def _selected(self):
        return list(self.tree.selection())

    def _sync_buttons(self):
        docs = self._selected()
        has = bool(docs)
        runnable = any(self.docs.get(d, {}).get("pdf") for d in docs)
        retry_count = sum(bool(d.get("pdf") and d.get("miss")) for d in self.docs.values())
        self.btn_rerun.configure(state="normal" if runnable else "disabled",
                                 text="一键重跑（%d） ▾" % len(docs) if docs else "一键重跑 ▾")
        self.btn_quick.configure(state="normal" if runnable else "disabled")
        self.btn_retry.configure(state="normal" if retry_count else "disabled",
                                 text="重试全部缺失渠道（%d篇）" % retry_count)
        self.btn_view.configure(state="normal" if has else "disabled")
        self.btn_index.configure(state="normal" if has else "disabled")
        self.btn_archive.configure(state="normal" if has else "disabled")
        _visible = list(self.tree.get_children())
        _full = bool(_visible) and set(self.tree.selection()) >= set(_visible)
        self.btn_clear_sel.configure(state="normal" if _visible else "disabled",
                                     text="清除选择" if _full else "全选")
        self.btn_stop.configure(state="normal" if self.runner.is_busy()
                                and not self.runner.is_stopping() else "disabled")
        n = len(self._checked_stages()) if hasattr(self, "stage_vars") else 0
        if has:
            self.sel_lbl.configure(text="已选 %d ｜ 勾选 %d 段" % (len(docs), n))
        else:
            self.sel_lbl.configure(text="未选中")

    # ---------- 动作(对选中集批量) ----------
    def _toggle_select_all(self):
        """全选/清除 二合一(2026-11-17): 对**当前筛选出的**全部文档,
        未全选 → 一键全选; 已全选 → 清除。按钮文字随状态切换。"""
        visible = list(self.tree.get_children())
        if not visible:
            return
        if set(self.tree.selection()) >= set(visible):
            self.tree.selection_remove(self.tree.selection())
        else:
            self.tree.selection_set(visible)
        self._sync_buttons()

    def _stop_batch(self):
        self.runner.stop_after_current()
        self._sync_buttons()

    def _run_stages(self, ids, trigger=None):
        """按给定阶段子集批量提交(预设/自定义走同一入口)。"""
        ids = [i for i in stages.ORDER if i in set(ids)]   # 规范顺序 + 去重
        if not ids:
            self._log("[阶段] 未选择任何阶段")
            return
        self.runner.out_root = self.workspace.out
        self.runner.history_fp = self.workspace.history
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
        """快速重跑(收敛链): 只跑会改 metadata/查看器的后段, 不重取数、不跑 NER。

        不改动菜单里的勾选状态: 菜单勾选是另一条路(自定义子集), 替用户改勾选
        会让他下一次"执行选中阶段"跑出没预期的集合。
        """
        # 快速重跑语义 = stages.LEAN_IDS(元数据+呈现), 不再默认重取数/单渠道查看器/NER。
        ids = list(stages.LEAN_IDS)
        self._run_stages(ids, "快速重跑(收敛链)")

    def _retry_missing(self):
        jobs = [(doc, self.docs[doc]) for doc in sorted(self.docs)
                if self.docs[doc].get("pdf") and self.docs[doc].get("miss")]
        if not jobs:
            self._log("[批量] 当前没有缺失渠道的文档")
            return
        counts = {c: sum(c in d["miss"] for _doc, d in jobs) for c in CH}
        msg = ("将重试 %d 篇文档的缺失渠道：\n\nMinerU %d 篇 ｜ dots %d 篇 ｜ Paddle %d 篇\n\n"
               "只清除缺失渠道的 raw 缓存并重新调用 API；已有渠道继续复用。\n"
               "本次不执行 NER，不产生该阶段的 LLM 用时。\n确定继续吗？"
               % (len(jobs), counts["mineru"], counts["dots"], counts["paddle"]))
        if not messagebox.askokcancel("确认重试全部缺失渠道", msg, parent=self):
            return
        n = 0
        ids = [sid for sid in stages.ORDER if sid != "ner"]
        self.runner.out_root = self.workspace.out
        self.runner.history_fp = self.workspace.history
        for doc, d in jobs:
            if self.runner.submit(doc, d["pdf"], ids,
                                  "重试缺失:" + "/".join(d["miss"]), clear=tuple(d["miss"])):
                n += 1
        if n:
            self._log("[批量] 重试缺失渠道已提交 %d 篇" % n)


    def _on_double_click(self, ev):
        if not self.entity_view.get() and self.tree.identify_column(ev.x) == "#10":
            self._edit_note()
        else:
            self._open_viewer()

    def _edit_note(self):
        doc = self.tree.focus() or (self._selected()[0] if self._selected() else None)
        if not doc:
            return
        win = tk.Toplevel(self)
        win.title("备注 — %s" % doc)
        win.geometry("560x260")
        win.transient(self)
        text = tk.Text(win, wrap="word", font=("Microsoft YaHei", 10))
        text.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        text.insert("1.0", self._notes.get(doc, ""))
        text.focus_set()
        actions = ttk.Frame(win)
        actions.pack(fill="x", padx=10, pady=8)
        ttk.Button(actions, text="取消", command=win.destroy).pack(side="right", padx=4)
        ttk.Button(actions, text="保存", command=lambda: self._save_note(
            doc, text.get("1.0", "end-1c"), win)).pack(side="right", padx=4)

    def _save_note(self, doc, value, win=None):
        value = value.strip()
        if value:
            self._notes[doc] = value
        else:
            self._notes.pop(doc, None)
        try:
            save_notes(self._notes, self.workspace.notes)
        except Exception as e:
            messagebox.showerror("备注保存失败", str(e), parent=self)
            return
        self._apply_view()
        if win is not None:
            win.destroy()

    def _context_menu(self, ev):
        iid = self.tree.identify_row(ev.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
        if not iid:
            return
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="编辑备注…", command=self._edit_note)
        m.add_command(label="打开对照查看器", command=self._open_viewer)
        m.add_command(label="打开结果总览", command=lambda: self._open(os.path.join(self.workspace.out, iid, "index.html"))
                      if os.path.exists(os.path.join(self.workspace.out, iid, "index.html")) else None)
        m.add_command(label="打开产物目录", command=lambda: self._open(os.path.join(self.workspace.out, iid)))
        m.add_command(label="归档选中…", command=self._archive_selected)
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
        fp = os.path.join(self.workspace.out, doc, "viewer_compare.html")
        if os.path.exists(fp):
            self._open(fp)

    def _open_index(self):
        for doc in self._selected():
            fp = os.path.join(self.workspace.out, doc, "index.html")
            if os.path.exists(fp):
                self._open(fp)

    # ---------- 归档 ----------
    def _archive_selected(self):
        """对选中集归档。一次确认, 不逐篇问——但把"含几篇红的"写在确认框里。

        红项必须在确认框里说出来: 归档后文档从工作台消失, 缺项只剩归档清单里那一行,
        将来回看容易误以为都通过了。
        """
        docs = [d for d in self._selected() if d in self.docs]
        if not docs:
            return
        busy = [d for d in docs if d in getattr(self.runner, "queued", set())]
        if busy:
            messagebox.showwarning(
                "正在运行", "以下文档正在运行/排队，先等它跑完再归档：\n\n" +
                "\n".join("· " + d for d in busy[:10]), parent=self)
            return
        rows, bad = [], []
        for d in docs:
            q = qc_of(d)
            if q["issues"]:
                bad.append(d)
                rows.append("· %s    ✘ 缺 %s" % (d, "/".join(q["issues"])))
            else:
                rows.append("· %s    ✔ 质检通过" % d)
        msg = "归档 %d 篇：\n\n%s" % (len(docs), "\n".join(rows[:12]))
        if len(rows) > 12:
            msg += "\n… 其余 %d 篇" % (len(rows) - 12)
        if bad:
            msg += ("\n\n⚠ 其中 %d 篇质检未通过。归档后它们不再出现在工作台，"
                    "缺项只会留在归档清单里。" % len(bad))
        msg += "\n\nPDF 与 out/ 产物会移到 archive/，之后可在「已归档…」里恢复。"
        if not messagebox.askokcancel("确认归档", msg, parent=self):
            return
        # 归档原因(2026-11-24): 与备注分离落库——备注是工作台杂项批注, 原因回答"当初
        # 为什么归档"; 不填也能归档, 但清单里会一直缺这一列的锚点(用户实锤定位不到)。
        reason = simpledialog.askstring(
            "归档原因",
            "这批文献为什么归档？\n\n可空。示例：质检红-原文证据缺失 / 重复版本 / 合格入库。\n"
            "会写进归档清单的「归档原因」列。",
            parent=self) or ""
        try:
            r = archive.archive_docs(docs, out_root=self.workspace.out,
                                     up_root=self.workspace.uploads, arch_root=self.workspace.archive,
                                     meta_note=self._notes, reason=reason)
        except Exception as e:
            messagebox.showerror("归档失败", str(e), parent=self)
            return
        for d in r["ok"]:
            self._log("[归档] %s" % d)
            qc.clear_cache(d)      # 产物已搬走, 正文判定的缓存留着会误用
        for d, why in r["skipped"]:
            self._log("[归档·跳过] %s: %s" % (d, why))
        for d, why in r["warnings"]:
            self._log("[归档·提示] %s: %s" % (d, why))
        self._log("[归档] 完成 %d 篇 · 跳过 %d 篇" % (len(r["ok"]), len(r["skipped"])))
        if r["warnings"]:
            messagebox.showinfo("归档完成，但有提示",
                                "已归档 %d 篇。\n\n" % len(r["ok"]) +
                                "\n".join("%s: %s" % (d, w) for d, w in r["warnings"]),
                                parent=self)
        self.refresh()

    def _open_archive_list(self):
        ArchiveWindow(self)

    def _open_api_config(self):
        ApiConfigWindow(self)

    def _open_webapp(self):
        if not self.workspace.is_main:
            messagebox.showinfo("实验区独立运行", "实验区请使用当前 GUI，不共用正式区的 Web 上传服务。", parent=self)
            return
        """浏览器工作台的唯一入口。已在跑就开浏览器; 没跑就起一个(新控制台窗口)。

        两者都会监视 uploads, 同时解析同一篇会写坏产物(13563526 实证), 所以 GUI 有任务在跑时
        先问一句——不是怕它崩, 是怕两份进程交替写同一批文件。
        """
        import subprocess
        import sys
        import webbrowser
        _wport = webapp_alive()
        if _wport:
            webbrowser.open("http://127.0.0.1:%d" % _wport)
            self._log("[web] 已在运行(端口%d)，已打开浏览器 · 注意别与 GUI 同时解析同一篇" % _wport)
            return
        if self.runner.is_busy() and not messagebox.askokcancel(
                "GUI 正在跑任务",
                "浏览器工作台同样会监视 uploads；两处同时解析同一篇会写坏产物。\n仍要启动吗？",
                parent=self):
            return
        subprocess.Popen([sys.executable, os.path.join(ROOT, "webapp.py")],
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        self._log("[web] 已启动浏览器工作台(新窗口)，稍候自动打开 http://127.0.0.1:8765")

    def _restart(self):
        """一键重启: 确保"只有一个 GUI"地换新进程, 以加载 gui.py 等外壳改动。

        2026-11-17 修复"越重启越多"——旧实现先 Popen 新进程再 destroy 自己窗口,
        但窗口关闭 ≠ 进程退出: Runner 的 daemon 线程 + 在跑的 daemon=False 子进程
        (stages.run_batch) 会让进程吊着, 端口迟迟不放; 新进程抢不到就打印"已在运行"
        后自行退出, 于是旧进程还在、新进程没了。反复点就叠出多个 GUI 进程, 名额被
        占满 → 任务卡住。

        现在反过来做: ①停任务、②终止子进程、③真正释放端口/名额、④拉新进程、
        ⑤确认新进程接管后本进程 os._exit 硬退出(不走 mainloop 的自然退出)。
        """
        if getattr(self, "_restarting", False):
            self._log("[重启] 正在重启中, 忽略重复点击")
            return
        if any(r.is_busy() for r in self._runners.values()):
            if not messagebox.askyesno("重启", "有任务正在运行/排队。\n"
                                                "重启会终止这些任务(含后台子进程)，然后开一个干净的新工作台。\n确定要重启吗?"):
                return
        self._restarting = True
        self._log("[重启] 停止任务并终止子进程…")
        # ① 取消排队 + 递增 generation; 不写假失败记录(进程随即退出, 产物由新进程重扫)
        for r in self._runners.values():
            r.stop_after_current()
        # ② 终止本进程起的全部子进程(daemon=False, 否则会拖住本进程退出、占着名额)
        kids = self._terminate_children()
        # ③ 释放单实例端口与自己的名额: 让新进程立刻接管, 不误删别的进程(如 webapp)占的槽
        sock = getattr(self, "_instance_sock", None)
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
            self._instance_sock = None
        freed = self._free_own_slots(kids)
        self._log("[重启] 已终止 %d 个子进程, 释放 %d 个自己的名额" % (len(kids), freed))
        # ④ 拉新进程
        import subprocess, sys
        try:
            subprocess.Popen([sys.executable, os.path.abspath(__file__)],
                             close_fds=True,
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        except Exception as e:
            self._restarting = False
            messagebox.showerror("重启失败", str(e))
            return
        self._log("[重启] 新进程已拉起, 本进程立即退出(不等待, 避免与新版并存)")
        # ⑤ 硬退出: 不 destroy 窗口后靠 mainloop 自然退——那样 daemon 线程/子进程
        #    会吊住进程, 出现"两个 GUI 并存"。os._exit 保证本进程即刻消失。
        self.after(200, os._exit, 0)

    @staticmethod
    def _terminate_children():
        """终止本进程起的全部 multiprocessing 子进程, 返回其 pid 列表。"""
        import multiprocessing as mp
        kids = list(mp.active_children())
        pids = [p.pid for p in kids if getattr(p, "pid", None)]
        for p in kids:
            try:
                p.terminate()
            except Exception:
                pass
        for p in kids:
            try:
                p.join(timeout=1.5)
            except Exception:
                pass
        return pids

    @staticmethod
    def _free_own_slots(child_pids):
        """只清 pid 属于本进程/本进程子进程的名额文件, 不动其他进程(如 webapp)占的槽。

        槽文件首字段是 pid(见 crossproc.Slots._try)。子进程被 terminate 时不会走
        __exit__, 槽会残留到 ttl 过期 —— 重启必须当场清掉, 否则新界面的重跑要等。
        """
        mine = set(child_pids or ()) | {os.getpid()}
        n = 0
        try:
            import crossproc
            for name in os.listdir(crossproc.LOCK_ROOT):
                d = os.path.join(crossproc.LOCK_ROOT, name)
                if not os.path.isdir(d):
                    continue
                for f in os.listdir(d):
                    if not f.startswith("slot_"):
                        continue
                    fp = os.path.join(d, f)
                    try:
                        with io.open(fp, encoding="utf-8") as fh:
                            pid = int(fh.read().split()[0])
                    except Exception:
                        continue
                    if pid in mine:
                        try:
                            os.remove(fp)
                            n += 1
                        except OSError:
                            pass
        except Exception:
            pass
        return n

    def _on_close(self):
        if any(r.is_busy() for r in self._runners.values()):
            n = sum(r._active_count() for r in self._runners.values())
            if not messagebox.askyesno("任务仍在运行",
                                       "正在跑的 %d 篇会在后台继续跑完（一篇一个子进程，不随窗口退出）。\n"
                                       "排队中的任务会丢失。\n\n确定要关闭吗?" % n):
                return
        self.destroy()

    @staticmethod
    def _open(path):
        if os.name == "nt":
            os.startfile(path)  # noqa: S606 - 本地工作台双击等价物
        else:
            import webbrowser
            webbrowser.open("file://" + path)

    def destroy(self):
        self._qc_generation += 1
        super().destroy()

    # ---------- 日志/状态 ----------
    def _log(self, s, workspace_root=None):
        root = workspace_root or self.workspace.root
        line = time.strftime("%H:%M:%S ") + s + "\n"
        if root == self.workspace.root:
            self.q.put((root, line))
        else:
            text, top = self._log_views.get(root, ("", 0.0))
            self._log_views[root] = ((text + line)[-500000:], top)

    def _on_log_scroll(self, _ev=None):
        self.after_idle(self._sync_log_follow)

    def _sync_log_follow(self):
        self.log_follow_var.set(self.logtxt.yview()[1] >= 0.995)

    def _clear_log_view(self):
        self.logtxt.configure(state="normal")
        self.logtxt.delete("1.0", "end")
        self.logtxt.configure(state="disabled")

    def _drain(self):
        try:
            n = 0
            while n < 300:   # 单次回调限量: 日志洪峰不许把 UI 线程钉死
                root, s = self.q.get_nowait()
                n += 1
                if root != self.workspace.root:
                    text, top = self._log_views.get(root, ("", 0.0))
                    self._log_views[root] = ((text + s)[-500000:], top)
                    continue
                self.logtxt.configure(state="normal")
                self.logtxt.insert("end", s)
                # 日志上限: 只保留最后 ~3000 行, 防长会话内存膨胀
                tail = self.logtxt.index("end-1c")
                if tail and int(tail.split(".")[0]) > 3000:
                    self.logtxt.delete("1.0", "2000.0")
                if self.log_follow_var.get():
                    self.logtxt.see("end")
                self.logtxt.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def _tick_status(self):
        txt = self.runner.status()
        ocr_total, llm_total = self.runner.timing_totals()
        txt += " ｜ 启动累计 OCR %s · LLM %s" % (fmt_duration(ocr_total), fmt_duration(llm_total))
        if self._webapp_on:
            txt = "⚠ 检测到 web 服务(端口%s)也在运行, 勿与 GUI 同时解析同一篇 · " % self._webapp_on + txt
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
        running_key = (tuple(sorted(self.runner.running)), tuple(sorted(self.runner.queued)))
        if running_key != self._running_view_key:
            self._running_view_key = running_key
            self._apply_view()
        self._sync_buttons()
        self.after(1000, self._tick_status)


class ArchiveWindow(tk.Toplevel):
    """已归档文献清单: 查、打开产物、恢复、导出。

    独立窗口而非表格的一个筛选档位: 归档后产物不在 out/, 打开路径、恢复动作、导出口径
    都与工作台不同, 混在同一张表里会让"归档"看起来只是打了个标(用户 2026-11-13 要求能查看)。
    """

    COLS = (("title", 300, "标题"), ("journal", 120, "刊名"), ("year", 50, "年"),
            ("language", 50, "语言"), ("qc", 130, "质检(归档时)"), ("when", 126, "归档时间"),
            ("reason", 150, "归档原因"), ("note", 150, "备注"))

    def __init__(self, app):
        tk.Toplevel.__init__(self, app)
        self.app = app
        self.title("已归档文献")
        self.geometry("1020x540")
        self.transient(app)
        self.items = []
        self._build()
        self.refresh()

    def _build(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(bar, text="搜索").pack(side="left")
        self.qvar = tk.StringVar()
        e = ttk.Entry(bar, textvariable=self.qvar, width=34)
        e.pack(side="left", padx=6)
        e.bind("<KeyRelease>", lambda ev: self.refresh())
        ttk.Button(bar, text="清空", command=lambda: (self.qvar.set(""), self.refresh())).pack(side="left")
        self.cnt = ttk.Label(bar, text="")
        self.cnt.pack(side="left", padx=12)
        ttk.Button(bar, text="打开归档目录", command=lambda: app_open(self.app.workspace.archive)).pack(side="right")

        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True, padx=10)
        cols = tuple(c[0] for c in self.COLS)
        self.tree = ttk.Treeview(wrap, columns=("doc",) + cols, show="headings", height=14,
                                 selectmode="extended")
        for cid, w, txt in (("doc", 210, "文档"),) + self.COLS:
            self.tree.heading(cid, text=txt)
            anchor = "w" if cid in ("doc", "title", "journal", "qc", "reason") else "center"
            self.tree.column(cid, width=w, anchor=anchor)
        self.tree.tag_configure("qc_bad", foreground="#b03030")
        self._archive_column_base = {"doc": 110}
        self._archive_column_base.update({c[0]: c[1] for c in self.COLS})
        wrap.bind("<Configure>", lambda _ev: self._fit_columns())
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda ev: self._open_item())

        act = ttk.Frame(self)
        act.pack(fill="x", padx=10, pady=8)
        ttk.Button(act, text="打开产物", command=self._open_item).pack(side="left")
        ttk.Button(act, text="恢复选中", command=self._restore).pack(side="left", padx=6)
        ttk.Button(act, text="导出清单", command=self._export).pack(side="left")
        ttk.Button(act, text="刷新", command=self.refresh).pack(side="left", padx=6)
        self.hint = ttk.Label(act, text="双击一行 = 打开产物")
        self.hint.pack(side="left", padx=12)

    def _fit_columns(self):
        available = max(400, self.tree.master.winfo_width() - 24)
        total = float(sum(self._archive_column_base.values()))
        ratio = available / total
        for name, width in self._archive_column_base.items():
            self.tree.column(name, width=max(48, int(width * ratio)))

    def refresh(self):
        try:
            self.items = archive.list_items(arch_root=self.app.workspace.archive,
                                            q=self.qvar.get().strip())
        except ValueError as e:
            messagebox.showerror("归档索引损坏", str(e), parent=self)
            self.items = []
        self.tree.delete(*self.tree.get_children())
        for it in self.items:
            miss = it.get("qc_issues") or []
            if not miss:
                qtxt = "✔ 通过"
            else:
                # 与工作台质检列同口径(✘ N 项 前四项): 两处看到同一篇不再长得不一样
                head = "/".join(miss[:4]) + ("…" if len(miss) > 4 else "")
                qtxt = "✘ %d 项 %s" % (len(miss), head)
            # 老索引无 reason 字段: 退备注, 再退 "—"(2026-11-24 之前的归档只有 note)
            reason = it.get("reason") or it.get("note") or "—"
            self.tree.insert("", "end", iid=it["doc"], tags=("qc_bad",) if miss else (),
                             values=(it.get("doc"), it.get("title") or "",
                                     it.get("journal") or "", it.get("year") or "",
                                     it.get("language") or "",
                                     qtxt, (it.get("archived_at") or "").replace("T", " ")[:16],
                                     reason, it.get("note") or ""))
        n_bad = sum(1 for it in self.items if it.get("qc_issues"))
        self.cnt.configure(text="共 %d 篇%s" % (
            len(self.items), "，其中 %d 篇有缺项" % n_bad if n_bad else ""))

    def _sel(self):
        return list(self.tree.selection())

    def _open_item(self):
        docs = self._sel()
        if not docs:
            return
        for doc in docs:
            it = archive.item_of(doc, self.app.workspace.archive) or {}
            base = os.path.join(self.app.workspace.archive, doc, "out")
            for name in ("index.html", "viewer_compare.html"):
                fp = os.path.join(base, name)
                if os.path.isfile(fp):
                    app_open(fp)
                    break
            else:
                self.hint.configure(text="%s 只有 PDF，没有产物（归档时 out/ 已不存在）" % doc)

    def _restore(self):
        docs = self._sel()
        if not docs:
            return
        if not messagebox.askokcancel("恢复归档",
                                      "把 %d 篇搬回工作区？\n\n%s" % (
                                          len(docs), "\n".join("· " + d for d in docs[:10])),
                                      parent=self):
            return
        msgs = []
        for doc in docs:
            it = archive.item_of(doc, self.app.workspace.archive) or {}
            ok, msg = archive.restore_doc(doc, out_root=self.app.workspace.out,
                                          up_root=self.app.workspace.uploads,
                                          arch_root=self.app.workspace.archive, keep_note=True)
            msgs.append(("✔ " if ok else "✘ ") + doc + ": " + msg)
            if ok:
                if it.get("note"):
                    self.app._notes[doc] = it["note"]
                    save_notes(self.app._notes, self.app.workspace.notes)
                qc.clear_cache(doc)
        messagebox.showinfo("恢复结果", "\n".join(msgs), parent=self)
        self.refresh()
        self.app.refresh()

    def _export(self):
        if not self.items:
            messagebox.showinfo("没有可导出的内容", "归档为空。", parent=self)
            return
        name = "已归档文献_%s.md" % time.strftime("%Y%m%d")
        fp = filedialog.asksaveasfilename(
            parent=self, title="导出已归档文献清单", initialfile=name,
            defaultextension=".md",
            filetypes=[("Markdown 表格", "*.md"), ("CSV（Excel 可开）", "*.csv")])
        if not fp:
            return
        try:
            body = (archive.export_csv(self.items) if fp.lower().endswith(".csv")
                    else archive.export_markdown(self.items))
            enc = "utf-8-sig" if fp.lower().endswith(".csv") else "utf-8"
            with io.open(fp, "w", encoding=enc) as fh:
                fh.write(body)
        except Exception as e:
            messagebox.showerror("导出失败", str(e), parent=self)
            return
        self.hint.configure(text="已导出 %d 篇 -> %s" % (len(self.items), fp))


def app_open(path):
    """双击等价物: Windows 用关联程序, 其他平台交给浏览器。"""
    if os.name == "nt":
        os.startfile(path)  # noqa: S606
    else:
        import webbrowser
        webbrowser.open("file://" + path)


def main():
    os.makedirs(UP, exist_ok=True)
    sock = _acquire_instance_sock()
    if sock is None:
        print("GUI 已在运行(见任务栏另一个窗口), 请勿重复启动。")
        return
    app = App()
    app._instance_sock = sock   # 重启时由 App._restart 提前 close, 交接端口给新进程
    app.mainloop()   # sock 随进程存活, 退出即释放端口


if __name__ == "__main__":
    main()
