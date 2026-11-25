# -*- coding: utf-8 -*-
import io
import json
import os
import re


class Workspace(object):
    def __init__(self, name, root, uploads, out, ner_root, history, notes):
        self.name = name
        self.root = root
        self.uploads = uploads
        self.out = out
        self.ner_root = ner_root
        self.history = history
        self.notes = notes

    @property
    def archive(self):
        return os.path.join(self.root, "archive")

    @property
    def is_main(self):
        return self.name == "main"

    def ensure(self):
        for path in (self.uploads, self.out, self.ner_root):
            os.makedirs(path, exist_ok=True)
        return self


def get_workspace(root, name="main"):
    if name == "main":
        return Workspace("main", root,
                         os.path.join(root, "uploads"), os.path.join(root, "out"),
                         os.path.join(root, "out_llm_trial"),
                         os.path.join(root, "gui_runs.jsonl"),
                         os.path.join(root, "gui_notes.json"))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}", name or ""):
        raise ValueError("工作区名称须为 1-40 位字母、数字、下划线或短横线")
    base = os.path.join(root, "workspaces", name)
    return Workspace(name, base,
                     os.path.join(base, "uploads"), os.path.join(base, "out"),
                     os.path.join(base, "out_llm_trial"),
                     os.path.join(base, "gui_runs.jsonl"),
                     os.path.join(base, "gui_notes.json"))


def custom_workspace(path):
    path = os.path.abspath(path)
    name = os.path.basename(path.rstrip(os.sep)) or "workspace"
    return Workspace(name, path, os.path.join(path, "uploads"), os.path.join(path, "out"),
                     os.path.join(path, "out_llm_trial"), os.path.join(path, "gui_runs.jsonl"),
                     os.path.join(path, "gui_notes.json"))


def list_workspaces(root, custom=None):
    names = ["main"]
    base = os.path.join(root, "workspaces")
    if os.path.isdir(base):
        names.extend(sorted(d for d in os.listdir(base)
                            if os.path.isdir(os.path.join(base, d)) and d != "main"))
    names.extend((custom or {}).keys())
    return list(dict.fromkeys(names))


def resolve_ws_root(explicit=None, here=None, prefs_fp=None):
    """工作区根解析(2026-11-24 起 stages/failures/meta_diff 三 CLI 共用口径):
      --ws 显式值 > 环境变量 KCI_WS_ROOT > GUI 当前工作区(.gui_prefs.json) > 主库(空串)。
    返回 (root, name, source): root 为绝对路径(空串=主库); source 是决策出处,
    CLI 开跑前打印一行 —— "以为在跑 KCI11_17 其实在跑主库"这类口径错已实锤过
    (2026-11-19 ner_root 不跟工作区走, 台账虚增 112 vs 93)。
    显式值可以是: "main" / .gui_prefs 里的自定义名 / 受管名(workspaces/<名>) / 磁盘路径。
    """
    here = here or os.path.dirname(os.path.abspath(__file__))

    def _read_prefs():
        fp = prefs_fp or os.path.join(here, ".gui_prefs.json")
        try:
            with io.open(fp, encoding="utf-8") as fh:
                p = json.load(fh)
            return p.get("workspace_paths") or {}, p.get("workspace") or "main"
        except Exception:
            return {}, "main"

    def _from_val(val, source):
        val = (val or "").strip()
        if not val or val == "main":
            return "", "main", source
        custom, _name = _read_prefs()
        if val in custom:
            return os.path.abspath(custom[val]), val, source + "(命名)"
        if os.path.isdir(val):
            return os.path.abspath(val), os.path.basename(val.rstrip("/\\")) or "workspace", source + "(路径)"
        try:
            ws = get_workspace(here, val)   # 受管名; 非法名字在此抛 ValueError
        except ValueError:
            raise ValueError("不认识的工作区 %r: 既不是已知工作区名, 也不是磁盘路径" % val)
        if not os.path.isdir(ws.root):
            raise ValueError("受管工作区 %r 在 %s 下尚无目录(先在 GUI 新建, 或直接给路径)"
                             % (val, ws.root))
        return ws.root, ws.name, source + "(受管)"

    if explicit:
        return _from_val(explicit, "--ws")
    env = os.environ.get("KCI_WS_ROOT")
    if env:
        return _from_val(env, "环境变量 KCI_WS_ROOT")
    custom, current = _read_prefs()
    if current in custom:
        return os.path.abspath(custom[current]), current, ".gui_prefs.json 当前工作区"
    if current and current != "main":
        ws = get_workspace(here, current)
        if not os.path.isdir(ws.root):
            raise ValueError(".gui_prefs.json 当前工作区 %r 在 %s 下已无目录, "
                             "先修正 GUI 工作区或显式 --ws" % (current, ws.root))
        return ws.root, ws.name, ".gui_prefs.json 当前工作区(受管)"
    return "", "main", "默认主库"
