# -*- coding: utf-8 -*-
"""MinerU 桌面版 CDP 驱动: 启动/接管 App -> 上传 PDF -> 等待解析完成 -> 返回输出目录。

依赖: MinerU.exe 支持 --remote-debugging-port(Electron 35 实测可行); websocket-client。
由 pipeline.py 在 mineru 云端 API 获取失败时兜底调用(API 优先), 也可独立使用:
    python mineru_drive.py <input.pdf>

路径/端口来自 channels.json 的 "desktop" 段(--channels 或 channels.local.json 可覆盖),
也可用环境变量 MINERU_EXE / MINERU_LANDING / MINERU_CDP_PORT 临时指定:
    "desktop": {"exe": "...\\MinerU.exe", "landing": "...", "port": 9223, "kill_existing": false}
"""
import os, re, io, json, time, socket, subprocess, sys


def _ts():
    return time.strftime("%H:%M:%S")


ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, "_state_mineru.json")  # 归仓器去重状态(webapp watch_mineru 共读), 不入库


def _cfg():
    """读 channels.json 的 desktop 段; 导入失败(单独运行/无配置)时退回空字典。"""
    try:
        import pipeline
        return pipeline.load_cfg().get("desktop") or {}
    except Exception:
        return {}


def _exe():
    c = _cfg()
    return (os.environ.get("MINERU_EXE") or c.get("exe") or
            (r"D:\MinerU\MinerU.exe" if os.name == "nt" else
             "/Applications/MinerU.app/Contents/MacOS/MinerU"))


def _landing():
    c = _cfg()
    return (os.environ.get("MINERU_LANDING") or c.get("landing") or
            os.path.join(os.path.expanduser("~"), "MinerU"))


def _port():
    c = _cfg()
    try:
        return int(os.environ.get("MINERU_CDP_PORT") or c.get("port") or 9223)
    except (TypeError, ValueError):
        return 9223


def _cdp_up(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False


def _launch():
    """启动带 CDP 端口的 App。默认不动已运行的实例(用户可能正开着自己用);
    单实例应用抢不过旧进程时, 在 desktop 段设 "kill_existing": true 才会先结束同名进程。"""
    port, exe = _port(), _exe()
    if _cfg().get("kill_existing"):
        if os.name == "nt":
            subprocess.run(["taskkill", "/IM", os.path.basename(exe), "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(["pkill", "-f", os.path.basename(exe)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
    subprocess.Popen([exe, "--remote-debugging-port=%d" % port, "--remote-allow-origins=*"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        time.sleep(1)
        if _cdp_up(port):
            return True
    raise RuntimeError("MinerU CDP 端口 %d 未就绪(检查 desktop.exe 路径, 或 App 是否已有实例在跑)" % port)


def _page_ws_list():
    import urllib.request
    port = _port()
    tabs = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json" % port, timeout=5))
    return [t["webSocketDebuggerUrl"] for t in tabs
            if t.get("type") == "page" and "MinerU" in (t.get("title") or "")] or \
           [t["webSocketDebuggerUrl"] for t in tabs if t.get("type") == "page"]


def drive(pdf_path, timeout=1800):
    """上传并等待解析完成, 返回 MinerU 输出目录路径。"""
    import websocket
    port = _port()
    if not _cdp_up(port):
        print("[mineru_drive %s] 启动 MinerU(CDP:%d)..." % (_ts(), port))
        _launch()
    time.sleep(3)
    landing = _landing()
    base = os.path.basename(pdf_path)
    doc = re.sub(r"\.pdf$", "", base, flags=re.I)
    doc = re.sub(r"[\s]*[（(]\d+[)）]$", "", doc).strip()
    before = set(os.listdir(landing)) if os.path.isdir(landing) else set()

    ws, node, root = None, None, None
    last_err = None
    for ws_url in _page_ws_list():
        try:
            w = websocket.create_connection(ws_url, timeout=15)
            mid = [0]
            def call(method, params=None, _w=w, _mid=mid):
                _mid[0] += 1
                _w.send(json.dumps({"id": _mid[0], "method": method, "params": params or {}}))
                while True:
                    m = json.loads(_w.recv())
                    if m.get("id") == _mid[0]:
                        return m.get("result", {})
            try:
                call("Runtime.evaluate", {"expression": "location.hash='#/';'ok'"})
                time.sleep(1.5)
            except Exception:
                pass
            r = call("DOM.getDocument", {"depth": 1})
            rt = r["root"]["nodeId"]
            nd = call("DOM.querySelector", {"nodeId": rt, "selector": "input[type=file]"}).get("nodeId")
            if nd:
                ws, node, root, ev_call = w, nd, rt, call
                break
            w.close()
        except Exception as e:
            last_err = e
    if not ws:
        raise RuntimeError("未找到上传 input (%s)" % last_err)
    def ev(x):
        r = ev_call("Runtime.evaluate", {"expression": x, "returnByValue": True})
        v = r.get("result", {})
        return v.get("value")
    ev_call("DOM.setFileInputFiles", {"files": [os.path.abspath(pdf_path)], "nodeId": node})
    time.sleep(1)
    ev("var i=document.querySelector('input[type=file]');"
       "i.dispatchEvent(new Event('change',{bubbles:true}));"
       "i.dispatchEvent(new Event('input',{bubbles:true}));'ok'")
    print("[mineru_drive %s] 已提交上传: %s" % (_ts(), base))
    JS = ("(function(){var ts=document.querySelectorAll('[class*=task],[class*=item],[class*=Task]');"
          "for(var i=0;i<ts.length;i++){var t=ts[i].innerText;"
          "if(t.indexOf(%s)>=0){return t.split(String.fromCharCode(10)).join('|').slice(0,90)}}return ''})()"
          % json.dumps(doc))
    t0 = time.time()
    final = ""
    last_ping = 0.0
    while time.time() - t0 < timeout:
        time.sleep(3)
        final = str(ev(JS) or "")
        if "成功" in final or "失败" in final:
            break
        if time.time() - last_ping > 30:
            last_ping = time.time()
            print("[mineru_drive %s] 桌面版解析中... 已 %.0fs" % (_ts(), time.time() - t0))
    ws.close()
    if "失败" in final:
        raise RuntimeError("MinerU 解析失败: %s" % final)
    # 等新输出目录出现且含 content_list_v2
    out_dir = None
    for _ in range(40):
        for d in set(os.listdir(landing)) - before:
            if d.startswith(base.replace(".pdf", "") + ".pdf-") or d.startswith(doc + ".pdf-"):
                full = os.path.join(landing, d)
                if os.path.isdir(full) and any(x.endswith("_content_list_v2.json") for x in os.listdir(full)):
                    out_dir = full
        if out_dir:
            break
        time.sleep(2)
    if not out_dir:
        raise RuntimeError("解析完成但未找到输出目录")
    # 登记到归仓器状态, 避免重复复制
    try:
        seen = set(json.load(io.open(STATE, encoding="utf-8")) if os.path.exists(STATE) else [])
    except Exception:
        seen = set()
    seen.add(os.path.basename(out_dir))
    json.dump(sorted(seen), io.open(STATE, "w", encoding="utf-8"))
    print("[mineru_drive %s] 解析完成: %s" % (_ts(), out_dir))
    return out_dir


if __name__ == "__main__":
    print(drive(sys.argv[1]))
