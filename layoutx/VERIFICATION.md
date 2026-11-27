# layoutx REPORT.md 独立复核结果

> 由 `audit_verify.py` 生成（只读输入，可重复运行）。生成时间：2026-11-27 16:56:34

## 总览

| 结论 | 条数 |
|---|---:|
| PASS | 12 |
| FAIL | 4 |
| WARN | 5 |
| UNVERIFIABLE | 0 |
| **合计** | **21** |

### 基线关键 FAIL 项

- **R06 FAIL**：token 清理声明：7 个 token 是否仍出现在新作者中
- **R07 FAIL**：报告第 28 行“13570535 清掉 조록 但保留 배성중”真伪
- **R19 FAIL**：代码仅在 layoutx、输出仅在 out_layout
- **R21 FAIL**：复现命令顺序是否合理（fingerprint.py 缺失）

## 逐条声明

### R01 PASS — diff.jsonl 总数/分类计数与汇总、报告表格一致

- diff.jsonl 实际行数=953；comparison_summary.json:2 total=953
- 新版更优：diff=710 汇总=710 报告表=710(REPORT.md:11)
- 一致：diff=224 汇总=224 报告表=224(REPORT.md:12)
- 双错且不同：diff=16 汇总=16 报告表=16(REPORT.md:13)
- 新版更差：diff=3 汇总=3 报告表=3(REPORT.md:14)

### R02 PASS — 六字段 same/new_better/new_worse/different_unresolved 计数与汇总一致且每项和为总数

- title：四项和=953(=总数953) diff={'same': 786, 'new_better': 152, 'different_unresolved': 15} 汇总={'same': 786, 'new_better': 152, 'different_unresolved': 15} 报告(REPORT.md:94)={'same': 786, 'new_better': 152, 'new_worse': 0, 'different_unresolved': 15} → 一致
- title_en：四项和=953(=总数953) diff={'new_better': 393, 'same': 533, 'different_unresolved': 27} 汇总={'new_better': 393, 'same': 533, 'different_unresolved': 27} 报告(REPORT.md:95)={'same': 533, 'new_better': 393, 'new_worse': 0, 'different_unresolved': 27} → 一致
- authors：四项和=953(=总数953) diff={'same': 862, 'new_better': 63, 'different_unresolved': 27, 'new_worse': 1} 汇总={'same': 862, 'new_better': 63, 'different_unresolved': 27, 'new_worse': 1} 报告(REPORT.md:96)={'same': 862, 'new_better': 63, 'new_worse': 1, 'different_unresolved': 27} → 一致
- affiliations：四项和=953(=总数953) diff={'same': 585, 'new_better': 271, 'different_unresolved': 95, 'new_worse': 2} 汇总={'same': 585, 'new_better': 271, 'different_unresolved': 95, 'new_worse': 2} 报告(REPORT.md:97)={'same': 585, 'new_better': 271, 'new_worse': 2, 'different_unresolved': 95} → 一致
- keywords：四项和=953(=总数953) diff={'same': 900, 'different_unresolved': 35, 'new_better': 18} 汇总={'same': 900, 'different_unresolved': 35, 'new_better': 18} 报告(REPORT.md:98)={'same': 900, 'new_better': 18, 'new_worse': 0, 'different_unresolved': 35} → 一致
- keywords_en：四项和=953(=总数953) diff={'same': 750, 'new_better': 187, 'different_unresolved': 16} 汇总={'same': 750, 'new_better': 187, 'different_unresolved': 16} 报告(REPORT.md:99)={'same': 750, 'new_better': 187, 'new_worse': 0, 'different_unresolved': 16} → 一致

### R03 PASS — 作者超集判优 14 篇、audit_sample.json 的 seed/total/sample 与抽样超 10%

- audit.py 逻辑重算：superset=14 篇（old_names < new_names 且 assessment==new_better）
- audit_sample.json seed=20261127（期望 20261127）→ True
- audit_sample.json author_superset_total=14（重算=14，报告 14）→ True
- sample 实际=['13564501', '13570535']，random.Random(20261127).sample(k=2) 复算=['13564501', '13570535'] → True
- 抽样比例=2/14=14.3% > 10% → True（REPORT.md:28）
- 报告第 28 行声明的抽样 doc=['13564501', '13570535'] → True
- 报告超集表 doc 列表(14)与重算一致 → True

### R04 PASS — 全部剩余“新版更差”3 篇 doc 列表与 diff.jsonl 一致

- diff.jsonl category=='新版更差' 共 3 篇：['13564429', '13569396', '13569428']
- REPORT.md 表格(49-56行)共 3 篇：['13564429', '13569396', '13569428']
- 逐条顺序与集合一致 → True

### R05 PASS — 全部剩余双异 16 篇 doc 列表与 diff.jsonl 一致

- diff.jsonl category=='双错且不同' 共 16 篇：['13563533', '13563546', '13563600', '13565663', '13567051', '13567061', '13567085', '13567094', '13567111', '13567139', '13567236', '13567244', '13567332', '13568662', '13568678', '13568682']
- REPORT.md 表格(61-79行)共 16 篇：['13563533', '13563546', '13563600', '13565663', '13567051', '13567061', '13567085', '13567094', '13567111', '13567139', '13567236', '13567244', '13567332', '13568662', '13568678', '13568682']
- 逐条顺序与集合一致 → True

### R06 FAIL — token 清理声明：7 个 token 是否仍出现在新作者中

- token 본체론：新作者中出现 0 次 
- token 부산：新作者中出现 0 次 
- token 경남：新作者中出现 0 次 
- token 알고리즘：新作者中出现 0 次 
- token 유형분류：新作者中出现 0 次 
- token 평가하였고：新作者中出现 0 次 
- token 조록：新作者中出现 6 次 [('13570533', '조록'), ('13570535', '조록'), ('13570539', '조록'), ('13570541', '조록'), ('13570545', '조록'), ('13570546', '조록')]
- REPORT.md:28 声称 본체론/부산/경남/알고리즘/유형분류/평가하였고 等垃圾已清除
- 结论：본체론/부산/경남/알고리즘/유형분류/평가하였고 类垃圾确实清除；조록 仍残留 6 处 → 整体声明不成立

### R07 FAIL — 报告第 28 行“13570535 清掉 조록 但保留 배성중”真伪

- diff.jsonl 13570535 authors.old=['배성중']
- diff.jsonl 13570535 authors.new=['조록', '배성중']，assessment=new_better
- 新增 조록 的证据片段：value=조록 channel_supported=True excerpt=조록 hits=[{'page': 1, 'channel': 'paddle', 'box': [534.4, 771.1, 585.5, 796.0]}]
- REPORT.md:28 正文称“13570535 清掉 조록 并保留 배성중” → True
- REPORT.md:41 表格新作者=| 13570535 | 배성중 | 조록, 배성중 | 是 |（含 조록=True）
- 判定：清掉 조록=True（实际仍在=True），保留 배성중=True → 不成立

### A1 WARN — 锚点 13569913：题名、백서인/당정、双机构均正确。（锚点判定：**PARTIAL**）

- REPORT.md:82 13569913：题名、백서인/당정、双机构均正确。
- 题名字段被判 new_better 且新值为真题名 → 成立
- 新作者恰为 백서인/당정 且判 new_better → 成立
- 双机构被判为已验证(new_better) → 不成立
- manual_reason：锚点字段中不存在（仅 32 条 manual_review 记录，均不含锚点）
- 关键证据摘录(≤200字)：{"title": "중국의 전자상거래 기업의글로벌 진출 전략:알리, 테무, 쉬인, 틱톡샵을 중심으로", "authors_new": ["백서인", "당정"], "affiliations_new": ["한양대학교 ERICA 글로벌문화통상학부", "한양대학교 중국지역통상학과"], "affiliations_assessment": "different_unresolve

### A2 WARN — 锚点 13567494：리우커 + 대구가톨릭대학교 正确拆分。（锚点判定：**PARTIAL**）

- REPORT.md:83 13567494：리우커 + 대구가톨릭대학교 正确拆分。
- 新作者中出现“리우커” → 不成立
- 新机构含正确写法“대구가톨릭대학교” → 成立
- 机构字段判 new_better → 成立
- 新作者/机构中无“가틀릭”异写 → 不成立
- manual_reason：锚点字段中不存在（仅 32 条 manual_review 记录，均不含锚点）
- 关键证据摘录(≤200字)：{"authors_new": [{"name": "리우", "affiliation": "커 대구가틀릭대학교", "roman": null}], "affiliations_new": ["커 대구가틀릭대학교", "커 대구가톨릭대학교"], "affiliations_assessment": "new_better"}

### A3 WARN — 锚点 13568651：音译名保持一人，不再切出假机构。（锚点判定：**PARTIAL**）

- REPORT.md:84 13568651：音译名保持一人，不再切出假机构。
- 音译名保持一人（新作者数=1） → 成立
- 不再切出假机构（新作者 affiliation 为空） → 成立
- 作者字段判 new_better（已验证） → 不成立
- manual_reason：锚点字段中不存在（仅 32 条 manual_review 记录，均不含锚点）
- 关键证据摘录(≤200字)：{"authors_new": [{"name": "타이푼카르타브", "affiliation": null, "roman": null}], "authors_assessment": "different_unresolved"}

### A4 PASS — 锚点 13569417：四格作者及罗马名恢复。（锚点判定：**TRUE**）

- REPORT.md:85 13569417：四格作者及罗马名恢复。
- 作者为四格（新作者数=4） → 成立
- 四名作者均带罗马名 → 成立
- 作者字段判 new_better → 成立
- manual_reason：锚点字段中不存在（仅 32 条 manual_review 记录，均不含锚点）
- 关键证据摘录(≤200字)：{"authors_new": [{"name": "김상열", "affiliation": null, "roman": "Sangyeol Kim"}, {"name": "권용욱", "affiliation": null, "roman": "Yongwook Kwon"}, {"name": "김성균", "affiliation": null, "roman": "Sunggyun 

### A5 WARN — 锚点 13569428：五格作者恢复，`Radar Production` 消除；机构当前为 경북대학교、전기공학과、LIG넥스원 레이다기술팀及对应英文，已去除早期角色/折行碎片。（锚点判定：**PARTIAL**）

- REPORT.md:86 13569428：五格作者恢复，`Radar Production` 消除；机构当前为 경북대학교、전기공학과、LIG넥스원 레이다기술팀及对应英文，已去除早期角色/折行碎片。
- 作者为五格（新作者数=5） → 成立
- Radar Production 已消除 → 成立
- 机构列表含 경북대학교/전기공학과/LIG넥스원 레이다기술팀及对应英文 → 成立
- 新机构无早期角色/折行碎片（如 “김상열” 附着） → 成立
- 机构字段判 new_better（与“已验证锚点”一致） → 不成立
- manual_reason：锚点字段中不存在（仅 32 条 manual_review 记录，均不含锚点）
- 关键证据摘录(≤200字)：{"authors_new": ["권용욱", "김성균", "김상열", "이준하", "김홍준"], "affiliations_new": ["경북대학교", "전기공학과", "LIG넥스원, 레이다기술팀", "Department of Electric Engineering", "Kyungpook National University"], "affiliations_asse

### A6 PASS — 锚点 13564500：原 rescue 篇作者保留或补全。（锚点判定：**TRUE**）

- REPORT.md:87 13564500/13564501：原 rescue 篇作者保留或补全。
- 原 rescue 作者保留（authors.same=true） → 成立
- 新作者非空 → 成立
- manual_reason：锚点字段中不存在（仅 32 条 manual_review 记录，均不含锚点）
- 关键证据摘录(≤200字)：{"authors_new": [{"name": "심창용", "affiliation": "경인교육대학교", "roman": null}, {"name": "황필아", "affiliation": "경인교육대학교", "roman": null}, {"name": "남보라", "affiliation": "경인교육대학교", "roman": null}], "authors

### A7 PASS — 锚点 13564501：原 rescue 篇作者保留或补全。（锚点判定：**TRUE**）

- REPORT.md:87 13564500/13564501：原 rescue 篇作者保留或补全。
- 补全 조국（新作者含 조국） → 成立
- 保留原作者 김형엽 → 成立
- 作者字段判 new_better → 成立
- manual_reason：锚点字段中不存在（仅 32 条 manual_review 记录，均不含锚点）
- 关键证据摘录(≤200字)：{"authors_new": ["조국", "김형엽"], "authors_assessment": "new_better"}

### A8 PASS — 锚点 13569720：题名与两位作者正确，机构残片不再冒充作者。（锚点判定：**TRUE**）

- REPORT.md:88 13569720：题名与两位作者正确，机构残片不再冒充作者。
- 题名字段判 new_better → 成立
- 作者恰为两位（김지영/류현숙） → 成立
- 作者名中无机构残片（大学/学会/卷期） → 成立
- manual_reason：锚点字段中不存在（仅 32 条 manual_review 记录，均不含锚点）
- 关键证据摘录(≤200字)：{"title_new": "대학생의 신체이미지와 긍정심리자본", "authors_new": ["김지영", "류현숙"], "affiliations_new": ["중원대학교", "대진대학교"], "affiliations_assessment": "different_unresolved"}

### R16 PASS — 代码声明：未调用 front_doc()、未使用 LLM/网络

- 扫描 6 个 layoutx/*.py（字符串字面量已剔除）：audit.py, compare.py, extract.py, fingerprint.py, report.py, test_layoutx.py
- 命中网络/LLM/front_doc 调用：无
- REPORT.md:112 声称“未调用 front_doc()，未使用 LLM/网络” → True
- 注：extract.py/compare.py 使用 `from metapy import front` 的 _looks_affil/_parse_author_line 等内部纯函数，非 front_doc()

### R17 PASS — 代码声明：比较逻辑中无按 doc-id 的分类覆盖

- REPORT.md:21 声称删除 13569913/13569417 的硬编码分类覆盖
- compare.py（比较/分类逻辑）去字符串后的 doc-id 代码：无
- 去字符串后全部 layoutx/*.py 的 doc-id 代码：无
- 原始源码中出现（含字符串/注释）：[('fingerprint.py', 11, 'ANCHORS = ["13569913", "13567494", "13568651", "13569417", "13569428",'), ('report.py', 56, 'add("- 删除 `13569913`、`13569417` 的硬编码分类覆盖。锚点结果由实际字段和渠道证据计算。")'), ('report.py', 105, 'add("- 13569913：题名、백서인/당정、双机构均正确。")'), ('report.py', 108, 'add("- 13569417：四格作者及罗马名恢复。")'), ('test_layoutx.py', 213, 'authors = self._new_values("13569417", "authors") or []')]
- 13569913/13569417 仅出现在 fingerprint.py 的 ANCHORS 列表与 report.py 的文本模板中，不参与分类判定 → True

### R18 WARN — manual_review.json 是否覆盖 8 个锚点

- manual_review.json 共 32 条，doc 集合=['13563543', '13563550', '13563553', '13563564', '13563573', '13563576', '13563583', '13563585', '13563586', '13563587', '13564000', '13564003', '13564013', '13565659', '13565660', '13567150', '13567197', '13567220', '13567255', '13567258', '13567295', '13567357', '13567401', '13567429', '13568643', '13568652', '13568670', '13568685', '13569376', '13569408', '13569416', '13569421']
- 8 个锚点中被覆盖：无
- 未覆盖：['13569913', '13567494', '13568651', '13569417', '13569428', '13564500', '13564501', '13569720']
- 结论：锚点判定只能由 diff.jsonl 字段/渠道证据支持，无人工复核记录（报告未声称有，故记 WARN）

### R19 FAIL — 代码仅在 layoutx、输出仅在 out_layout

- 代码位置：audit.py, compare.py, extract.py, fingerprint.py, report.py, test_layoutx.py 全部位于 pipeline/layoutx/ → 成立
- 写入 audit.py:28 open(os.path.join(HERE, "audit_sample.json"),'w') → layoutx
- 写入 compare.py:462 open(os.path.join(HERE, "diff.jsonl"),'w') → layoutx
- 写入 compare.py:471 open(os.path.join(HERE, "comparison_summary.json"),'w') → layoutx
- 写入 extract.py:1076 open(os.path.join(folder, "metadata.json"),'w') → out_layout（<NEW>/<doc>/metadata.json）
- 写入 fingerprint.py:159 open(args.out,'w') → layoutx（fingerprints.jsonl 默认 HERE）
- 写入 fingerprint.py:170 open(os.path.join(HERE, "clusters.json"),'w') → layoutx
- 写入 report.py:127 open(os.path.join(HERE, "REPORT.md"),'w') → layoutx
- REPORT.md:112 声称“代码仅在 pipeline/layoutx/，输出仅在 pipeline/out_layout/”
- 结论：extract.py 写入 out_layout，但 compare.py/audit.py/report.py/fingerprint.py 写入 layoutx → 不成立（FAIL）
- 注：audit_verify.py 仅在 --md 时写 VERIFICATION.md 到 layoutx（复核脚本自身，不计入流水线输出声明）

### R20 PASS — mtime 声明：metapy 最新 2026-11-26、out 最新 2026-11-27 09:31

- REPORT.md:112 代码仅在 `pipeline/layoutx/`，输出仅在 `pipeline/out_layout/`。未调用 `front_doc()`，未使用 LLM/网络。最终 mtime 自查：旧 `metapy/` 最新文件为 2026-11-26，旧 `out/` 最新文件为 2026-11-27 09:31，均未被本原型刷新。
- metapy/ 最新文件=2026-11-26 18:12:56 (D:\Workspace\KCI\pipeline\metapy\__pycache__\front.cpython-313.pyc，35 个文件) → 与 2026-11-26 一致
- out/ 最新文件=2026-11-27 09:31:23 (D:\Workspace\KCI\pipeline\out\13569913\metadata_audit.json，217851 个文件) → 与 2026-11-27 09:31 一致
- out_layout/ 最新文件=2026-11-27 16:37:42 (D:\Workspace\KCI\pipeline\out_layout\13625974\metadata.json，953 个文件，仅供参照)

### R21 FAIL — 复现命令顺序是否合理（fingerprint.py 缺失）

- REPORT.md:105 复现命令=['layoutx/extract.py', 'layoutx/compare.py', 'layoutx/audit.py', 'layoutx/report.py']
- fingerprint.py 在复现命令中：False
- extract.py 读取的旧产物：[('extract.py', 1066, 'clusters = load(os.path.join(HERE, "clusters.json"))["cluster_list"]'), ('extract.py', 1067, 'fps = [json.loads(x) for x in open(os.path.join(HERE, "fingerprints.jsonl"), encoding="utf')]
- 这些旧产物由 fingerprint.py 产生：[('fingerprint.py', 148, 'ap.add_argument("--out", default=os.path.join(HERE, "fingerprints.jsonl"))'), ('fingerprint.py', 170, 'with open(os.path.join(HERE, "clusters.json"), "w", encoding="utf-8") as f:')]
- 静态判断：fingerprint.py 未列入，但 clusters.json/fingerprints.jsonl 是其产物且被 extract.py 依赖；按报告命令在干净环境复现会在 extract.py 处失败 → 不成立（FAIL）

