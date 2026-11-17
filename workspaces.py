# -*- coding: utf-8 -*-
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
