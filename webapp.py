# -*- coding: utf-8 -*-
"""拖拽式三渠道解析对比工作台(纯标准库, 零依赖)
用法: python webapp.py  → 自动打开 http://127.0.0.1:8765(端口被占则顺延到 8774)
把 PDF 拖入页面即自动: 上传 → 跑 pipeline → 页内出结果。
渠道产物约定: 解析完后丢进 inbox\，命名 <文档名>__mineru / <文档名>__dots / <文档名>__paddle.md
"""
import os, re, io, json, threading, time, webbrowser, functools, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

ROOT = os.path.dirname(os.path.abspath(__file__))
UP = os.path.join(ROOT, "uploads")
OUT = os.path.join(ROOT, "out")
INBOX_MINERU_ROOT = os.path.join(ROOT, "inbox")
os.makedirs(UP, exist_ok=True)
JOBS = {}          # job_id -> {state, log[], doc, err}
JOB_LOCK = threading.Lock()
RUN_LOCK = threading.Lock()  # 批处理串行锁: 同时丢多个 PDF 按顺序逐个解析
MIME = {".html": "text/html; charset=utf-8", ".jpg": "image/jpeg", ".png": "image/png",
        ".json": "application/json", ".md": "text/plain; charset=utf-8", ".css": "text/css", ".js": "text/javascript"}
import shutil

CFG_FP = os.path.join(ROOT, "channels.json")
API_SECTIONS = ("paddle_api", "mineru_api", "dots_api")  # web 界面允许改的渠道 API 配置段

def _load_cfg():
    try:
        return json.load(io.open(CFG_FP, encoding="utf-8"))
    except Exception:
        return {}

def _save_cfg(updates):
    """合并 web 端提交的 API 配置(仅白名单段、忽略 _ 前缀键), 写入 channels.local.json。

    写本地覆盖而非入库的 channels.json: token 等隐私值不进版本库(.gitignore 已忽略 channels.local.json),
    且只落改动过的键, 基线里的标定/模型名仍继承 channels.json。原子替换, 避免写一半把配置读空。
    """
    import pipeline
    path = pipeline.LOCAL_CFG
    base = _load_cfg()
    try:
        cfg = json.load(io.open(path, encoding="utf-8")) if os.path.exists(path) else {}
    except Exception:
        cfg = {}
    for sec in API_SECTIONS:
        if sec not in updates or not isinstance(updates[sec], dict):
            continue
        cur = cfg.setdefault(sec, {})
        for k, v in updates[sec].items():
            if not k or k.startswith("_"):
                continue
            old = cur[k] if k in cur else base.get(sec, {}).get(k)  # 类型按有效值(含基线)强转
            if old is None and isinstance(v, str) and v == "":
                continue  # 不存在的键不存空串(前端占位符空值会污染配置, 如 base="" 会让 API 地址变相对路径)
            try:
                if isinstance(old, bool):
                    v = bool(v) if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
                elif isinstance(old, int) and not isinstance(old, bool):
                    v = int(v)
                else:
                    v = str(v)
            except (TypeError, ValueError):
                continue
            cur[k] = v
    tmp = path + ".tmp"
    io.open(tmp, "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=1))
    os.replace(tmp, path)
    return {x: _load_cfg().get(x, {}) for x in API_SECTIONS}


def run_pipeline(job, pdf_path):
    import pipeline

    def _log(*a):
        line = " ".join(str(x) for x in a)
        JOBS[job]["log"].append(line)
        JOBS[job]["logline"] = line[:90]
        print("  [解析] %s" % line)  # 同步打到控制台, 黑窗口可见

    try:
        JOBS[job]["state"] = "排队中"    # 批处理串行: 多文件逐个跑; 日志重定向必须在锁内, 否则排队任务会截获运行中任务的日志
        with RUN_LOCK:
            JOBS[job]["state"] = "运行中"
            old = pipeline.log
            pipeline.log = _log
            try:
                argv = [pdf_path]
                doc = re.sub(r"\.pdf$", "", os.path.basename(pdf_path), flags=re.I)
                JOBS[job]["doc"] = doc
                t0 = time.time()
                pipeline.main(argv)
                idx = os.path.join(OUT, doc, "index.html")   # 只认本次跑出来的 index: 上一轮残留的旧文件不能算成功
                ok = os.path.exists(idx) and os.path.getmtime(idx) >= t0 - 2
                JOBS[job]["state"] = "done" if ok else "error"
            except Exception as e:
                JOBS[job]["state"] = "error"; JOBS[job]["err"] = str(e)
                JOBS[job]["log"].append("ERROR: %s" % e)
            finally:
                pipeline.log = old
    except BaseException as e:  # SystemExit/KeyboardInterrupt 等不是 Exception 的抬走
        JOBS[job]["state"] = "error"
        JOBS[job]["err"] = JOBS[job].get("err") or "任务异常终止: %s" % e
        raise
    finally:
        # 兜底: 任何没走到 done/error 的路径(含被抬走的线程)都不许把任务永远挂在"运行中"
        if JOBS[job]["state"] not in ("done", "error"):
            JOBS[job]["state"] = "error"
            JOBS[job]["err"] = JOBS[job].get("err") or "任务异常终止"


PAGE = u"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>三渠道解析对比工作台</title>
<style>
body{font-family:"Microsoft YaHei";margin:0;background:#f4f5f7;color:#222}
header{background:#1f2937;color:#fff;padding:14px 22px}
header b{font-size:17px}header span{color:#9ca3af;font-size:12px;margin-left:12px}
.wrap{max-width:1100px;margin:24px auto;padding:0 16px}
#drop{border:3px dashed #93c5fd;border-radius:14px;background:#fff;padding:70px 20px;text-align:center;color:#6b7280;transition:.15s}
#drop.over{border-color:#2563eb;background:#eff6ff;transform:scale(1.01)}
#drop b{color:#2563eb;font-size:18px}
#log{background:#111827;color:#a7f3d0;font:12px/1.7 Consolas,monospace;border-radius:10px;padding:12px 16px;margin-top:18px;min-height:60px;white-space:pre-wrap;display:none}
#jobs{display:none;margin-top:16px}
#jobs .jrow{display:flex;align-items:center;gap:10px;background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:8px 12px;margin:6px 0;cursor:pointer;font-size:13px}
#jobs .jrow:hover{border-color:#2563eb}
#jobs .jrow.cur{outline:2px solid #93c5fd}
#jobs .st{font-size:11px;border-radius:6px;padding:2px 8px;color:#fff;white-space:nowrap}
#jobs .st.run{background:#2563eb}#jobs .st.que{background:#9ca3af}#jobs .st.ok{background:#059669}#jobs .st.err{background:#dc2626}
#jobs .jlog{font-size:12px;color:#6b7280;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#jobs a{margin-left:6px;color:#2563eb;text-decoration:none;display:none}
.tip{font-size:12px;color:#9ca3af;margin-top:10px}
#cfgbtn{float:right;background:#374151;color:#e5e7eb;border:1px solid #4b5563;border-radius:8px;padding:6px 14px;cursor:pointer;font-size:13px}
#cfgbtn:hover{background:#4b5563}
#cfgpanel{display:none;position:fixed;right:0;top:0;height:100%;width:430px;background:#fff;box-shadow:-6px 0 24px rgba(0,0,0,.18);z-index:99;overflow:auto;padding:18px 22px}
#cfgpanel h3{font-size:15px;margin:0 0 4px}
#cfgpanel .sec{border:1px solid #e5e7eb;border-radius:10px;padding:10px 12px;margin:12px 0}
#cfgpanel .sec b{font-size:13px;color:#1f2937}
#cfgpanel label{display:block;font-size:12px;color:#6b7280;margin:8px 0 2px}
#cfgpanel input[type=text],#cfgpanel input[type=number]{width:100%;box-sizing:border-box;border:1px solid #d1d5db;border-radius:6px;padding:6px 8px;font-size:13px}
#cfgpanel .row{display:flex;gap:14px}
#cfgpanel .row>div{flex:1}
#cfgpanel .acts{position:sticky;bottom:0;background:#fff;padding:10px 0;display:flex;gap:10px}
#cfgpanel .acts button{flex:1;border:0;border-radius:8px;padding:9px 0;cursor:pointer;font-size:14px}
#savecfg{background:#2563eb;color:#fff}#closecfg{background:#e5e7eb}
#cfgmsg{font-size:12px;color:#059669;min-height:16px}
</style></head><body>
<header><button id="cfgbtn">⚙ 渠道 API 配置</button><b>三渠道解析对比工作台</b><span>拖入 PDF（或把 PDF 复制到 uploads 文件夹）→ 自动解析 → 生成对比查看器 ｜ 渠道产物放 inbox\，命名 &lt;文档名&gt;__渠道</span></header>
<div class="wrap">
<div id="drop"><b>把 PDF 拖到这里</b><br><br>或点击选择文件<input id="f" type="file" accept="application/pdf" style="display:none"></div>
<div id="jobs"></div>
<div id="log"></div>
<p class="tip">上传的 PDF 保存在 pipeline\\uploads\\；结果在 pipeline\\out\\&lt;文档名&gt;\\。渠道产物未到齐的渠道会在报告中标注"未提供"。</p>
</div>
<div id="cfgpanel">
 <h3>渠道 API 配置</h3>
 <p class="tip" style="margin:0">写入 channels.json，下个文档自动生效（pipeline 每次调用都会重读）。</p>
 <div id="cfgform"></div>
 <div id="cfgmsg"></div>
 <div class="acts"><button id="closecfg">取消</button><button id="savecfg">保存</button></div>
</div>
<script src="/app.js"></script></body></html>"""

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        b = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.end_headers()
        self.wfile.write(b)
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._send(200, PAGE)
        if self.path.startswith("/app.js"):
            fp = os.path.join(ROOT, "app.js")
            if os.path.isfile(fp):
                return self._send(200, io.open(fp, "rb").read(), "text/javascript; charset=utf-8")
            return self._send(404, "not found")
        if self.path.startswith("/status"):
            q = re.search(r"job=(\w+)", self.path)
            job = q.group(1) if q else ""
            if job == "all":  # 批处理任务总览
                return self._send(200, json.dumps({"all": [
                    {"id": k, "state": j.get("state", ""), "doc": j.get("doc", ""),
                     "logline": (j.get("log") or [""])[-1][:90]}
                    for k, j in JOBS.items()][-30:]}, ensure_ascii=False), "application/json")
            if job == "latest":
                job = list(JOBS.keys())[-1] if JOBS else ""  # 任意方式触发的任务都能在页面看到
            j = JOBS.get(job, {})
            return self._send(200, json.dumps({"state": j.get("state", "gone"), "log": j.get("log", [])[-60:], "doc": j.get("doc", ""), "err": j.get("err", "")}), "application/json")
        if self.path.startswith("/api/config"):
            return self._send(200, json.dumps({s: _load_cfg().get(s, {}) for s in API_SECTIONS}, ensure_ascii=False), "application/json")
        if self.path.startswith("/out/"):
            rel = unquote(self.path[len("/out/"):].split("?")[0])  # 中文文档名/文件名在 URL 里是 %XX 编码, 不解码必然 404
            fp = os.path.abspath(os.path.join(OUT, rel.lstrip("/").replace("/", os.sep)))
            if not (fp == OUT or fp.startswith(OUT + os.sep)):   # 解析后越界即拒(比删 ".." 子串可靠: 前者挡不住编码形式)
                return self._send(403, "forbidden")
            if os.path.isfile(fp):
                ext = os.path.splitext(fp)[1].lower()
                return self._send(200, io.open(fp, "rb").read(), MIME.get(ext, "application/octet-stream"))
            return self._send(404, "not found")
        self._send(404, "not found")
    def do_POST(self):
        if self.path.startswith("/api/config"):
            n = int(self.headers.get("Content-Length", 0))
            if n <= 0 or n > 64 * 1024:
                return self._send(413, "bad request")
            try:
                updates = json.loads(self.rfile.read(n).decode("utf-8"))
                saved = _save_cfg(updates)
                return self._send(200, json.dumps({"ok": 1, "cfg": saved}, ensure_ascii=False), "application/json")
            except Exception as e:
                return self._send(400, json.dumps({"ok": 0, "err": str(e)}, ensure_ascii=False), "application/json")
        if not self.path.startswith("/upload"):
            return self._send(404, "not found")
        name = re.search(r"name=([^&]+)", self.path)
        name = name.group(1) if name else "upload.pdf"
        from urllib.parse import unquote
        name = unquote(name)  # 中文名经过 URL 编码, 先解码再做字符过滤
        name = re.sub(r"[^\w\-.一-鿿]+", "_", name) or "upload.pdf"
        if not name.lower().endswith(".pdf"): name += ".pdf"
        n = int(self.headers.get("Content-Length", 0))
        if n <= 0 or n > 200 * 1024 * 1024:
            return self._send(413, "file too large or empty")
        fp = os.path.join(UP, name)
        with io.open(fp, "wb") as f:
            left = n
            while left > 0:
                chunk = self.rfile.read(min(1 << 20, left))
                if not chunk: break
                f.write(chunk); left -= len(chunk)
        job = "%x" % int(time.time() * 1000)
        doc0 = re.sub(r"\.pdf$", "", name, flags=re.I)
        doc0 = re.sub(r"[\s]*[（(]\d+[)）]$", "", doc0).strip()
        with JOB_LOCK:
            JOBS[job] = {"state": "running", "log": [], "doc": doc0, "err": "", "logline": ""}
        threading.Thread(target=run_pipeline, args=(job, fp), daemon=True).start()
        self._send(200, json.dumps({"job": job}), "application/json")

def watch_mineru():
    """MinerU 产物归仓: 监视 App 输出目录, 完成的运行自动复制进 inbox 并把原PDF放回 uploads 触发全流程"""
    import pipeline
    land = (pipeline.load_cfg().get("desktop") or {}).get("landing") or os.path.join(os.path.expanduser("~"), "MinerU")
    state_fp = os.path.join(ROOT, "_state_mineru.json")
    seen = set(json.load(io.open(state_fp, encoding="utf-8")) if os.path.exists(state_fp) else [])
    print("[watch] MinerU 产物归仓监视:", land)
    while True:
        time.sleep(5)
        try:
            names = os.listdir(land)
        except OSError:
            continue
        for d in names:
            if d in seen: continue
            if not re.match(r".+\.pdf-[0-9a-fA-F-]{10,}$", d): continue
            full = os.path.join(land, d)
            if not os.path.isdir(full): continue
            done = any(x.endswith("_content_list_v2.json") for x in os.listdir(full))
            if not done: continue  # 解析还没跑完
            doc = re.sub(r"[\s]*[（(]\d+[)）]$", "", d.split(".pdf-")[0]).strip()
            try:
                tgt = os.path.join(INBOX_MINERU_ROOT, doc + "__mineru", d)
                os.makedirs(os.path.dirname(tgt), exist_ok=True)
                shutil.copytree(full, tgt, dirs_exist_ok=True)
                orig = [x for x in os.listdir(full) if x.endswith("_origin.pdf")]
                if orig:
                    updf = os.path.join(UP, doc + ".pdf")
                    if not os.path.exists(updf):
                        shutil.copy2(os.path.join(full, orig[0]), updf)
                        print("[watch] MinerU 归仓: %s → inbox/%s__mineru, 原PDF已入 uploads" % (d, doc))
                    else:
                        print("[watch] MinerU 归仓: %s → inbox/%s__mineru (uploads 已有同文档)" % (d, doc))
                seen.add(d)
                json.dump(sorted(seen), io.open(state_fp, "w", encoding="utf-8"))
            except Exception as e:
                print("[watch] MinerU 归仓失败 %s: %s" % (d, e))


def _rescue_run(job, pdf, doc):
    """补跑包装: 先跑 pipeline 补齐渠道, 再刷新该文档的 merged 合并产物(快照一致性)"""
    run_pipeline(job, pdf)
    try:
        import merge_out
        merge_out.merge_doc(doc, os.path.join(OUT, doc))
        print("[rescue] %s merged 已随补跑刷新" % doc)
    except Exception as e:
        print("[rescue] merged 刷新失败 %s: %s" % (doc, e))

def rescue_scan():
    r"""失败渠道兜底补跑: out\<doc> 已有 index.html 但某渠道既无 raw\<ch> 也无 raw\<ch>_api
    (渠道从未成功获取), 且 uploads 里 PDF 还在 -> 自动重跑该文档。
    重跑走缓存: 成功渠道秒回, 只重取缺失渠道; 产物快照同步刷新。一次只补一个, 防并发重跑。"""
    try:
        docs = os.listdir(OUT)
    except OSError:
        return
    for d in sorted(docs):
        dd = os.path.join(OUT, d)
        if not os.path.isdir(dd) or d.startswith("_"):
            continue
        if not os.path.exists(os.path.join(dd, "index.html")):
            continue  # 还没跑过的文档由 watch_uploads 的正常路径处理
        miss = [ch for ch in ("mineru", "dots", "paddle")
                if not os.path.exists(os.path.join(dd, "raw", ch))
                and not os.path.exists(os.path.join(dd, "raw", ch + "_api"))]
        if not miss:
            continue
        pdf = os.path.join(UP, d + ".pdf")
        if not os.path.exists(pdf):
            continue
        if RUN_LOCK.locked():
            return  # 有任务在跑, 下一轮再补
        jid = "rescue-%x" % int(time.time() * 1000)
        with JOB_LOCK:
            JOBS[jid] = {"state": "running", "log": [], "doc": d,
                         "err": "", "logline": "", "rescue": ",".join(miss)}
        print("[rescue] %s 缺渠道(%s), 自动重跑补齐" % (d, "/".join(miss)))
        threading.Thread(target=_rescue_run, args=(jid, pdf, d), daemon=True).start()
        return

def watch_uploads():
    """监视 uploads\\ 目录: 复制/移入/覆盖 PDF 即自动开工(无需拖拽手势)"""
    def mtime(fp):
        try: return os.path.getmtime(fp)
        except OSError: return -1
    seen = {}
    skip_logged = set()  # "文件无变化"只提示一次, 重复放同一文件不再刷屏
    for f in os.listdir(UP):  # 积压处理: 已产出且结果比PDF新的才算见过; 否则启动即补跑
        fp = os.path.join(UP, f)
        if not (f.lower().endswith(".pdf") and os.path.isfile(fp)): continue
        d = re.sub(r"\.pdf$", "", f, flags=re.I)
        d = re.sub(r"[\s]*[（(]\d+[)）]$", "", d).strip()
        idx = os.path.join(ROOT, "out", d, "index.html")
        if os.path.exists(idx) and os.path.getmtime(idx) >= mtime(fp):
            seen[fp] = mtime(fp)
    print("[watch] 监视目录:", UP, "— 把 PDF 复制进来即可自动解析")
    while True:
        time.sleep(2)
        rescue_scan()  # 兜底: 渠道曾失败的文档自动补跑(每次只补一个, 有任务在跑则跳过)
        for f in os.listdir(UP):
            if not f.lower().endswith(".pdf"): continue
            fp = os.path.join(UP, f)
            mt = mtime(fp)
            if mt <= 0: continue
            if seen.get(fp) == mt:
                if fp not in skip_logged:
                    skip_logged.add(fp)
                    print("[watch] 文件无变化, 跳过(缓存命中): %s" % f)
                continue  # 内容未变(覆盖会改 mtime -> 触发重跑)
            skip_logged.discard(fp)
            time.sleep(1.2)  # 等写入完成(mtime 稳定)
            if mtime(fp) != mt: continue
            seen[fp] = mt
            jid = "%x" % int(time.time() * 1000)
            with JOB_LOCK:
                JOBS[jid] = {"state": "running", "log": [], "doc": re.sub(r"[\s]*[（(]\d+[)）]$", "", re.sub(r"\.pdf$", "", f, flags=re.I)).strip(), "err": "", "logline": ""}
            print("[watch] 检测到新文件:", f)
            threading.Thread(target=run_pipeline, args=(jid, fp), daemon=True).start()

class SingleServer(ThreadingHTTPServer):
    allow_reuse_address = False  # 禁止同端口重复绑定(Windows 的 SO_REUSEADDR 会允许多实例叠听!)

if __name__ == "__main__":
    LOCK = os.path.join(ROOT, "_server.lock")
    if os.path.exists(LOCK):  # 单实例锁: 防重复双击 start.bat 叠出多服务
        try:
            os.kill(int(io.open(LOCK, encoding="ascii").read().strip()), 0)
            print("服务已在运行(见另一个控制台窗口), 请勿重复启动。")
            print("浏览器访问: http://127.0.0.1:8765")
            sys.exit(0)
        except (OSError, ValueError):
            pass  # 残留锁(进程已死)
    io.open(LOCK, "w", encoding="ascii").write(str(os.getpid()))

    port = 0
    for off in range(10):  # 端口被占则自动顺延
        try:
            srv = SingleServer(("127.0.0.1", 8765 + off), H)
            port = 8765 + off
            break
        except OSError:
            continue
    if not port:
        print("错误: 8765-8774 端口全被占用"); sys.exit(1)
    url = "http://127.0.0.1:%d" % port
    print("=" * 60)
    print("  解析对比工作台已启动:", url)
    print("  用法1: 浏览器里把 PDF 拖入页面")
    print("  用法2: 把 PDF 复制到  %s" % UP)
    print("  本窗口保持开着服务才运行; 关闭窗口 = 停止服务")
    print("=" * 60)
    threading.Thread(target=watch_uploads, daemon=True).start()
    threading.Thread(target=watch_mineru, daemon=True).start()
    threading.Timer(5.0, rescue_scan).start()  # 启动即扫一遍: 上次会话渠道失败的文档自动补
    try:
        webbrowser.open(url)
    except Exception:
        pass
    srv.serve_forever()
