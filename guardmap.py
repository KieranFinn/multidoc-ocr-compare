# -*- coding: utf-8 -*-
"""guardmap.py — 守卫影响面查询(2026-11-24): 改一条守卫/正则/函数之前,
先查清楚谁在用它——把"牵一发而动全身"里"要考虑全局"的那一半, 从脑内搜索
变成一条 30 秒的命令。

用法:
  python guardmap.py AY_HEAD        # 查名字: 所有引用点(文件:行 + 上下文)
  python guardmap.py _looks_affil   # 私有函数同查
  python guardmap.py --top [N]      # 引用最多的共享守卫排行(最该小心的前 N 个)
  python guardmap.py --defs AY_HEAD # 同时显示定义处

口径: 静态文本扫描 pipeline 根与 metapy/ 下全部 .py; 注释/字符串里的命中
也列出(守卫名出现在注释里往往就是互指登记, 同样值得看一眼)。
不重 import 被扫描模块——零副作用。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCAN_DIRS = [HERE, os.path.join(HERE, "metapy")]
SKIP_FILES = {"guardmap.py", "golden.py", "test_pipeline.py"}
_DEF_RE_TMPL = r"^(?:%s\s*=|def\s+%s\s*\()"


def _py_files():
    for d in SCAN_DIRS:
        for f in sorted(os.listdir(d)):
            if f.endswith(".py") and f not in SKIP_FILES:
                yield os.path.join(d, f)


def _load():
    files = {}
    for fp in _py_files():
        try:
            with io_open(fp) as fh:
                files[os.path.relpath(fp, HERE).replace("\\", "/")] = fh.read().splitlines()
        except Exception:
            pass
    return files


def io_open(fp):
    import io
    return io.open(fp, encoding="utf-8")


def lookup(name, files):
    pat = re.compile(r"\b%s\b" % re.escape(name))
    hits, defs = [], []
    dpat = re.compile(_DEF_RE_TMPL % (re.escape(name), re.escape(name)))
    for rel, lines in files.items():
        for i, ln in enumerate(lines, 1):
            if dpat.search(ln.strip()):
                defs.append((rel, i, ln.strip()))
            if pat.search(ln):
                hits.append((rel, i, ln.strip()))
    return defs, hits


def top(n, files):
    """共享守卫排行: 模块级大写正则名 + _私有函数名, 按全库引用次数降序。"""
    cand = {}
    name_def = {}
    for rel, lines in files.items():
        for i, ln in enumerate(lines, 1):
            m = re.match(r"^([A-Z][A-Z0-9_]{2,})\s*=\s*re\.compile", ln)
            if m:
                cand.setdefault(m.group(1), 0)
                name_def.setdefault(m.group(1), (rel, i))
            m = re.match(r"^def\s+(_[a-z_0-9]{3,})\s*\(", ln)
            if m:
                cand.setdefault(m.group(1), 0)
                name_def.setdefault(m.group(1), (rel, i))
    blob_all = []
    for rel, lines in files.items():
        blob_all.append("\n".join(lines))
    blob = "\n".join(blob_all)
    for name in cand:
        cand[name] = len(re.findall(r"\b%s\b" % re.escape(name), blob))
    ranked = sorted(cand.items(), key=lambda x: -x[1])[:n]
    out = []
    for name, cnt in ranked:
        rel, i = name_def.get(name, ("?", 0))
        out.append((cnt, name, rel, i))
    return out


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    files = _load()
    if argv[0] == "--top":
        n = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 20
        print("%-6s %-28s %-22s %s" % ("引用", "守卫", "定义于", ""))
        for cnt, name, rel, i in top(n, files):
            print("%-6d %-28s %s:%d" % (cnt, name, rel, i))
        print("\n改这些名字之前, 先跑: python guardmap.py <名> 看全部引用点。")
        return 0
    show_defs = "--defs" in argv
    name = next((a for a in argv if not a.startswith("-")), None)
    if not name:
        print(__doc__)
        return 2
    defs, hits = lookup(name, files)
    if not hits:
        print("未找到 %r: 不是守卫名? 支持模块级正则(AY_HEAD)与私有函数(_looks_affil)。" % name)
        return 1
    print("%s —— 全库 %d 处引用:" % (name, len(hits)))
    cur = None
    for rel, i, ln in hits:
        if rel != cur:
            cur = rel
            print("\n  %s" % rel)
        mark = "定义" if show_defs and (rel, i, ln) in defs else "    "
        print("    %-6s %4d  %s" % (mark, i, ln[:110]))
    if not show_defs and defs:
        print("\n定义处: %s (加 --defs 可标注)" % ", ".join("%s:%d" % d[:2] for d in defs))
    print("\n改动建议: 凡命中其它阶段的守卫, 先确认其 selftest/样本覆盖, 再按轮次闭环准入。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
