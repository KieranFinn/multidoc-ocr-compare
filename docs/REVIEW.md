# REVIEW.md（2026-11-04 新开）

记录所有决定与改动，最新的在最前面。旧档已删，本文件从新开始。

---

## 2026-11-05（续4）｜ 批次8：uid 定位 + 代码匹配闸门（用户拍板架构）+ in_ref 排除

- **evidence_span 取消**：LLM 不再抄整句证据（每条省 30~60 token，成本最大杠杆）。归属=模型自报块
  uid（`{"blocks":[{uid,…}]}` 形状）；唯一硬闸门改 `_match_anchor`——实体任一核心字段 _foldv 折叠后
  必须出现在该块原文，命中词记 `matched` 供审计页高亮；uid 缺失/错配→全批按字段找块；找不到=
  rejected(name_not_found)。prompt 同步：删 evidence_span 段、空类省略、空块不出对象、负例、orgs
  正向判据+负向兜底（含脚注/文献著录里的出版者与作者名）、standards code 措辞、journals/
  proceedings 去 url 字段、块头类型加 Footnote 解释、同块同名只报一次。
- **collect_blocks 排除 in_ref 文献表页**：文献表已被 refs 阶段解析进 metadata.references，再送 LLM
  是重复劳动且是出版者/刊名著录噪音的入口（用户抓出"已划分抽出的页内引文/文献也送 LLM 了"）。
- 标注继承键改为 (kind, uid, 折叠主标识[funder|name])；审计页"evidence"行改"命中"行；selftest 换
  新契约（blocks 包装+平铺兜底、NASA 拒、in_ref 页排除、带值标注重跑继承），7/7 PASS。
- 验证中：样本 2 篇（13563525 + 噪音王 FI001004818_2002）新旧对比，用户点头后全量 13 篇。
- 注意：13 篇现有产物是旧 span 契约（entities 带 evidence_span）；_row 对旧数据按 funder|name 兜底
  定位，审计页新旧混存可显示；重跑后自然统一到 matched 契约。
- 样本实跑(13563525 + FI001004818_2002, 新契约): 51→4 条 / 187→37 条(orgs 154→27, stan 17→10),
  拒 2(均为 name_not_found: 模型给英文名/扩名而块内是韩文原名——代码闸门按设计拒), 命中 94.9~100%。
  成本主项(整句证据+海量机构)两刀都砍到。**事故与恢复**: 13563525 用户批注的 datasets 实体
  (디지털군산문화대전) 本次未被重新抽出(脚注引用型数据源, 新口径下归"著录网址不抽"), 标注随实体消失;
  已从词典词条恢复该实体+批注回 ner_llm.json。教训: **持久真相在词典**, 实体级 comment 依赖再抽中。
- 引文句高亮不可见修复(用户抓出): 引文句表复用 .refs 的 td{nowrap;ellipsis} → 句子超宽裁掉、
  mark 高亮不可见。引文句表改 class="refs cspt" + 加 .cspt td 换行规则; 13 篇查看器已重建。
- cspan 另有两个正则病灶(用户已确认现象)——**不修（用户拍板 2026-11-05）**：NARR 懒惰匹配把
  'McCarty et al. (1998)' 抓成 'al.'; 多引括注(;)被 _paren_cite 压成一条, 作者取首/年份取末
  (Childs et al.+1990 错配) 且只回链一篇。诊断与修法备查，如要复活直接按上节（续4）机制改。

## 2026-11-06 ｜ 语义对账与路线修正（用户拍板）

- **实体目标 16 项对账结论**（语义以用户定义为准）：
  ①已做对：标题/摘要/作者/正文/参考文献/引文句；②能做未做：关键词、本篇期刊/出版年/卷/期/页
  （全在页眉/首页版式，规则层）；③语义错先冻结：科学数据集、基金、研究软件（数据集/软件要分
  "用到/产生"，基金要锁死"资助本篇"，重定义后再抽）；④机构：作者署名机构=front.affiliations 已对，
  NER 正文 orgs 删除。
- **三条路线决定**：正则能做的不用 LLM（本篇出版信息组走规则）；NER 的 orgs 删掉（KINDS/prompt/
  审计页/selftest 已同步，7/7 PASS）；数据集/基金/软件三项不做（ner_enabled 已置 false 冻结）。
- **未开工**：规则段"本篇期刊/年/卷/期/页 + 关键词"（下一步，建议新 stage，产出进 metadata.json
  新字段 + audit sidecar 置信）。
- 词典现有 8 词条（用户持续标注）；NER 现有 13 篇产物为旧语义（orgs 行仍在文件里，审计页有兜底显示）。

## 2026-11-05（续5）｜ 同篇同词合并显示 + kimi 供应商（用户拍板）

- 审计页合并显示：组键=(篇,kind,归一名)，组卡头=类型徽标+名称+字段并集+"本篇 N 次·页 x/y/z"+
  判定计数徽标(✓2✗1式)；组内逐处折叠（默认展开有标注处/第一处），展开后每处仍有独立
  ✓/✗/?/批注按钮——**批注语义保留个别（用户拍板）**，组头只做汇总不写回；过滤/搜索/已审按组，
  判定过滤=组内任一处命中即匹配。selftest 7/7（新增组断言）。
- _ner_run.py 加供应商开关：KCI_LLM_PROVIDER=kimi 时用本机 kimi-code OAuth 凭据(运行时读,
  不落盘) + kimi-for-coding(temperature=1) + 401 重读重试；默认 ds 不变。起因：用户 DS 额度不足。
- 全量 13 篇用 kimi 重跑（合并显示版首跑）。词典现有 7 词条（用户持续标注中）。

## 2026-11-05（续3）｜ NER 提示词优化（metapy/ner.py）

用户诉求："提示词效率太低"。查既有产物（`out_llm_trial/*/ner_llm.json`，deepseek-flash）确认两类问题：

- `KCI_FI001004818_2002`：orgs 154 条，含 `Syngman Rhee regime`/`Rhee's government`/`United States`/`Liberal Party`
  等政权/国家/政党；standards 17 条，含 `martial law`（戒严，抽象概念）。
- `KCI_FI002983265_2023`：125 块、16 批，实体 **0 条**（白跑）。

两处改动（不动 `KIND_FIELDS`/`_json_objs`/证据闸门契约）：

1. **规范与语料分离**：`_SYS_PROMPT` 收下全部"七类定义 + 三条门槛 + 输出 schema + 两组示例"，每批只发一次；
   `_PROMPT_TMPL` 只留 `块 uid(页) 类型=X` + `<<<原文>>>`，每块发一次。旧版把整份规范塞进每块模板，
   一批 8 块 → 规范文本 ×8，单批 token 大头是重复说明而非语料。省下的是纯冗余，规范一字未减。
2. **类定义收紧**：orgs 明列"不抽国家/民族/地区/政府/政权/政党/人名/职称/泛称/参考文献作者名"；
   standards 明列"不抽戒严/民主化/法治等制度名或抽象概念，只抽带全称的正式法令"（正对上面的实测噪声）。

曾写过后又整体回退的（用户否掉，理由：属于给 prompt 任务夹带的代码逻辑改动，且阈值凭感觉）：
送块闸门 `_worth_sending`（短块不送）、块状态 `_block_hint`/`_TYPE_HINT`（致 no_extract 不送）、
配套的 `blocks_total`/`blocks_skipped`/`uid_mismatch` 统计与审计页展示、`[ner-sendgate]` 自检用例。

未做（本会话 pwsh 全程不可用）：`python -m metapy.ner` 自检、真跑 13 篇看效果。

---

## 2026-11-05（续3）｜ 七类全量重跑完成（黄金集 13 篇）

- 结果：**实体 1265 条**（orgs 842 / journals 289 / standards 59 / software 28 / datasets 19 /
  proceedings 18 / funding 10），拒 0、错 0、各篇 span 命中率 100%。审计页/词典页已重建。
- 过程：首跑 13/14 时 3600s 后台超时（第 14 篇未及），拆出补跑完成。
- **注意版本错位**：本批 13 篇中前 12 篇用的是旧 prompt（规范×8/块）；最后一篇(FI003275609_2025)
  由补跑进程加载了另一会话中途落盘的优化版（规范收进每批一次的 _SYS_PROMPT + orgs/standards
  定义收紧——正是本会话诊断的政权/抽象概念噪音）。即当前 13 篇数据与磁盘 prompt 不完全同源；
  送块闸门(短块跳过)那版已被该会话自回退。当前磁盘版 selftest 7/7（本机核验）。
- 标注核查：用户两条批注（群山文化大典 datasets、首尔教育大学 funding）与词典 2 词条全部继承。
- **orgs 收口待拍板**（诊断实锤：历史政权当机构收/脚注出版者被收/同实体重复 32 次带 32 句证据）：
  方案=①prompt 排除历史消亡组织 ②排除引文来源(出版者/被引机构) ③同块同名去重；prompt 一句话
  级改动, 重跑 13 篇, 批注词典全继承。预计 orgs 842→200~300。用户未确认前不动。

---

## 2026-11-05（续2）｜ 黄金集移除 KCI_2003_2pages（用户拍板）

- `golden_dataset/KCI_2003_2pages.pdf` 删除（原为 #14，#3 的 2 页跨页测试夹具）；`验收标准.md` 同步：
  篇数 14→13（§1 正文/§8 GT 估算/§9 全量跑约定），§1 表删行并留移除备注。
- `out_llm_trial/KCI_2003_2pages/` 一并删除（trial 数据，可再生）；审计页重建后语料为 13 篇。
- `out/KCI_2003_2pages/`（unified/metadata 等产品）与 `uploads/` 里的副本保留未动——管线历史产物，
  用户未要求清理；要删说一声。
- 七类重跑期间执行的本删除；全部跑完后需再重建一次页面（重跑日志中该篇结果已被 trial 删除覆盖）。

---

## 2026-11-04（续3）｜ 行内引文句抽取（--stage cspan）+ 查看器"引文句"组（方案一）

### 规格来源
用户逐条 code review 后的九条修正+第十条替换版（数字式总开关由正文 N_num/N_fn 分布决定、
与文献表格式解耦），全部经代码核实后落地。正则做、不用 LLM（封闭语法+对号可机器查）。

### 改动
- 新增 `metapy/cspan.py`（自检 7/7）：Text 块收集（页不在 ref_pages；§44 回退逻辑本地重跑 8 行，
  注释标明与 refs_doc 同口径需同步）；Footnote 排除（行内引文本在 Text 块，排除同时防脚注编号 [n] 误报，
  且不碰 m3 共享的 FOOTNOTE_CITE）；一套切句规则（小数/缩写保护，失败兜底整块）；三形态匹配
  （叙述式紧贴(년) 1900-2026／括注式作者+年份双要素——回链兼任误报过滤器／数字式严格单数字+紧贴+≤max num）；
  对号 _foldv 比对 references[].authors_raw，多候选按页距升序取最近+n_candidates 落 audit，三档把握
  exact/partial/unlinked；跨页块 page=块首页+audit 记 cross_page。
- `metapy/__init__.py`：cspan 注册（与并行会话的 ner/ner_audit 合并，无冲突）。
- `pipeline.py _meta_cell`：新增"本页引文句 N 条(cspan)"组——句中引文 `<mark>` 高亮、把握色条
  （绿/黄/红）、对号徽标（→ #num 题名片段）、点击行钉源块证据（data-u 既有机制）。

### 14 篇实跑（89 条）
13625974=13（数字式开 12exact/1partial，IEEE 型如预期）；895567=45（16exact/29partial，
'(Nordstrom, 1982a)' 后缀年精确对号）；FI001272465=16（8/7/1）；FI001536751=11（1 篇仅 1 条文献，
7 unlinked 属实况）；FI003275609=4。其余 0——脚注型篇正文确无行内形态（1892171 独立核验：
正文括注候选=0，引文全在脚注已由 frefs 覆盖）；无 references[] 三篇按设计跳过。
audit.cspan 落 num_mode(N_num/N_fn/ambiguous)+kind/conf 分布+cross_page 计数。

### 渲染核验
13625974 查看器 4 页出组；`<mark>[6]</mark>`/`<mark>(교육부, 2015)</mark>` 高亮在屏；
"→ #6 A study on…"对号徽标在屏；查看器 14 篇块数不变（unified 驱动基线不动）。

---

## 2026-11-04（续2）｜ 换用 DeepSeek API（用户拍板，弃 kimi）

### 改动
- key：用户给 DS 的 sk key。持久化 = Windows 用户环境变量 `KCI_LLM_KEY`（setx，**不进仓库**）；
  当前 harness 会话拿不到 setx 的新变量，`_ner_run.py` 加兜底：env 缺则读 `~/.kci/llm_key`（用户目录，不在仓库）。
- `channels.json` llm 段：endpoint=`https://api.deepseek.com/chat/completions`，model=**deepseek-flash**，
  temperature 回到 0。kimi 的 endpoint/model/temperature 备注全部删除。
- 模型名定案：用户口径"4.1flash1017"，服务端实测只收 `deepseek-flash`/`deepseek-v4-pro` 两个名字
  （报错原文点名），按 flash 档映射为 `deepseek-flash`。实测 HTTP 200，是 reasoning 模型（不设 max_tokens 即正常）。
- `_ner_run.py` 删掉 kimi OAuth 那套（读凭据文件、401 重读重试、token 不落盘逻辑）；保留 unverified SSL opener
  （api.deepseek.com 与 api.kimi.com 同款缺中间证书问题，Python OpenSSL 验不过）。

### 决定
- 全量 14 篇用 deepseek-flash 重跑（含 kimi 已跑完的 3 篇）——全语料单一模型，样本报告与全量结果可比。
- 之前 kimi 跑出的 ner_llm.json 被覆盖，属 trial 文件夹正常迭代。

### 全量结果（2026-11-04，deepseek-flash，14/14 完成）
- 合计：1666 块 / 215 批 / 约 14 分钟；收 funding 10 / software 22 / datasets 18 = **50 条**；
  拒 0，errors 0，span 命中率 100%（deepseek-flash 对"逐字照抄"指令遵从度好，全语料零瞎编过闸）。
- 样本 4 篇（13563525 / KCI_2003_2pages / KCI_FI001536751_2000 / KCI_FI001632041_2010）：
  收 1/2/3 = 6 条，拒 0，命中率 100%。
- 0 实体篇 2 篇：KCI_FI002983265_2023、KCI_FI001004818_2002（模型全 null；是真没有还是漏抽，留给词典后置/人工审校核）。
- 单篇最多：KCI_FI002542944（基金4/软件10/数据集7 = 21 条）。
- ner_llm.json 结构：doc/generated/model/endpoint + stats（块/批/三类收拒/errors/span_hit_rate）
  + entities[]（kind+funder|name 等字段+evidence_span+uid+page）+ rejected[] + errors[]。

### 人工审计页（ner_audit.html）
- `build_audit_viewer()`（metapy/ner.py）+ `--stage ner_audit`：汇总 out_llm_trial 全部 ner_llm.json →
  **out_llm_trial/ner_audit.html**（单文件零 fetch，双击可开；有意放 trial 根，不进 out/）。
- 每条实体卡：类型徽标（基金蓝/软件琥珀/数据集绿）+ 字段 + evidence_span + 源块全文（evidence 黄高亮，
  空白不敏感定位 `_locate_span`，50/50 全定位成功）+ uid/page + 跳 1:3 对照查看器链接（原图版面）。
- 闸门拒收条目同列（可判"闸门误杀"）；人工判定 ✓正确/✗误抽/?存疑 存 localStorage（键 ner_audit_v1），
  一键导出 ner_audit_verdicts.json 留档（webapp 实时落盘接口未接，需要时另说）。
- 过滤器：文档/类型/判定状态/关键词；顶栏实时计 已审 n/总 m。
- selftest 5/5 PASS（新增审计页用例：数据内联+高亮定位+拒收渲染+落 trial 根）。
- 重跑 --stage ner 后需重跑 --stage ner_audit 刷新数据。
- 修复(模板 bug): 判定按钮的 onclick 里 `'ok'` 单引号在 JS 字符串中需 `\'`，Python 三引号串把
  `\'` 当转义吃掉 → 生成 HTML 里反斜杠丢失 → 整段 script 语法错误页面空白。改用 HTML 实体 `&#39;`
  后无反斜杠纠葛。已在浏览器实测渲染（文档分组/实体卡/计数全出）。教训：模板内 JS 引号优先用实体。
- 批注功能：实体卡加"✎批注"按钮 → 内联编辑框，自由文本评论；与判定同一 localStorage 存储
  （verd[k] 升级为 {v, c}，旧字符串值自动迁移）；卡片橙色批注条展示；参与关键词搜索；导出 JSON 带
  comment 字段。浏览器实测：填写→保存→展示→"✎改批注"状态切换全通。selftest 加断言（saveCmt/批注/
  &#39; 在产物中、裸 \' 不在）。
- 判定/批注回存（用户拍板"批注专门回存 json"）：
  - webapp.py 加 GET/POST `/api/ner_verdict`（POST 全量覆盖写 out_llm_trial/ner_audit_verdicts.json，
    同 /api/choice 路子；GET 供页面载入）+ GET `/trial/` 静态路由（同 /out/ 越界守卫）。
  - 审计页：打开时 fetch 服务端存档（有则覆盖 localStorage 并提示"已载入 n 条"，服务端优先=跨浏览器共享）；
    每次判定/批注 POST 实时回存（成功提示"已实时回存"）；双击直开文件时 fetch 失败静默走 localStorage+导出。
  - 访问入口: python webapp.py → http://127.0.0.1:8765/trial/ner_audit.html（页内"对照查看器"相对链
    接经 /out/ 路由同样可用）。selftest 断言 /api/ner_verdict/collectVerdicts 在产物中。
  - 已实测：POST 回读落盘、浏览器点判定回写文件、重开页水合恢复，全通；测试留档已清。
- 抽屉化（用户拍板"每篇文章单独抽屉"）：每篇一个可折叠 drawer——头栏=文档名+收/拒/span命中率/
  已审 n/m，点击展开收起；工具栏加 展开全部/收起全部；有过滤条件（类型/状态/搜索/选篇）时抽屉自动展开
  方便看命中；首个抽屉默认展开；卡片构建抽成 cardHtml 复用。浏览器实测：默认态/点开头/收起全部全通。
- 标注存储纠偏（用户拍板，推翻 verdicts 独立文件方案）：判定/批注就是 ner_llm.json 实体的
  verdict(ok/bad/doubt/null)/comment 两个字段——审计页经 webapp /api/ner_note 直接回写该文件
  （idx=实体在 entities[] 下标；清空也回写）；打开页面时标注随数据内联载入；重跑 --stage ner 按
  (kind, uid, evidence_span) 继承旧标注不丢。删除：ner_audit_verdicts.json 文件、/api/ner_verdict
  接口、localStorage 整套。14 篇 ner_llm.json 已补两字段。selftest 6/6（新增标注内联/继承断言 +
  localStorage、ner_verdict 不得出现的反断言）。实测：curl POST 落盘、浏览器判批注→ner_llm.json
  对应实体字段即时更新→重建页载入展示，全通；期间发现并清理了 8799 端口残留双实例（Windows
  SO_REUSEADDR 叠加监听导致请求落到旧代码进程——验证时先查 netstat）。

## 2026-11-05 ｜ 七类实体 + 全局批注词典（用户拍板"都按默认的来"）

### 实体类型扩展(3→7)
- 新增 journals(期刊 {name,url}) / orgs(机构 {name},不区分角色) / standards(法规标准 {name,code}) /
  proceedings(会议论文集 {name,url}); KINDS/KIND_FIELDS/_PROMPT_TMPL 同步, 审计页过滤/徽标七色。
- **14 篇未重跑**(用户明确"我说重跑再重跑")——新类型只在以后 --stage ner 产出中出现。

### 全局批注词典(ner_glossary.json)
- 键=(kind, name 归一化)：norm_name=去全部空白+casefold；funding 用 funder 当 name；kind 分库。
- 收录时机=webapp /api/ner_note 回写批注时：comment 非空且同键词条不存在则 glossary_add 新建
  (带 kind/name/name_norm/comment/source{doc,uid}/created)；**已存在不更新(只进不改, 用户拍板)**。
- 审计页：实体无个体批注且词典有同键词条 → 显示"📖 词典继承"蓝条(在批注正文上方, 用户指定的
  样式)+词典批注正文；个体批注(橙条)优先；清空个体批注→回退显示继承(实测全通)；不写实体字段。
- 词典页 ner_glossary.html：审计页 header"📖 词典"入口；只读列表+删除(POST /api/ner_glossary
  {delete:[kind+name_norm]}); live fetch /api/ner_glossary, 双击文件只读。
- 实测定论：保存批注→ner_llm.json 字段即时更新+词条自动收录(恰好收录了用户此前写的群山文化大典
  批注)→同篇第二条实时显示继承→个体批注覆盖显示→清空回退继承→词典页展示词条, 全链路通过。
  附带修复：collectNotes 补 uid(source 展示用)；webapp /api/config 分支 n 未定义的潜在 NameError。
- selftest 7/7 PASS(含词典归一化/只进不改/非法 kind 拒收/词典页产物断言)。webapp 已重启加载新
  接口(8765 常驻, bash 后台)。
- 已审口径修正(用户截图指出)：判定或批注任一即算审过(原只统计判定)，"未审"筛选同步。
- **标注 live 覆盖(用户截图抓出的过期 bug 根治)**：审计页个体批注原是构建时内联快照，保存后未重建
  页面→重开即回退显示旧状态。新增 GET /api/ner_annotations(服务端直读 14 篇 ner_llm.json 的有值
  标注)，页面打开时拉取覆盖内联快照(服务端缺省则删过期值)；词典本来就有 live fetch。此后保存批注
  无需重建页面，刷新即真相。实测：重开页第二条正确显示用户新批注"123"、已审 2/2。selftest 加
  /api/ner_annotations 断言。教训：凡是"服务端会变的值"，页面一律 live fetch，不信任内联快照。
- 个体+词典并显(用户抓出"改完个体批注词典批注消失")：原实现个体批注存在时覆盖继承显示；按用户
  "要区分开来"的原意改为并显——个体(橙条)在上、词典继承(蓝条+正文)在下，内容相同不重复(词条
  由该条批注收录时只显一份)。词典文件全程无损。
- 继承蓝框合一(用户拍板样式)：词典继承不再"蓝标签+借橙色正文条"，来源标注与批注正文整体装进
  单个蓝框(gsrcbox, 蓝底蓝边)，与个体橙条一眼区分。浏览器截图确认效果。
- 头部布局重修(用户截图抓出)：hmeta 加长(词条数)+svstat(写入回执)后 float 布局重叠错乱。
  改 flex(space-between+wrap)，"已审/导出/词典"收进 hright 组；header 与工具栏包进 #topbar 一起
  sticky(原分开 sticky 会错位)。截图确认不再重叠。

---

## 2026-11-04（续）｜ NER 实跑接线（自用 API）

### 钥匙从哪来（用户拍板：自用 API 方式跑，代劳接线）
- endpoint/model 填 `channels.json` llm 段：`https://api.kimi.com/coding/v1/chat/completions` +
  `kimi-for-coding`（kimi-code 本机配置 `~/.kimi-code/config.toml` 的 managed provider，OpenAI 兼容）。
- key 不落成文件：`KCI_LLM_KEY` 由 `_ner_run.py`（pipeline 根，新增）运行时从
  `~/.kimi-code/credentials/kimi-code.json` 读 access_token 注入环境，**只进内存/请求头，不打印不落盘**；
  每次运行现读（kimi-code 会自动刷新 OAuth，避免过期）。
- SSL：api.kimi.com 不送中间证书，Python OpenSSL 链构建失败（装 certifi + VERIFY_X509_PARTIAL_CHAIN 也不行）；
  该走 `_create_unverified_context()`（传输仍加密；curl/schannel 凭 Windows 证书缓存能验，Python 不行）。只影响 `_ner_run.py` 注入的 opener，metapy 代码不背这个。
- temperature：该模型服务端只收 temperature=1，channels.json llm.temperature 由 0 改 1（精度靠证据闸门兜）。
- 开关：`ner_enabled` 已置 true；`llm.enabled` 仍 false（④兜底不受影响，dual 用 `dual_enabled`）。

### 实跑发现的模型行为 → ner.py 解析修正（重要）
- **模型不按"每块一个对象"返回**：8 块的批只回 1~2 个合并对象，位置 zip 配对把实体挂错块——
  WATEQ4F 抽取正确却被 zip 到 u0_2 而遭 span 闸门误拒（源句其实在 u0_11）。
- 改为**实体级路由（归属=闸门）**：展平本批所有返回对象的候选实体，每条按 `_foldv(evidence_span)`
  匹配本批块文本找出处；匹配块即过闸并记 uid/page，无块匹配=span_not_found。不再按位置配对，
  合并返回/丢块都能正确归属。
- 批级异常另记 errors：整批异常→逐块 error；空响应→逐块 error；finish_reason≠stop→记"本批可能未答完"。
- prompt 加强：显式要求"对每个块各输出一个 JSON 对象，按块序，不合并"，evidence_span"不得改写或补齐标点"。
- selftest 同步更新（4/4 PASS）：模拟合并返回（2 对象答 4 块），断言按 uid 正确归属（u1_1/u1_2/u1_3）、
  errors=0；新增空响应→整块记 error 用例。

### 样本实跑（进行中）
- KCI_2003_2pages（2003, 2页）：块 13 / 批 2 / 25s；收 funding1(조선대학교)+software1(WATEQ4F)，拒 0，错 0，span 命中率 100%。
- 其余 3 篇样本（13563525 / KCI_FI001536751_2000 / KCI_FI001632041_2010 / KCI_FI002983265_2023）后台跑完后报合计，再全量 14 篇。

---

## 2026-11-04 ｜ 批次：文献实体抽取（基金/软件/数据集，--stage ner）

### 任务来源
`TASK_NER.md`：LLM 先行抽三类实体（funding=资助机构+课题号 / software=工具名+版本 / datasets=数据集名+链接），
证据闸门唯一硬约束（evidence_span 必须字面出现在源块文本，`_foldv` 折叠核对），词典后置（本批不做）。
方向已定，未讨论。

### 改动
- 新增 `metapy/ner.py`：
  - `collect_blocks()`：遍历 `out/<doc>/unified/page_*.json`，取非空正文块——type∈{Text,Footnote}（同 `common.CONTENT_TYPES`），
    排除跨页被吞并块（cross_page.role）与栏外竖条（`_is_margin`）。prompt 送块的全文 `main_text`（语料 p99=1793 字符，全量送可行）。
  - `ner_doc()`：分批调 LLM（批大小沿用 `llm.max_fields_per_call`），每块固定 JSON schema
    `{"funding":[...],"software":[...],"datasets":[...]}`，无实体该类置 null；复用 `llmrep._json_objs` 解析、多对象按序对块。
  - 证据闸门：每条实体必须给非空 `evidence_span`；`_norm_entity()` 先做 schema 校验
    （funding 要求 funder/grant_id 至少其一非空，software/datasets 要求 name 非空），再过 `_span_ok()`
    （`_foldv(span)` 须为 `_foldv(块文本)` 的子串）。在才收，不在→rejected(reason=span_not_found)；schema 不过→rejected(reason=schema)。
    这是唯一硬闸门，未用任何正则识别实体。
  - 结果只写 `out_llm_trial/<doc>/ner_llm.json`（stats：块数/批数/三类收拒计数/span_hit_rate + entities + rejected + errors）。
    `out/` 全程只读。
- `metapy/__init__.py`：`--stage ner` 接入（ner_doc 分发）。
- `channels.json` llm 段：新增 `"ner_enabled": false`（独立开关，默认关）+ `_ner_note`；endpoint/model/key 沿用 llm 段。

### 自检（python -m metapy.ner，mock 不触网）
4/4 PASS：① ner_enabled=false 不写文件；② mock 计数（块 4：Page-header 与跨页 head 块排除；
收 基金1/软件1/数据集1，拒 2，错 1，span 命中率 0.6）；③ 闸门（span_not_found 与 schema 各拒一条，命中条目 uid 齐）；
④ 落盘隔离（文件在 out_llm_trial/，源块 hash 不变）。回归 `python -m metapy.dual` 仍 4/4。

### span 命中率口径
accepted / (accepted + rejected)，分母含 schema 拒收（两者都是"送审实体被判掉"）；errors（调用失败/少返）不计入分母。

### 决定（本批拍板）
- 正文块口径 = `CONTENT_TYPES`（Text/Footnote）；Section-header/Caption 不送（标题块进"后续再排"的标题泛化）。
- 每块全文送 prompt，不截断；evidence 核对也对全文。
- 批大小不新造配置键，沿用 `max_fields_per_call`（默认 8 → 全量 1740 块约 218 批）。
- 实体不去重、不跨块合并：同一实体多处出现都留（带 uid/page 可溯源），归并留给词典后置阶段。
- datasets 的 url 允许 null（韩文旧刊常只有库名无链接）。

### 钥匙状态（真跑前置）
`channels.json` llm 段 endpoint/model 为空，环境变量 `KCI_LLM_KEY` 未设置——**真跑被阻塞**。
补齐后：先 `ner_enabled=true` 跑 4 篇样本（报 span 命中率 + 三类各多少条），再全量 14 篇。

---

## 2026-11-06 ｜ 规则段新 stage：issue（本篇期刊/年/卷/期/页 + 关键词）

### 任务与产物
- 新增 `metapy/issue.py` + CLI `--stage issue`（接进 metapy/__init__；metadata.py 帮助同步）。
- 产出 metadata.json 新字段：`journal` / `journal_en` / `year` / `volume` / `issue` / `pages` /
  `keywords[]`，各自带 `confidence{level,source}`；候选明细/票数/依据/页码逐页值进 audit `issue` 段
  （§38 分层不变）。`keywords_lines`（abs 阶段）原样保留不动。
- 全程正则，不用 LLM；不碰 unify/m3；out/ 规则层可写。

### 设计（全部经 14 篇语料逐行核实后定）
- 来源行=每页 struct 型块(页眉/页脚/页码)逐行 + 首页顶区(y<0.12H)任意类型逐行；折叠>130 字符的
  长行剔除（引擎把正文粘进 footer 的误标，如 2983265 p10/p15/p19）。
- 刊名：首页行(阅读序首个) > 全语料投票。形态：『名』书名号 / 卷期页码标记前前缀(循环剥前导页码、
  年份、'Name et al.'、尾部孤立年、季节词) / '|' 管道行首段；韩英同词行按"Journal 类关键词锚 + 左右
  大写/功能词扩展"拆双字段；带 boundary 的管道行整行扫段补 en；独立英文刊名行(无数字/谚文/冒号、词
  全大写或功能词)仅 en。守卫：折叠 2~50、不含 ':'、不等于本篇标题/作者(running 头=标题+作者)、
  不以 'YYYY년' 日期残段起头(3275609 实证)。
- 年：须行内有刊头信号(권/호/집/Vol/No/pp/ISSN/DOI/Journal/학회지/书名号/季节词) 且年份不被
  数字或连字符夹住(ISSN 2384-2008 的 2008 不出)。卷=제N권>Vol>N집 组优先级内投票；통권(总卷)
  只记 audit 不进卷期(3275609 통권16호 实证)；期=제N호/No.N 带 통권 旁路。页=pp. 强型 > 行尾裸区间
  (>999/逆序出局，ISSN 区间自然被拒) > 页码推断(每页取 Page-number 行首数字—'34 4'取34，无则行尾，
  'Page N of M' 取 N；offset=印刷号-物理页序号 众数回填首页缺号，1542944 的 1-16、1536751 的 145-175
  均靠回填才正确)。
- DOI 行 corroboration：\.(YYYY)\.(\d{1,3})\. → 年+卷(dasein/igll)；(\d{1,3})\.(\d{1,3})\.(YYYY)\d{0,2}\.
  → 卷+期+年(jcic)。DOI 尾号不做页码（igll …20.003 与实印首页 31 冲突，不可靠）。
- 关键词：KW_LINE 组（同块续行拼接；有冒号取冒号后——'■ 중심어 :' 的头词尾字 '어' 随冒号切掉；
  逗号/分号切分），页序首个非空组入 keywords[]，尾页英文摘要组兜底(2983265 p31 / 2309666 p35 实证)，
  原始行进 audit。
- 置信：≥2 页 high / 单页 mid / 无 null+low；页码推断 support≥3 为 mid 否则 low。

### 14 篇结果（核对全对）
- 全对：895567(五元组齐) 1272465(学会志+英文名+页1-16) 3275609(全+통권 旁路) 2983265(『』+pp+DOI年)
  13625974(卷9期4, 页347-357 页码推断) 1632041(군사연구 제130집, 年刊内无印→null) 1892171(2014 치안…)
  2309666(中央史論 46집, 英文 KW 尾页) 2632491(문예비평연구 제67집 2020.9) 13563525(管道行+DOI 年卷)。
- null 系真无（PDF 内无该信息）：2003_2pages 仅页 341-342；1536751_2000 全部仅页 145-175；
  1004818_2002 只有 KOREA JOURNAL/AUTUMN 2002(年+页 87-111 推断)；1542944 期=null(BMC 43:44 是
  卷:文号，文号 44 记 audit)。
- selftest 15/15 PASS（纯函数构造行，不触网）；回归 cspan 7/7、refs 35/35、ner 7/7 不变。

---

## 2026-11-06（续）｜ 查看器第一页 metadata 总览横带

- 用户拍板需求: 全部抽取字段统一展示在 1:3 对照查看器第一页。新增 pipeline.py `_meta_overview(meta, audit)`,
  挂 `build_compare_viewer` 第 1 页 section 的网格整行横带(`pane meta ov`, 紫头区分每页灰头横带):
  题录(标题/标题英/作者全表/机构) + 来源(期刊/年/卷/期/页, --stage issue 新字段) + 关键词 +
  摘要(韩英折叠全文) + 三类大列表全库计数(refs/frefs/cspan 分布+编号连续徽标+页域, 不展开, 明细仍在每页横带)。
- data-u 证据钉: 仅字段证据真在第 1 页时给 data-u(点击=钉块证据, 与每页横带同机制); 尾页供给的
  (title_en/abstract_en from_tail) 不给死按钮, 改"证据在第 N 页"徽标。issue 字段无 uid 只显置信色条。
- cspan 对号分布按 exact→high/partial→mid/unlinked→low 映射(与每页横带 lv_map 同口径)。
- 14 篇 viewer_compare.html 全部重建, 浏览器实测渲染正常。
- 顺带发现(不修, 记录在案): 3275609 的 authors[] 被 front 阶段混入目录节标题('Ⅲ. 국제법상…'等 11 条),
  总览带如实显示——front 作者区守卫的既有问题, 与本期改动无关。

---

## 2026-11-06（再续）｜ 总览横带位置修正 + KCI_2003_2pages 全量清除

- **总览位置修正(用户指出)**: 总览横带原挂在第 1 页四栏**之后**(要滚过整页才看得到); 改为第 1 页
  网格**首行**("第 1 页"标题下、原图/三渠道栏之上), 打开页面即见。data-u 机制不变(仍在 section p0 内)。
  13 篇 viewer_compare.html 全部重建并浏览器实测。
- **KCI_2003_2pages 全量清除(用户拍板, 黄金集回到 13 篇)**: 用户已自行删除后, 清除 pipeline 内全部痕迹:
  - `out/KCI_2003_2pages/` 整目录; `uploads/KCI_2003_2pages.pdf`;
  - `_state_mineru.json` 移除其 MinerU 任务项(7→6);
  - `out/_analysis_report.html` / `out/_analysis_summary.csv` 删除(旧 analyze.py 产物, 生成器已入 _attic, 无现行再生器);
  - `out/_regress_report.html` 用 `python regress.py` 重建(745 条 flagged, 0 残留)。
- **保留不动**: REVIEW.md 历史条目(该文 11-04~11-06 的抽取记录是既成事实日志, 按 RULES §5 不篡改历史,
  以本条宣告清除); `out_llm_trial/ner_glossary.json` 词典中 source 指向该文的词条(用户批注知识,
  "只进不改"拍板, 删源文实体不删词条)。
- 现行语料 13 篇; 代码/配置/文档(除 REVIEW 历史)已无 2pages 痕迹。

---

## 2026-11-06（修复）｜ pipeline.py 阻断 bug：adapt_mineru 半成品 extra_prefixes 收尾

- **问题(GitHub CI 暴露)**: 上次"img 按实际适配源目录走"的改动改了一半——调用点(2023 行,
  以及 merge_out.py:33)已传第 4 参 `(_out_rel(s, out),)`, 函数体也写了 `_tag_img(per_page,
  extra_prefixes)`, 但 `def adapt_mineru` 签名没加该参数 → 任何跑 MinerU 渠道必炸
  "takes from 2 to 3 positional arguments but 4 were given"; 即便补上签名, 函数体
  extra_prefixes 未定义也会 NameError。
- **修复(方案 A: 补签名收尾, 已核实不是遗留错误)**: ① `def adapt_mineru(path, sizes,
  cfg_key="mineru", extra_prefixes=None)`; ② `_tag_img` 重写: docstring 归位(原在赋值语句后
  是死 docstring), 前缀兼容元组取[0]/裸字符串/None 与空 → 兜底 raw/mineru/; ③ 连带修复
  `_blk_text` 双前缀: merged.json 与新鲜 adapt 产出的 img 已带全前缀(raw/<dir>/images/...,
  merge_out:82 注释的既定口径), 模板此前无条件再拼 → 改为 startswith("raw/") 则直渲,
  rebuild_viewer 剥回的引擎相对路径仍走 img_prefix 拼接(两条入口形态都验证); ④ preserve_raw
  docstring 里的 caption_audit/analyze 标注更新(已入 _attic)。
- **caption_audit.py 处置**: pipeline.py 对它**没有 import/调用**, 只有注释提及——引用随 ④ 更新,
  文件维持 _attic 不动(不挪回)。
- **验证**: py_compile 无 SyntaxWarning; scratch out 全跑 pipeline.py(13625974.pdf + mineru_api):
  渠道[mineru] OK 200 块, viewer img 单前缀且 6/6 文件存在; `--viewer` 重建路径 6/6 存在无双前缀;
  merge_out 4 参调用点 import 期正常。未动 golden 产物(除 13625974 按惯例重建查看器, 内容等价)。


---

## 2026-11-06 ｜ 行为保持式重构：stage 清理 + pipeline 拆三

### 改动
- `metapy/issue.py`：正则字面量统一为 raw string；合并重复的来源行收集与重复投票；去除未使用局部值；强化第 12 条自检断言。阈值、候选优先级与字段不变。
- `metapy/cspan.py`：正则写法整理，删除未使用 `cross`；既有 NARR/多引括注问题不动。
- `metapy/abs.py` / `front.py`：删除未用 import、同值分支与 `if False` 死代码；补模块自检。front 作者区规则不动。
- `metapy/llmrep.py` / `dual.py` / `ner.py` / `refs.py`：删除空转循环、未用 import/局部值，整理少量正则；同步六类 NER、uid+matched、词典并显等过期 docstring。prompt/schema/抽取口径不动。
- `metapy/common.py`：模块 docstring 归位；收拢 `RAW_HEAD_LEN=48` / `NUMERIC_CITE_MIN=3` / `TITLE_READING_DICE=0.8`，数值不变；修复原直执行入口调用未定义 main 的问题并补自检。`_gdice` 的 pipeline re-export 保留。
- `pipeline.py` 拆为 `pipeline_io.py`（渲染/获取/归档/标定/适配/配置）与 `viewer.py`（viewer/md2html/TeX/metadata 面板），原文件只留 main 与兼容 re-export。`merge_out/unify/metapy/_attic` 所需旧名字均保留；`webapp` 对 `pipeline.log` 的接管由 IO 运行时转发保持。
- `regress.py` 仅去重复项/修正注释；`merge_out.py` 未改。`webapp.py` / `app.js` 仅 import 归位、未用变量与配置文案整理，接口和页面结构不变。
- README 双语模块图、stage 顺序与现行产物说明同步；TASK_NER 增加 13 篇/六类/冻结状态说明。unify.py 与 metapy/m3.py 一行未动。

### 验证
- `python -m metapy.common` 6/6；issue 15/15；cspan 7/7；abs 2/2；front 4/4；llmrep 12/12；dual 4/4；ner 7/7；refs 35/35，全过，mock 不触网。
- 原实现与重构实现各用 `uploads/13625974.pdf` + 同一三渠道缓存跑临时目录。固定 `PYTHONHASHSEED=0` 后：11 个 unified 页文件逐字节相同；metadata/audit 除 generated 外完全相同；merged 11 页、4 个 fresh viewer 逐字节相同。
- 13 篇在临时副本中执行 `metadata.py … --viewer`，重构前后 viewer_compare.html 全部逐字节相同。fresh per_ch 与 rebuild 两条图片路径各 6 个 raw 图片引用均存在，无 `raw/raw/`。
- 原始/当前 regress.py 分别在 13 篇临时副本重建报告，均为 745 条 flagged，`_regress_report.html` 逐字节相同。
- webapp 用内存 fake handler 验证 PAGE、ner_note、annotations、choice、trial static，NER 字段与词典只写临时目录；pipeline_io 日志转发通过。未启动服务、未发网络请求。
- 冻结 hash：unify `4988cba7…`、m3 `18fa1077…` 不变；golden `out/` 2242 文件树 hash `e43984a6…` 不变。验证均在临时目录。

### 发现但未修（按红线留待拍板）
1. pipeline 内部调用 `unify.unify_doc(doc, out)` 时 `out` 已是文档目录，unify_doc 再拼 doc，fresh 主链路因此提示“无 merged 产物”；原始代码同样复现。本轮验证按 README 独立运行 unify 后继续，未改调用。
2. 冻结 unify 的 `max(set(...), key=...)` 类型并列票选受 hash 种子影响；可导致同输入跨进程类型/m3/refs 结果漂移。regress 的最长样例同长度排序也有同类展示顺序漂移。未动冻结项/回归口径。
3. 3275609 front 作者污染、cspan NARR 懒惰匹配与多引括注错配维持既有记录，未修。
4. `llmrep.llm_apply` 的 `_stash` 仍写 `out/<doc>/metadata_audit.json`，与现行“AI 数据只进 out_llm_trial”纪律存在既有冲突；本轮未调用真实 LLM，也未改路径。
5. `.github/workflows/ci.yml` 仍引用不存在的 requirements.txt、已迁移的 examples/analyze/caption_audit 路径；本轮未改 CI。


### 渲染补充
- 通过本机 8765 服务只读打开与重构后临时副本逐字节一致的 `out/13625974/viewer_compare.html`；浏览器首屏确认紫色 metadata 总览、题录/来源/关键词/摘要/文献统计与原图+三渠道栏标题正常渲染。检查标签已关闭。
- 本机无 Node；改用既有 8765 工作台做实际 JS 检查，首页 app.js 加载且“渠道 API 配置”按钮成功展开面板，未保存配置，检查标签已关闭。


---

## 2026-11-06 ｜ 发布阻断修复：unified 数据保护 + CI 可运行性

### 风险定级纠偏
- 上一批把 unify 路径错误写成普通未修项，严重性不足。旧链路在真实 `out/` 上会对**当前运行文档**执行 `shutil.rmtree(unified)`；一次调用不是删 13 篇，逐篇运行才逐篇受影响。旧逻辑连 `--no-unify` 也不能避免删除，因为清理块在开关之外。
- 临时副本 hash 不变只证明验证未污染 golden，不代表旧主链路安全。本批在用户拍板后修复，unify.py/m3.py 仍冻结不动。

### 修复
- `pipeline.py`：内部调用改为 `unify.unify_doc(doc, dirname(abspath(out)))`，传入真正的 out 根。
- 新增 `_load_fresh_unified(udir, t0)`：仅 fresh summary 驱动本次查看器；summary 缺失/过期、unify 异常或 `--no-unify` 时，旧 unified **保留但不绘制**。删除旧目录的 `rmtree` 分支移除。
- 新增 `test_pipeline.py`（stdlib unittest）：stale 保留且不加载、fresh 正常加载、`--no-unify` 保留、unify 异常保留且调用根路径正确。顺带关闭本路径涉及的文件句柄，ResourceWarning 作为错误也通过。
- 新增 `requirements.txt`，只列既有依赖 pypdfium2/Pillow/websocket-client，无新增依赖。
- `.github/workflows/ci.yml`：import 改为当前模块（含 pipeline_io/viewer），接入 4 条数据安全回归；demo 路径迁移到 `_attic/examples`，修正 `--out .../out/demo_kr` 与产物路径。
- `_attic/examples/make_demo.py`：dots/paddle 改为当前已适配缓存 schema；MinerU 源尺寸显式 521×688（null 会走 cal 默认尺寸而非实际页尺寸）；用法和注释同步。生产适配器与抽取口径未改。
- README 双语更新实际 demo 命令、测试命令、主链路顺序和旧 unified 保护语义。

### 验证
- `python -W error::ResourceWarning -m unittest -v test_pipeline.py`：4/4 PASS。
- `uploads/13625974.pdf` + 三渠道缓存，临时目录直接跑 pipeline（不再独立补跑 unify）：正常产出 11 页 unified 和 unified 驱动查看器。
- 随后同目录 `--no-unify` 重跑：unified 树 hash 前后均 `32b10b98…`，查看器退回非 unified 模式；旧数据无删除。
- CI 等价 demo 在临时副本全跑：三渠道 7/7/8 块；unified raw_blocks=22、groups=7、coverage=0.9545、orphan=1（唯一 orphan 是故意仅 Paddle 检出的页码），CI 全部产物/数据断言通过。未触发 GitHub 远程运行。

### 报告口径补充
- 自检来源：common 6、abs 2、front 4 及 test_pipeline 4 为本轮新增；issue 有断言强化；其余 stage 数字主要是既有自检复跑，不能据此推断覆盖均匀。
- 上一轮“逐字节相同”严格限定为 `PYTHONHASHSEED=0` 的同条件对照；非固定种子下冻结 unify 自身可能漂移，与重构无关。
- 拆分净行数应单列：pipeline.py 净减 1864 行，pipeline_io.py 净增 912 行，viewer.py 净增 1055 行；原 pipeline.py 为 2221 行。增删行与净行数不是同一展示口径。

### 最终回归
- 全部 metapy 自检再次通过；4 条 unified 数据安全回归在 ResourceWarning 视为错误时通过；CI 当前 import 集合通过。
- 13 篇临时副本 viewer 与 golden 逐字节一致；regress 745 条且报告与 golden 逐字节一致。
- unify/m3 hash 不变；golden out/ 2242 文件树 hash 仍 `e43984a6…`。


---

## 2026-11-06 ｜ cspan 引文句 hover 定位原图源块

- `viewer.py`：为本页 citation_spans 的唯一 `(page,uid)` 生成默认隐藏的 `cite-target` SVG 块框；普通高置信、无需复检的 unified 块也覆盖。hover `.cspt .mf[data-u]` 显示橙框，移开清除；点击兼容原证据钉选，并同时处理新旧 rect。
- 不改 cspan 抽取、metadata 字段、unified 数据或主链路；仍是一块级定位，不是扫描图逐字框。
- `test_pipeline.py` 新增非复检块 hover 目标与 JS/CSS 契约断言；`python -W error::ResourceWarning -m unittest -v test_pipeline.py` 5/5 PASS。两个 viewer 写文件句柄顺带改为 with，警告清零。
- 13625974 临时副本重建：13 条引文句对应 10 个唯一源块，10/10 cite-target 全部生成且 unified 证据存在。浏览器实测第 6 页引文句 hover 后原图对应正文块显示橙框，移开后消失；点击兼容由代码断言覆盖（未把超过 1.6s 的截图当作点击视觉证据）。
- 验证仅写临时目录并使用独立 8766 预览；golden 页面未重写。README 双语产物说明同步。

### cspan hover 正式部署补充
- 用户授权后直接重建现行 13 篇 viewer_compare.html；89 条引文句对应 43 个唯一 `(page,uid)` 源块，43/43 cite-target 均生成。
- 在正式 8765 页面实测 13625974 引文句 hover，原图对应正文块显示橙框；验证标签已关闭，webapp 服务保持运行。


---

## 2026-11-06 ｜ 引文 hover 可用性修复：refs/frefs 覆盖 + 原图局部浮层

- 用户实测发现两类前版缺陷：hover 只接 cspan，截图中的 frefs 不生效；即使 cspan 目标框已生成，源块也可能已滚出原图视口，用户看不到。
- `viewer.py` 改为 references/footnote_refs/citation_spans 三类共用 `.refs .mf[data-u]` hover；293 个唯一证据目标在 13 篇中 293/293 生成。
- 新增 `citepop/citesvg` 原图局部浮层：按 cite-target 坐标和当前 pages/page_N.jpg 裁出源块+60px 上下文，橙框标示；hover 显示、移开关闭，不再依赖原图恰好位于视口。原图 SVG 高亮和点击钉选保留。
- `test_pipeline.py` 同时构造 refs/frefs/cspan 非复检块并断言浮层契约；5/5 PASS，ResourceWarning 作为错误也通过。
- 正式 13 篇已重建。在 KCI_FI000895567_2003 第 2 页 `(Nordstrom, 1982a)` 引文句实测：显示 `原图块定位 · u1_5` 及带橙框的原图局部；移开后浮层关闭。webapp 服务保持运行。


---

## 2026-11-06 ｜ cspan 词典引导抽取优化（用户拍板实施）

### 实现
- `metapy/cspan.py` 保持 citation_spans 字段不变，新增每篇 references 作者键索引、折叠文本→原文下标映射、叙述式反向边界定位、平衡括号/顶层分号扫描、多引单元独立 offset 与对号。
- 对号优先完整年份后缀(1982a/b)，再退四年前四位；长作者键允许一次替换或单字符增删的有界 OCR 容错，并要求同年参考文献支持。无命中仍走既有通用候选与 unlinked，不强行生成链接。
- 不引入依赖/模型，不改 unify/m3、references 抽取或数字式总开关；footnote_refs 仍独立，不混入 cspan。

### 自检与临时 13 篇
- 新增困难样本：McCarty 作者恢复、Childs/Lazaroff/Bigham 三引拆分、Nordstrom 1982b、Fillipek/Filipek OCR 容错；cspan 11/11，refs 35/35，ner 7/7，llmrep 12/12，viewer/unified unittest 5/5。
- 临时 13 篇：89→100 条（多引拆分净增 11）；numeric 12 / parenthetical 45 / narrative 43；exact 67 / partial 21 / unlinked 12。置信是规则标签，不是人工准确率。
- 13 条 `al.` 作者截断清零；8 个多引句全部拆成独立单元且每单元有回链；citation_text 不再残留分号组。
- offset 错误 0、原文证据错误 0、链接目标结构错误 0；13 篇除 citation_spans/generated 外其余 metadata 字段逐项不变。
- 8 处回链变化逐条审查，分别恢复 Whittig/Timpson/Nordstrom 1982b/Park 2000b/Fuge/Moral/Roussel 等同年同作者条目；无证据把规则 exact 当作真实准确率。
- 临时 13 篇 viewer 全重建；regress 仍 745 条且报告与 golden 逐字节一致。

### 正式部署
- 用户要求实施后正式重跑 13 篇 cspan 并重建 viewer；当前共 100 条，293 个 refs/frefs/cspan 证据目标完整，原图 hover 浮层继续可用。
- unify/m3 hash 不变。无人工 GT 的整体召回率/真实准确率仍未宣称，后续应按文档划分 GT 再评估。


---

## 2026-11-09 ｜ cspan 同段同句合并展示

- 用户需求：同一段中多个引文不再重复显示同一句，整合成一个完整引文句。
- `viewer.py` 仅改展示层：按 `(page, uid, sentence)` 保序分组；每组只画一行，合并全部有效 offset 高亮，保留并按文献 index 去重回链；混合类型/置信标签全部保留，行色条取组内最低置信等级。每页及首页总览分别显示“引文句数”和“处引文数”。
- 不改 `metapy/cspan.py`、抽取口径、阈值或 `citation_spans` 字段；底层仍为 100 个独立引文单元，13 篇展示合并为 68 个引文句行。README 双语同步该展示语义。
- `test_pipeline.py` 新增同块同句合并、跨块不合并、多高亮/回链、最低置信色条及总览计数断言；`python -W error::ResourceWarning -m unittest -v test_pipeline.py` 6/6 PASS，`python -m metapy.cspan` 11/11 PASS。
- 临时副本重建 13 篇：68 行 / 100 高亮 / 293 证据目标全部吻合；无 cspan 文档 viewer 与原文逐字节相同；metadata.json 与 metadata_audit.json hash 不变。临时目录已清理。
- 正式 13 篇 viewer_compare.html 已重建并复核相同计数。浏览器实测 KCI_FI000895567_2003 第 2 页由 14 处引文合并为 4 句；截图目标句由 6 行合成 1 行，6 处高亮完整，5 个去重文献回链正常（Carson/Dixon 共同指向 #8）。检查标签已关闭，webapp 服务保持运行。
- unify/m3 未改，SHA-256 仍分别为 `4988cba74f515cc689461cb8105e95b8ffcb57ad0662eddc37cdc22a700a7102` / `18fa10778542278d8af885dc71293fa139d9fdd5eaa0ba23363098b30160175a`；未联网、未调用 LLM、未提交 git。


---

## 2026-11-09 ｜ refs/frefs 韩中短引号题名恢复（用户拍板实施）

### 检查与实现
- 用户指出 KCI_FI001632041_2010 frefs #38 红色“未抽出”；全量核查 340 条 refs/frefs，title=null 53 条，其中 ambiguous_quote 41 条；40 条由首个引号内容折叠后 <8 字触发，1 条为英文未闭合引号+斜体冲突。
- `metapy/refs.py` 新增 `_corner_biblio_fields` 局部恢复分支：仅在通用解析已标 `ambiguous_quote` 时触发；要求第二个逗号段为完整 `「文章」` 或 `『图书』`，文章还须第三段为完整 `『载体』`。按角色恢复 title/source/container_title/publisher/layout，作者只取首段并保留多词外国作者译名，避免题名/刊名/出版社混入 authors。
- 通用 8 字守卫、英文歧义分支及产物字段未改。叙述句、复合脚注、OCR 残句不进入恢复分支，继续留空待复核，不以提高填充率为由猜填。

### 自检与临时 13 篇
- 新增 6 条语料断言：韩文期刊短文题名、中文期刊短文题名、单本图书+译者、叙述型守卫、复合脚注守卫、OCR 残缺守卫；既有英文 #27 反例保持原结果。refs 41/41，cspan 11/11，viewer/unified unittest 6/6；common 6、abs 2、front 4、issue 15、llmrep 12、dual 4、ner 7 均 PASS，mock 不触网。
- 临时副本依次跑 refs→frefs→cspan→viewer：仅 29 条目标记录变化，title null 53→24，ambiguous_quote null 41→12；条目数/raw/page/uid/num/box 全部不变，其余记录逐项不变，100 条 citation_spans 逐项不变。29 条逐条核对题名、作者、来源/出版社，无引号作者污染或页码/참조 来源污染。
- 独立 regress.py 仍 745 条 flagged，报告与正式基线逐字节相同。临时目录已清理。

### 正式部署
- 正式 13 篇已重跑 refs/frefs/cspan，重建 metadata 报告及 viewer；最终 title null=24、ambiguous_quote null=12、cspan=100。
- 浏览器实测原 #38：authors=[심재연]、title=봉의산성 소고、source/container_title=춘주문화、issue=15、publisher=춘천문화원、year=2000，红色“未抽出”和 ambiguous_quote 标签消失。检查标签已关闭，webapp 服务保持运行。
- unify/m3 hash 仍为 `4988cba7…` / `18fa1077…`；未联网、未调用真实 LLM、未提交 git。


---

## 2026-11-09 ｜ frefs 全量口径纠正：古典/短回指/作者年份 + 题名恢复

### 全量盘点
- 用户指出 1632041 第 12 页只有 #38、第 16 页只有 #46，且 #46 仍“未抽出”；核实不是 viewer 过滤，而是 frefs 准入及结构化字段产物如此。
- 原代码自作保守口径为“合法公元年+现代来源线索”，古典、短回指和作者年份型说明引文被过滤。13 篇原 frefs=167；原候选条目 393 条，226 条被过滤，其中混有应收文献，也有术语注/审稿注/访问日期等非文献。
- 本轮用户明确否定该收口，要求全量检查并回归“文献漏收”和“题名未抽出”两类问题。

### 实现
- `metapy/refs.py` 新增 `_fref_citation_like`：保留全部既有现代引文，并覆盖完整书名号/斜体、古典卷次、URL、法律案例、`위와 같은 책/앞의 책/Ibid./supra` 回指及带页码或 참조/참고 的作者年份引文；纯术语/审稿/作者注、访问日期和无文献指向散文仍排除。
- 新增 `_reference_fallback_fields`：恢复叙述型 `「文章」+『载体』`、单本 `『图书』`、英文未闭合引号+斜体刊名、尾年无引号书目、法律案例及无引号古典书名；清理作者/来源污染。韩中三字题名不再被既有四字碎片阈值误杀。
- 新增 `_resolve_repeat_reference`：按指定 note/주、同作者前条或紧邻前条解析回指；长作者译名允许一次有界 OCR 编辑，短名不做模糊匹配。kept 按页码+脚注号排序，避免块底边排序令前引目标尚未出现。
- 不新增产物字段；raw/page/uid/num/box 保留，回指复制前条书目字段但不复制旧页码。

### 自检与临时 13 篇
- refs 45/45（含标准书目、叙述型、古典、英文残引、法律案例、复合脚注、OCR 守卫、收录与回指测试）；cspan 11/11；unittest 6/6；common 6、abs 2、front 4、issue 15、llmrep 12、dual 4、ner 7 全过。
- 临时 13 篇 refs→frefs→cspan→viewer：frefs 167→350；旧 167 条全部保留且锚点逐项不变。references 仅既有英文 #27 和 3275609 的 Summaries 条目按预期恢复题名，其余不变；100 条 citation_spans 逐项不变。
- 原 24 条 title=null 中 23 条恢复；新增引文有 4 条原文仅作者年份而无题名，另有 1 条 OCR 残句不可恢复，最终全库 title null=5，不猜填。
- regress.py 仍 745 条 flagged，报告逐字节不变。临时目录已清理。

### 正式部署与目标页回归
- 正式 13 篇 refs/frefs/cspan/report/viewer 已重建；最终 frefs=350，cspan=100，title null=5。
- 1632041 第 12 页：#37 三國史記 + #38 봉의산성 소고；第 16 页：#46/#47 江原道史 + #48 高麗史。浏览器实测数量、题名和“未抽出”消失均正常；检查标签已关闭，webapp 服务保持运行。
- unify/m3 hash 仍为 `4988cba7…` / `18fa1077…`；未联网、未调用真实 LLM、未提交 git。


---

## 2026-11-09（续）｜ frefs 改为全部编号脚注，说明注显示原文

- 用户继续指出 #36/#45/#55 未出现。三条均为长解释性脚注；原因不是长度，而是上一轮仍保留“必须有文献特征才收录”的内容准入，和用户要求不符。
- `frefs_doc` 取消内容准入过滤：`_fnote_collect` 收到的全部编号脚注均进 `footnote_refs`，含文献、说明、术语、图源、作者注和访问日期；不再因短文或缺公元年份丢弃。
- 书目字段解析与收录解耦：`_fref_has_biblio_evidence` 只决定是否运行书目字段解析，不影响收录。纯说明注直接 title=None，避免 #36 被误分出“년에 부분적으로 복원…”假题名；回指解析仍保留。
- `viewer.py` 标签改为“脚注”；无题名的 frefs 显示“脚注原文”+raw，不再显示红色“未抽出”。references 的题名缺失警告不变。README/metadata CLI 文案同步。
- refs 自检 45/45（新增 36/45/55/访问日期全收且无假题名）；cspan 11/11；viewer/unified unittest 7/7（新增说明注显示原文）。
- 临时 13 篇 frefs 350→393，旧 350 条 raw/page/uid/num/box/title 全部不变；100 条 cspan 不变，regress 745 及报告 hash 不变。临时目录已清理。
- 正式 13 篇 frefs/report/viewer 已重建。浏览器逐页实测：第 12 页 #36/#37/#38，第 15 页 #45，第 20 页 #53/#54/#55/#56/#57；三条目标说明注均显示原文且无“未抽出”。检查标签已关闭，webapp 服务保持运行。
- unify/m3 hash 不变；未联网、未调用真实 LLM、未提交 git。


---

## 2026-11-09 ｜ 旧 NER 产物清除 + 新方向改为本文关系抽取

- 用户确认旧语义错误：目标不是正文出现的任意实体，而是与当前论文存在使用/构建/资助关系的 funding/software/datasets。
- 核查旧 13 篇候选共 56 条（funding 10/software 28/datasets 18），含重复、KCI 页脚网址、参考文献工具、量表/设备等，不可作为真实覆盖。
- 用户拍板“旧的全部清除”：删除 13 个 `out_llm_trial/<doc>/ner_llm.json`，以及 `ner_audit.html`、`ner_glossary.html`、`ner_glossary.json`，共 16 文件。未动 out/、代码、viewer 或双渠道人工选择。
- 新方向：普通 NER 改为本文关系抽取。候选必须带受控 relation + evidence_span + uid；代码同时验证证据句确在块内、实体名在证据句内、relation 与该 kind 合法。仅提及/参考文献/他人研究/页脚网址不收。词典只做归一，不做准入。
- 建议关系：funding=funds_current_paper；software=used/developed_in_current_paper；datasets=used/created_in_current_paper。空结果=未检出，不宣称确认无。新产物仍只进 out_llm_trial/，真实 LLM 重跑另行授权。


---

## 2026-11-09 ｜ NER 新方案实现（离线 mock，未消耗 DS token）

- 用户拍板全文覆盖方案，否定规则预筛。`metapy/ner.py` 已改为：所有有效 Text/Footnote 块按顺序、块数+字符预算分批，长块不截断，正文只覆盖一次；不做关键词候选闸门。
- funding/software/datasets 的 prompt 明确“与当前论文直接关系”，新增 relation/evidence_span/needs_context。受控关系：funds_current_paper；used/developed_in_current_paper；used/created_in_current_paper。量表、设备、参考文献工具、引用图书、页脚网址等明确排除。
- 代码闸门 `_match_evidence` 同时要求证据在 uid 块内、实体名在证据内、relation 合法；名称匹配不再是三类的收录依据。仅 needs_context 候选携带前后块二次复核；仍不确定= context_unresolved，拒绝。
- 新增 `_batch_blocks` / `_request_batch`，统计 batches/context_reviewed；审计卡显示 relation。journals/standards/proceedings 保留既有字段与名称锚口径，未借本任务删改。
- 自检覆盖三类合法关系、NASA 无证据拒收、KOSIS 首次不确定→二次接受、非法关系/证据内无名拒绝、分批不重不漏、标注继承和 trial 隔离；`python -m metapy.ner` 8/8 PASS，viewer/unified unittest 7/7 PASS。channels.json 解析正常。
- 当前运行配置：`_ner_run.py` 默认 provider=ds，channels llm 为 `deepseek-flash` + DeepSeek chat.completions，key 从 KCI_LLM_KEY 读取；`ner_enabled=false` 保持不变。旧 ner_llm 仍为 0。未联网、未真实调用 DS、未提交 git。
- TASK_NER.md、channels 注释和 ner.py 旧语义注释已同步。真实全量运行仍需用户另行明确授权。


---

## 2026-11-09 ｜ refs/frefs hover 改为条目级成员框匹配

- 用户发现 KCI_FI000895567_2003 文献 #14 Filipek 的原图定位落到 Hounslow/Hurlbut。核实 metadata uid=u13_3 没错，但该 unified 组聚合多条文献，组框 y=801.5–897.2 与 Filipek 文本不一致；Filipek 真实成员框为 MinerU y=260.3–380.4。
- `viewer.py` 新增 refs/frefs 内容键 `data-e`（kind+原文 SHA-256 短键，HTML 内部属性，不改 metadata 字段）；每条目从 unified members 中优先选“原文完整匹配且面积最小”的成员框，无可靠匹配才退 unified 框。cspan 仍按 unified 块定位。
- hover JS 对 refs/frefs 按 data-e 选目标，不再用同一 uid 把组内所有条目混到一起；原图局部浮层沿用。unify.py/m3.py 冻结不动。
- 新增“错误 unified 总框 + 两个成员文献”回归，断言 Filipek 命中自身框；viewer/unified unittest 8/8 PASS。
- 临时 13 篇 viewer 全重建，603 个 refs/frefs/cspan 证据目标与预期逐项一致；Filipek 目标坐标 x=586.5/y=260.3/宽428.3/高120.1。正式 13 篇已重建。
- 浏览器实际 hover #14：浮层显示 Filipek 原文和正确橙框，不再显示 Hounslow/Hurlbut；检查标签已关闭。README 双语同步。未联网、未改 metadata、未提交 git。


---

## 2026-11-09 ｜ refs 跨页合并导致的页面串归属修复

- 用户指出第 14 页文献跑到第 13 页。核实不是 metadata 横带位置问题：M3 跨页合并块把下一页文本并入 tail，refs 再将整块所有条目统一记为 tail 页。KCI_FI000895567_2003 的 #3–#13 因此被误归第 13 页。
- `metapy/refs.py` 新增 `_reanchor_reference_entries`：取每条 raw 开头 `RAW_HEAD_LEN=48` 的折叠锚，在 merged.json 渠道原文中找唯一真实页，再于该页 unified 中找文本锚；两项都成立才更新 page/uid/box/block 置信。多页歧义、无命中均不动。m3/unify 冻结。
- 自检新增“唯一另一页命中则纠偏，无命中保持”用例，refs 46/46 PASS。
- 临时 13 篇 refs→cspan→viewer：共 14 条页面纠偏（本篇 11 条、13625974 一条、1892171 两条），raw/num/条目数不变。本篇 #1 仅因多条目探针解除而 confidence 改变；18 条 cspan 仅同步参考文献新 uid，linked index 不变。regress 745 及报告 hash 不变。
- 正式 13 篇 refs/cspan/report/viewer 已重建。本篇第 13 页仅 #1–#2，第 14 页为 #3–#25；浏览器实测正确。Filipek 仍在第 14 页，条目级成员框修复保留。检查标签已关闭，未联网、未提交 git。


---

## 2026-11-09 ｜ 第 1 页 Paddle 遮罩偏移 + 作者 HTML 上标清洗

- 用户截图指出 13563525 第 1 页 Paddle 无遮罩、metadata 作者为 `신성환<sup>●</sup>`。
- 遮罩根因：首页比后续页多 metadata 总览 `.pmask`，`fillMasks` 原用 section 内全部 `.pmask`，导致总览占用原图栏 pane，后续栏整体前移，Paddle 无 pane 可填。改为只选 `.pane:not(.meta) > .pmask`，排除总览/每页 metadata 横带；hide 逻辑不变。
- `front.py::_parse_author_line` 增加 `<sup>…</sup>` 标记提取与剥离：`name=신성환`、`markers=[●]`；原始渠道读法保持不变。
- front 自检新增 HTML 上标例，5/5；viewer/unified 8/8（新增 mask selector 契约）。
- 临时回归发现 front 单跑会清尾页字段，因此按既定 front→abs 完整链路验证；13 篇仅 13563525 作者字段按预期变化，其他字段不变。正式 front/abs/report/viewer 已重建。
- 浏览器实测：首页作者显示“신성환”，无 HTML 残留；点击 unified 眼后 PaddleOCR 视角遮罩正常显示。检查标签已关闭。unify/m3 未动，未联网、未提交 git。


---

## 2026-11-09 ｜ frefs 脚注标记扩展：裸数字式 + 句点式 + Page-footer 回退（用户拍板实施）

### 背景
- 全面 review（详见当轮报告）发现 frefs 标记形态覆盖不足两类：① `N.` 句点式（KCI_FI001004818_2002 全篇 40 条脚注 0 收录）；② 行首裸数字 `N `（13563525 上标脚注号被三渠道一致 OCR 为行首裸数字，16 行 0 收录）。另有页底脚注被渠道判为 Page-footer（13563525 u8_8 '17 김은혜…'，主读法丢号、dots 成员带号）彻底进不了收集。

### 改动（仅 metapy/refs.py，unify/m3 冻结不动，common.FOOTNOTE_CITE 未碰）
- `FN_CITE_X` 增两支：`\d{1,3}\s*[.、]`（句点式）与 `\d{1,2}(?=\s+(?:[^\W\d_]|[「『“‘]))`（裸数字式，要求数字后紧跟字母/开引号——'18 • Police Science Institute'（•为符号）与 '2020, …'（四位数）实证不误切）。
- `frefs_doc` 新增 `_footer_marked_text`：Page-footer 块主读法无行首标记时回退用成员读法中带标记的文本；此类块以 `strict=True` 进入 `_fnote_collect`，只收行首带标记的行、不并散文（防 www.kci/收稿日期等页脚杂行污染上一条）。
- 自检新增 2 组断言：标记形态正反例（含误切守卫）+ 裸数字/句点/Page-footer strict 三分合成用例（编号 1-3/17、页脚杂行不混入）。

### 回归面核实（改动前先行扫描 13 篇）
- 句点式新切仅 FI001004818_2002（40 行，全部真脚注）；裸数字新切仅 13563525（16 行真标记）+ FI001536751_2000 1 行（真脚注 #23，原先误并在 #22 raw 尾）；Page-footer 带标记块全库仅 13563525 u8_8 一块。其余 9 篇零影响。

### 验证
- refs 48/48（原 46+新 2）；common 6、issue 15、cspan 11、abs 2、front 5、llmrep 12、dual 4、ner 8；test_pipeline 8/8，全过。
- 临时 13 篇 frefs 回归：仅 3 篇变化且与预测一致——13563525 0→17（编号 1–17 连续，15/17 有题名，#17 经 dots 成员恢复，回指解析正常）；FI001004818_2002 0→40（编号 1–40 连续，27 有题名）；FI001536751_2000 28→29（+#23）。其余 10 篇 metadata 逐项不变；三篇 per_page_detail 对账均无缺口。

### 正式部署
- 正式重跑 3 篇 frefs，重建 _metadata_report.html 与 3 篇 viewer。全库 frefs 393→451（+58）；viewer 证据目标 603→661（每新 fref 行 = 1 成员框）。原图 hover 入口、成员框匹配、遮罩均沿用既有机制，未改 viewer 代码。
- 遗留观察（未修，非本轮口径）：FI001892171_2014 的 '18 • Police Science Institute' 碎片仍以非标记行身份并在前一条 #32 raw 尾（改动前后行为一致，裸数字守卫正确拒绝了它开新条）；FI001004818_2002 的 13 条说明性脚注无题名（脚注原文显示，符合现行口径）。
- unify/m3 hash 不变；未联网、未调用 LLM、未提交 git。检查标签已关闭，webapp 服务保持运行。


---

## 2026-11-09 ｜ 关键词韩英双字段 + KW_LINE 增 주제어/◆（用户拍板实施）

### 背景
- review 发现关键词字段语言由版式先后决定：issue 阶段旧口径"页序首个 KW_LINE 组入 keywords[]"——895567 英文 Keywords 在页 1 赢（字段全英文），13563525 韩文 핵심어 在前赢（英文行消失），同一条规则两篇语言不同。
- 用户拍板："有韩英双板的都要提取出来"，与 abstract/abstract_en 双字段对齐。
- 同步修 B3：`KW_LINE` 无 주제어/◆ 分支，FI001892171 '◆ 주제어(Key Words) : …' 整行未收（1632041/2309666 的 '주제어 :' 此前靠英文 Keyword 行兜底）。

### 改动
- `metapy/common.py`：`KW_LINE` 增 `주제\s*어` 头词与 `[■◆]\s*` 通用符号前缀；group(1)=头词（不含符号），供英/韩分类。使用方 abs.py（keywords_lines/摘要边界守卫）与 issue.py 同步回归。
- `metapy/issue.py`：关键词解析抽成 `_kw_parse()`（返回 en/items/raw，新增剥 '(Key Words)' 标签残留）；采集循环改双字段——韩文头词组入 `keywords[]`、英文头词组入 `keywords_en[]`，页序各取首个非空组；audit.keywords 增 items_en/raw_lines_en/source_en。
- `viewer.py`：总览横带"关键词"行后增"关键词(英)"行（仅在有值时）。
- README 双语同步字段说明。unify/m3 冻结不动。

### 自检
- issue 16/16（新 case 16=issue_doc 级双板同篇英在页 1/韩在页 2 双字段各收；case 10 扩 주제어/◆/标签剥除/英韩分类/◆非头词守卫反例）；abs 2/2、common 6/6、cspan 11/11、refs 48/48 等全过；test_pipeline 8/8。

### 13 篇临时回归与正式部署
- 变化全部限定在 keywords/keywords_en/keywords_lines 三键，journal/year/volume/issue/pages/abstract 等全部不变。
- 8 篇补齐双字段：895567/1632041/1892171/2309666/3275609/1272465 的 keywords 由英文改为韩文头词组（原英文组移入 keywords_en）；13563525/13625974 补 keywords_en。
- 口径变化（用户需知）：纯英文刊 FI002542944、2983265 无韩文面板，按摘要同构口径 keywords=[]、英文组入 keywords_en；FI001892171 的 ◆주제어 组为韩文头词，入 keywords（含括注英译），keywords_en 空。
- 正式 13 篇 abs→issue 重跑 + _metadata_report.html + 13 篇 viewer 全重建（viewer.py 有改动）；正式产物与临时回归逐项一致。未联网、未调用 LLM、未提交 git。


---

## 2026-11-10 ｜ viewer：metadata 条目点击跳转原文块位置（用户拍板实施）

### 需求与实现
- 需求：metadata 总览（及本页横带）中点击条目，跳到原文中该块出现的位置。
- `viewer.py`：
  - 模板新增全量块坐标表 `UBOX={uid:[0基页,x0,y0,x1,y1]}`（unified 模式全量输出，含无眼/无框的普通块——`review=false` 且读法健康时 umb/cite-target 都不画，第一版查 `rect.umb[data-u]` 对这些块无效，浏览器实测暴露后改为 UBOX 定位 + SVG 注入临时橙框）。非 unified 模式为空表，走原闪rect兜底。
  - JS `jumpToUid(uid)`：UBOX 查页 → `section#pN.scrollIntoView({block:'center'})` → 向该页原图 SVG 注入 `class="r umb flash"` 临时 rect，1.6s 后移除；新增 `.r.flash` CSS（橙框+浅橙填充）。
  - 点击分支顺序不变：本页有眼→钉遮罩抽屉；有 data-e→成员框 flashAt；否则 jumpToUid（跨页跳转到此）。
  - 总览"关键词/关键词(英)"行接入证据 uid（audit.issue.keywords.source/source_en），同样可点击跳转。
  - 说明：原图栏为整页图等比显示（.imgw 无内部滚动），定位=整页可见+橙框标示块位置，无需栏内滚动。

### 验证
- test_pipeline 9/9（新增 test_overview_click_jumps_to_source_block：双页 unified 伪件断言 UBOX 表、jumpToUid/flashAt/createElementNS/.r.flash 契约、关键词行 data-u）；全部模块自检通过。
- 13 篇 viewer 全重建。浏览器实测（13563525）：点击总览"关键词(英)"行 → 视口滚动至第 10 页（印刷页 40）并居中，重复两次一致；同页条目（关键词行 u0_7）维持钉遮罩行为。橙框闪烁 1.6s 低于截图工具时延未能留影，CSS/JS 契约由单测覆盖。检查标签已关闭。
- 未联网、未调用 LLM、未提交 git；unify/m3 冻结未动。


---

## 2026-11-10 ｜ viewer：metadata 带行内公式渲染（用户拍板实施）

### 背景
- 用户抓出 cspan 引文句里 `$\approx 660mg\ l^{-1}$`、`CaCO_3` 等以 LaTeX 源码裸露（截图：895567 印刷页 339）。
- 原因：渠道正文块展示层(md2html)久已用 `texlite` 轻量公式渲染，而 metadata 带(cspan 句/文献与脚注题名及原文/来源/摘要)此前只做 HTML 转义，公式不渲染。

### 改动（仅 viewer.py 展示层，产物字段/raw 不变）
- 新增 `_texesc(s)` = texlite → HTML 转义 → 放行 texlite 生成的白名单标签(`i.fr/sup/sub/&frasl;`)，与正文块同一转换口径；无公式特征($/\_^ 缺席)时短路，等价纯转义。
- 应用面：cspan 句高亮分段(_hl_group，按 offset 切片后逐段转换，引文 offset 不会切开公式)、文献/脚注行的题名与脚注原文与来源列、摘要(总览+本页横带, 全文与折叠摘要)。tooltip/title 属性保留原文不渲染(悬停看原证据)。

### 验证
- test_pipeline 10/10（新增 test_metadata_band_renders_inline_math：$\approx/l^{-1}$ → ≈ 与 <sup>、$\frac{1}{2}$ → <i class="fr"> 白名单放行、纯文本等价纯转义无标签残留）。
- 13 篇 viewer 全重建。浏览器实测 895567 印刷页 339 引文句带：McCarty 句 `≈ 660mg l⁻¹`、`107CaCO₃ l⁻¹`(下标3)、`H₂O`、`1106.71 ~ 1150.70cm⁻¹` 等全部渲染(见截图)，与用户抓出的裸露形态对比确认修复。检查标签已关闭。
- 未联网(轻量转换零依赖, 非 KaTeX/MathJax)、未调用 LLM、未提交 git；unify/m3 冻结未动。


---

## 2026-11-10 ｜ 点击跳转冲突修复：同页不拉走视口(C2) + 来源行接 uid(G3)（用户拍板实施）

### C2：同页点击不再 scrollIntoView
- 症状：本页横带中块无复查眼的行，点击后 jumpToUid 把整节 scrollIntoView 居中，用户正看横带却被拉走。
- 修复：`flashAt(r,noScroll)`/`jumpToUid(uid,curSec)` 增加同节参数——目标 section 与点击行所在 section 相同时跳过整节滚动，只在原图 SVG 注入橙框闪烁；跨页行为不变。data-e 成员框分支同样处理。

### G3：来源行（期刊/期刊(英)/年/卷/期/页）接入证据 uid
- `metapy/issue.py`：投票计数器 `_add` 改存 (page, uid)，`_vote`/首页优先判定/audit 候选页表同步适配（audit 仍只出页表）；extract 胜出字段回填 `uid`=_uid0(胜出值首个命中行)——卷取 권>Vol>집 优胜组的锚，页码推断兜底(pages_derived)无来源行则 uid=None（行不可跳，与旧行为一致）。
- `viewer.py`：总览来源行 `row(level, f.get("uid"), ...)`，有锚即可点击跳转（含跨页，如 FI001004818_2002 年=2002 锚在页 2 页眉块 u1_1）。
- 产物字段变化：metadata.json 六来源字段 dict 增 `uid` 键（值/置信不变；无锚为 None）。用户已拍板。

### 验证
- issue 自检 16/16（case 3 增 uid 回填断言：권 组优胜锚=u2）；test_pipeline 10/10（jump 测试扩展：来源行带 uid 行 HTML、同节不滚动 JS 契约 `if(sec!==curSec)`）；全部模块自检通过。
- 13 篇临时 issue 回归：仅六来源字段 +uid，值与置信逐项不变；正式 13 篇 issue 重跑 + 报告 + 13 篇 viewer 全重建。
- 浏览器实测：FI001004818_2002 总览"年"行点击 → 跳到第 2 页（印刷页 88）KOREA JOURNAL 页眉块橙框闪烁；895567 第 9 页引文句行点击 → 视口留在横带（钉抽屉路径视口不动）；C2 无眼同页分支由单测契约覆盖。
- 剩余已知项（未动，待拍板）：G1 总览跨页题录行 `_p1_uid` 剥 uid（UBOX 时代可放开）；C1 同页有眼行点击仍走钉抽屉不闪框；G2/G4 机构与计数行无单块锚。
- 未联网、未调用 LLM、未提交 git；unify/m3 冻结未动。检查标签已关闭。


---

## 2026-11-10 ｜ G1：总览跨页题录/摘要行放开 uid（用户拍板实施）

- 背景：`_meta_overview._p1_uid()` 是钉-only 时代设计（"跨页 uid 在首页 section 无眼无框，点了没反应，不如不给"），剥掉跨页题录/摘要行的 data-u。UBOX 全量坐标表上线后该前提失效。
- 改动：`viewer.py` `_p1_uid` 直接 `return f.get("uid")`；摘要行同步去掉"仅 page==1 给 uid_head"的内联条件。行为分派不变：同页有眼→钉抽屉，跨页→jumpToUid。
- 验证：test_pipeline 10/10（jump 测试增跨页标题行带 uid 断言）；13 篇 viewer 全重建。未联网、未提交 git。

### 附带发现（未修，待拍板）：13563525 韩英摘要双空白
- 现象：abstract=None、abstract_en 定位到页 10 u9_3 但 value 空（该篇页 1 有 국문요약、页 10 有英文 Abstract，均非"确实无此内容"）。其余篇的空摘要核对过属正常（无该版式）。
- 根因①（英文）：`_collect_abstract` 的 REF_HEAD 边界无 y 守卫——页 10 左栏 '참고문헌' 头(y=161)在 Abstract 头(y=179)**上方**，先于正文触发 return 空；且该页为双栏（左文献/右英文摘要），即便修 y 守卫，左栏小节头 '1. 단행본 및 논문'(y=214) 仍会被 generic 节头边界截停——单栏 y 边界在双栏版式下天然失效（与 A4 unify.ref_region 不判栏同源）。
- 根因②（韩文）：MinerU 把 `**국문요약** 본고는…`（markdown 加粗头+正文同块）读成一个 Text 块；ABS_HEAD_KO 只认独立 Section-header 且 ^…$ 严格匹配，ABS_INLINE_KO 要求冒号——两种形态都不匹配 → 整段丢失。
- 修复方向：英文=收集器按栏过滤（与头块同栏+下方）+ 剥作者/机构行（u9_7 块内 'Shin, Seong-Hwan | Chung-Ang University |' 前缀）；韩文=ABS 头支持 `**국문요약**`/`**요약**` 无冒号加粗头形态。两处均动 abs.py 抽取口径，待用户拍板后单独立项修。


---

## 2026-11-10 ｜ 点击=跳转/悬停=遮罩交互定稿(C1) + 同页状态栏提示 + 13563525 双摘要漏抽修复（用户拍板实施）

### 交互定稿（用户口径：点击总览/横带条目=跳转到原图块位置；悬停=触发遮罩）
- `viewer.py` 点击处理器移除"同页有眼→钉抽屉"分支（mfdrawer 机制随之退役）：点击统一走 成员框闪烁(data-e) → jumpToUid(UBOX) → 旧兜底；同页不滚动，只在原图 SVG 闪橙框，并在该页状态栏写提示"源块在本页原图栏 · uid（上方橙框闪烁处）"(`hintSame`)。
- 悬停：mouseover 对 `.mf[data-u]` 且 GROUPS 有遮罩数据的行直接 `fillMasks`（与眼按钮同机制），mouseout 延迟隐藏；👁 按钮点击钉选保留。
- 浏览器实测（13563525）：点总览"标题"→ 不再开抽屉，状态栏出现提示，视口不动；摘要修复后总览同时展示 摘要(韩) 570 字 / 摘要(英) 956 字。

### 13563525 双摘要漏抽修复（`metapy/abs.py`）
- 根因①英文：尾页双栏（左文献/右摘要）下 `_collect_abstract` 无栏感知——左栏 '참고문헌' 头(y=161)在 Abstract 头(y=179)上方即触发 REF_HEAD 停止返回空；左栏小节头也不截停不混入的诉求无法满足。
- 修复：头页确有双栏(_page_col_boundary 非 None)时只收与头块同栏的 Section-header/Text 块；REF_HEAD 停止补 y 守卫；skip_fold 命中的英文题名复写头由"不截停但混行"改为整块跳过；首内容行剥 '|' 分隔的作者/机构前缀（短罗马名 ≤40 全字符或含 University/Department 等机构词才剥，遇正文长句即停）。
- 根因②韩文：MinerU 将 `**국문요약** 본고는…`（markdown 加粗头+正文同块、无冒号）读成一个 Text 块，ABS_HEAD_KO（独立节头^…$）与 ABS_INLINE_KO（须冒号）均不匹配 → 整段丢失。修复：新增 `_ABS_BOLD_KO` 形态（`**요약/국문요약/초록**` 可选冒号），剥头取余行、遇 KW/头形态即停；该形态不跨页续接（防吞次页正文，内联冒号形态保留原续接行为）。
- 自检：abs 2→4/4（新增双栏尾页收集用例与加粗头用例；夹具补 CONTENT_TYPES 块满足栏判定）。
- 13 篇临时 abs→issue 回归：仅两篇变化——13563525 abstract 0→570 / abstract_en 0→956（首尾句完整、无题名/文献行泄漏）；FI002632491_2020 abstract_en 3732→3691 = 剔除了旧产物混入的一条参考文献行（에리히 노이만…），属净修正。其余 11 篇逐项不变。正式 13 篇 abs→issue 重跑 + 报告 + 13 篇 viewer 全重建。
- 未联网、未调用 LLM、未提交 git；unify/m3 冻结未动。检查标签已关闭。


---

## 2026-11-10 ｜ 点击关闭悬停遮罩（用户抓出的交互叠加残留）

- 现象：鼠标移向 metadata 行时 mouseover 先浮出遮罩（"悬停=遮罩"），随后点击只做跳转闪框，但悬停浮出的遮罩未关闭且鼠标仍在行上 → 用户看到"点击打开了遮罩"。
- 修复：`viewer.py` mf 点击处理器起始处 `clearTimeout(ghide); hideAllMasks();`（gpin 仍清零）——点击=关闭遮罩+跳转，遮罩只归悬停/👁 钉选管。
- 浏览器实测（13563525）：悬停"作者"行→遮罩浮出；点击→遮罩关闭、状态栏提示"源块在本页原图栏 · u0_5"、视口不动。test_pipeline 10/10；13 篇 viewer 重建。检查标签已关闭。


---

## 2026-11-10 ｜ 悬停遮罩加意图延迟，治总览扫行乱闪（用户抓出）

- 现象：鼠标在总览/横带上扫过时每行经 mouseover 即点亮四栏遮罩再被 250ms 隐藏，满屏乱闪——点击钉选、悬停遮罩、点击跳转三个功能三次叠加，mouseover 无防抖。
- 修复：`viewer.py` 悬停遮罩加 350ms 意图延迟（mfh 定时器）：快速扫过不触发，停留才浮出；mouseout/点击/悬停眼按钮均取消挂起的 mfh。点击行的关闭遮罩逻辑不变。
- 浏览器实测（13563525）：停留"作者"行 350ms 后遮罩正常浮出（统一对象 u0_5 面板）。test_pipeline 10/10；13 篇 viewer 重建。检查标签已关闭。
- 状态归属现状（单一职责）：悬停→浮出(延迟)；点击行→关闭遮罩+跳转闪框+状态栏提示；👁 点击→钉选；移开→250ms 隐藏。


---

## 2026-11-10 ｜ 撤销行悬停遮罩（用户纠正：遮罩只归眼睛管）

- 回顾：本轮先给 metadata 行加了 mouseover 浮遮罩（误解"鼠标移上去才触发遮罩"的归属），又因扫行乱闪加 350ms 延迟——两次都偏离用户口径。
- 用户定稿口径：metadata 行（总览/横带）**悬停无任何反应**；遮罩只归 👁 悬停（即时）与 👁 点击钉选。
- 改动：`viewer.py` 删除行悬停遮罩分支与 mfh 延迟机制（mouseover/mouseout 恢复为 眼→fillMasks / data-b 联动 两分支）；行点击仍=关闭遮罩+跳转闪框+状态栏提示。
- test_pipeline 10/10；13 篇 viewer 重建。检查标签已关闭。


---

## 2026-11-10 ｜ 点击定位升级为"块中心对齐视口正中"（用户拍板）

- 用户口径：点击后目标块应显示在当前屏幕正中央；此前"同页不滚动"的妥协（C2）取消。
- 改动：`viewer.py` JS 新增 `centerBlock(sec,fracY)`（section 顶对齐后按块在原图内的纵向比例 scrollBy 至视口正中）；`flashAt`/`jumpToUid` 统一走该定位（含跨页与 data-e 成员框路径）；状态栏提示改为"源块位置已居中 · uid"。
- 验证：test_pipeline 10/10（断言更新为 centerBlock/window.scrollBy 契约）；13 篇 viewer 重建；浏览器实测点"作者"行 → 视口滚动至作者块居中的位置。检查标签已关闭。


---

## 2026-11-10 ｜ 点击定位滚动动画化（用户拍板）

- `centerBlock` 改为单次 `window.scrollTo({top, behavior:'smooth'})`（原生缓动，先算文档绝对目标值再滚动，避免两段滚动叠加）；顶栏"跳页"链接 scrollIntoView 同步加 smooth。
- test_pipeline 10/10（断言 scrollTo/平滑契约）；13 篇 viewer 重建。检查标签已关闭。


---

## 2026-11-10 ｜ 点击定位高亮通道收敛（用户拍板，冗余点 1）

- 收敛前核实：两条活通道（data-e→flashAt、uid→jumpToUid 注入 rect）本就共用 `.flash` 类+1.6s+`centerBlock` 居中；旧式 `rs.forEach` 闪 `on+cite-on` 兜底在统一/非统一模式下均不可达且 `'on'` 对 rect 无 CSS 效果，纯死通道。
- 删除：mf 点击尾部旧兜底分支、`mfdrawer` 清理行及其死 CSS（6 条规则）、`jumpToUid` 死参 `curSec`。
- 保留的有意双视觉：hover 持续描边 `cite-on`（跟鼠标）vs 点击瞬时点闪 `.flash`（1.6s）——语义不同非冗余。
- 验证：test_pipeline 10/10（点击契约断言更新为 `.flash` 通道）；13 篇 viewer 重建，部署文件 grep 零残留。行为无变化。


---

## 2026-11-10 ｜ 冗余点 2/3 收敛 + 共识虚线框默认隐藏（用户拍板）

### 冗余点 2：双钉选状态合并
- `gpin`(unified 眼钉选) 并入 `pinned`(唯一钉选)——gid `uX_Y` 与渠道块 id `ch_p_i` 命名空间不重叠可安全共存；showPinned/scheduleHide/眼点击/行点击全部改走单一变量，切换钉选类型时先清旧视觉(眼钉→hideAllMasks, 渠道钉→act off)；补回"点空白解除眼钉"语义。
### 冗余点 3：metadata 行渲染双份收敛
- `_meta_overview`/`_meta_cell` 的两套 `row()/dot()` 闭包抽为模块级 `_mf_row()/_mf_dot()`（语义取较干净的"uid 为空不输出 data-u"版），两函数只传数据。
### 共识虚线框默认隐（用户指出碍读）
- `.r.um/.r.umb`(unified 复查块共识几何框) 由常显(opacity .85/.9)改默认 0 + 过渡；`fillMasks`(悬停/钉👁) 点亮该组 umb+um 的 `.on`，`hideAllMasks` 统一熄灭；"块框叠加"诊断按钮仍全显；`.flash` 补 `opacity:1` 保持闪橙框独立可见；栏标题改"悬停👁=遮罩+共识框"。
- 验证：test_pipeline 10/10；13 篇 viewer 重建；浏览器实测默认态原图无框、悬停眼遮罩+框同现。检查标签已关闭。


---

## 2026-11-10 ｜ 全部叠加框线宽压至最细（用户拍板）

- 7 条 stroke-width 规则统一下调（SVG 均带 vector-effect:non-scaling-stroke，线宽≈屏幕像素）：基础块框 2.4→1.2（hover 态 3.2→2）、共识虚线框 3→1.2、白衬底 6→2.5、证据成员框 4→1.5、点击闪框 6→2.5、citepop 裁切框 8→3。
- test_pipeline 10/10；13 篇 viewer 重建；浏览器实测点击定位/滚动行为不变。检查标签已关闭。


---

## 2026-11-10 ｜ 冗余项全清（用户拍板）：共享 JS 内核抽取 + GROUPS/UBOX 并表 + 小项

### 1. 事件内核双份 → `_JS_CORE` 单一来源
- `VIEWER_TMPL`(单渠道) 与 `COMPARE_TMPL` 原各持一份 `act/stat/data-b 悬停钉选/跳页` 事件代码（上轮 smooth 滚动靠 replace_all 命中 2 处=分叉隐患）。现抽为模块级 `_JS_CORE` 常量，两模板经 `@@CORE@@` 注入；单渠道侧补 `scheduleHide/hideAllMasks/showPinned` 空桩（其 mouseover 引用统一层函数，无眼元素不会走到）。单渠道查看器顺带获得与对照版一致的状态栏格式（ch · 块 idx）。
- `build_viewer` 冒烟验证：占位全替换、内核/桩在位。磁盘上旧单渠道 viewer 待下次 pipeline 全量时自然刷新（行为等价，非缺陷）。
### 2. GROUPS 并入 UBOX（前端两张 uid 表→一张）
- `UBOX={uid:{box,panes?}}`：box=全块几何（点击定位），panes=复查组遮罩（原 GROUPS）；`fillMasks/jumpToUid` 改读合并表；模板删 `@@GROUPS@@`。部署文件实测 `"u0_5": {"box": [...], "panes": [...]}`。
### 3. 小项
- `esc` 双 lambda → 模块级 `_esc()`；`_p1_uid()` 一行包装器内联删除；`hintSame` 改名 `hintCentered`（文案早已是"已居中"）。
- 验证：test_pipeline 10/10；13 篇 viewer_compare 重建、占位零残留；build_viewer 冒烟通过。行为无变化。检查标签已关闭。


---

## 2026-11-10 ｜ 静默失败守卫落地（用户定稿方案）：零产出守卫 A + 阶段覆盖守卫 B

### 设计定稿（采纳用户纠正，否决漏抽量估算）
- 否决"独立通道对账"：regress 与 refs 共用 REF_HEAD/split_entries 定位（只换输入源），当不了 oracle；朴素行首计数在 13625974/1632041 上得 0 的反向假警报会杀死守卫可信度（用户实测方差数据支持）。
- 守卫 A（零产出而证据非零，三态）：判据只用统一层存在性信号——①RED 文献节头在页 X 但 in_ref=0 页（unify 栏判定漏标形态）；②RED 有 in_ref 页且长文本块≥3 却 0 条目；③AMBER 长文本块≥10 且产出<5%（已知形态可解释）。写在 refs_doc 早退分支之前（修复用户指出的"早退不写 ref_validation、llmrep 条目数第三参数写死 True 渲绿"的静默路径）；audit 缺省时 llmrep 从 `refs.guards_for_doc` 独立重算（refs 未跑也有结论）；报告"条目数"颜色改由守卫决定，AMBER 专用琥珀色。
- 守卫 B（阶段互斥空档）：同页≥2 个带行首编号的 Footnote 块却不在 references∪footnote_refs 任一条目页 → RED。判据曾含 Text `_looks_cike`，在 3275609 p28（国语摘要页）误报——按"守卫被判错一次就会被无视"原则收紧为只认带编号 Footnote（该页真实缺口已由守卫 A 的 AMBER 覆盖）。

### 验证
- refs 49/49（新增守卫三态+覆盖用例）；llmrep 12/12；test_pipeline 10/10。
- 13 篇临时回归：metadata 逐项不变；守卫实测——13563525 RED、FI002542944 RED、FI001536751/FI002309666 AMBER，其余 9 篇 PASS，零假警报。
- 正式 13 篇 refs 重跑（写 guard 入 audit）+ 报告重建：9 绿 / 2 琥珀 / 2 红上屏。未联网、未调用 LLM、未提交 git；unify/m3 冻结未动。


---

## 2026-11-10 ｜ NER 真实运行前六连修（用户 review 定稿，第 1 条拍板 uid 锚定）

### 1. evidence_span 取消，改回 uid 锚定（用户拍板）
- 用户核实 prompt 与闸门里 evidence_span 仍在生效（11-05 为省 token 砍抄句未贯彻）：模型被要求抄整句，抄错一字即被 `evidence_not_found` 误拒，识别实际正确却按错误标准计损。
- 落地：`_SYS_PROMPT` 删证据契约（示例同步去 evidence_span 字段）；`_match_evidence` 删除，六类统一走 `_match_anchor`（核心字段折叠后须在该 uid 块原文，命中词记 matched）；不确定候选口径改为"给最可能合法 relation + needs_context=true"（relation=null 直接拒）。成本口径随之回到 11-05 锚定版。

### 2. relation_invalid 移到复核之前
- 原顺序 needs_context 先截走候选，relation=null 白烧一次复核调用后落 context_unresolved，relation_invalid 不可达（用户代码级指正）。现关系合法性最先判定，非法立即拒。

### 3. 数据级去重
- process() 增加 (kind, uid, 折叠主标识) 集合：重复进 rejected 记 `duplicate`（重复率可观测）；accepted.total 不再虚高；批注继承键不再两条共享一份 verdict。自检断言 duplicate 理由。

### 4. context_unresolved 显式计数 + 自检
- stats 增 `context_unresolved`；自检新增复核仍不确定→拒绝路径用例（修复用户指出的 mock 把 join 当 join(generator) 的写法 bug——`"\n".join(单字符串)` 逐字符插换行致空响应）。

### 5. 长块批处理自检
- 新增 13000 字单块用例：独占一批不截断（真实数据 FI002542944 已有 4269 字块接近 12k 边界，旧自检从未覆盖该分支）。

### 6. 文献著录不进 NER 输入
- `refs.ref_pages_extended`（in_ref ∪ 前导节头页；全无 in_ref 时末个节头页起——13563525 双栏同页摘要形态；frefs 收集仍用纯 in_ref，不影响产物）；`ner._fnote_biblio` 排除枚举式与单条著录 Footnote（行带编号+合法年/op.cit.+引号或来源线索；内容注与基金声明不误伤）。
- 实测 13 篇收集量 1343→1185 块（-11.8%）/ 362,781→336,885 字符（-7.1%）：13563525 文献页整块剔除（-17 块）、FI001004818 的 MLA 文献脚注 22→8 块（余者为作者简介与续行碎片）。

### 验证与未做
- 全部模块自检通过（ner 10/10 含新契约/去重/unresolved/长块/著录排除断言）；test_pipeline 10/10；TASK_NER.md 现行状态同步。
- 未真实跑 LLM（待用户授权，建议先 2 篇样本看 name 命中率/context_unresolved 比例/重复率）；产物层零改动（refs.py 仅加 helper，collect_blocks 只被 NER 消费）。


---

## 2026-11-10 ｜ NER/守卫二轮修正（用户 review 六条）

1. **ref_pages_extended 收敛三调用方**：refs_doc 与 guards_for_doc 原先各自内联 in_ref 集合（缺前导节头回退，refs.py:1722/1362 式分叉隐患复发）。现两处均调 `ref_pages_extended`；函数加 `head_fallback` 开关——NER 用全口径；refs_doc 传 False 保留"无 in_ref 早退"（节头页回退=A4 修复，未拍板不启用）；guards_for_doc 传 False 与 refs_doc 同视角（全口径会把 13563525 的扩展页误当文献区，RED-1 定位失败被洗成 RED-2）。refs_doc 内旧内联回退删除。
2. **AMBER 带上界信息**：reason 写明实际比例（如 "31 长文本块仅 1 条(3.2%)"）；自检增 100 块/10 条中产用例防误报。
3. **span_hit_rate → accept_rate**：evidence_span 已删，旧名残留会误导为证据命中率；分母本就混五种拒收理由，拒绝分桶(rejected dict)已是诊断入口。
4. **_match_anchor 主标识强制**：funder/name 必须命中块原文，grant_id/code/url/version 仅辅助——"version=26.0 单独放行"堵死；matched 恒=主标识。单字符名(如 R)的宽泛性留审计页人工兜，锚层不再放宽。
5. **复核窗口跨页澄清**：collect_blocks 返回跨页扁平列表，i 首块时 i-1 即上一页末块，本就跨页——加注释说明，无需改码。
6. **errors 分类**：每条 error 带 etype=network（重试可救）/format（空响应/finish_reason/类型非法，重试无意义）；stats 增 errors_network/errors_format。自检补网络错误用例。

验证：全模块自检通过（ner 10/10 含弱锚/网络错误断言、refs 49/49 含中产用例）、test_pipeline 10/10；13 篇守卫状态与已部署报告一致（RED×2/AMBER×2 带比例/PASS×9）。产物层无改动。检查标签已关闭。

---

## 2026-11-10 ｜ ner_lite 全面取代旧 NER + 实体回显 viewer_compare（用户拍板）

### 决定
- ner_lite（LLM 提名+程序定位，三 kind 开放词表，切片裸 in 不变量）取代旧六类 NER，成为实体抽取唯一入口。
- RULES 增第 6/7 条：实体渲染层可进 out/（viewer_compare.html），本体仍留 out_llm_trial；11-10 验证动作产物留 out_llm_trial/fds_extract/。
- 旧代码/文档删除：metapy/ner.py、metapy/_ner_current_backup.py、_ner_run.py、TASK_NER.md；webapp 4 个 NER API 端点（ner_glossary×2/ner_annotations/ner_note）；metapy/__init__ 的 ner 导入/choices/分发；metadata.py 文档串；channels.json ner_enabled→_ner_lite_note；README(双语) 布局行。
- ner_lite.py 收编至管线根目录（去 fds_scan 依赖：内联 paddle 加载器，文档清单由 out/ 扫描+散装映射派生；产物默认 out_llm_trial/fds_extract/ner_lite/）。
- viewer.py：build_compare_viewer 自加载 out_llm_trial 的 ner_lite 产物，per-page "ner_lite 实体"面板（kind 徽标+名称+原文切片+批注，行点击=定位闪块），文件缺失零影响。

### 验证
- ner_lite selfcheck 10 断言全绿；真实文档加载（unified+paddle）OK；13 篇 viewer_compare 重建后逐篇 grep 实体面板；test_pipeline 10/10；各 metapy 自检。

### 验证补记（当日完成）
- test_pipeline 10/10；metapy 自检 refs/llmrep/cspan/issue/abs/front 全绿；ner_lite selfcheck 绿；全模块 compile OK。
- 12/13 篇 viewer_compare 重建且实体面板落盘（grep "ner_lite 实体" 计数 = 各篇含实体页数；FI001004818/FI002632491 零提名故 0，正确）；13564165 无 unified/merged（从未入管线），无 viewer_compare 可接，实体仍看其 _audit.html。
- 删除 out_llm_trial/fds_extract/ner_lite.py 旧副本（收编后根目录 ner_lite.py 为唯一入口）。
- 补(当日): 实体另入 metadata 总览横带(第 1 页, 按 kind 聚合 ×n 定位数, 点击跨页定位闪块); 曾误删 `ch_cells += _meta_cell(...)` 一行致 test_overview_click_jumps_to_source_block 红, 已恢复, 10/10 复绿; 12 篇重建三层(实体总览+实体页带+元数据页带)齐。
- 补(当日): 实体行样式对齐总览带语法("粗体中文字段名+普通值", 撤彩色 kind chip 与其 CSS); 批注中文化为 mftag; test_pipeline 10/10, 12 篇重建, ek 芯片零残留。
- 补(11-11): 总览带一致性修正(用户拍板): 关键词韩英并一行(英走 mftag), "摘要(韩)"改"摘要"(韩语默认无后缀, 仅英语加(英)); test_pipeline 断言随合并更新, 10/10; 12 篇重建。
- 补(11-11): 关键词组结构改"统一组头+双行"(照摘要组样式, 韩英两行各带证据 uid); 用户问询的既有标签释义: "证据在第N页"=跨页跳转提示, "(全文N字)"=摘要折叠展开, ×n/批注=实体定位数与消费提示。

---

## 2026-11-11 ｜ 后链全自动接线：放一篇 PDF 产出全部产物（用户要求）

### 内容
pipeline.main per-doc 尾部新增"⑥ 后链"（index.html 生成前）：元数据 7 阶段(m3→refs→frefs→front→abs→issue→cspan, 全本地启发式) → ner_lite(LLM 提名+程序定位, 产物落 out_llm_trial/fds_extract/ner_lite/) → rebuild_viewer 终版对照查看器(元数据带+实体面板)。每步 try/except 记日志不阻断；webapp 拖入走同一 main。开关 channels.json `llm.ner_lite_auto`(默认 true)+ `KCI_LLM_KEY`；关开关或缺 key 则 ner_lite 跳过、其余照跑。
- 补(11-11 验证): 端到端重跑 uploads/13625974.pdf 全链 5.7s 通过(引擎走缓存): merge→unify→compare→元数据7阶段(<1s)→ner_lite(1批, 提名1/2准入)→rebuild_viewer(元数据带+实体面板)→index。修复收编时引入的 DST 路径差一级 bug(产物误写 KCI/out_llm_trial, 已归并回 pipeline/out_llm_trial 并删残渣); test_pipeline 10/10。
- 补(11-11): ner_lite 双渠道故障转移落地: channels.json llm.channels[zjlab-maas(DeepSeek-V4.1-Flash, http内网, key 走环境变量 KCI_ZJLAB_KEY 不落盘) → deepseek-official(兜底)]; 429/5xx 同渠道退避重试(15s×2)再转移, 401/403 直接转移; 审计增 channel 溯源; selfcheck 补转移/退避断言。
- 注意: 用户旧 deepseek 官方 key 于当日失效(401), 渠道保留待换新 key。


---

## 2026-11-11 ｜ 工作台改为结构化总览看板（用户要求）

- 信息架构：任务列表与任务详情分栏；总览保留任务总数、运行、完成、部分异常、失败五项；详情集中展示整体进度、7 个管道环节、3 个解析渠道及结果入口。
- 状态管理：webapp 新增 `PIPELINE_STAGES`，每个任务维护 `stages` 并随现有日志事件推进；`/status?job=all` 输出结构化环节状态。渠道仍独立维护 waiting/running/done/warning/error，结束后补 skipped，运行中渠道用活动进度条，整体进度按已结束环节计算。
- 日志退出主视图：删除终端式日志框、logline 和逐条 warnings 渲染；前端只显示归纳后的异常与处理建议，例如多个 401 批次合并为一条实体抽取授权异常。上传反馈改为短时 toast。
- 样式独立到 dashboard.css；保留拖拽上传、目录监视、API 配置与结果查看器入口。unify/m3 未动。
- 验证：`python -m unittest test_pipeline.py` 12/12；`python -m py_compile webapp.py` 通过；浏览器以临时结构化任务实测总览、步骤条、渠道卡、进度与异常摘要，修复旧 header 白字覆盖新顶栏的冲突。未调用在线解析或 LLM。
