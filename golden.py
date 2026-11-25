# -*- coding: utf-8 -*-
"""golden.py — 黄金样本回归集(2026-11-24): 每修好一篇, 把它的 metadata 存成
"标准答案"; 改完代码跑 golden run, 秒级知道以前修好的有没有被改坏。

  python golden.py add KCI11_17 13571795        # 冻结当前 metadata 为样本
  python golden.py add KCI11_17 13571795 --note "作者序口径"
  python golden.py run [--ws KCI11_17] [--workers 4]   # 重跑元数据段并逐字段 diff
  python golden.py list
  python golden.py rm KCI11_17 13571795

为什么是 golden run 而不是"重跑全库": 改一条守卫只需证明"已知正确的还正确",
样本集(几十篇)秒级跑完; 全库 800+ 篇是 meta_diff/failures 的活, 不需要天天做。

样本 = golden/<ws键>/<doc>.json (metadata.json 全量拷贝, 附冻结说明)。
run 复用 stages.run_one_child(一篇一子进程, 与 GUI 同实现), 阶段 = 收敛链元数据段
(m3,refs,frefs,front,abs,issue,cspan); viewer/index 是 HTML 呈现, 不进样本比对。
比对忽略 "generated" 时间戳; 其余字段有任何不同即报, 由人判定是回归还是该更新样本。
"""
import argparse
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

GOLDEN_DIR = os.path.join(HERE, "golden")
# 收敛链元数据段(与 stages.META_IDS 同序); m3 会写回 unified, 与真实重跑同行为。
IDS = ("m3", "refs", "frefs", "front", "abs", "issue", "cspan")
IGNORE_KEYS = {"generated"}


def _ws_root(ws):
    import workspaces
    root, name, src = workspaces.resolve_ws_root(explicit=ws, here=HERE)
    return root, name, src


def _ws_key(name, root):
    return name if name != "main" else "main"


def _sample_fp(ws_name, doc):
    d = os.path.join(GOLDEN_DIR, ws_name)
    return os.path.join(d, doc + ".json")


def _load_meta(fp):
    try:
        return json.load(io.open(fp, encoding="utf-8"))
    except Exception:
        return None


_PROV_KEYS = {"uid", "box", "page"}


def _norm(v):
    """语义值归一: 剥出处键(uid/box/page), 解包 {'value':...} 置信度壳。
    golden 判"值对不对", 不判"出处标注变没变"——后者是 meta_diff/audit 的活。"""
    if isinstance(v, dict):
        if "value" in v:
            return _norm(v.get("value"))
        return {k: _norm(x) for k, x in v.items() if k not in _PROV_KEYS}
    if isinstance(v, list):
        return [_norm(x) for x in v]
    return v


def diff_meta(expected, actual):
    """逐字段 diff(忽略 IGNORE_KEYS 与出处类扰动)。返回 [(字段, 前值摘要, 后值摘要)]。"""
    out = []
    keys = [k for k in expected if k not in IGNORE_KEYS]
    for k in keys:
        b, a = _norm(expected.get(k)), _norm(actual.get(k))
        if json.dumps(b, sort_keys=True, ensure_ascii=False) \
                != json.dumps(a, sort_keys=True, ensure_ascii=False):
            out.append((k, _brief(b), _brief(a)))
    return out


def _brief(v, n=70):
    s = json.dumps(v, ensure_ascii=False)
    return s[:n] + ("…" if len(s) > n else "")


def cmd_add(ws, doc, note=""):
    root, name, src = _ws_root(ws)
    fp = os.path.join(root, "out", doc, "metadata.json")
    meta = _load_meta(fp)
    if meta is None:
        raise SystemExit("无 metadata: %s(先跑收敛链)" % fp)
    os.makedirs(os.path.dirname(_sample_fp(name, doc)), exist_ok=True)
    payload = {"doc": doc, "ws": name, "frozen_at": time.strftime("%Y-%m-%d %H:%M"),
               "frozen_by": "golden add", "note": note, "metadata": meta}
    io.open(_sample_fp(name, doc), "w", encoding="utf-8").write(
        json.dumps(payload, ensure_ascii=False, indent=1))
    print("已冻结 %s/%s (%d 字段%s)" % (name, doc, len(meta),
                                      " · " + note if note else ""))


def cmd_rm(ws, doc):
    _, name, _ = _ws_root(ws)
    fp = _sample_fp(name, doc)
    if not os.path.isfile(fp):
        raise SystemExit("无此样本: %s" % fp)
    os.remove(fp)
    print("已删除 %s/%s" % (name, doc))


def cmd_list(ws=None):
    if not os.path.isdir(GOLDEN_DIR):
        print("（无样本）")
        return
    for wname in sorted(os.listdir(GOLDEN_DIR)):
        if ws and wname != ws:
            continue
        d = os.path.join(GOLDEN_DIR, wname)
        if not os.path.isdir(d):
            continue
        docs = sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))
        print("%s: %d 篇" % (wname, len(docs)))
        for doc in docs:
            p = json.load(io.open(os.path.join(d, doc + ".json"), encoding="utf-8"))
            print("  %-14s %s%s" % (doc, p.get("frozen_at", "?"),
                                    " · " + p["note"] if p.get("note") else ""))


def cmd_run(ws=None, workers=4):
    import stages
    root, name, src = _ws_root(ws)
    out_root = os.path.join(root, "out") if root else os.path.join(HERE, "out")
    d = os.path.join(GOLDEN_DIR, name)
    if not os.path.isdir(d):
        raise SystemExit("无样本目录: %s" % d)
    samples = sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))
    if not samples:
        raise SystemExit("无样本")
    print("[golden] %s · %d 篇 × %d 段 · 工作区 %s (%s)" % (name, len(samples), len(IDS),
                                                             name, src))
    jobs = [{"doc": doc, "ids": list(IDS), "pdf": None} for doc in samples]
    t0 = time.time()
    results = stages.run_batch(jobs, workers=workers,
                               log=lambda s: print("  " + s), out_root=out_root)
    wall = time.time() - t0
    n_ok = n_diff = n_fail = 0
    for doc in samples:
        res = results.get(doc) or {}
        if not res.get("ok"):
            n_fail += 1
            print("✗ %-14s 阶段失败: %s" % (doc, str(res.get("error"))[:80]))
            continue
        actual = _load_meta(os.path.join(out_root, doc, "metadata.json")) or {}
        expected = json.load(io.open(os.path.join(d, doc + ".json"), encoding="utf-8"))["metadata"]
        diffs = diff_meta(expected, actual)
        if diffs:
            n_diff += 1
            print("≠ %-14s %d 处差异:" % (doc, len(diffs)))
            for k, b, a in diffs[:6]:
                print("    %-14s 样本: %s" % (k, b))
                print("    %-14s 现值: %s" % ("", a))
        else:
            n_ok += 1
            print("✓ %s" % doc)
    print("\n[golden] 墙钟 %.1fs · ✓%d ≠%d ✗%d —— %s" % (
        wall, n_ok, n_diff, n_fail,
        "全部通过" if not (n_diff or n_fail) else "有差异: 逐条判定是回归还是该 golden add 更新"))
    return 1 if (n_diff or n_fail) else 0


def main(argv):
    ap = argparse.ArgumentParser(description="黄金样本回归集")
    sub = ap.add_subparsers(dest="cmd")
    p_add = sub.add_parser("add")
    p_add.add_argument("ws")
    p_add.add_argument("doc")
    p_add.add_argument("--note", default="")
    p_rm = sub.add_parser("rm")
    p_rm.add_argument("ws")
    p_rm.add_argument("doc")
    p_ls = sub.add_parser("list")
    p_ls.add_argument("ws", nargs="?")
    p_run = sub.add_parser("run")
    p_run.add_argument("--ws", default=None)
    p_run.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    if a.cmd == "add":
        return cmd_add(a.ws, a.doc, a.note) or 0
    if a.cmd == "rm":
        return cmd_rm(a.ws, a.doc) or 0
    if a.cmd == "list":
        return cmd_list(a.ws) or 0
    if a.cmd == "run":
        return cmd_run(a.ws, a.workers)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
