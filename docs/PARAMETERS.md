# 参数参考与设置方法

## 1. 如何设置参数

运行形式是 `python -m image_cleaner <子命令> ...`；安装后也可用 `image-cleaner <子命令> ...`。Windows 下建议直接使用虚拟环境的 `.\.venv\Scripts\python.exe`，Linux 使用 `.venv/bin/python`。

CLI 提供流程与常用尺寸条件；质量、文本、去重、导出等设置集中在 JSON 配置文件中，由 `--config` 加载。配置采用 UTF-8，不能写注释；布尔值用 `true/false`，关闭可选阈值用 `null`。所有未知字段会报错，避免拼写错误被静默忽略。

优先级：

1. `analyze/run`：Config 默认值 → JSON 配置 → 显式 CLI 尺寸/宽高比参数。
2. `filter/merge` 未传配置：继承分析分片完整配置 → 显式 CLI 参数。
3. `filter/merge` 传配置：Config 默认值 → 新 JSON 配置 → 显式 CLI 参数；不与分片字段逐项合并。
4. `export`：使用 curated/report.json 中的配置；不接受另一个 `--config`。

例如统计分片使用 `statistics_only.json` 时去重关闭，仅加 `--resolution 256p` 不会开启去重；添加 `--config configs/audit.json` 才切换到该配置中的去重设置。

## 2. 所有 CLI 参数

### prepare：固定输入清单

| 参数 | 必填/默认 | 说明 |
| --- | --- | --- |
| `--input` | 必填 | 图片目录或 UTF-8 JSONL |
| `--manifest` | 必填 | 写入固定清单，同时生成 `.meta.json`；不能覆盖已有清单 |
| `--root` | 自动推断 | 相对图片路径的根目录；目录输入默认自身，JSONL 默认其父目录 |
| `--sidecar-text` | false | 文件夹输入时读取同名 UTF-8 `.txt`；JSONL 文本直接来自 text |

### analyze：分析一个分片

| 参数 | 必填/默认 | 说明 |
| --- | --- | --- |
| `--manifest` | 必填 | prepare 生成的清单，必须有配套 `.meta.json` |
| `--output` | 必填 | 分片输出目录 |
| `--config` | Config 默认值 | JSON 配置文件 |
| `--root` | 清单元数据 root | 当前节点的图片根目录，可与制作清单的机器不同 |
| `--workers` | min(8, CPU 数量) | 本机分析进程数，正整数 |
| `--rank` | 0 | 当前分片编号，0 开始 |
| `--world-size` | 1 | 总分片数，正整数，rank 必须小于该值 |
| `--resume` | false | 使用相同清单、配置、版本和分片参数恢复已有 SQLite |
| 尺寸/桶参数 | 见下表 | 支持 resolution 与三个 aspect 参数 |

### filter / merge：重筛选与全局去重

两个名称调用同一实现。

| 参数 | 必填/默认 | 说明 |
| --- | --- | --- |
| `--shards` | 必填 | 同一 manifest、版本和分析配置的全部 rank SQLite 所在目录 |
| `--output` | 必填 | 一个新的输出目录，不接受覆盖已有合并结果 |
| `--config` | 继承分析配置 | 新筛选配置；不能改变预处理/测量设置 |
| 尺寸/桶参数 | 见下表 | 覆盖有效配置对应字段 |

### 共用尺寸与宽高比参数

适用于 `analyze`、`run`、`filter/merge`。

| 参数 | Config 默认值 | 取值与规则 |
| --- | --- | --- |
| `--resolution` | null | `256p` 短边 >=256；`384x256` 精确宽高；`any/none` 关闭；必须为正整数尺寸 |
| `--aspect-buckets` | null | 多选例如 `"1:1,2:3"`；`all` 八桶并集；`none` 关闭；空选择报错 |
| `--aspect-mode` | strict | strict 精确整数比例；loose 容差模式 |
| `--aspect-tolerance` | 0.05 | 有限非负数，只作用于 loose；0.05=5%，0=不允许偏差 |

八桶固定为 `1:1,2:3,3:2,3:4,4:3,9:16,16:9,21:9`，不支持自定义。所有尺寸条件基于 EXIF 校正后的宽和高；不自动互换横竖桶。严格模式忽略容差参数；未选择桶时模式和容差不会启用过滤。

### export：标准化输出

| 参数 | 必填/默认 | 说明 |
| --- | --- | --- |
| `--curated` | 必填 | filter/merge 输出目录，或 run 的 curated 子目录 |
| `--output` | 必填 | 独立导出目录 |
| `--root` | 记录 source_path | 图片移动后覆盖源图片根目录 |
| `--workers` | min(8, CPU 数量) | 本机导出进程数，正整数 |
| `--resume` | false | 使用相同配置和输出目录续跑，校验源文件和已导出文件哈希 |

### run：单机完整流程

| 参数 | 必填/默认 | 说明 |
| --- | --- | --- |
| `--input` | 必填 | 图片文件夹或 JSONL |
| `--output` | 必填 | 工作目录，下设 input、shards、curated 等 |
| `--root` | 自动推断 | 图片根目录 |
| `--config` | Config 默认值 | 分析与筛选配置 |
| `--workers` | min(8, CPU 数量) | 本机多进程数量 |
| `--sidecar-text` | false | 文件夹输入读取同名文本 |
| `--export` | false | 合并后导出到 normalized 子目录 |
| `--resume` | false | 恢复同输入、配置、根目录和 sidecar 设置的已有任务 |
| 尺寸/桶参数 | 见上表 | CLI 覆盖相应 JSON 字段 |

每个子命令均支持 `--help`。没有 CLI 参数直接设置质量阈值，也没有 `--workers` 控制 merge：最终合并由单进程协调。

## 3. 全部 JSON 配置字段

### 尺寸与比例

| 字段 | 默认值 | 单位/范围与行为 |
| --- | --- | --- |
| resolution | null | Np / WxH / any / none；语义同 CLI |
| aspect_buckets | null | 数组、逗号字符串、all、none 或 null；规范化成固定顺序，重复值合并 |
| aspect_mode | strict | strict / loose |
| aspect_tolerance | 0.05 | 非负有限数，包含边界；不得为 null；设置很大将显著放宽筛选 |
| min_short_edge | null | 非负整数像素，下限 |
| max_long_edge | null | 非负整数像素，上限 |
| min_pixels | null | 非负整数像素总数，下限 |
| max_pixels | null | 非负整数像素总数，上限，不小于 min_pixels |
| min_aspect_ratio | null | 非负有限数 W/H，下限 |
| max_aspect_ratio | null | 非负有限数 W/H，上限，不小于 min_aspect_ratio |

所有下限用 `>=`、上限用 `<=`，条件取交集。选择多个桶只在桶之间取并集，不会放宽其他规则。

### 质量与文字

| 字段 | 默认值 | 单位/范围与行为 |
| --- | --- | --- |
| min_blur_score | null | 非负有限数；拉普拉斯方差下限 |
| max_noise_sigma | null | 非负有限数；灰度 0–255 尺度噪声估计上限 |
| min_snr_proxy_db | null | 有限数，允许负数；SNR 代理 dB 下限 |
| max_blockiness | null | 非负有限数；块效应代理上限 |
| min_text_chars | null | 非负整数，规范化文本字符数量下限 |
| max_text_chars | null | 非负整数，字符上限，不小于 min_text_chars |
| forbidden_text_patterns | [] | 字符串数组；任意 Python 正则命中则拒绝；忽略大小写可用 (?i) |

质量指标缺失时不会触发对应阈值，应另行审计报告 missing 与 warnings。所有质量阈值都需要在目标数据域校准，不能将默认 null 理解为“自动判断质量”。SNR 是代理指标，不是真实 SNR/PSNR。

### 预处理与测量

| 字段 | 默认值 | 单位/范围与行为 |
| --- | --- | --- |
| reject_animated | true | 布尔值；拒绝动画/多页；false 时只处理所读首帧/第一页 |
| max_decode_pixels | 100000000 | 正整数；单图解码像素上限，超限记录为错误 |
| metric_patch_size | 256 | 整数 >=16；角落和中心的原生像素测量区域边长上限 |
| alpha_background | [255,255,255] | 三个 0–255 整数；透明区域合成背景 |
| convert_icc | true | 有 ICC 时转 sRGB；无 ICC 时按 Pillow 的 RGB 转换处理 |
| text_unicode_form | NFC | NFC / NFKC / none；仍执行换行与首尾空白规范化 |

这六项会改变解码、标准化或指标，重筛选时必须与源分片一致；改变它们需重新 analyze。EXIF 校正与 RGB 转换始终执行。设置 max_decode_pixels 是容量限制，不会缩小超限原图。

### 去重

| 字段 | 默认值 | 范围与行为 |
| --- | --- | --- |
| deduplicate | true | 去重总开关，关闭后所有去重均关闭 |
| near_deduplicate | true | 开启近重复，须同时开启 deduplicate；false 仍做精确去重 |
| duplicate_scope | image_text | image_text：图片与规范化文字都满足；image：忽略文字差异 |
| phash_distance | 4 | 整数 0–8，64 位 pHash 汉明距离上限 |
| dhash_distance | 4 | 整数 0–64，64 位 dHash 汉明距离上限 |
| near_rgb_rmse | 8.0 | 非负有限数；16×16 RGB 缩略图 RMSE 上限 |
| near_aspect_log_tolerance | 0.03 | 非负有限数；abs(log(r1/r2)) 上限，与桶容差是不同参数 |

近重复条件全部满足才归并。代表优先级为像素多、噪声低、清晰度高、清单 seq 小；不依赖 worker 完成顺序。精确去重检查原文件哈希和标准化像素哈希，作用域包含文本规则。

### 导出

| 字段 | 默认值 | 范围与行为 |
| --- | --- | --- |
| export_max_edge | null | 正整数像素；导出时限制长边，只缩小，不放大 |
| export_format | png | png / jpeg |
| export_jpeg_quality | 95 | 整数 1–100；JPEG 无色度子采样，仍为有损输出 |

导出参数不修改统计阶段尺寸。缩图可能因整数取整产生微小比例变化，报告桶归属针对导出前的规范化原尺寸。修改导出设置先重新 filter 到新 curated 目录再 export。

## 4. 典型配置组合

### 只做第一轮统计

```json
{"deduplicate":false,"near_deduplicate":false}
```

### 尺寸、比例、文字与去重

```json
{
  "resolution": "256p",
  "aspect_buckets": ["1:1","2:3","3:2","9:16","16:9"],
  "aspect_mode": "loose",
  "aspect_tolerance": 0.05,
  "min_text_chars": 1,
  "deduplicate": true,
  "near_deduplicate": true,
  "duplicate_scope": "image_text",
  "export_format": "png"
}
```

### 原图规范化并限制导出长边

```json
{"resolution":"256p","export_max_edge":1024,"export_format":"jpeg","export_jpeg_quality":95}
```

`generation_example.json` 的 512 下限和比例范围是演示配置，不是通用训练推荐。`exact_256p.json` 是兼容旧文件名，内容实际为短边 >=256，请优先使用含义清晰的 `above_256p.json`。

## 5. 无效设置与返回码

常见非法值：`resolution=256`、`aspect_buckets=[]`、`aspect_tolerance=5%`、数值 NaN/Infinity、负容差、拼错字段、质量阈值写成字符串、正则语法错误。容差 CLI 只能写数值，5% 写 0.05。

正常完成返回 0；CLI 捕获的配置/文件/SQLite 错误返回 2；export 存在导出失败，或 run 的导出环节存在失败，返回 1。单图分析失败会记入 error，通常整个分析仍返回 0，应核对报告。非法正则等未统一捕获的异常可能显示 Python traceback 并返回非零。

完整算法公式与各字段的评测解释见 [使用手册](USAGE.md)，故障恢复见 [部署指南](DEPLOYMENT.md)。
