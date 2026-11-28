# layoutx REPORT.md 独立复核结果

> 由 `audit_verify.py` 生成（只读输入，可重复运行）。生成时间：2026-11-27 19:13:29

> 判定口径：PASS/FAIL 仅针对「报告文字与当前规则产出的机器可核查数据是否一致」；人工正确性一律 UNVERIFIABLE，规则判定不等于人工真值。

## 总览

| 结论 | 条数 |
|---|---:|
| PASS | 20 |
| FAIL | 0 |
| WARN | 2 |
| UNVERIFIABLE | 1 |
| **合计** | **23** |

### 基线关键 WARN 项

- **R18 WARN**：manual_review.json 是否覆盖 8 个锚点
- **R23 WARN**：未做全量人工验证 / 仍有未决双异的边界声明

## 逐条声明

### R01 PASS — diff.jsonl 总数/分类计数与汇总、报告表格/正文一致

- diff.jsonl 实际行数=953；comparison_summary.json total=953
- 新版更优：diff=709 汇总=709 报告=709(正文「分类计数」，REPORT.md:11) → True
- 一致：diff=227 汇总=227 报告=227(正文「分类计数」，REPORT.md:11) → True
- 双错且不同：diff=17 汇总=17 报告=17(正文「分类计数」，REPORT.md:11) → True
- 新版更差：diff=0 汇总=0 报告=0(正文「分类计数」，REPORT.md:11) → True
- 报告正文合计=953（REPORT.md:11），与 diff 行数 953 一致
- 注：基线对比表的「返工前基线」列为历史常量，非本轮数据，本机无法从当前产物复核。

### R02 PASS — 六字段 same/new_better/new_worse/different_unresolved 计数与汇总一致且每项和为总数

- title：四项和=953(=总数953) diff={'same': 787, 'new_better': 152, 'different_unresolved': 14} 汇总={'same': 787, 'new_better': 152, 'new_worse': 0, 'different_unresolved': 14} 报告(REPORT.md:147)={'same': 787, 'new_better': 152, 'new_worse': 0, 'different_unresolved': 14} → 一致
- title_en：四项和=953(=总数953) diff={'new_better': 393, 'same': 540, 'different_unresolved': 20} 汇总={'same': 540, 'new_better': 393, 'new_worse': 0, 'different_unresolved': 20} 报告(REPORT.md:148)={'same': 540, 'new_better': 393, 'new_worse': 0, 'different_unresolved': 20} → 一致
- authors：四项和=953(=总数953) diff={'same': 868, 'new_better': 58, 'different_unresolved': 27} 汇总={'same': 868, 'new_better': 58, 'new_worse': 0, 'different_unresolved': 27} 报告(REPORT.md:149)={'same': 868, 'new_better': 58, 'new_worse': 0, 'different_unresolved': 27} → 一致
- affiliations：四项和=953(=总数953) diff={'same': 588, 'new_better': 312, 'different_unresolved': 53} 汇总={'same': 588, 'new_better': 312, 'new_worse': 0, 'different_unresolved': 53} 报告(REPORT.md:150)={'same': 588, 'new_better': 312, 'new_worse': 0, 'different_unresolved': 53} → 一致
- keywords：四项和=953(=总数953) diff={'same': 900, 'different_unresolved': 35, 'new_better': 18} 汇总={'same': 900, 'new_better': 18, 'new_worse': 0, 'different_unresolved': 35} 报告(REPORT.md:151)={'same': 900, 'new_better': 18, 'new_worse': 0, 'different_unresolved': 35} → 一致
- keywords_en：四项和=953(=总数953) diff={'same': 750, 'new_better': 187, 'different_unresolved': 16} 汇总={'same': 750, 'new_better': 187, 'new_worse': 0, 'different_unresolved': 16} 报告(REPORT.md:152)={'same': 750, 'new_better': 187, 'new_worse': 0, 'different_unresolved': 16} → 一致

### R03 PASS — 作者超集判优 8 篇、audit_sample.json 的 seed/total/sample 与抽样超 10%

- audit.py 逻辑重算：superset=8 篇（old_names < new_names 且 assessment==new_better）
- audit_sample.json seed=20261127（要求 20261127）→ True
- audit_sample.json total=8（重算=8）→ True
- sample 实际=['13564501']，random.Random(20261127).sample(k=1) 复算=['13564501'] → True
- 抽样比例=1/8=12.5% > 10% → True
- 报告正文(REPORT.md:45)声明 seed=20261127、抽取 1 篇、样本=['13564501']，与 audit_sample 一致 → seed:True count:True docs:True
- 报告超集表 doc 列表(8)与重算一致 → True

### R04 PASS — 全部剩余「新版更差」doc 列表与 diff.jsonl 一致

- diff.jsonl category=='新版更差' 共 0 篇：[]
- REPORT.md「全部剩余「新版更差」」节声明共 0 篇，表格逐条 0 篇：[]
- 逐条顺序与集合一致 → True

### R05 PASS — 全部剩余双异 doc 列表与 diff.jsonl 一致

- diff.jsonl category=='双错且不同' 共 17 篇：['13563533', '13563546', '13563549', '13563590', '13563600', '13567051', '13567061', '13567094', '13567111', '13567139', '13567236', '13567244', '13567332', '13568678', '13569382', '13569399', '13569404']
- REPORT.md「全部剩余双异」节声明共 17 篇，表格逐条 17 篇：['13563533', '13563546', '13563549', '13563590', '13563600', '13567051', '13567061', '13567094', '13567111', '13567139', '13567236', '13567244', '13567332', '13568678', '13569382', '13569399', '13569404']
- 逐条顺序与集合一致 → True

### R06 PASS — token 清理声明：7 个 token 是否仍出现在新作者中

- token 본체론：新作者命中 0 篇 
- token 부산：新作者命中 0 篇 
- token 경남：新作者命中 0 篇 
- token 알고리즘：新作者命中 0 篇 
- token 유형분류：新作者命中 0 篇 
- token 평가하였고：新作者命中 0 篇 
- token 조록：新作者命中 0 篇 
- REPORT.md:58 声明「조록 复算…0 篇，已从新作者中清零」→ True
- 结论：7 个 token 在新作者中命中数全部为 0 → True（仅为规则数据核对，非人工真值）

### R07 UNVERIFIABLE — 旧声明「13570535 清掉 조록 但保留 배성중」

- 新 REPORT.md 中不再存在「13570535 清掉 조록」声明，原声明已撤回 → 无声明可核。
- 当前数据供参照：diff.jsonl 13570535 新作者=['배성중']（含 조록=False）
- 当前报告的 조록 清零声明见 R06（REPORT.md 作者超集专项）。

### A1 PASS — 锚点 13569913：报告字段表与 diff.jsonl 一致性（数据一致性判定：**TRUE**；人工真值 UNVERIFIABLE）

- 报告锚点表取自 diff.jsonl，且明示「不作人工正确性断言」→ 人工真值 UNVERIFIABLE。
- 报告表覆盖全部 6 个字段 → 成立
- 字段 title 的 assessment 与 diff.jsonl 一致(REPORT.md:94) → 成立
- 字段 title 的旧值前缀一致 → 成立
- 字段 title 的新值前缀一致 → 成立
- 其余 15 项字段级核对：15 成立 / 0 不成立
- 关键证据摘录(≤200字)：{"title": "new_better", "title_en": "new_better", "authors": "new_better", "affiliations": "different_unresolved", "keywords": "—", "keywords_en": "—"}
- manual_reason：锚点字段中不存在；manual_review.json 未覆盖任何锚点（见 R18）。

### A2 PASS — 锚点 13567494：报告字段表与 diff.jsonl 一致性（数据一致性判定：**TRUE**；人工真值 UNVERIFIABLE）

- 报告锚点表取自 diff.jsonl，且明示「不作人工正确性断言」→ 人工真值 UNVERIFIABLE。
- 报告表覆盖全部 6 个字段 → 成立
- 字段 title 的 assessment 与 diff.jsonl 一致(REPORT.md:100) → 成立
- 字段 title 的旧值前缀一致 → 成立
- 字段 title 的新值前缀一致 → 成立
- 其余 15 项字段级核对：15 成立 / 0 不成立
- 关键证据摘录(≤200字)：{"title": "—", "title_en": "new_better", "authors": "different_unresolved", "affiliations": "new_better", "keywords": "—", "keywords_en": "—"}
- manual_reason：锚点字段中不存在；manual_review.json 未覆盖任何锚点（见 R18）。

### A3 PASS — 锚点 13568651：报告字段表与 diff.jsonl 一致性（数据一致性判定：**TRUE**；人工真值 UNVERIFIABLE）

- 报告锚点表取自 diff.jsonl，且明示「不作人工正确性断言」→ 人工真值 UNVERIFIABLE。
- 报告表覆盖全部 6 个字段 → 成立
- 字段 title 的 assessment 与 diff.jsonl 一致(REPORT.md:106) → 成立
- 字段 title 的旧值前缀一致 → 成立
- 字段 title 的新值前缀一致 → 成立
- 其余 15 项字段级核对：15 成立 / 0 不成立
- 关键证据摘录(≤200字)：{"title": "—", "title_en": "new_better", "authors": "different_unresolved", "affiliations": "—", "keywords": "different_unresolved", "keywords_en": "—"}
- manual_reason：锚点字段中不存在；manual_review.json 未覆盖任何锚点（见 R18）。

### A4 PASS — 锚点 13569417：报告字段表与 diff.jsonl 一致性（数据一致性判定：**TRUE**；人工真值 UNVERIFIABLE）

- 报告锚点表取自 diff.jsonl，且明示「不作人工正确性断言」→ 人工真值 UNVERIFIABLE。
- 报告表覆盖全部 6 个字段 → 成立
- 字段 title 的 assessment 与 diff.jsonl 一致(REPORT.md:112) → 成立
- 字段 title 的旧值前缀一致 → 成立
- 字段 title 的新值前缀一致 → 成立
- 其余 15 项字段级核对：15 成立 / 0 不成立
- 关键证据摘录(≤200字)：{"title": "new_better", "title_en": "—", "authors": "new_better", "affiliations": "new_better", "keywords": "—", "keywords_en": "—"}
- manual_reason：锚点字段中不存在；manual_review.json 未覆盖任何锚点（见 R18）。

### A5 PASS — 锚点 13569428：报告字段表与 diff.jsonl 一致性（数据一致性判定：**TRUE**；人工真值 UNVERIFIABLE）

- 报告锚点表取自 diff.jsonl，且明示「不作人工正确性断言」→ 人工真值 UNVERIFIABLE。
- 报告表覆盖全部 6 个字段 → 成立
- 字段 title 的 assessment 与 diff.jsonl 一致(REPORT.md:118) → 成立
- 字段 title 的旧值前缀一致 → 成立
- 字段 title 的新值前缀一致 → 成立
- 其余 15 项字段级核对：15 成立 / 0 不成立
- 关键证据摘录(≤200字)：{"title": "—", "title_en": "—", "authors": "new_better", "affiliations": "new_better", "keywords": "—", "keywords_en": "—"}
- manual_reason：锚点字段中不存在；manual_review.json 未覆盖任何锚点（见 R18）。

### A6 PASS — 锚点 13564500：报告字段表与 diff.jsonl 一致性（数据一致性判定：**TRUE**；人工真值 UNVERIFIABLE）

- 报告锚点表取自 diff.jsonl，且明示「不作人工正确性断言」→ 人工真值 UNVERIFIABLE。
- 报告表覆盖全部 6 个字段 → 成立
- 字段 title 的 assessment 与 diff.jsonl 一致(REPORT.md:124) → 成立
- 字段 title 的旧值前缀一致 → 成立
- 字段 title 的新值前缀一致 → 成立
- 其余 15 项字段级核对：15 成立 / 0 不成立
- 关键证据摘录(≤200字)：{"title": "—", "title_en": "—", "authors": "—", "affiliations": "new_better", "keywords": "—", "keywords_en": "—"}
- manual_reason：锚点字段中不存在；manual_review.json 未覆盖任何锚点（见 R18）。

### A7 PASS — 锚点 13564501：报告字段表与 diff.jsonl 一致性（数据一致性判定：**TRUE**；人工真值 UNVERIFIABLE）

- 报告锚点表取自 diff.jsonl，且明示「不作人工正确性断言」→ 人工真值 UNVERIFIABLE。
- 报告表覆盖全部 6 个字段 → 成立
- 字段 title 的 assessment 与 diff.jsonl 一致(REPORT.md:130) → 成立
- 字段 title 的旧值前缀一致 → 成立
- 字段 title 的新值前缀一致 → 成立
- 其余 15 项字段级核对：15 成立 / 0 不成立
- 关键证据摘录(≤200字)：{"title": "new_better", "title_en": "—", "authors": "new_better", "affiliations": "new_better", "keywords": "—", "keywords_en": "—"}
- manual_reason：锚点字段中不存在；manual_review.json 未覆盖任何锚点（见 R18）。

### A8 PASS — 锚点 13569720：报告字段表与 diff.jsonl 一致性（数据一致性判定：**TRUE**；人工真值 UNVERIFIABLE）

- 报告锚点表取自 diff.jsonl，且明示「不作人工正确性断言」→ 人工真值 UNVERIFIABLE。
- 报告表覆盖全部 6 个字段 → 成立
- 字段 title 的 assessment 与 diff.jsonl 一致(REPORT.md:136) → 成立
- 字段 title 的旧值前缀一致 → 成立
- 字段 title 的新值前缀一致 → 成立
- 其余 15 项字段级核对：15 成立 / 0 不成立
- 关键证据摘录(≤200字)：{"title": "new_better", "title_en": "new_better", "authors": "—", "affiliations": "different_unresolved", "keywords": "—", "keywords_en": "—"}
- manual_reason：锚点字段中不存在；manual_review.json 未覆盖任何锚点（见 R18）。

### R16 PASS — 代码声明：未调用 front_doc()、未使用 LLM/网络

- 扫描 6 个 layoutx/*.py（字符串字面量已剔除）：audit.py, compare.py, extract.py, fingerprint.py, report.py, test_layoutx.py
- 命中网络/LLM/front_doc 调用：无
- REPORT.md:172 声明静态扫描命中 0 处 → True
- 注：`from metapy import front` 导入的是内部纯函数，非 front_doc() 调用。

### R17 PASS — 代码声明：比较/抽取逻辑中无按 doc-id 的分类覆盖

- REPORT.md:41 声明比较/抽取逻辑无按 doc-id 的分类特判
- 去字符串后 compare.py/extract.py 中的 doc-id 代码：无
- 去字符串后全部 layoutx/*.py 的 doc-id 代码：无
- 原始源码出现（含字符串/注释）：[('fingerprint.py', 11, 'ANCHORS = ["13569913", "13567494", "13568651", "13569417", "13569428",'), ('test_layoutx.py', 213, 'authors = self._new_values("13569417", "authors") or []')]
- doc-id 仅见 fingerprint.py 的 ANCHORS 展示清单，不参与判定 → True

### R18 WARN — manual_review.json 是否覆盖 8 个锚点

- manual_review.json 共 32 条；8 个锚点被覆盖：无
- 未覆盖：['13569913', '13567494', '13568651', '13569417', '13569428', '13564500', '13564501', '13569720']
- REPORT.md:90 明示「不作人工正确性断言」，故本报告无人工真值可供核对
- 结论：仍属未做全量人工验证，记 WARN（不把规则判定当人工真值）

### R19 PASS — 范围声明：代码在 layoutx、元数据在 out_layout、其余产物在 layoutx

- 代码位置：audit.py, compare.py, extract.py, fingerprint.py, report.py, test_layoutx.py 全部位于 pipeline/layoutx/
- 写入 audit.py:28 open(os.path.join(HERE, "audit_sample.json"),'w') → layoutx
- 写入 compare.py:532 open(os.path.join(HERE, "diff.jsonl"),'w') → layoutx
- 写入 compare.py:541 open(os.path.join(HERE, "comparison_summary.json"),'w') → layoutx
- 写入 extract.py:1228 open(os.path.join(folder, "metadata.json"),'w') → out_layout
- 写入 fingerprint.py:159 open(args.out,'w') → layoutx
- 写入 fingerprint.py:170 open(os.path.join(HERE, "clusters.json"),'w') → layoutx
- 写入 report.py:298 open(os.path.join(HERE, "REPORT.md"),'w') → layoutx
- REPORT.md:171 声明：元数据输出在 out_layout，diff/summary/audit/REPORT/clusters/fingerprints/VERIFICATION 在 layoutx
- 结论：所有写目标均落在 out_layout 或 layoutx，无改写 metapy/ 与 out/ → True
- 注：audit_verify.py 仅在 --md 时写 VERIFICATION.md（复核脚本自身，不计入流水线输出）。

### R20 PASS — mtime 声明：与报告给出的 metapy/out 最新文件时间一致

- REPORT.md:173 - mtime 自查（运行时计算）：`metapy/` 最新文件 2026-11-27 17:38:19（metapy/__pycache__/issue.cpython-315.pyc）；`out/` 最新文件 2026-11-27 09:31:23（out/13569913/metadata_audit.json）。本原型只写 `out_layout/` 与 `layoutx/` 产物，未修改 `metapy/` 与 `out/` 中的数据文件；导入 `metapy.front` 时 Python 可能在 `metapy/__pycache__/` 生成字节码缓存，这不算源码或数据改动。
- 报告声明 metapy=2026-11-27 17:38:19；实测=2026-11-27 17:38:19 (D:\Workspace\KCI\pipeline\metapy\__pycache__\issue.cpython-315.pyc，34 文件) → True
- 报告声明 out=2026-11-27 09:31:23；实测=2026-11-27 09:31:23 (D:\Workspace\KCI\pipeline\out\13569913\metadata_audit.json，217851 文件) → True
- out_layout/ 实测最新=2026-11-27 19:02:36 (D:\Workspace\KCI\pipeline\out_layout\13625974\metadata.json，953 文件，仅供参照)

### R21 PASS — 复现命令顺序是否合理（fingerprint.py 在 extract.py 之前）

- REPORT.md:158 复现命令=['layoutx/fingerprint.py', 'layoutx/extract.py', 'layoutx/compare.py', 'layoutx/audit.py', 'layoutx/report.py']
- fingerprint.py 在复现命令中：True，且位于 extract.py 之前：True
- extract.py:1218 读取旧产物：clusters = load(os.path.join(HERE, "clusters.json"))["cluster_list"]
- extract.py:1219 读取旧产物：fps = [json.loads(x) for x in open(os.path.join(HERE, "fingerprints.jsonl"), encoding="utf
- fingerprint.py:148 产生该产物：ap.add_argument("--out", default=os.path.join(HERE, "fingerprints.jsonl"))
- fingerprint.py:170 产生该产物：with open(os.path.join(HERE, "clusters.json"), "w", encoding="utf-8") as f:
- 静态判断：fingerprint.py 已列入且顺序正确 → True

### R22 PASS — 「本轮规则判定零退化」声明

- REPORT.md:7 声称「规则判定零退化：doc 级与字段级 new_worse 均为 0」
- diff.jsonl doc 级「新版更差」=0
- diff.jsonl 字段级 assessment==new_worse 的实例=0
- comparison_summary.json 各字段 new_worse 合计=0（缺键按 0）
- REPORT.md 字段计数表 new_worse 合计=0
- 结论：PASS 仅表示「按当前规则与数据，零退化成立」；不代表全库人工真值或准确率（见 R23）。

### R23 WARN — 未做全量人工验证 / 仍有未决双异的边界声明

- REPORT.md:9 明示不能宣称「全库已人工验证全面优于旧版」
- REPORT.md:177 明示「无全量人工真值，也无随机样本误差估计」
- 仍未决：双错且不同 17 篇；字段 different_unresolved={'affiliations': 53, 'keywords': 35, 'title_en': 20, 'authors': 27, 'keywords_en': 16, 'title': 14}
- 结论：报告如实披露人工验证缺口 → WARN；这是局限提示，不是证伪，也不是人工真值。

