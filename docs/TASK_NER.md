# 任务：文献实体抽取（基金 / 软件 / 数据集，LLM 先行）

## 你在哪

`D:\Workspace\KCI\pipeline`——韩文期刊三渠道 OCR 管道。13 篇已完成统一层（`out/<doc>/unified/`，按块的正文文本）和元数据层（`out/<doc>/metadata.json`：标题/作者/机构/摘要/参考文献条目等）。旧 REVIEW 文档已删除，代码注释里的 §xx 编号是历史痕迹，不用理。

**先读 `RULES.md`**——唯一有效的规矩，只有 5 条。

> 现行状态（2026-11-09）：旧 NER 产物已按用户要求全部清除。funding/software/datasets 改为“当前论文关系 + evidence_span + uid”抽取，全文覆盖分批，仅 needs_context 候选携带前后块复核；不再以名称出现代替关系证据。NER 开关仍为 false，真实 DS 运行需另行授权。journals/standards/proceedings 保留既有口径。下文保留最初任务背景，现行决定以 RULES.md 与 REVIEW.md 末尾记录为准。

## 目标

从每篇文献抽三类实体（开放词表，LLM 先行，词典后置）：

- **基金 funding**：资助机构 + 课题号
- **研究软件 software**：工具名 + 版本
- **科学数据集 datasets**：数据库/资料集名 + 链接

后续再排：引文句、正文产物、文章自身的期刊/年/卷/期/页、已有实体（标题/作者等）在新期刊上的泛化修复。这三样先行。

## 做法（方向已定，勿再讨论）

1. **LLM 全量**：遍历每篇 `unified/` 的非空正文块，分批送 prompt，固定 JSON schema 输出三类实体；prompt 要求"只从给定文本抽，没有就给 null"。
2. **证据闸门**：每条实体必须附带 `evidence_span`（原文照抄的一句）。程序用 `_foldv` 折叠后核对这句是否字面出现在源块文本里——**在才收，不在就是 AI 瞎编，rejected**。这是唯一硬闸门，不是正则。
3. **词典后置**：用 LLM 全量结果反建词典，做精度比对和人工审校辅助；词典不做 baseline（"先正则"已被否）。

## 产出与纪律

- AI 结果只写 `out_llm_trial/<doc>/ner_llm.json`，**不进 `out/`**；`out/` 只读；unify/m3 不动。
- 新增 `--stage ner`（`metapy/ner.py`），自检 `python -m metapy.ner`（mock 响应，不触网）。
- 可直接复用：`metapy/dual.py` 的 `llm_run_all`（分块/prompt/解析全链）、`llmrep._json_objs`、`_MockResp`、`common._foldv`。
- 配置沿用 `channels.json` 的 llm 段，新增独立开关 `ner_enabled`（默认 false）。

## 钥匙（没有就先写代码 + mock 自检）

`channels.json` llm 段填 **endpoint + model**，环境变量设 **KCI_LLM_KEY**。

## 验收

- mock 自检覆盖：schema 解析、span 闸门命中与拒绝、计数正确；
- 有钥匙后先跑 4 篇样本，报告 span 命中率 + 三类实体各多少条，再全量 14 篇。
