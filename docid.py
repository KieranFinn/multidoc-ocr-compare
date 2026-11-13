# -*- coding: utf-8 -*-
"""文件名 -> doc id 的唯一实现。

为什么单独一个模块(2026-11-13 收口)
----------------------------------
这条规则此前在多处各写了一遍（gui.doc_of / archive.norm_doc / pipeline.main /
webapp），共 8 份字面正则。它同时决定三件事：产物目录 `out/<doc>/`、
归档判重的键、webapp 监视 uploads 时"这一份跑过没有"。任何一处写歪，就是同一个 PDF 出现
两个 id——归档判重会漏（archive.norm_doc 的注释正是为此写的），或 out/ 下留两份产物。

模块不 import 项目内任何东西：GUI（不能拖 tkinter）、CLI、webapp、归档共用同一份。
"""
import os
import re

__all__ = ["doc_of"]

_PDF_EXT = re.compile(r"\.pdf$", re.I)
_COPY_SUFFIX = re.compile(r"[\s]*[（(]\d+[)）]$")   # " (1)" / "（1）" 之类的下载复制后缀


def doc_of(name):
    """'13563525（1）.pdf' -> '13563525'；接受文件名或完整路径。"""
    d = _PDF_EXT.sub("", os.path.basename(str(name or "")))
    return _COPY_SUFFIX.sub("", d).strip()
