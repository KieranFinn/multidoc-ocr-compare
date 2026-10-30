# 渠道配置与获取

## 配置文件的三层结构

`pipeline.load_cfg()` 按顺序合并, 后者覆盖前者:

1. `channels.json` —— 入库基线: 标定值、模型名、超时、开关。**不含任何 token。**
2. `channels.local.json` —— 本地私有覆盖, 已 `.gitignore`。只写想改的键, 其余继承基线。
3. 环境变量 `MINERU_API_TOKEN` / `PADDLE_API_TOKEN` —— 覆盖对应渠道的 token(适合 CI)。

`MDOCR_CHANNELS` 可把第 2 层的路径指到别处; `python pipeline.py --channels <file>` 则把第 1 层换成指定文件
(示例 demo 用 `examples/demo/channels.json`, 标定是 identity)。

```json
{
  "mineru":     {"src_w": 1034, "src_h": 1012, "x_off": 17.8, "y_off": 12.3},
  "mineru_api": {"token": "", "src_w": 1017, "src_h": 1008, "x_off": 13, "y_off": 8,
                 "model_version": "vlm", "language": "korean", "is_ocr": false,
                 "enable_formula": true, "enable_table": true, "timeout": 1800},
  "dots_api":   {"base": "https://dotsocr.xiaohongshu.com", "model": "dots.mocr",
                 "prompt": "prompt_layout_all_en", "timeout": 1800, "insecure_tls": false},
  "paddle_api": {"token": "", "base": "https://paddleocr.aistudio-app.com",
                 "model": "PaddleOCR-VL", "timeout": 900},
  "desktop":    {"exe": "", "landing": "", "port": 9223, "kill_existing": false}
}
```

要点:

- `insecure_tls`: 默认关闭。只有自签名/证书链不完整的私有实例才需要打开 —— 打开等于关掉该渠道的证书校验。
- `enable_formula/enable_table` 请写布尔值; 若写字符串, 代码会把 `"false"` 显式解析为假
  (`bool("false")` 在 Python 里是 `True`, 这是个容易踩的坑)。
- `desktop.kill_existing`: 默认 `false`。打开后启动 CDP 前会结束同名 App 进程 ——
  早期版本无条件 `taskkill /IM MinerU.exe /F`, 会连带杀掉用户自己的实例, 已改。

## 每个渠道怎么拿产物

优先级: **已有产物 → 云端 API → (仅 mineru)CDP 桌面版兜底**。

| 渠道 | 缓存目录 | 命中判据 | 上游要点 |
| --- | --- | --- | --- |
| mineru | `raw/mineru_api/` | 存在 `*_content_list_v2.json` | 官方 batch 签名上传; OSS 裸 PUT 必须零额外 header, 否则 `SignatureDoesNotMatch`; `waiting-file` 超 180s 直接失败 |
| dots | `raw/dots_api/` | 存在 `page_0.json` | Gradio5 内部接口: `/config` → `/upload` → `fn1` 预热 → `fn0` 取 prompt → `fn6` 推理 → 下载 zip; SSE 必须逐行解析 |
| paddle | `raw/paddle_api/` | 存在 `page_0.json` | 异步 jobs 轮询; 产物 page JSON 是归一化坐标 |

也可以完全离线: 把引擎产物放进 `inbox/<文档名>__<渠道>/`(`__mineru`/`__dots`/`__paddle`),
`pipeline.py` 会按名称或内容签名识别。

## 上游已知怪癖(踩过, 记下来省时间)

- **dots**: 提交参数里的组件 state 必须是对象, 不能是字符串; 下载的 zip 内页 JSON 可能是
  双重编码(JSON 里再套 JSON 字符串), 极端情况整页被截断 —— 适配器会容错解析并正则抢救完整块。
- **MinerU**: 上传 URL 是预签名的, `urllib` 自动补的 `Content-Type` 就会导致验签失败;
  导出的 `content_list_v2.json` 键名随版本变化(新版有 `page_header_content` 等), 拍平函数按
  `*_content` 后缀兜底。
- **Paddle**: 单文件导出可能缺少页号, 适配器按块序号均匀推断页号 —— 这是有损的,
  能让用户手工指定页号就别依赖它。

## Web 工作台的配置面板

`python webapp.py` 后右上角"渠道配置"改的是 `channels.local.json`(不是入库的 `channels.json`),
白名单只放行 `paddle_api / mineru_api / dots_api` 三段, 按旧值类型强转, 跳过空串和 `_` 前缀键,
写盘用临时文件替换。下个文档生效, 不用重启。
