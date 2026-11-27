import collections
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PIPE = os.path.dirname(HERE)


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def norm(s):
    return "".join(str(s or "").split()).lower()


def short(v, n=130):
    if isinstance(v, list):
        if v and isinstance(v[0], dict):
            s = ", ".join(str(a.get("name")) for a in v)
        else:
            s = ", ".join(str(x) for x in v)
    else:
        s = str(v)
    s = s.replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= n else s[:n - 1] + "…"


def main():
    records = [json.loads(x) for x in open(os.path.join(HERE, "diff.jsonl"), encoding="utf-8")]
    summary = load(os.path.join(HERE, "comparison_summary.json"))
    audit = load(os.path.join(HERE, "audit_sample.json"))
    counts = collections.Counter(r["category"] for r in records)
    total = len(records)
    lines = []
    add = lines.append
    add("# 版面模板抽取原型：返工后总结报告")
    add("")
    add("## 最终结论")
    add("")
    add("**版面模板方向成立，但不能宣称“全库 0 退化”或“新版已全面优于旧版”。** 网格署名、渠道仲裁和绕过 unify 过并的价值已由锚点真实证明；作者超集污染和对比器偏盲也真实存在，已据此返工。此前报告中的“0 退化”头条数字正式撤回。")
    add("")
    add("主库 953 个纯数字 doc-id 全部重跑，排除 9 个归档目录。以下计数是规则与人工复核后的工作台账，不是准确率估计：")
    add("")
    add("| 分类 | 篇数 |")
    add("|---|---:|")
    for k in ("新版更优", "一致", "双错且不同", "新版更差"):
        add("| %s | %d |" % (k, counts[k]))
    add("")
    add("## 本次返工修正了什么")
    add("")
    add("- 删除按渠道作者数量取最多结果的选择逻辑，改为正面署名结构、来源行覆盖率、跨渠道票数与同人异写折叠。")
    add("- 作者证据限制到首页署名区；名字相邻的星标、罗马名、角色和机构配对才算结构证据，长正文/摘要行不再凭任意子串支持作者。")
    add("- 判差对称化：新版非空但错误可以判差，不再要求新版为空；旧版也接受同样的署名结构检查。")
    add("- 删除 `13569913`、`13569417` 的硬编码分类覆盖。锚点结果由实际字段和渠道证据计算。")
    add("- 新增题名/关键词 token 与来源行验证、英文题名参考文献/日期/图注排除、机构角色和双语折行清理。")
    add("")
    add("## 作者超集判优专项")
    add("")
    add("初始问题样本中，题名形容词、章节头、统计变量和地名确实被新增为作者。返工后，当前自动超集判优为 **%d 篇**，比返工前 52 篇继续下降，但仍不是全部人工验证。" % audit["author_superset_total"])
    add("")
    add("固定随机种子 20261127，抽取 %d 篇（超过 10%%）：%s。逐条渠道复核均为真补名/纠错：13564501 补 조국，13570535 清掉 조록 并保留 배성중。早期审计发现的 본체론、부산/경남、알고리즘、유형분류、평가하였고 等垃圾已清除。样本不用于外推准确率。" % (len(audit["author_superset_sample"]), ", ".join(r["doc"] for r in audit["author_superset_sample"])))
    add("")
    add("| doc | 旧作者 | 新作者 | 本次抽样 |")
    add("|---|---|---|---|")
    samples = {r["doc"] for r in audit["author_superset_sample"]}
    for r in records:
        old = r["fields"]["authors"]["old"]
        new = r["fields"]["authors"]["new"]
        if {norm(a.get("name")) for a in old} < {norm(a.get("name")) for a in new} and r["fields"]["authors"].get("assessment") == "new_better":
            add("| %s | %s | %s | %s |" % (r["doc"], short([a["name"] for a in old]), short([a["name"] for a in new]), "是" if r["doc"] in samples else "否"))
    add("")
    add("## 全部剩余“新版更差”")
    add("")
    worse = [r for r in records if r["category"] == "新版更差"]
    add("共 **%d** 篇；这些不能由其他字段的改善抵消。" % len(worse))
    add("")
    add("| doc | 字段与差异 | 初判 |")
    add("|---|---|---|")
    for r in worse:
        changes = ["%s: %s → %s" % (f, short(v["old"]), short(v["new"])) for f, v in r["fields"].items() if not v["same"]]
        reason = "需人工复核"
        if r["doc"] == "13564429":
            reason = "真实退化：题名粘 조일형/유진호，作者漏空；需按带星标署名尾段切分。"
        elif r["doc"] == "13569396":
            reason = "五名真作者恢复并删除机构假作者，但 김해겸/김해견 仍有 OCR 异读，机构粒度也需复核。"
        elif r["doc"] == "13569428":
            reason = "作者 5 人实际正确；判差主要来自机构识别规则对 LIG넥스원 的支持不足，不应误读为作者退化。"
        add("| %s | %s | %s |" % (r["doc"], "<br>".join(changes), reason))
    add("")
    add("## 全部剩余双异")
    add("")
    unresolved = [r for r in records if r["category"] == "双错且不同"]
    add("共 **%d** 篇。每篇的完整渠道页码、框坐标和摘录均在 `diff.jsonl`。" % len(unresolved))
    add("")
    add("| doc | 字段 | 旧值 → 新值 |")
    add("|---|---|---|")
    for r in unresolved:
        changes = ["%s: %s → %s" % (f, short(v["old"], 85), short(v["new"], 85)) for f, v in r["fields"].items() if not v["same"]]
        add("| %s | %s | %s |" % (r["doc"], ", ".join(f for f, v in r["fields"].items() if not v["same"]), "<br>".join(changes)))
    add("")
    add("## 已验证的锚点与机构粒度")
    add("")
    add("- 13569913：题名、백서인/당정、双机构均正确。")
    add("- 13567494：리우커 + 대구가톨릭대학교 正确拆分。")
    add("- 13568651：音译名保持一人，不再切出假机构。")
    add("- 13569417：四格作者及罗马名恢复。")
    add("- 13569428：五格作者恢复，`Radar Production` 消除；机构当前为 경북대학교、전기공학과、LIG넥스원 레이다기술팀及对应英文，已去除早期角色/折行碎片。")
    add("- 13564500/13564501：原 rescue 篇作者保留或补全。")
    add("- 13569720：题名与两位作者正确，机构残片不再冒充作者。")
    add("")
    add("## 字段计数与解释边界")
    add("")
    add("| 字段 | 一致 | 更优 | 更差 | 双异/未裁定 |")
    add("|---|---:|---:|---:|---:|")
    for f, c in summary["fields"].items():
        add("| %s | %d | %d | %d | %d |" % (f, c.get("same", 0), c.get("new_better", 0), c.get("new_worse", 0), c.get("different_unresolved", 0)))
    add("")
    add("这些数字不能当成准确度或置信区间：目前没有全量人工真值，也没有随机样本的规模误差估计。下一论应优先全量复核剩余 14 个作者超集判优及上述 19 篇非一致记录，并抽样审计其他自动清洗类别。")
    add("")
    add("## 复现与安全")
    add("")
    add("```bash\npython layoutx/extract.py\npython layoutx/compare.py\npython layoutx/audit.py\npython layoutx/report.py\n```")
    add("")
    add("代码仅在 `pipeline/layoutx/`，输出仅在 `pipeline/out_layout/`。未调用 `front_doc()`，未使用 LLM/网络。最终 mtime 自查：旧 `metapy/` 最新文件为 2026-11-26，旧 `out/` 最新文件为 2026-11-27 09:31，均未被本原型刷新。")
    with open(os.path.join(HERE, "REPORT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(json.dumps({"total": total, "categories": dict(counts), "report": os.path.join(HERE, "REPORT.md")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
