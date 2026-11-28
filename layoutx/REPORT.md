# 版面模板抽取原型：最终重跑报告

本报告由 `layoutx/report.py` 依据 `diff.jsonl`、`comparison_summary.json`、`audit_sample.json` 生成，表格行均由数据循环产出。历史基线为返工前备份常量，仅用于对比。

## 最终结论

**本轮重跑后规则判定零退化：doc 级与字段级 `new_worse` 均为 0（「新版更差」0 篇，字段 `new_worse` 合计 0）。**

但不能宣称「全库已人工验证全面优于旧版」：仍有 17 篇「双错且不同」，各字段 unresolved 未清零（title 14, title_en 20, authors 27, affiliations 53, keywords 35, keywords_en 16），且没有全量人工真值。

分类计数：新版更优 709、一致 227、双错且不同 17、新版更差 0，合计 953。相对基线（710/224/16/3）为更优 -1、一致 +3、未决 +1、更差 -3。原因是严格化证据标准后，原被误判为「更优/更差」的文档改判为「一致/未决」（基线 3 篇「更差」不再判差，另 1 篇原「更优」因证据不足改列未决）；这是证据标准收紧，不是质量退化。

## 基线 vs 本轮对比

| 指标 | 返工前基线（备份） | 本轮重跑 |
|---|---:|---:|
| 文档总数 | 953 | 953 |
| 新版更优 | 710 | 709 |
| 一致 | 224 | 227 |
| 双错且不同 | 16 | 17 |
| 新版更差 | 3 | 0 |
| 字段 `new_worse` 合计 | 3 | 0 |
| 作者超集（自动判优） | 14 | 8 |
| `조록` 残留（新作者） | 6 篇 | 0 篇 |

基线来源：返工前备份（历史常量，非本轮数据）；本轮来源：`compare.py` / `audit.py` 输出，由 `report.py` 重读并复算。

## 修复内容（通用规则，无按 doc-id 特判）

- 作者区几何与结构证据：用版面框坐标限定署名区，名字须与星标、罗马名、角色和机构构成结构证据。
- 题名尾部署名切分：把题名末尾粘连的署名从题名中切出。
- 音译名切分：按署名结构折叠或切分音译名，避免误并、误拆。
- 机构异读、部门合并与学位残片折叠：归一化后合并部门层级并折叠学位等残片。
- 英题跨渠道投票与后部页图注排除：以 mineru/dots/paddle 投票裁定，排除后部页图注。
- 题名内联 ISSN 清理：移除题名中的内联 ISSN 片段。
- 判差对称化：新版非空但错误也可判 `new_worse`，旧版接受同样的署名结构检查。
- 人工覆盖审计保留 `manual_reason`：覆盖可改变 assessment，但理由始终保留。
- 跨语言机构覆盖：机构覆盖判定兼容韩文/英文异写。
- 访问/兼职机构子集判优：新值为旧值子集且仅去掉访问/兼职类次要机构时判优。

以上均为 `extract.py` / `compare.py` 中的通用规则。静态扫描（剔除字符串字面量）在比较/抽取逻辑中未发现按 doc-id 的分类特判；doc-id 仅作为 `fingerprint.py` 的锚点清单用于展示，不参与判定。

## 作者超集专项

规则判定的作者超集判优为 **8 篇**（`audit_sample.json` 记 8 篇）；固定随机种子 **20261127**，抽取 **1 篇**（1/8=12.5%）：13564501。

| doc | 旧作者 | 新作者 | 本次抽样 |
|---|---|---|---|
| 13564501 | 김형엽 | 조국, 김형엽 | 是 |
| 13567203 | 이창호 | 이창호, 울트사이크 비진쿠 | 否 |
| 13567372 | 이조은 | 이조은, 조아라 | 否 |
| 13567421 | 선봉규 | 선봉규, 압둘라에바 샤흘로 | 否 |
| 13567433 | 이영민 | 김미희, 이영민 | 否 |
| 13567462 | 임영언 | 임영언, 김태영 | 否 |
| 13569417 | 김상열 | 김상열, 권용욱, 김성균, 이준하 | 否 |
| 13569425 | 안혜원 | 이민철, 안혜원 | 否 |

`조록` 复算：遍历 `diff.jsonl` 全部 953 条记录，新作者 name 命中该 token 的文档为 **0 篇**（基线 6 篇），已从新作者中清零。上表逐条取自 `diff.jsonl`，不代表人工逐条验证。

## 全部剩余「新版更差」

共 **0 篇**。doc 级 category「新版更差」为 0，字段级 `new_worse` 合计为 0；无记录可列。

## 全部剩余双异（双错且不同）

共 **17 篇**。以下逐条取自 `diff.jsonl`（doc / 字段 / 旧→新）；完整渠道页码、框坐标与摘录见 `diff.jsonl`。

| doc | 字段 | 旧值 → 新值 |
|---|---|---|
| 13563533 | affiliations | affiliations: 글로컬대학으로서 강원대학교 특성화 전략과 과제 → 강원대학교 기획처장. 강원대학교 법학전문대학원 |
| 13563546 | affiliations | affiliations: 지방행정연구학회 → 강원대학교 국어국문학과 |
| 13563549 | title_en | title_en: An Analysis of the Editorial Process in Jeong Cheol's Poetry Collections : With a Fo… → An Analysis of the Editorial Process in Jeong Cheol's Poetry Collections : With a Fo… |
| 13563590 | keywords | keywords: K- antis비, 뛰는 K- antis비, 포스트 K- antis비, 타자, 공존 → K- 좀비, 뛰는 K- 좀비, 포스트 K- 좀비, 타자, 공존 |
| 13563600 | affiliations | affiliations: 현대영화연구소 → 국립부경대학교 국어국문학과 |
| 13567051 | title_en | title_en: Research Trends on Human Interaction Related to Media Use in Infants and Young Child… → Human Ecology Research』 55(1), 1-12. |
| 13567061 | keywords_en | keywords_en: Moon Jae-in government, defense policy, defense white paper, defense reform 2.0, MND) → Moon Jae-in government, defense policy, defense white paper, defense reform 2.0, COV… |
| 13567094 | title_en | title_en: None → A Study on the Educational Experience of Museum Theater Viewing : Focusing on ‘Lu Du… |
| 13567111 | title_en | title_en: None → A Case Study on Privately-Led Complex Cultural Spaces and Local Regeneration : Focus… |
| 13567139 | title_en | title_en: None → A Study on the Characteristics and Development Strategy of Arts Center in Urban and … |
| 13567236 | keywords_en | keywords_en: the Good Doctor Autism Spectrum Disorder (ASD), disability representation, diversity… → the Good Doctor, Autism Spectrum Disorder (ASD), disability representation, diversit… |
| 13567244 | title | title: 손은경 경북대학교 → # 이주와 공간 그리고 트랜스내셔널 서사 : 테브픽 바세르의 영화 <40평방미터 독일>을 중심으로 |
| 13567332 | keywords_en | keywords_en: Local Cultural Resources, Alleyways, neighborhood unit, cultural value, social value… → Local Cultural Resources, Alleyways, neighborhood unit, cultural value, social value… |
| 13568678 | affiliations | affiliations: 년 이전 윤석중 문학과 한국 구비 동요의 관계 → 서울대학교 국어국문학과 |
| 13569382 | affiliations | affiliations: Kyungpook National University, Department of Clothing and Textiles, Chungnam Nationa… → 충남대학교, 의류학과, 경북대학교 |
| 13569399 | affiliations | affiliations: Department of Business Administration, Changwon National University, Department of I… → 국립창원대학교 경영학과 생산관리전공, 경기대학교, 산업경영정보공학과 |
| 13569404 | affiliations | affiliations: Planning Department, Ammunition center → 국방기술품질원, 유도탄약센터, 연구원 |

## 锚点字段与判定（读者自行判断）

以下 8 篇锚点的字段值与 assessment 直接取自 `diff.jsonl`，本报告不作人工正确性断言。

| doc | 字段 | 旧值 | 新值 | assessment |
|---|---|---|---|---|
| 13569913 | title | 모바일 페이와 안면인식을 기반으로 한 유례없는 플랫폼 혁신으로 전 세계를 놀라게 했던 중국의 전자상거래 · 플랫폼 혁신은 COVID-19, 미 · 중 전략경쟁, 중국 경제성장 둔… | 중국의 전자상거래 기업의글로벌 진출 전략:알리, 테무, 쉬인, 틱톡샵을 중심으로 | new_better |
| 13569913 | title_en | None | Global Expansion Strategies of Chinese E-commerce Companies: Focusing on Alibaba, Temu, Shein, and … | new_better |
| 13569913 | authors | 테무, 쉬인, 틱톡샵을, 백서인당정, 알리, 틱독삼을 | 백서인, 당정 | new_better |
| 13569913 | affiliations | ” 대외경제정책연구원 『연구자료』 | 한양대학교 ERICA 글로벌문화통상학부, 한양대학교 중국지역통상학과 | different_unresolved |
| 13569913 | keywords | 전자상거래, 알리, 테무, 쉬인, 틱톡샵 | 전자상거래, 알리, 테무, 쉬인, 틱톡샵 | — |
| 13569913 | keywords_en | E-commerce, Alibaba, Temu, SHEIN, TikTok Shop | E-commerce, Alibaba, Temu, SHEIN, TikTok Shop | — |
| 13567494 | title | K콘텐츠에 대한 분석 : K영화, 중국에서의 발전을 중심으로 | K콘텐츠에 대한 분석 :K영화, 중국에서의 발전을 중심으로 | — |
| 13567494 | title_en | None | Analysis of K Content : Based on the Development and Prospect of K-Movie in China | new_better |
| 13567494 | authors | 리우 | 리우 커 | different_unresolved |
| 13567494 | affiliations | 리우 커 대구가톨릭대학교 | 대구가톨릭대학교 | new_better |
| 13567494 | keywords | K영화, 한류, 흥행, 심사제도리우 커 대구가톨릭대학교 | K영화, 한류, 흥행, 심사제도리우 커 대구가톨릭대학교 | — |
| 13567494 | keywords_en | K-movie, Hallyu, Korean Wave, Box Office, Censorship LIU KE (Daegu Catholic University) | K-movie, Hallyu, Korean Wave, Box Office, Censorship LIU KE (Daegu Catholic University) | — |
| 13568651 | title | 터키어로 번역된 한국문학 작품 현황 -어린이 문학을 중심으로- | # 터키어로 번역된 한국문학 작품 현황 -어린이 문학을 중심으로- | — |
| 13568651 | title_en | None | Current Status of Korean Literature Translated into Turkish - Focusing on Children’s Literature - | new_better |
| 13568651 | authors | 타이푼 | 타이푼, 카르타브 | different_unresolved |
| 13568651 | affiliations | 튀르키예 에르시예스대학교 | 에르시예스대학교 | — |
| 13568651 | keywords | 가, 출판인 | 튀르키예(터어키), 한·터 양국관계, 한·터 직역(direct translation), 이중 번역, 번역 | different_unresolved |
| 13568651 | keywords_en |  |  | — |
| 13569417 | title | 거리별 유효 빔폭 임계치 알고리즘 적용을 통한 레이다 근거리 해상표적 분리현상 해소에 대한 연구 ISSN 2671-4744 (Print) ISSN 2671-9673 (Online) | 거리별 유효 빔폭 임계치 알고리즘 적용을 통한 레이다 근거리 해상표적 분리현상 해소에 대한 연구 | new_better |
| 13569417 | title_en | Resolving Near-Range Maritime Target Splitting Using Distance-Specific Effective Beam and Threshold… | Resolving Near-Range Maritime Target Splitting Using Distance-Specific Effective Beam and Threshold… | — |
| 13569417 | authors | 김상열 | 김상열, 권용욱, 김성균, 이준하 | new_better |
| 13569417 | affiliations |  | LIG 넥스원, 레이다기술팀 | new_better |
| 13569417 | keywords | Parabolic Antenna, Target Split, Effective Beamwidth, N ear Target Detect, Look Angle | Parabolic Antenna, Target Split, Effective Beamwidth, N ear Target Detect, Look Angle | — |
| 13569417 | keywords_en | Parabolic Antenna, Target Split, Effective Beamwidth, N ear Target Detect, Look Angle | Parabolic Antenna, Target Split, Effective Beamwidth, N ear Target Detect, Look Angle | — |
| 13569428 | title | 고정형 레이다의 운용주파수 가용성 확대 및 위치기반 재밍 회피 최적화 | 고정형 레이다의 운용주파수 가용성 확대 및위치기반 재밍 회피 최적화 | — |
| 13569428 | title_en | Optimization of Operational Frequency Availability and Location-Based Jamming Avoidance of Fixed Ra… | Optimization of Operational Frequency Availability and Location-Based Jamming Avoidance of Fixed Ra… | — |
| 13569428 | authors | Radar Production, 권용욱, 김성균, 이준하, 김홍준, 김상열 | 권용욱, 김성균, 김상열, 이준하, 김홍준 | new_better |
| 13569428 | affiliations | Department of Electric Engineering, Kyungpook National University 김상열 | 경북대학교, 전기공학과, LIG넥스원, 레이다기술팀 | new_better |
| 13569428 | keywords | Radar Interference, Interference Prediction, Jamming Avoidance, Operational Frequency Availability | Radar Interference, Interference Prediction, Jamming Avoidance, Operational Frequency Availability | — |
| 13569428 | keywords_en | Radar Interference, Interference Prediction, Jamming Avoidance, Operational Frequency Availability | Radar Interference, Interference Prediction, Jamming Avoidance, Operational Frequency Availability | — |
| 13564500 | title | 챗봇과 생성형 AI 활용 영어교육 | 챗봇과 생성형 AI 활용 영어교육 | — |
| 13564500 | title_en | AI-powered English education with chatbots and generative AIs | AI-powered English education with chatbots and generative AIs | — |
| 13564500 | authors | 심창용, 황필아, 남보라 | 심창용, 황필아, 남보라 | — |
| 13564500 | affiliations |  | 경인교육대학교, 1(경인교육대학교)황필아2(경인교육대학교)남보라3(경인교육대학교 | new_better |
| 13564500 | keywords | AI 활용 영어교육, 챗봇, 생성형 AI, AI 디지털교과서, 특수 목적 영어 | AI 활용 영어교육, 챗봇, 생성형 AI, AI 디지털교과서, 특수 목적 영어 | — |
| 13564500 | keywords_en | AI-powered English education, chatbot, generative chatbot, generative AI, AI digital textbook, ESP | AI-powered English education, chatbot, generative chatbot, generative AI, AI digital textbook, ESP | — |
| 13564501 | title | 모국어에 기반한 음운인식이 파닉스 학습에 미치는 영향 | 모국어에 기반한 음운인식이 파닉스 학습에 미치는 영향¹ | new_better |
| 13564501 | title_en | Enhancing in reading skills in phonics’ acquisition method by the use of native language basis | Enhancing in reading skills in phonics’ acquisition method by the use of native language basis | — |
| 13564501 | authors | 김형엽 | 조국, 김형엽 | new_better |
| 13564501 | affiliations |  | 고려대학교, 2 제1저자. 고려대학교 아동코칭학과, 3 교신저자. 고려대학교 글로벌대학 영미학, 조 국2(고려대학교)김형엽3(고려대학교 | new_better |
| 13564501 | keywords | 읽기학습, 음소인식, 음운인식, 파닉스, 연음, 특수목적영어 | 읽기학습, 음소인식, 음운인식, 파닉스, 연음, 특수목적영어 | — |
| 13564501 | keywords_en | L1 to L2 reading ability, phonetic awareness, phonological awareness, phonics, prolonged, ESP L1 to… | L1 to L2 reading ability, phonetic awareness, phonological awareness, phonics, prolonged, ESP L1 to… | — |
| 13569720 | title | 1.1. 연구의 필요성 | 대학생의 신체이미지와 긍정심리자본 | new_better |
| 13569720 | title_en | None | Body Image and Positive Psychological Capital Among University Students | new_better |
| 13569720 | authors | 김지영, 류현숙 | 김지영, 류현숙 | — |
| 13569720 | affiliations | 한국휴먼이미지디자인학회 제7권 제2호 | 중원대학교, 대진대학교 | different_unresolved |
| 13569720 | keywords | 대학생, 신체이미지, 외모지향성, 외모평가, 긍정심리자본 | 대학생, 신체이미지, 외모지향성, 외모평가, 긍정심리자본 | — |
| 13569720 | keywords_en | Body Image, Appearance Orientation, Appearance Evaluation, Positive psychological capital, Universi… | Body Image, Appearance Orientation, Appearance Evaluation, Positive psychological capital, Universi… | — |

## 字段计数（comparison_summary.json）

| 字段 | same | new_better | new_worse | different_unresolved |
|---|---:|---:|---:|---:|
| title | 787 | 152 | 0 | 14 |
| title_en | 540 | 393 | 0 | 20 |
| authors | 868 | 58 | 0 | 27 |
| affiliations | 588 | 312 | 0 | 53 |
| keywords | 900 | 18 | 0 | 35 |
| keywords_en | 750 | 187 | 0 | 16 |

各字段 `different_unresolved` 均未清零；这些计数是规则判定台账，不是准确率。

## 复现命令

```bash
python layoutx/fingerprint.py
python layoutx/extract.py
python layoutx/compare.py
python layoutx/audit.py
python layoutx/report.py
```

`fingerprint.py` 生成 `clusters.json` / `fingerprints.jsonl`，`extract.py` 依赖这两个文件。

## 范围与安全声明

- 代码在 `pipeline/layoutx/`；`compare.py` 导入 `pipeline/metapy.front`（`from metapy import front` 的内部纯函数）并读取 `pipeline/out/`。
- 元数据输出在 `pipeline/out_layout/`；`diff.jsonl`、`comparison_summary.json`、`audit_sample.json`、`REPORT.md`、`clusters.json`、`fingerprints.jsonl`、`VERIFICATION.md` 均在 `pipeline/layoutx/`。
- 静态扫描（剔除字符串字面量）未发现 `front_doc()` 调用、网络或 LLM 调用：命中 0 处。
- mtime 自查（运行时计算）：`metapy/` 最新文件 2026-11-27 17:38:19（metapy/__pycache__/issue.cpython-315.pyc）；`out/` 最新文件 2026-11-27 09:31:23（out/13569913/metadata_audit.json）。本原型只写 `out_layout/` 与 `layoutx/` 产物，未修改 `metapy/` 与 `out/` 中的数据文件；导入 `metapy.front` 时 Python 可能在 `metapy/__pycache__/` 生成字节码缓存，这不算源码或数据改动。

## 解释边界

无全量人工真值，也无随机样本误差估计；以上数字仅作规则判定工作台账，不用于外推准确率。
