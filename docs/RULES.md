# 现行约定（唯一有效版本，2026-11-04 起；2026-11-10 修订）

旧规矩全部作废，散落各处的不算数，只认这一份。
改动记 REVIEW.md（规矩第 5 条），改前看一眼文件末尾。

## 还在生效的

1. **unify 和 m3 不动。** 要动另立项。
2. **AI 抽的东西本体一律放 `out_llm_trial/` 文件夹**，不进 `out/`。唯一例外见第 6 条。
3. **`out/` 里已有的文件，AI 阶段只读不写。** 唯一例外见第 6 条。
4. 改完代码跑一下对应的自检（`python -m metapy.refs` 等）；大改动再跑一遍 14 篇。
5. 决定和改动从头记在新 REVIEW.md（旧档已删, 新文件新开, 写之前看一眼文件末尾）。
6. **（2026-11-10 增）ner_lite 实体的渲染层可进 out/**：viewer.py 读取 `out_llm_trial/fds_extract/ner_lite/<doc>/ner_lite.json`，把实体行画进 `out/<doc>/viewer_compare.html`（文件不存在则跳过，viewer 行为不变）。ner_lite.json 本体与审计仍只在 out_llm_trial。ner_lite 是实体抽取唯一入口（根目录 `ner_lite.py`，LLM 提名+程序定位）；旧 `--stage ner/ner_audit`、metapy/ner.py、批注词典已删。
7. **（2026-11-10 增）2026-11-10 验证动作**（fds_scan/C′ 靶区过读/benchmark/ner_lite 全量）的产物留 `out_llm_trial/fds_extract/`，不进 out/；该动作本身不写 REVIEW.md。

## 钥匙

LLM 真跑需要：`channels.json` llm 段的 endpoint + model（现 deepseek-flash），环境变量 `KCI_LLM_KEY`（不落盘）。
本机 TLS 需 Windows 证书库（ner_lite.py 内 `_ssl_ctx()` 已处理）。

## 已作废（随 §60 封存，别再引用）

- "AI 结果只落 audit / suggestion / applied 恒 0" 那一套——当时是 AI 打辅助时定的，现在 AI 直接干活，结果放新文件夹就行。
- "回灌 out/"" verdict 怎么合并"" 双渠道对比页怎么选" 等流程性约定——要用到时重新单说。
- （2026-11-10）旧 NER 六类体系（uid 锚定/relation 受控值/复核二刷/五桶统计/webapp 批注词典）——被 ner_lite 取代，代码与 TASK_NER.md 已删。
