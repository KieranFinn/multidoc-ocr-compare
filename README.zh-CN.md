# multidoc-ocr-compare

**[English](README.md) · [中文](README.zh-CN.md)**

> 这是 **中文** 说明。英文版见 [README.md](README.md)。

把**多个 OCR / 文档解析引擎跑在同一份 PDF 上**，把每一块文本放进同一个坐标系，然后逐页、逐块地并排看——底下垫着原始扫描图。

它是为扫描版学术 PDF（韩 / 中 / 英）写的：这类文档单引擎输出*看着*没问题，却在跨页时悄悄自相矛盾。本项目要回答的不是"谁得分高"，而是**归因**——三个引擎在某页对不上时，到底是文档本身怪，还是某个引擎坏了。

```
 PDF --> 渲染 150 dpi 页图 --> 取三渠道产物（缓存 -> API -> 桌面版兜底）
                                     |
                                     v
                           适配为 {page: [{cat, box, text}]}
                                     |
                     +---------------+----------------+
                     v                                v
        单渠道查看器 + 1:3 对照查看器             merged/ + 指标
```

---

## 我该从哪儿开始？

挑一行和你目的相符的即可，**每行都自成一体**：

| 我想…… | 怎么做 | 需要账号吗 |
| --- | --- | --- |
| **看看产物长什么样** | clone 后直接打开 [`examples/13625974/index.html`](examples/13625974/)，或点下面的在线渲染链接 | 不需要 |
| **确认它在我机器上能跑** | [离线快速开始](#离线快速开始无账号无网络)：合成 PDF + 三渠道 mock 产物 | 不需要 |
| **跑我自己的 PDF** | [跑你自己的 PDF](#跑你自己的-pdf)，只开一个引擎也能起步 | 只对你启用的引擎需要 |
| **要结构化字段**（题目 / 作者 / 参考文献……） | [结构化元数据](#结构化元数据可选在-unify-之后运行) | 不需要（规则优先，LLM 可选） |

**没有任何一步是"必须先把上一步跑通"的前置**：可以零凭据起步，一个一个渠道加，产物够用就停。

## 在线实例

仓库里放了一份真实跑出来的产物——[`examples/13625974/`](examples/13625974/)：一篇 11 页的韩文期刊论文（창의정보문화연구 2023，DOI `10.32823/jcic.9.4.202311.347`），三个引擎全部跑过。

| 查看 | 在线渲染 | 源文件 |
| --- | --- | --- |
| 总览 + 指标 | [打开渲染页](https://kieranfinn.github.io/multidoc-ocr-compare/examples/13625974/index.html) | [index.html](https://github.com/KieranFinn/multidoc-ocr-compare/blob/main/examples/13625974/index.html) |
| 1:3 对照查看器（四栏，悬停联动框与文本） | [打开渲染页](https://kieranfinn.github.io/multidoc-ocr-compare/examples/13625974/viewer_compare.html) | [viewer_compare.html](https://github.com/KieranFinn/multidoc-ocr-compare/blob/main/examples/13625974/viewer_compare.html) |
| 单渠道查看器 | [MinerU](https://kieranfinn.github.io/multidoc-ocr-compare/examples/13625974/viewer_mineru.html) · [dots.ocr](https://kieranfinn.github.io/multidoc-ocr-compare/examples/13625974/viewer_dots.html) · [PaddleOCR-VL](https://kieranfinn.github.io/multidoc-ocr-compare/examples/13625974/viewer_paddle.html) | [viewer_mineru.html](https://github.com/KieranFinn/multidoc-ocr-compare/blob/main/examples/13625974/viewer_mineru.html) |
| 合并文本 | — | [merged.md](https://github.com/KieranFinn/multidoc-ocr-compare/blob/main/examples/13625974/merged.md) |

> 为什么要多一列：GitHub 是**把 `.html` 当源码显示**的，所以单纯的文件链接永远看不到查看器。那一列指向同一个目录的静态站点。如果还没生效，去 Settings → Pages → Deploy from a branch → `main` / `/` 打开一次；或者干脆 clone 下来本地打开。

这份 11 页的跑批说明了什么：

| 渠道 | 块数 | 韩文字符 | U+FFFD |
| --- | ---: | ---: | ---: |
| MinerU（batch API） | 200 | 8,929 | 0 |
| dots.ocr（Gradio 5） | 196 | 9,024 | 0 |
| PaddleOCR-VL-1.6（云端任务） | 214 | 9,032 | 0 |

三渠道文本捕获量相差在 **1.2%** 以内，块数却差约 9%：**切分粒度不是文本质量**。unify 阶段把 610 个原始块归并为 176 个逻辑组，`one_to_one` 0.898、`coverage` 1.0、`orphan_rate` 0.0，并把每一处分歧归因为"文档自身"（三个引擎在同处一致地怪）或"单引擎"。

> 想在本地看：clone 后打开 `examples/13625974/index.html`，或者 `python -m http.server -d examples/13625974`。

## 产物一览

| 产物 | 是什么 |
| --- | --- |
| `out/<doc>/index.html` | 总览：页数、块数、CJK 字符数、U+FFFD 数、各渠道素材来源 |
| `viewer_<channel>.html` | 单引擎：页图 + 它的框，悬停/点击联动框与文本 |
| `viewer_compare.html` | 1:3 视图：每页四栏（原图 + 三渠道），渠道分色，悬停才显框；同一句的重复引用归成一行，列出全部高亮与文献链接；文献行/脚注行悬停会尽量选中对应的成员块；引用行悬停用它的 unified 块；两者都带原始页局部裁图；点元数据行（总览或页内条带）会滚到该页并在原扫描图上闪烁对应源块 |
| `merged/`（+`merged.md`） | 逐页合并块 + provenance，由 `merge_out.py` 产出 |
| `unified/` | 跨引擎归并组 + `_unified_summary.json`（coverage、one_to_one、orphan_rate），由 `unify.py` 产出 |
| `metadata.json` + `_metadata_report.html` | 结构化字段（题目、作者、单位、摘要、关键词、参考文献条目），每字段带置信度与页锚点，由 `metadata.py` 产出 |
| `out_llm_trial/fds_extract/ner_lite/<doc>/` | 实体抽取（基金 / 软件 / 数据集）产物，由 `ner_lite.py` 写在 `out/` **之外**：`ner_lite.json`、`ner_lite_audit.json`、`_audit.html`；存在时会被 `viewer_compare.html` 读成一列 |
| `metadata_audit.json` | 同一批阶段写出的诊断副文件（sidecar）：每字段的 basis/review 备注、参考文献样式、L1 校验（`ref_validation`）。它**不在** `metadata.json` 里，要读校验数据必须读这个文件 |

## 离线快速开始（无账号、无网络）

```bash
pip install -r requirements.txt
python _attic/examples/make_demo.py                    # 合成 PDF + 三渠道 mock 产物，生成到 _attic/examples/demo/
python pipeline.py _attic/examples/demo/samples/demo_kr.pdf \
    --mineru _attic/examples/demo/inbox/demo_kr__mineru \
    --dots   _attic/examples/demo/inbox/demo_kr__dots \
    --paddle _attic/examples/demo/inbox/demo_kr__paddle \
    --channels _attic/examples/demo/channels.json \
    --out _attic/examples/demo/out/demo_kr
python -W error::ResourceWarning -m unittest -v test_pipeline.py
python -m http.server -d _attic/examples/demo/out/demo_kr  # 然后打开 /index.html
```

`--channels` 把整份配置换成 `_attic/examples/demo/channels.json`：MinerU 声明真实的 521×688 页面像素空间，dots/Paddle 用归一化坐标，所有云渠道关闭。合成文档里刻意放了异常读数、空块和一个只在 Paddle 出现的页码（一个故意留下的孤儿块）。CI 在没有 token、没有网络的情况下跑这条链路。

> 单元测试守的是 unified 新旧判定 / 数据保全契约，以及查看器的引用与元数据渲染；它们**不**覆盖元数据启发式（文献切分、作者区、关键词面板）。后者靠和仓库里这份实例做 diff 来验证，见[状态与已知限制](#状态与已知限制)。

## 跑你自己的 PDF

### 第 0 步 —— 只开一个引擎能跑吗？

**能。** 下面每一步都是按渠道独立的：只开一个渠道，你会得到 `viewer_<channel>.html` 和总览；1:3 对照和 `merged/` 需要两个以上渠道；`unified/`（元数据层要读它）要求各引擎至少在某些内容上达成一致。没提供的渠道会在报告里标成 "not provided"，而不是让整次运行失败。

### 第 1 步 —— 只挑你手上真有的引擎

| 渠道 | 需要什么 | 账号 / 成本 | 备注 |
| --- | --- | --- | --- |
| **dots.ocr** | 一个 Gradio 5 端点，默认用小红书公共实例 `https://dotsocr.xiaohongshu.com` | **不需要——没有 token 字段** | 拿到第一次真实运行最快的路；只有自建时才需要改 `dots_api.base` |
| **PaddleOCR-VL** | `channels.local.json` 里的 `token` | AI Studio 服务（`paddleocr.aistudio-app.com`）申请的 key | 异步任务，归一化坐标，免标定 |
| **MinerU** | `channels.local.json` 里的 `token` | mineru.net 申请的 key | batch 签名上传；兜底走 CDP 驱动桌面版（仅 Windows） |

```bash
cp channels.local.example.json channels.local.json   # 只填你有的 token，该文件已被 gitignore
python pipeline.py mypaper.pdf                       # 用上所有有 token（或有缓存 / inbox 产物）的渠道
python webapp.py                                     # 或者开拖拽工作台：127.0.0.1:8765
python gui.py                                        # 桌面工作台（tkinter，零依赖，Windows 可双击 启动GUI.bat）
```

没有 token、没有缓存、`inbox/` 里也没产物的渠道会被**直接跳过**，不需要显式禁用。先只开 `dots`（它不要 token）是拿到第一次真实运行的最短路径；之后补上 `paddle` / `mineru` 的 token，下一篇文档就会自动用上。

每个渠道的完整请求流程（端点、轮询、各种坑）见 [docs/channels.md](docs/channels.md)；标定见 [docs/calibration.md](docs/calibration.md)。

### 第 2 步 —— 或者直接喂你手头已有的引擎产物

如果你已经在别处跑过某个引擎（它的网页界面、上一次批处理、同事导出的结果），**完全不需要 API key**：把产物丢进 `inbox/`，管线就会用它而不是去调接口。

```
inbox/<doc>__<channel>/     <doc> = PDF 文件名去掉 .pdf
```

| 渠道 | 目录里放什么 |
| --- | --- |
| `<doc>__mineru` | MinerU 那次运行产出的目录，里面要有 `*_content_list_v2.json`（如果有 `layout.json` 也一起放——它用来区分 OCR / 非 OCR 运行） |
| `<doc>__dots` | 逐页 `page_0.json`、`page_1.json`……（块列表，每块带 `bbox` 和文本） |
| `<doc>__paddle` | 同上逐页 `page_N.json`，**或者**一份 Paddle 的整篇 JSON 导出 `*.json` |

匹配是**严格按文档名**的：目录必须以 `<doc>` 开头，打错字的结果是"没找到这个渠道"，而不是"拿最像的那份凑合"（早期的跨文档兜底会把一份文档的块画到另一份上）。不带后缀标记时管线还会按内容猜（`*_content_list_v2.json` → mineru，`page_N.json` → dots），但显式写后缀永远更稳。

### 第 3 步 —— 看结果

```
out/<doc>/index.html            从这里开始
out/<doc>/viewer_compare.html   1:3 对照
```

## 测试

```bash
pip install -r requirements.txt      # test_pipeline 会 import pipeline -> pipeline_io -> pypdfium2/PIL
python -W error::ResourceWarning -m unittest -v test_pipeline.py
python ner_lite.py selfcheck         # 离线：7 个合成块 + mock 模型，不联网
```

10 个用例，不联网、不需要 token。它们钉住三件事：改动合并层时最容易踩坑的契约（"过期的 `unified/` 留在盘上但绝不画进新查看器"）、查看器的引用 / 元数据渲染，以及实体定位的不变量（折叠坐标映射、右向外延、跨块 segments、著录脚注批注）。`ner_lite.py selfcheck` 单独覆盖实体闸门自身的用例。CI 在 Python 3.10 和 3.12 上同时跑单测和 `selfcheck`。

## 配置

三层，后面的覆盖前面的（按段浅合并）：

1. `channels.json` —— 提交在仓库里的基线：标定、模型名、超时、开关。**不含 token。**
2. `channels.local.json` —— 你的私有覆盖（已 gitignore）。只写你要改的键。
3. 环境变量 `MINERU_API_TOKEN` / `PADDLE_API_TOKEN` —— 覆盖这两个 token（在 CI 里方便）。

`python pipeline.py --channels <file>` 会**整体替换**基线并且**不再读第 2、3 层**，所以离线 / demo 运行不可能顺走你机器上的真实 token。每次调用都重新读合并后的配置，改标定或改 token 下一篇文档就生效，不用重启。细节见 [docs/channels.md](docs/channels.md)。

## 设计笔记（花了最久的部分）

- **一个坐标空间，而且写在配置里。** 每个引擎用自己的一套框坐标，有时还是各向异性的。`src_w / src_h / x_off / y_off` 这四个数是数据不是代码，每次调用都重读，所以重标定不用重启服务。配方见 [docs/calibration.md](docs/calibration.md)。
- **标定键跟着数据一起落盘。** 同一个 `mineru` 目录可能来自桌面版，也可能来自云端 API（几何差约 1.6%）。一个标记文件记下是哪一种，这样查看器与合并器永远不会对同一批字节各说各话。
- **缓存优先，其次 API，最后桌面版。** 重跑一份文档是免费的；想强制重新拉取，删掉一个缓存目录即可（`out/<doc>/raw/<channel>_api`）。
- **上游的怪就是上游的怪。** 被截断的页 JSON、双重编码的负载、引擎版本间改过的键名：适配层防御性解析并把情况**写进日志**，而不是默默产出空块。
- **U+FFFD 是指标，不是打扫卫生。** 按渠道计数并显示在总览里，因为它是没有真值（ground truth）时最便宜的文本完整性信号。

## 结构化元数据（可选，在 unify 之后运行）

上面的对照链路告诉你**谁读到了什么**。`metadata.py` 把留下来的证据变成**可引用的字段**——题目（韩文 + 英文）、作者、单位、摘要、双语关键词（`keywords` 韩文 / `keywords_en` 英文，各取页序中第一个匹配到的面板），以及参考文献条目——每项都带置信度和一个能跳回 1:3 查看器的页锚点。

```bash
python unify.py <doc>                      # 产出 out/<doc>/unified/（pipeline.py 自己就会跑；只有已有 merged/ 数据时才需要单独跑）
python metadata.py <doc> --stage m3        # 跨页拼接：把被换页切断的段落接回来
python metadata.py <doc> --stage refs      # 参考文献条目 -> {authors, year, title, vol, no, pages} 列表
python metadata.py <doc> --stage frefs     # 所有编了号的脚注（文献与注释）-> footnote_refs
python metadata.py <doc> --stage front     # 题目 / 作者 / 单位（韩文 + 罗马化）
python metadata.py <doc> --stage abs       # 韩文 + 英文摘要
python metadata.py <doc> --stage issue     # 刊名 / 年 / 卷 / 期 / 页 + 关键词
python metadata.py <doc> --stage cspan     # 正文中的引用句
python metadata.py <doc> --stage report    # -> _metadata_report.html
```

规则优先，模型其次。每个字段都由确定性启发式解析（按版面类别切分的韩式五段引用与 `In:` 书章引用、上标集合匹配作者脚注、逐条目判断 `[n]` / `N.` / 作者-年份样式）。产品字段带 `layout`（引文形式类别）、`container_title` / `publisher` / `editor`（CSL 命名；`source` 保留原始容器字符串以向后兼容——下游应把 `container_title` 当容器读，而不是 `source`）。可选的 LLM 阶段（`--stage llm`）只**询问正则失败或低分的字段**，绝不改写高置信度的值。它默认关闭（`channels.json` 里 `llm.enabled: false`），密钥从环境变量 `KCI_LLM_KEY` 读取——任何机密都不写盘。

```bash
python metadata.py <doc> --viewer          # 用已有的 merged/ + unified/ 重画 viewer_compare.html
```

> `--stage m3` 会回写 `unified/page_N.json`；重跑 unify 会把这些标记冲掉。顺序是：pipeline（它已经跑过 unify）→ metadata（m3 → refs → frefs → front → abs → issue → cspan → report）。如果 unify 失败或被禁用，旧的 unified 数据会留在盘上，但不会画进新查看器。`--viewer` 只重画。

## 实体抽取（`ner_lite.py`，可选，在 unify 之后运行）

上面的阶段恢复的是**书目字段**。`ner_lite.py` 恢复的是引用解析器不管的三类实体——**基金**（资助机构 / 项目 / 课题号）、**软件**、**数据集**——契约比常规 NER 管线窄得多：

```bash
# 端点/模型写进 channels.json 的 llm 段，密钥放环境变量 KCI_LLM_KEY（不落盘）
python ner_lite.py selfcheck               # 离线闸门自检，不联网
python ner_lite.py 13625974                # 单篇
python ner_lite.py all                     # out/ 下所有带 unified/ 的文档
```

| 阶段 | 做什么 | 为什么 |
| --- | --- | --- |
| 提名 | 一句 prompt 覆盖三类开放词表，模型只回 `[{kind, name}]` | 不要 `uid`、不要关系、不要二次复核：模型写出的任何东西都不被当成坐标 |
| 定位 | 名称折叠后（忽略空白/BOM/NFC）必须在该批文本里出现，否则该提名**被丢弃**（`anchor_miss`） | 模型可以编名字，但编不出「原文里出现」 |
| 产出 | 值一律是**原文切片**，在整篇上枚举（跨块给 `segments`，取重叠更紧的块） | 每条都能用一句 `slice in block_text` 验 |
| 批注 | 右向字面外延（版本号 / 括号课题号）标 `extended`；`multi_block`、`multi_occurrence`、`cross_block`、`mention_in_biblio_footnote` | 每个标记对应审阅者本来要手工问的一个问题 |

这个闸门只结构性保证**一件事**：这条记录说的位置上确实出现了这个字符串。它**不**保证实体「属于那句话」（截断的名字只要是邻近读法的子串就照样通过），而**被丢掉的提名在产物里看不见**——引用计数前，先读 `ner_lite_audit.json` 里的 `dropped_anchor_miss` / `dropped_parse_fail` / `errors_network`。分批约 8,000 字符一次，切批不拆块；跨批缝的出现照样能定位，因为定位是**全篇搜索**，不是批内搜索。

产物写在 `out_llm_trial/fds_extract/ner_lite/<doc>/`（`ner_lite.json`、`ner_lite_audit.json`、`_audit.html`）。`viewer_compare.html` 在文件存在时多画一列，不存在时安静地不画。

## 状态与已知限制

- 研究与工程工具，不是托管服务。三个云渠道都是第三方服务：端点和请求格式随时可能变，它们的条款约束的是你。
- `dots_api.base` 默认指向一个社区公共实例；万一它没了，请指向你自己的 Gradio 5 部署（或者用 `inbox/` 喂已有产物）。
- **准确率并不均匀。** 文献条目与前置信息（题目/作者区）的启发式是在 13 篇韩中期刊语料上调的，并用仓库里这份实例做 diff 验证。已知弱点（都能在该语料上复现）：年份前只有句点的引文样式（"Author. 1999. Title."）、全汉字作者行、`N.` 式脚注标记、文献表最后一页与另一栏摘要同页、以及摘要没有可识别引导词时的题目/作者区越界。把 `metadata.json` 当高精度草稿用：引用前请检查每个字段的置信度（并对照 `viewer_compare.html`）。
- **实体抽取是召回受限，不是精度受限。** 在 13 篇语料上，`ner_lite.py` 产出 57 条记录（数据集 35 / 软件 15 / 基金 7），61 条提名全部通过定位闸门，网络与解析零失败；但对人工核过的 `verified.json` 的召回并不齐：软件 6/7、基金 3/4（漏的是被拆成两条——机构与项目各成一条）、数据集 0/0（35 条全是误报，多为含「史」「報告書」的旧书与国际法院报告题名）。这 35 条仍然**每条都可验**——每个 `slice` 都逐字在原文里——这正是「定位到」和「抽对了」的区别。
- 只有 MinerU 的桌面版兜底是 Windows 专属；`pipeline.py`、`webapp.py`、`metadata.py` 和各查看器都是跨平台的。`start.bat` / `start.sh` 只是便捷包装。
- 不包含在线服务测试（需要凭据和额度）。CI 会编译每个模块、跑单元测试、`ner_lite.py selfcheck` 和离线 demo；CI 不调用 LLM。

## 仓库结构

```
pipeline.py        主编排 + 兼容性 re-export
pipeline_io.py     渲染 -> 抓取 -> 归档 -> 标定 -> 适配
viewer.py          单渠道 / 对照查看器 + md2html + 元数据面板
webapp.py app.js dashboard.css   浏览器拖拽工作台（任务总览 + 管道/渠道状态 + 渠道配置面板）
gui.py 启动GUI.bat  桌面工作台（tkinter，零依赖）：状态从 out/ 派生、历史追加到 gui_runs.jsonl
merge_out.py       跨渠道逐页合并
unify.py           跨引擎归并 -> unified/ + 汇总指标（已冻结）
metadata.py        结构化元数据 CLI（11 行壳；实现在 metapy/）
ner_lite.py        实体抽取（基金 / 软件 / 数据集）：LLM 提名，代码定位
regress.py         参考文献质量检查（截断、作者、页拼接）
test_pipeline.py   unified 新旧判定、数据保全与查看器回归测试
mineru_drive.py    MinerU 桌面版的 CDP 驱动（兜底）
requirements.txt   渲染 / CDP 运行时依赖
channels.json      标定 + 模型默认值（无机密）
channels.local.example.json   复制为 channels.local.json 后填自己的 token
docs/              channels.md · calibration.md · architecture.md · REVIEW.md · RULES.md
examples/          13625974/（一份真实跑批产物，首页链接）
uploads/ inbox/    输入 PDF + 缓存/外部渠道产物（已 gitignore）
out/               管线产物；out_llm_trial/ 放实体抽取产物（已 gitignore）
metapy/            __init__（CLI）· common / m3(frozen) / issue / cspan / refs / front / abs /
                   llmrep / dual
_attic/            归档代码：analyze.py · caption_audit.py · examples/（make_demo.py 与
                   CI 用的合成离线 demo）· docs/ 旧副本
```

## 许可证

MIT —— 见 [LICENSE](LICENSE)。仓库里那份产物（`examples/13625974/`）含一篇已发表的韩文期刊论文，仅用于展示输出格式；著作权仍归作者与期刊。请只处理你有权处理的文件。
