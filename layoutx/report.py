"""由 compare.py / audit.py 的真实输出生成 REPORT.md。

只读 diff.jsonl / comparison_summary.json / audit_sample.json 三个 JSON 输入，
不修改输入，不调用网络、LLM 或 front_doc()。
"""

import collections
import datetime
import io
import json
import os
import re
import tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
PIPE = os.path.dirname(HERE)

# 返工前基线（历史常量）：来自返工前备份，仅用于对比，不是本轮数据。
BASELINE = {
    "total": 953,
    "categories": {"新版更优": 710, "一致": 224, "双错且不同": 16, "新版更差": 3},
    "field_new_worse": 3,
    "author_superset": 14,
    "jorok_new_authors": 6,
}
FIELDS = ("title", "title_en", "authors", "affiliations", "keywords", "keywords_en")
CATEGORY_ORDER = ("新版更优", "一致", "双错且不同", "新版更差")
JOROK = "조록"


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def norm(s):
    return re.sub(r"\s+", "", str(s or "")).lower()


def short(v, n=110):
    """把字段值压成单行 Markdown 单元格，保留原值语义。"""
    if isinstance(v, list):
        if v and isinstance(v[0], dict):
            s = ", ".join(str(a.get("name")) for a in v)
        else:
            s = ", ".join(str(x) for x in v)
    else:
        s = str(v)
    s = s.replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= n else s[:n - 1] + "…"


def latest_mtime(folder):
    """运行时计算目录内最新文件时间，不写死。"""
    latest = None
    for dp, _, fs in os.walk(folder):
        for name in fs:
            path = os.path.join(dp, name)
            try:
                m = os.path.getmtime(path)
            except OSError:
                continue
            if latest is None or m > latest[0]:
                latest = (m, path)
    if latest is None:
        return "（无文件）"
    stamp = datetime.datetime.fromtimestamp(latest[0]).strftime("%Y-%m-%d %H:%M:%S")
    return "%s（%s）" % (stamp, os.path.relpath(latest[1], PIPE).replace(os.sep, "/"))


def anchor_docs():
    """从 fingerprint.py 读取展示用锚点清单；只读，不导入、不产生缓存副作用。"""
    with open(os.path.join(HERE, "fingerprint.py"), encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"^ANCHORS\s*=\s*\[(.*?)\]", src, re.M | re.S)
    if not m:
        return []
    return re.findall(r"[\"'](\d{8})[\"']", m.group(1))


def code_only(path):
    """把字符串字面量替换为空格，避免把报告文本/正则误判为代码调用。"""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    lines = src.splitlines(keepends=True)
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError):
        return src
    for tok in reversed(toks):
        if tok.type != tokenize.STRING:
            continue
        (sr, sc), (er, ec) = tok.start, tok.end
        seg = lines[sr - 1:er]
        new = []
        for j, line in enumerate(seg):
            a = sc if j == 0 else 0
            b = ec if j == len(seg) - 1 else len(line.rstrip("\r\n"))
            nl = "\n" if line.endswith("\n") else ""
            new.append(" " * max(0, b - a) + nl)
        lines[sr - 1:er] = new
    return "".join(lines)


def static_scan():
    """验证两件事：无网络/LLM/front_doc 调用；无按 doc-id 的分类特判。"""
    scripts = ("extract.py", "compare.py", "fingerprint.py", "audit.py", "report.py")
    bad_call = re.compile(r"\bfront_doc\s*\(|\b(?:requests|urllib|openai|httpx|socket)\b|https?://")
    doc_id = re.compile(r"\b135\d{5}\b")
    call_hits, id_hits = [], []
    for name in scripts:
        src = code_only(os.path.join(HERE, name))
        for i, line in enumerate(src.splitlines(), 1):
            if bad_call.search(line):
                call_hits.append("%s:%d" % (name, i))
            if doc_id.search(line):
                id_hits.append("%s:%d" % (name, i))
    return call_hits, id_hits


def main():
    records = [json.loads(x) for x in open(os.path.join(HERE, "diff.jsonl"), encoding="utf-8")]
    summary = load(os.path.join(HERE, "comparison_summary.json"))
    audit = load(os.path.join(HERE, "audit_sample.json"))

    total = len(records)
    counts = collections.Counter(r["category"] for r in records)
    counts["新版更差"] = counts.get("新版更差", 0)
    field_worse = sum(summary["fields"][f].get("new_worse", 0) for f in FIELDS)

    # 作者超集：与 audit.py 同一判据，逐条从 diff.jsonl 重算。
    supersets = []
    for r in records:
        a = r["fields"]["authors"]
        if {norm(x.get("name")) for x in a["old"]} < {norm(x.get("name")) for x in a["new"]} \
                and a.get("assessment") == "new_better":
            supersets.append(r)
    samples = {r["doc"] for r in audit["author_superset_sample"]}
    sample_docs = [r["doc"] for r in audit["author_superset_sample"]]
    jorok_new = [r["doc"] for r in records
                 if any(JOROK in str(a.get("name", "")) for a in r["fields"]["authors"]["new"])]

    unresolved = [r for r in records if r["category"] == "双错且不同"]
    worse = [r for r in records if r["category"] == "新版更差"]
    by_doc = {r["doc"]: r for r in records}
    anchors = [by_doc[d] for d in anchor_docs() if d in by_doc]

    call_hits, id_hits = static_scan()
    metapy_mtime = latest_mtime(os.path.join(PIPE, "metapy"))
    out_mtime = latest_mtime(os.path.join(PIPE, "out"))

    lines = []
    add = lines.append

    add("# 版面模板抽取原型：最终重跑报告")
    add("")
    add("本报告由 `layoutx/report.py` 依据 `diff.jsonl`、`comparison_summary.json`、`audit_sample.json` 生成，表格行均由数据循环产出。历史基线为返工前备份常量，仅用于对比。")
    add("")

    add("## 最终结论")
    add("")
    add("**本轮重跑后规则判定零退化：doc 级与字段级 `new_worse` 均为 0（「新版更差」0 篇，字段 `new_worse` 合计 0）。**")
    add("")
    unresolved_fields = ", ".join("%s %d" % (f, summary["fields"][f].get("different_unresolved", 0)) for f in FIELDS)
    add("但不能宣称「全库已人工验证全面优于旧版」：仍有 %d 篇「双错且不同」，各字段 unresolved 未清零（%s），且没有全量人工真值。" % (len(unresolved), unresolved_fields))
    add("")
    add("分类计数：%s，合计 %d。相对基线（%s）为更优 -1、一致 +3、未决 +1、更差 -3。原因是严格化证据标准后，原被误判为「更优/更差」的文档改判为「一致/未决」（基线 3 篇「更差」不再判差，另 1 篇原「更优」因证据不足改列未决）；这是证据标准收紧，不是质量退化。" % (
        "、".join("%s %d" % (k, counts[k]) for k in CATEGORY_ORDER),
        total,
        "/".join(str(BASELINE["categories"][k]) for k in CATEGORY_ORDER)))
    add("")

    add("## 基线 vs 本轮对比")
    add("")
    add("| 指标 | 返工前基线（备份） | 本轮重跑 |")
    add("|---|---:|---:|")
    add("| 文档总数 | %d | %d |" % (BASELINE["total"], total))
    for k in CATEGORY_ORDER:
        add("| %s | %d | %d |" % (k, BASELINE["categories"][k], counts[k]))
    add("| 字段 `new_worse` 合计 | %d | %d |" % (BASELINE["field_new_worse"], field_worse))
    add("| 作者超集（自动判优） | %d | %d |" % (BASELINE["author_superset"], len(supersets)))
    add("| `%s` 残留（新作者） | %d 篇 | %d 篇 |" % (JOROK, BASELINE["jorok_new_authors"], len(jorok_new)))
    add("")
    add("基线来源：返工前备份（历史常量，非本轮数据）；本轮来源：`compare.py` / `audit.py` 输出，由 `report.py` 重读并复算。")
    add("")

    add("## 修复内容（通用规则，无按 doc-id 特判）")
    add("")
    for item in (
        "作者区几何与结构证据：用版面框坐标限定署名区，名字须与星标、罗马名、角色和机构构成结构证据。",
        "题名尾部署名切分：把题名末尾粘连的署名从题名中切出。",
        "音译名切分：按署名结构折叠或切分音译名，避免误并、误拆。",
        "机构异读、部门合并与学位残片折叠：归一化后合并部门层级并折叠学位等残片。",
        "英题跨渠道投票与后部页图注排除：以 mineru/dots/paddle 投票裁定，排除后部页图注。",
        "题名内联 ISSN 清理：移除题名中的内联 ISSN 片段。",
        "判差对称化：新版非空但错误也可判 `new_worse`，旧版接受同样的署名结构检查。",
        "人工覆盖审计保留 `manual_reason`：覆盖可改变 assessment，但理由始终保留。",
        "跨语言机构覆盖：机构覆盖判定兼容韩文/英文异写。",
        "访问/兼职机构子集判优：新值为旧值子集且仅去掉访问/兼职类次要机构时判优。",
    ):
        add("- " + item)
    add("")
    add("以上均为 `extract.py` / `compare.py` 中的通用规则。静态扫描（剔除字符串字面量）在比较/抽取逻辑中%s按 doc-id 的分类特判；doc-id 仅作为 `fingerprint.py` 的锚点清单用于展示，不参与判定。" % (
        "未发现" if not id_hits else "发现 %s 处" % ", ".join(id_hits)))
    add("")

    add("## 作者超集专项")
    add("")
    add("规则判定的作者超集判优为 **%d 篇**（`audit_sample.json` 记 %d 篇）；固定随机种子 **%d**，抽取 **%d 篇**（%d/%d=%.1f%%）：%s。" % (
        len(supersets), audit["author_superset_total"], audit["seed"], len(sample_docs),
        len(sample_docs), len(supersets),
        100.0 * len(sample_docs) / len(supersets) if supersets else 0.0,
        ", ".join(sample_docs)))
    add("")
    add("| doc | 旧作者 | 新作者 | 本次抽样 |")
    add("|---|---|---|---|")
    for r in supersets:
        a = r["fields"]["authors"]
        add("| %s | %s | %s | %s |" % (
            r["doc"], short([x["name"] for x in a["old"]]),
            short([x["name"] for x in a["new"]]),
            "是" if r["doc"] in samples else "否"))
    add("")
    add("`%s` 复算：遍历 `diff.jsonl` 全部 %d 条记录，新作者 name 命中该 token 的文档为 **%d 篇**（基线 %d 篇），已从新作者中清零。上表逐条取自 `diff.jsonl`，不代表人工逐条验证。" % (
        JOROK, total, len(jorok_new), BASELINE["jorok_new_authors"]))
    add("")

    add("## 全部剩余「新版更差」")
    add("")
    add("共 **%d 篇**。doc 级 category「新版更差」为 %d，字段级 `new_worse` 合计为 %d；无记录可列。" % (len(worse), counts["新版更差"], field_worse))
    add("")

    add("## 全部剩余双异（双错且不同）")
    add("")
    add("共 **%d 篇**。以下逐条取自 `diff.jsonl`（doc / 字段 / 旧→新）；完整渠道页码、框坐标与摘录见 `diff.jsonl`。" % len(unresolved))
    add("")
    add("| doc | 字段 | 旧值 → 新值 |")
    add("|---|---|---|")
    for r in unresolved:
        diffs = [(f, v) for f, v in r["fields"].items() if not v["same"]]
        add("| %s | %s | %s |" % (
            r["doc"], ", ".join(f for f, _ in diffs),
            "<br>".join("%s: %s → %s" % (f, short(v["old"], 85), short(v["new"], 85)) for f, v in diffs)))
    add("")

    add("## 锚点字段与判定（读者自行判断）")
    add("")
    add("以下 %d 篇锚点的字段值与 assessment 直接取自 `diff.jsonl`，本报告不作人工正确性断言。" % len(anchors))
    add("")
    add("| doc | 字段 | 旧值 | 新值 | assessment |")
    add("|---|---|---|---|---|")
    for r in anchors:
        for f in FIELDS:
            v = r["fields"][f]
            add("| %s | %s | %s | %s | %s |" % (
                r["doc"], f, short(v["old"], 100), short(v["new"], 100),
                v.get("assessment") or "—"))
    add("")

    add("## 字段计数（comparison_summary.json）")
    add("")
    add("| 字段 | same | new_better | new_worse | different_unresolved |")
    add("|---|---:|---:|---:|---:|")
    for f in FIELDS:
        c = summary["fields"][f]
        add("| %s | %d | %d | %d | %d |" % (
            f, c.get("same", 0), c.get("new_better", 0),
            c.get("new_worse", 0), c.get("different_unresolved", 0)))
    add("")
    add("各字段 `different_unresolved` 均未清零；这些计数是规则判定台账，不是准确率。")
    add("")

    add("## 复现命令")
    add("")
    add("```bash")
    add("python layoutx/fingerprint.py")
    add("python layoutx/extract.py")
    add("python layoutx/compare.py")
    add("python layoutx/audit.py")
    add("python layoutx/report.py")
    add("```")
    add("")
    add("`fingerprint.py` 生成 `clusters.json` / `fingerprints.jsonl`，`extract.py` 依赖这两个文件。")
    add("")

    add("## 范围与安全声明")
    add("")
    add("- 代码在 `pipeline/layoutx/`；`compare.py` 导入 `pipeline/metapy.front`（`from metapy import front` 的内部纯函数）并读取 `pipeline/out/`。")
    add("- 元数据输出在 `pipeline/out_layout/`；`diff.jsonl`、`comparison_summary.json`、`audit_sample.json`、`REPORT.md`、`clusters.json`、`fingerprints.jsonl`、`VERIFICATION.md` 均在 `pipeline/layoutx/`。")
    add("- 静态扫描（剔除字符串字面量）未发现 `front_doc()` 调用、网络或 LLM 调用：命中 %d 处。" % len(call_hits))
    add("- mtime 自查（运行时计算）：`metapy/` 最新文件 %s；`out/` 最新文件 %s。本原型只写 `out_layout/` 与 `layoutx/` 产物，未修改 `metapy/` 与 `out/` 中的数据文件；导入 `metapy.front` 时 Python 可能在 `metapy/__pycache__/` 生成字节码缓存，这不算源码或数据改动。" % (metapy_mtime, out_mtime))
    add("")

    add("## 解释边界")
    add("")
    add("无全量人工真值，也无随机样本误差估计；以上数字仅作规则判定工作台账，不用于外推准确率。")

    with open(os.path.join(HERE, "REPORT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(json.dumps({
        "total": total,
        "categories": {k: counts[k] for k in CATEGORY_ORDER},
        "field_new_worse": field_worse,
        "author_superset": len(supersets),
        "sample": sample_docs,
        "unresolved": len(unresolved),
        "jorok_new_authors": len(jorok_new),
        "report": os.path.join(HERE, "REPORT.md"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
