# multidoc-ocr-compare

**[中文](README.zh-CN.md) · [English](README.md)**

> 这是中英双语 README 的**中文版**。英文版见 [README.md](README.md)。

把**同一个 PDF 丢给多个 OCR / 文档解析引擎**, 再把所有块换算到同一坐标系里逐页对照 —— 原图在下,
三家引擎的框和文本在上, 一眼看出"这块到底是谁错了"。

面向扫描版学术 PDF(韩/中/英)。目的不是给引擎打分, 而是**归因**: 三个引擎在同一页产生分歧时,
是文档本身怪, 还是某个引擎坏了?

```
 PDF --> 渲染 150 dpi 页图 --> 三渠道并发获取 (缓存 -> API -> 桌面版兜底)
                                     |
                                     v
                          统一成 {页: [{类别, 框, 文本}]}
                                     |
                     +---------------+----------------+
                     v                                v
         单渠道查看器 + 1:3 对照             merged/ + 指标
```

## 在线示例

仓库里放了一份**真实跑出来的**产物 —— [`examples/13625974/`](examples/13625974/): 一篇 11 页的韩文期刊论文
(창의정보문화연구 2023, DOI `10.32823/jcic.9.4.202311.347`), 三个引擎全部跑过。

| 查看 | 链接 |
| --- | --- |
| 总览 + 指标 | [examples/13625974/index.html](examples/13625974/index.html) |
| 1:3 对照查看器(四栏, 悬停联动框与文本) | [viewer_compare.html](examples/13625974/viewer_compare.html) |
| 单渠道查看器 | [MinerU](examples/13625974/viewer_mineru.html) · [dots.ocr](examples/13625974/viewer_dots.html) · [PaddleOCR-VL](examples/13625974/viewer_paddle.html) |
| 合并文本 | [merged.md](examples/13625974/merged.md) |

这一份跑出来是什么样:

| 渠道 | 块数 | 韩文字符 | U+FFFD |
| --- | ---: | ---: | ---: |
| MinerU(batch API) | 200 | 8,929 | 0 |
| dots.ocr(Gradio 5) | 196 | 9,024 | 0 |
| PaddleOCR-VL-1.6(云端 job) | 214 | 9,032 | 0 |

三家字符捕获量差 **1.2%**, 块数却差约 9% —— **切分粒度不等于文本质量**。unified 对齐给出:
610 个原始块 → 176 个逻辑块, `one_to_one` 0.898, `coverage` 1.0, `orphan_rate` 0.0;
每一处分歧都归因到"文档本身"(三家都认为这里怪)或"某一渠道"(只有一家错)。

> GitHub 只会把 HTML 当源码显示。要看可点的查看器, 请克隆仓库后用浏览器打开
> `examples/13625974/index.html`, 或起个静态服务: `python -m http.server -d examples/13625974`。

## 产出

| 产物 | 说明 |
| --- | --- |
| `out/<文档>/index.html` | 总览: 页数/块数/韩文字符/U+FFFD/各渠道产物来源 |
| `viewer_<渠道>.html` | 单渠道: 页图 + 块框, 悬停/点击联动文本 |
| `viewer_compare.html` | 1:3 对照: 每页四栏(原图 + 三渠道), 渠道分色, 悬停显现框 |
| `merged/` + `merged.md` | 逐页合并 + 每块来源(merge_out.py) |
| `unified/` | 跨引擎归并组 + `_unified_summary.json`(coverage / one_to_one / orphan_rate), 由 `unify.py` 产出 |
| `_analysis_report.html` / `_analysis_summary.csv` | 跨文档指标与归因(`analyze.py`, 单独运行) |

## 离线跑通(不需要账号, 不联网)

```bash
pip install -r requirements.txt
python examples/make_demo.py            # 生成 examples/demo/: 合成 PDF + 三份 mock 引擎产物
python pipeline.py examples/demo/samples/demo_kr.pdf \
    --mineru examples/demo/inbox/demo_kr__mineru \
    --dots   examples/demo/inbox/demo_kr__dots \
    --paddle examples/demo/inbox/demo_kr__paddle \
    --channels examples/demo/channels.json \
    --out examples/demo/out
python -m http.server -d examples/demo/out/demo_kr   # 然后打开 /index.html
```

`--channels` 把整份配置换成 `examples/demo/channels.json`: 标定是 **identity**(`src_w/src_h: null`,
引擎坐标即页图像素), 三个云端渠道全部禁用。demo 文档是合成的韩文, 刻意埋了三处分歧(离群读法、
空块、缺页码), 让查看器与归一化层都有东西可看。不含第三方语料、不需要 token、不联网。CI 跑的就是这条命令。

## 跑自己的文档

```bash
cp channels.local.example.json channels.local.json   # 填 token; 该文件不入库
python pipeline.py mypaper.pdf
python webapp.py                                      # 或: 拖拽式工作台 127.0.0.1:8765
```

配置分三层(`channels.json` → `channels.local.json` → 环境变量), 详见
[docs/channels.md](docs/channels.md); 标定方法见 [docs/calibration.md](docs/calibration.md);
整体流程见 [docs/architecture.md](docs/architecture.md)。

## 三个渠道

| 渠道 | 获取方式 | 坐标系 |
| --- | --- | --- |
| MinerU | batch 签名上传 API; 失败则用 CDP 驱动桌面版 | 各向异性像素空间, 分桌面/云端两套标定(`1034x1012` / `1017x1008`) |
| dots.ocr | Gradio5 实例: /config → 上传 → 预热 → 推理 → 下载 zip | 按页归一化(0-1), 免标定 |
| PaddleOCR-VL | 云端异步 jobs | 按页归一化(0-1), 免标定 |

已经有引擎产物? 放进 `inbox/<文档名>__<渠道>/`, 链路会直接用, 不调用任何服务。

## 设计取舍(踩坑最多的部分)

- **坐标系是配置, 不是代码**: `src_w / src_h / x_off / y_off` 落在 channels.json, 每次调用重读,
  改标定不用重启; 横纵非等比必须分别缩放, 统一比例会让框在非 A4 页上歪。
- **标定键跟数据放一起**: 同一个 mineru 目录可能来自桌面版或云端 API(几何差约 1.6%),
  归档时写 `_calib_key` 标记, 查看器与合并器读同一份判定, 避免"同一批字节两套坐标"。
- **缓存优先, 其次 API, 最后桌面版**: 重跑文档零成本; 强制重取就删掉对应的 `raw/<渠道>_api`。
- **上游的锅就记在上游**: 截断的页 JSON、双重编码、改名的键 —— 适配器容错解析并在日志里说明,
  不静默产出空块。
- **U+FFFD 是指标**: 每渠道单独计数进总览表, 它是最便宜的文本完整性信号。

## 局限

- 工程/研究工具, 不是服务; 三个云端渠道是第三方服务, 接口与条款都可能变, 使用时自负其责。
- 只有 MinerU 桌面兜底是 Windows 专有, 主链路跨平台。
- regress.py 的参考文献检查是中韩文学术 PDF 的启发式规则。
- 仓库不含联网测试(需要凭证与额度); CI 只做全模块编译 + 离线 demo 冒烟。

## 目录结构

```
pipeline.py        渲染 -> 获取 -> 适配 -> 查看器 -> 总览
webapp.py app.js   拖拽式工作台(标准库 HTTP 服务 + 渠道参数面板)
merge_out.py       跨渠道逐页合并
unify.py           跨引擎归并 -> unified/ 与指标摘要
analyze.py         跨文档指标 + 归因报告
regress.py         参考文献质量检查(截断/作者/跨页拼接)
caption_audit.py   图表标题审计
mineru_drive.py    MinerU 桌面版 CDP 驱动(兜底)
channels.json      标定与模型默认值(不含密钥)
examples/          make_demo.py -> 离线 demo; 13625974/ -> 真实产物示例
docs/              架构 / 标定 / 渠道配置
```

## 许可

MIT, 见 [LICENSE](LICENSE)。仓库里唯一一份真实产物(`examples/13625974/`)含一篇已发表的韩文期刊论文,
仅用于展示输出形态, 版权归原作者与期刊所有; 请只处理你有权处理的文件。
