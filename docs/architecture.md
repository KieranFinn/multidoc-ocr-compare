# 架构

一个文档从 PDF 到"可对照的查看器"要过五步。每一步的产物都落盘, 因此可以中断、重跑、单渠道替换,
而不必把整条链路重算一遍。

```
input.pdf
  │
  ├─① render_pages()       pages/page_N.jpg (150dpi) + .src_mtime
  │
  ├─② 获取(三路并行)      mineru / dots / paddle 各拿一份"引擎原生产物"
  │      缓存优先: out/<doc>/raw/<渠道>_api
  │      缺失才调云端 API, mineru 再兜底 CDP 驱动桌面版
  │
  ├─③ adapt_*()            统一中间表示 {页号: [{"cat","box","text"}]}
  │      box 一律换算到 150dpi 页图像素(见 docs/calibration.md)
  │
  ├─④ build_viewer()       viewer_<渠道>.html / viewer_compare.html / index.html
  │
  └─⑤ 汇总                 index.html 表格(页数/块数/韩文字符/U+FFFD/来源)
          merge_out.py      逐页合并三渠道 -> merged/
          analyze.py        跨文档指标 + 归因报告
```

## 统一中间表示

所有适配器只认一个结构:

```python
{ 0: [ {"cat": "Text", "box": [x0, y0, x1, y1], "text": "..."}, ... ], 1: [...] }
```

- `box`: 页图像素, 左上原点, 与 `pages/page_N.jpg` 同尺度。
- `cat`: 归一化类别(Section-header / Text / Footnote / Picture / Caption / Page-header /
  Page-footer / Page-number / Table / Formula), 映射表见 `NS`、`TMAP_MINERU`、`TMAP_PADDLE`。
- 新渠道只需实现一个 `adapt_xxx(path, sizes) -> dict|str`; 返回整篇字符串即进入"md 模式"
  (无块级联动, 只在查看器里给全文)。`pipeline.main` 里按 `chans` 字典分派。

## 产物树

```
out/<文档名>/
├── index.html                 总览: 每渠道页数/块数/韩文/U+FFFD + 四类查看器入口
├── viewer_mineru.html         单渠道: 原图 + 块框 + 块文本(悬停/点击联动)
├── viewer_dots.html
├── viewer_paddle.html
├── viewer_compare.html        1:3 对照: 每页四栏, 渠道分色, 仅同渠道联动
├── pages/page_N.jpg           150dpi 页图(渲染缓存) + page_N.jpg.src_mtime
├── raw/
│   ├── mineru_api/            引擎原生产物(缓存; 也是重跑复用的唯一依据)
│   ├── dots_api/
│   ├── paddle_api/
│   ├── mineru/                归档副本(见下"孪生目录")
│   └── <渠道>_calib_key       该目录用的是哪套标定(避免同字节被两套坐标解读)
├── merged/                    merge_out.py 产出: page_N.json + merged.json + merged.md
└── merged.md
```

### 缓存与重跑

- 命中条件: mineru 看 `*_content_list_v2.json` 是否存在; dots/paddle 看 `page_0.json`。
- 强制重跑: 删掉对应 `raw/<渠道>_api` 目录。
- `raw/<渠道>` 是 `preserve_raw()` 复制的归档副本(为 inbox/CDP 这类"外部产出目录"留证据)。
  当产物本来就落在 `raw/<渠道>_api` 时, 这一步等于把目录复制给自己 —— 同一批字节两份,
  好处是归档语义统一, 代价是体积翻倍。是否清理属于运维决定, 不影响正确性。

### 标定键的判定

同一个 `raw/mineru` 目录, 可能来自桌面版(1034x1012 空间), 也可能来自云端 API(1017x1008 空间)。
若靠目录名猜, 复制/改名就会让几何整体错位, 所以:

1. 获取/归档时把实际来源写进 `raw/<渠道>_calib_key`(`write_calib_marker`);
2. 读取时 `mineru_calib_key(src)` 先读该标记, 再退回目录名启发式, 最后默认桌面版;
3. 查看器与 `merge_out.py` 走同一函数 —— 同一份字节不会得到两套坐标。

## 失败语义

- 某渠道拿不到产物: 该渠道查看器不生成, `index.html` 标注"未提供", 其余照常。
- 适配器返回 `None` 或页数对不上: 记日志并按实际页数截断(`pi >= len(sizes)` 即停), 不静默补空页。
- dots 的页 JSON 可能是双重编码/被截断(上游导出 bug): `_parse_page_json` 先 json, 失败再正则抢救
  完整块, 并在日志里写明"经抢救"。
- U+FFFD 计数进总览表: 它是"文本完整性"的信号, 不是渲染问题。

## 并发

- `pipeline.main` 用 `ThreadPoolExecutor(max_workers=3)` 并行取三渠道(网络等待为主, GIL 不碍事)。
- `webapp.py` 用 `RUN_LOCK` 把多个文档串行化: 桌面版 App 只有一个任务槽, 并发会互相踢。
- 归仓线程(`watch_mineru`)与上传线程共用 `_state_mineru.json`, 只在单进程内使用。
