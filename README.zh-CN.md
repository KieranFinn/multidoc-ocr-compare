# multidoc-ocr-compare

把**同一个 PDF 丢给多个 OCR / 文档解析引擎**, 再把所有块换算到同一坐标系里逐页对照 —— 原图在下,
三家引擎的框和文本在上, 一眼看出"这块到底是谁错了"。

面向扫描版学术 PDF(韩/中/英)。目的不是给引擎打分, 而是**归因**: 三个引擎在同一页产生分歧时,
是文档本身怪, 还是某个引擎坏了?

## 产出

| 产物 | 说明 |
| --- | --- |
| `out/<文档>/index.html` | 总览: 页数/块数/韩文字符/U+FFFD/各渠道产物来源 |
| `viewer_<渠道>.html` | 单渠道: 页图 + 块框, 悬停/点击联动文本 |
| `viewer_compare.html` | 1:3 对照: 每页四栏(原图 + 三渠道), 渠道分色, 悬停显现框 |
| `merged/` + `merged.md` | 逐页合并 + 每块来源(merge_out.py) |
| `_analysis_report.html` / `_analysis_summary.csv` | 跨文档指标与归因(analyze.py) |

## 离线跑通(不需要账号, 不联网)

```bash
pip install -r requirements.txt
python examples/make_demo.py
python pipeline.py examples/demo/demo.pdf \
    --mineru examples/demo/mineru --dots examples/demo/dots --paddle examples/demo/paddle \
    --channels examples/demo/channels.json
# 打开 out/demo/index.html
```

demo 是合成的两页文档 + 三份 mock 产物, 用来验证"渲染 → 适配 → 查看器"整条主干, 不含任何真实语料。

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
| MinerU | batch 签名上传 API; 失败则用 CDP 驱动桌面版 | 各向异性像素空间, 分桌面/云端两套标定 |
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

## 许可

MIT, 见 [LICENSE](LICENSE)。仓库不附带任何文档; 请只处理你有权处理的文件。
