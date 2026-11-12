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

ROOT = os.path.dirname(os.path.abspath(__file__))
UP = os.path.join(ROOT, "uploads")
OUT = os.path.join(ROOT, "out")
HIST_FP = os.path.join(ROOT, "gui_runs.jsonl")
CH = ("mineru", "dots", "paddle")
CH_ZH = {"mineru": "MinerU", "dots": "dots.ocr", "paddle": "PaddleOCR"}


def doc_of(pdf_name):
    doc = re.sub(r"\.pdf$", "", pdf_name, flags=re.I)
    return re.sub(r"[\s]*[（(]\d+[)）]$", "", doc).strip()


def _ch_done(doc, ch):
    d = os.path.join(OUT, doc, "raw", ch)
    if os.path.isdir(d) and any(os.scandir(d)):
        return True
    d = os.path.join(OUT, doc, "raw", ch + "_api")
    if os.path.isdir(d) and any(os.scandir(d)):
        return True
    return False


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


class Runner(object):
    """串行跑 pipeline: 队列依次执行; 成功渠道走缓存, 只有 clear 指定的渠道重取。"""

    def __init__(self, log):
        self.log = log
        self.q = queue.Queue()          # (doc, pdf, clear_tuple, trigger)
        self.cur = None                 # (doc, trigger, started)
        self.lock = threading.Lock()
        threading.Thread(target=self._work, daemon=True).start()

    def submit(self, doc, pdf, clear=(), trigger="重跑"):
        self.q.put((doc, pdf, tuple(clear), trigger))
        self.log("[队列] %s 已加入 (%s)" % (doc, trigger))

    def _work(self):
        import pipeline
        while True:
            doc, pdf, clear, trigger = self.q.get()
            for ch in clear:
                for sub in (ch, ch + "_api"):
                    d = os.path.join(OUT, doc, "raw", sub)
                    if os.path.isdir(d):
                        shutil.rmtree(d, ignore_errors=True)
                self.log("[重取] %s 渠道 %s 缓存已清" % (doc, ch))
            with self.lock:
                self.cur = (doc, trigger, time.time())
            old = pipeline.log
            buf = []

            def _hook(*a):
                line = " ".join(str(x) for x in a)
                buf.append(line)
                self.log("  " + line)

            pipeline.log = _hook
            ok, err = False, ""
            t0 = time.time()
            try:
                pipeline.main([pdf])
                idx = os.path.join(OUT, doc, "index.html")
                ok = os.path.exists(idx) and os.path.getmtime(idx) >= t0 - 2
                if not ok:
                    err = "未生成本次总览"
            except Exception as e:  # 各阶段已内部不阻断; 这里兜 main 本身
                err = str(e)[:200]
            finally:
                pipeline.log = old
            rec = {"doc": doc, "trigger": trigger, "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)),
                   "elapsed_s": int(time.time() - t0), "ok": ok, "err": err or None,
                   "channels": {c: _ch_done(doc, c) for c in CH}}
            append_history(rec)
            with self.lock:
                self.cur = None
            self.log("[完成] %s %s (耗时 %ds)%s" % (doc, "成功" if ok else "失败", rec["elapsed_s"],
                                                    (" · " + err) if err else ""))
            self.q.task_done()
            try:
                from metapy.llmrep import rebuild_viewer
                rebuild_viewer(doc, OUT)
            except Exception as e:
                self.log("[提示] 查看器重建失败(不影响产物): %s" % str(e)[:80])

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


class App(tk.Tk):
    def __init__(self):
        tk.Tk.__init__(self)
        self.title("KCI 解析工作台")
        self.geometry("980x640")
        self.q = queue.Queue()
        self.runner = Runner(self._log)
        self._was_busy = False
        self._build()
        self.after(100, self._drain)
        self.after(500, self.refresh)
        self._log("就绪: 拖 PDF 到 uploads 或点下方按钮; 状态全部来自磁盘, 重启不丢。")

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

        # 文档表
        wrap = ttk.Frame(mid)
        cols = ("state", "mineru", "dots", "paddle", "done_at", "runs")
        self.tree = ttk.Treeview(wrap, columns=cols, show="tree headings", height=12)
        self.tree.heading("#0", text="文档")
        for c, w, t in (("state", 70, "状态"), ("mineru", 60, "MinerU"), ("dots", 60, "dots"),
                        ("paddle", 70, "Paddle"), ("done_at", 130, "上次完成"), ("runs", 90, "历史")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="center")
        self.tree.column("#0", width=200)
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._sync_buttons())
        self.tree.bind("<Double-1>", lambda e: self._open_viewer())
        mid.add(wrap, weight=3)

        # 操作条(对选中文档)
        act = ttk.Frame(mid)
        self.btn_rerun = ttk.Button(act, text="一键重跑", command=self._rerun)
        self.btn_retry = ttk.Button(act, text="重试缺失渠道", command=self._retry_missing)
        self.btn_view = ttk.Button(act, text="对照查看器", command=self._open_viewer)
        self.btn_index = ttk.Button(act, text="结果总览", command=self._open_index)
        for b in (self.btn_rerun, self.btn_retry, self.btn_view, self.btn_index):
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

    # ---------- 数据 ----------
    def refresh(self):
        self.docs = scan_docs()
        self.hist = load_history()
        sel = self._selected()
        self.tree.delete(*self.tree.get_children())
        for doc, d in sorted(self.docs.items()):
            h = self.hist.get(doc, [])
            last_ok = h[-1]["ok"] if h else None
            runs = "%d 次%s" % (len(h), "(末次失败)" if (h and not h[-1]["ok"]) else "")
            self.tree.insert("", "end", iid=doc, text=doc, values=(
                d["state"],
                "✔" if d["ch"]["mineru"] else "—",
                "✔" if d["ch"]["dots"] else "—",
                "✔" if d["ch"]["paddle"] else "—",
                time.strftime("%m-%d %H:%M", time.localtime(d["done_at"])) if d["done_at"] else "-",
                runs))
        if sel and sel in self.docs:
            self.tree.selection_set(sel)
        self._sync_buttons()

    def _selected(self):
        s = self.tree.selection()
        return s[0] if s else None

    def _sync_buttons(self):
        doc = self._selected()
        has = bool(doc)
        runnable = has and self.docs.get(doc, {}).get("pdf")
        self.btn_rerun.configure(state="normal" if runnable else "disabled")
        self.btn_retry.configure(state="normal" if (runnable and self.docs[doc]["miss"]) else "disabled")
        self.btn_view.configure(state="normal" if has else "disabled")
        self.btn_index.configure(state="normal" if has else "disabled")
        self.sel_lbl.configure(text=doc or "未选中")

    # ---------- 动作 ----------
    def _rerun(self):
        doc = self._selected()
        if doc and self.docs[doc]["pdf"]:
            self.runner.submit(doc, self.docs[doc]["pdf"], (), trigger="一键重跑")

    def _retry_missing(self):
        doc = self._selected()
        d = self.docs.get(doc)
        if d and d["pdf"] and d["miss"]:
            self.runner.submit(doc, d["pdf"], tuple(d["miss"]),
                               trigger="重试缺失:" + "/".join(d["miss"]))

    def _open_viewer(self):
        doc = self._selected()
        fp = os.path.join(OUT, doc, "viewer_compare.html")
        if os.path.exists(fp):
            self._open(fp)

    def _open_index(self):
        doc = self._selected()
        fp = os.path.join(OUT, doc, "index.html")
        if os.path.exists(fp):
            self._open(fp)

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
            while True:
                s = self.q.get_nowait()
                self.logtxt.configure(state="normal")
                self.logtxt.insert("end", time.strftime("%H:%M:%S ") + s + "\n")
                self.logtxt.see("end")
                self.logtxt.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def _tick_status(self):
        self.status_lbl.configure(text=self.runner.status())
        busy = self.runner.is_busy()
        if self._was_busy and not busy:
            self.refresh()   # 跑完自动刷新: 上次完成时间/渠道勾选/历史次数同步
        self._was_busy = busy
        self.after(1000, self._tick_status)


def main():
    os.makedirs(UP, exist_ok=True)
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
