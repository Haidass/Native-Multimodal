# 图片—文本数据清洗：完整操作与指标手册

## 1. 工具范围和运行环境

本工具处理本地图片与可选文本，完成导入、规范化测量、分布统计、分辨率/质量筛选、去重和独立导出。源文件不修改、不删除。保留/拒绝/重复/错误的判断通过输出清单表达。

Python 3.10+；Windows 与 Linux；基础处理不需要 GPU。先下载仓库，以下以安装目录为例：

```text
D:\projects\multimodal_training
```

Windows 首次安装需要建立 `.venv`，完成后进入项目目录使用：

```powershell
cd D:\projects\multimodal_training
.\.venv\Scripts\python.exe -m image_cleaner --help
```

新机器首次安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

Linux 对应安装与命令前缀：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m image_cleaner --help
```

下文 Windows 命令均在项目根目录运行。Linux 将前缀改为 `.venv/bin/python` 并使用相应路径即可。每次筛选使用新的输出目录。

## 2. 输入方式

### 2.1 文件夹：适合直接批量导入

```text
D:\datasets\images\
  animals\001.jpg
  animals\001.txt
  charts\002.png
  charts\002.txt
```

递归发现 `.jpg/.jpeg/.png/.webp/.bmp/.tif/.tiff/.gif/.avif`。图片有同名 UTF-8 `.txt` 时加 `--sidecar-text`，否则只导入图片。默认不跟随目录符号链接；扩展名发现列表与实际可解码格式不同，具体解码能力取决于 Pillow。

### 2.2 JSONL：适合图文配对与多机迁移

每行至少一个字符串 `path`，可以附带 `text`、`id`、`metadata`：

```json
{"id":"original-001","path":"animals/001.jpg","text":"一只猫坐在窗边。","metadata":{"source":"dataset-a"}}
{"id":"original-002","path":"charts/002.png","text":"一个展示月份销量的柱状图。"}
```

相对路径以 `--root` 为基准；省略时以输入 JSONL 所在目录为基准。原始 ID 保存为 `source_id`，其他额外字段归入 `metadata`。同一图片允许多条不同文本记录。跨节点推荐相对路径；绝对路径不受 `--root` 重映射。

```powershell
.\.venv\Scripts\python.exe -m image_cleaner run --input "D:\datasets\pairs.jsonl" --root "D:\datasets\images" --output runs/pairs_statistics --config configs/statistics_only.json --workers 8
```

格式错误的输入 JSONL、非字符串文本或无法读取的 sidecar 会使导入报错；单张图片缺失或损坏在分析阶段记录为错误。工具不直接下载 URL 或读取压缩包内的图片。

## 3. 推荐流程：先统计，再筛选，再导出

### 3.1 全量分布统计

```powershell
.\.venv\Scripts\python.exe -m image_cleaner run --input "D:\datasets\images" --output runs/statistics --config configs/statistics_only.json --workers 8 --sidecar-text
```

只处理图片且没有文本时移除 `--sidecar-text`。统计配置关闭尺寸/质量/文本可选阈值和去重，成功处理的静态图片全部保留；损坏、超过容量限制、动画/多页等仍作为错误记录。

查看报告：

```powershell
Start-Process -FilePath "runs/statistics/curated/report.html"
```

`report.json` 适合程序读取，`report.html` 包含无外部依赖的分布图。先检查分辨率、宽高比、噪声与清晰度分布，再确定规则。

### 3.2 保留 256p 及以上的图片

```powershell
.\.venv\Scripts\python.exe -m image_cleaner filter --shards runs/statistics/shards --output runs/only_256p --resolution 256p
```

`256p` 的定义是 `min(width, height) >= 256`，表示短边大于或等于 256 像素，256 本身保留。它不表示缩放图片，与 `min_short_edge=256` 的尺寸边界相同。

| 图片尺寸（宽×高） | `256p` | `256x256` | `384x256` |
|---|---|---|---|
| 256×256 | 保留 | 保留 | 拒绝 |
| 384×256 | 保留 | 拒绝 | 保留 |
| 256×384 | 保留 | 拒绝 | 拒绝 |
| 255×384 | 拒绝 | 拒绝 | 拒绝 |
| 257×384 | 保留 | 拒绝 | 拒绝 |
| 512×512 | 保留 | 拒绝 | 拒绝 |

其他选择：

```powershell
# 仅保留 256×256。
.\.venv\Scripts\python.exe -m image_cleaner filter --shards runs/statistics/shards --output runs/only_square --resolution 256x256

# 仅保留宽 384、高 256。
.\.venv\Scripts\python.exe -m image_cleaner filter --shards runs/statistics/shards --output runs/only_landscape --resolution 384x256

# 改为短边大于或等于 512。
.\.venv\Scripts\python.exe -m image_cleaner filter --shards runs/statistics/shards --output runs/only_512p --resolution 512p

# 关闭分辨率条件。
.\.venv\Scripts\python.exe -m image_cleaner filter --shards runs/statistics/shards --output runs/all_resolutions --resolution any
```

尺寸以 EXIF 方向校正后的宽高为准：编码为 256×384、EXIF 要求旋转 90° 的图片会以 384×256 判断。色彩转换不改变尺寸；导出缩放发生在筛选之后。

`filter` 等价于 `merge`，始终从分析分片里的全部记录重新判定，不能把它的 `--shards` 指向上一轮 `curated` 目录。即使第一轮某图片被阈值或去重排除，分析结果仍在分片中，可以再次参与筛选。原图不会被重新解码；物理导出仍需要原图存在。

### 3.3 分辨率与其他筛选一起执行

使用配置文件可同时加入宽高比、质量、文本条件或去重：

```powershell
.\.venv\Scripts\python.exe -m image_cleaner filter --shards runs/statistics/shards --output runs/256p_deduplicated --config configs/audit.json --resolution 256p
```

上例额外开启去重；符合尺寸的重复记录仍会进入 `duplicate.jsonl`。如果只希望剔除短边小于 256 的图片，沿用统计配置或指定 `--config configs/above_256p.json`，其去重关闭。

配置优先级：未传 `--config` 时继承分析时的完整配置；传了 `--config` 时从该 JSON 和代码默认值构造新配置，不是对原配置做部分补丁；`--resolution` 最后覆盖分辨率字段。所有启用规则取交集。若同时设置 `min_short_edge=512` 与 `resolution=256p`，最终仍要求短边至少 512；如需允许所有大于或等于 256 的尺寸，先关闭更严格的其他尺寸规则。

阈值修改和精确分辨率筛选可以复用分析结果。`reject_animated/max_decode_pixels/metric_patch_size/alpha_background/convert_icc/text_unicode_form` 属于测量与预处理设置，改变这些设置需要重新分析。

### 3.4 导出标准化图片与文本

```powershell
.\.venv\Scripts\python.exe -m image_cleaner export --curated runs/only_256p --output runs/export_256p --workers 8
```

只导出 `kept.jsonl` 中的记录，默认 PNG、RGB、保留已匹配尺寸，不做裁剪或放大。文件放在 `images` 子目录，`exported.jsonl` 的 `path` 为导出目录下的相对路径。导出会校验源文件 SHA-256；源文件在分析后发生变化会记录失败。

`export_max_edge` 如被启用，可能改变筛选后图片尺寸；需要导出文件仍满足筛选条件时保持 `export_max_edge=null`。例如 1024×512 符合 256p 及以上，但若导出最长边限制为 384，则会缩成 384×192，输出不再满足短边至少 256 的条件。

### 3.5 一键流程

已经明确目标时可以直接执行：

```powershell
.\.venv\Scripts\python.exe -m image_cleaner run --input "D:\datasets\images" --output runs/direct_256p --config configs/above_256p.json --workers 8 --export
```

也可以在 `run` 或 `analyze` 上直接传 `--resolution`；推荐第一次先完成全量统计。

### 3.6 宽高比桶筛选

比例始终为 EXIF 方向校正后的 **宽:高**。八个固定桶为 `1:1,2:3,3:2,3:4,4:3,9:16,16:9,21:9`。竖图与横图分别选择，例如选择 `2:3` 不会自动选择 `3:2`。

```powershell
# 在项目根目录执行。先运行 3.1 的统计任务，再用缓存分片筛选。
# 严格多选：实际尺寸必须与任意一个选中桶完全同比分配。
.\.venv\Scripts\python.exe -m image_cleaner filter --shards runs/statistics/shards --output runs/aspect_strict --aspect-buckets "1:1,2:3,3:2" --aspect-mode strict

# 宽松全选：5% 偏差，也可以省略 tolerance 参数使用默认值。
.\.venv\Scripts\python.exe -m image_cleaner filter --shards runs/statistics/shards --output runs/aspect_loose --aspect-buckets all --aspect-mode loose --aspect-tolerance 0.05

# 更严格的 3% 容差，同时保留短边 >=256 的图片。
.\.venv\Scripts\python.exe -m image_cleaner filter --shards runs/statistics/shards --output runs/aspect_3percent_256 --resolution 256p --aspect-buckets "3:4,4:3,9:16,16:9" --aspect-mode loose --aspect-tolerance 0.03

# 覆盖已有配置、关闭桶条件。其他筛选条件继续生效。
.\.venv\Scripts\python.exe -m image_cleaner filter --shards runs/statistics/shards --output runs/aspect_disabled --aspect-buckets none

# 从文件夹直接运行，支持多进程。
.\.venv\Scripts\python.exe -m image_cleaner run --input "D:\datasets\images" --output runs/aspect_run --workers 8 --aspect-buckets all --aspect-mode loose

# 使用示例配置。示例关闭去重以便单独观察桶筛选效果。
.\.venv\Scripts\python.exe -m image_cleaner filter --shards runs/statistics/shards --output runs/aspect_config --config configs/aspect_loose.json
```

每次重筛选使用一个新的输出目录。`filter` 读取全部分析记录，包括此前被规则拒绝的图片；因此既能收紧也能放宽条件，不需要再次解码。解码失败的记录仍归入错误。

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `--aspect-buckets` | 不启用，或继承配置/分片 | 英文逗号分隔多选；`all` 是八桶并集；`none` 关闭；空值或非预设桶报错 |
| `--aspect-mode` | `strict`，或继承配置/分片 | `strict` 精确匹配；`loose` 容差匹配 |
| `--aspect-tolerance` | `0.05`，或继承配置/分片 | 有限非负小数，仅宽松模式生效；`0.05` 表示 5%，`0` 表示不允许偏差 |

三个参数均支持 `analyze`、`filter`/`merge`、`run`。显式命令行参数优先于配置；未传参数保留配置值。没有传 `--config` 的 `filter` 继承分析分片配置；传入配置文件则按该文件及 Config 默认值构建整套筛选规则。

设实际比例 `r=width/height`，目标桶比例 `b`。严格模式使用整数交叉相乘：例如 `2:3` 要求 `width×3 == height×2`。宽松模式要求 `δ=max(r/b,b/r)-1 <= aspect_tolerance`，包含边界。实现使用精确分数比较。例如选 `1:1`、偏差 5% 时，`1050×1000` 和 `1000×1050` 均通过，`1051×1000` 不通过。此对称定义下允许区间为 `[b/1.05, b×1.05]`，并非普通的 `[b×0.95,b×1.05]`。

任意一个选中桶匹配即可通过。如果匹配多个桶，则只分配给偏差最小的桶；同偏差按上述固定八桶顺序选取，因此每张图片只计数一次。重复选择会合并。`all` 仍会过滤超出所有桶范围的图片。桶条件与分辨率、`min_aspect_ratio/max_aspect_ratio`、质量、文本条件取交集；不会裁剪或缩放图片。

配置文件新增字段示例：

```json
{
  "aspect_buckets": ["1:1", "2:3", "3:2"],
  "aspect_mode": "loose",
  "aspect_tolerance": 0.05
}
```

`aspect_buckets` 也接受 `"all"`、`"none"` 或 `null`。多机仍使用第 6 节的同一 manifest 和 rank/world-size 流程；所有节点分析配置一致，收集全部分片后统一执行上面的 `filter` 命令，桶筛选无需各节点读取源图片。

## 4. 各环节指标和判定规则

### 4.1 导入、解码和容量检查

| 字段/指标 | 意义 | 判定 |
|---|---|---|
| `input_count` | 清单记录数量，包含同图多文本记录 | 不是去重后的独立图片数 |
| `original_width/original_height` | 文件编码中的原始尺寸 | 与 EXIF 校正后的尺寸分别保存 |
| `original_format/original_mode` | 解码格式和像素模式 | 用于来源与格式审计 |
| `frame_count` | 帧数或页数 | 默认 `reject_animated=true` 时，多帧/多页报处理错误 |
| `max_decode_pixels` | 单图最大解码像素数 | 默认 100,000,000；超过限制报错 |
| `error_count`、`error.jsonl` | 文件缺失、损坏、截断或处理异常 | 不进入有效分布统计与导出 |
| `processing_success_rate` | 完成整套图片处理数 ÷ 输入记录数 | 是处理成功率，不是人工质量准确率 |

### 4.2 标准化

| 字段/设置 | 行为和验证方法 |
|---|---|
| `exif_transposed` | 是否按 EXIF 方向进行转置；查看标准化宽高并抽查方向 |
| `icc_converted` | 有 ICC 且开启转换时转为 sRGB；失败记错误 |
| `alpha_flattened` | 是否存在透明通道并按 `alpha_background` 合成；默认白色 |
| `text_unicode_form` | NFC 默认；可选 NFKC 或 none；同时统一换行并去首尾/行尾空白 |
| 导出的 `width/height`、像素模式 | 检查最终尺寸与 RGB 模式；导出可选等比例缩小 |

标准化属于数据规范化，未实现模型训练的 dropout/weight decay，也不会执行默认去噪或锐化。16 位/浮点图片最终转为 8 位 RGB，本工具主要面向常规网页图像。

### 4.3 分布统计、分辨率和宽高比

| 字段 | 定义/单位 | 筛选配置 |
|---|---|---|
| `width` | EXIF 校正后的宽度，像素 | `resolution=WxH` 可精确限定 |
| `height` | EXIF 校正后的高度，像素 | `resolution=WxH` 可精确限定 |
| `short_edge` | min(width,height)，像素 | `resolution=Np` 要求大于或等于 N；`min_short_edge` 为包含边界的下限 |
| `long_edge` | max(width,height)，像素 | `max_long_edge` 为上限 |
| `pixels` | width×height | `min_pixels/max_pixels` |
| `aspect_ratio` | width/height | `min_aspect_ratio/max_aspect_ratio` |
| `orientations` | 横、竖、近方图数量 | 0.95≤W/H≤1.05 视为近方图；只是统计类别 |

分辨率筛选不匹配时，原因是 `resolution_mismatch`。`min_short_edge=256` 与 `resolution=256p` 均包含 256，因此 255 不合格，256 和 257 合格。`resolution_filter.mode=short_edge_gte` 与 `comparison=">="` 在报告中记录这一判定；`WxH` 仍为精确宽高匹配。

每个分布提供有效数量、缺失数量、最小值、最大值、均值、总体标准差、p1/p5/p25/p50/p75/p95/p99，以及固定桶直方图。桶为左闭右开，最后覆盖到无穷大；直方图是全量精确计数。分位数最多使用固定种子 20,000 条蓄水池样本；更大规模时 `quantiles_approximate=true`。

`before` 覆盖成功处理的全部记录，`after` 覆盖最终保留记录；不代表按独立图片去重后的来源统计。分辨率匹配数另外直接按每条记录统计，避免粗直方图桶无法区分 255/256/257 的问题。

### 4.4 图片技术质量

默认在四角和中心最多五个原生像素区域测量，区域大小最多 `metric_patch_size=256`，相同区域去重，各指标取有效区域的中位数。不会先缩图再测量；可能漏掉未采样区域的局部缺陷。

| 字段 | 计算方式、解释 | 筛选条件 |
|---|---|---|
| `blur_score` | 灰度区域上 Var(up+down+left+right−4×center)，拉普拉斯方差；通常越低越模糊 | 低于 `min_blur_score` 拒绝 |
| `noise_sigma` | 在梯度最低约 30% 区域，用 3×3 高通残差计算 MAD/(0.67448975×6)，像素幅度范围 0–255 | 高于 `max_noise_sigma` 拒绝 |
| `snr_proxy_db` | 每区域 20log10(灰度标准差/noise_sigma)，然后取区域中位数 | 低于 `min_snr_proxy_db` 拒绝 |
| `blockiness` | 对水平/垂直 8 像素网格各相位求边界梯度与非边界梯度差；每轴取最大差，再平均并截断到非负 | 高于 `max_blockiness` 拒绝 |
| `luminance` | 灰度均值，0–255 | 当前仅记录，无直接阈值 |
| `contrast` | 灰度总体标准差 | 当前仅记录，无直接阈值 |
| `metric_patch_count` | 实际采样区域数 | 当前仅记录，方便审计 |

这些是代理指标：拉普拉斯方差可能因噪声升高，纹理会影响噪声估计，规则图案也会产生块效应分数。信噪比的“信号”采用局部亮度标准差，不是真实 SNR，也不是 PSNR。

噪声或对比度接近零时，SNR 返回 `null` 并写入 `snr_proxy_undefined`；极小图片部分指标为 `null`。缺失值不会触发对应质量阈值。报告的 `missing` 表示未得到指标值，不表示质量合格。

所有质量阈值默认 null（关闭）；先按照片/截图/图表/插画等类型抽查，再设置阈值。修改 `metric_patch_size` 会改变指标分布，必须重新分析并重新校准。

### 4.5 文本筛选

| 指标/设置 | 判定 |
|---|---|
| `text_chars` | 规范化后 Python 字符数量；缺失文本按 0 统计，不是 token 数或单词数 |
| `min_text_chars` | 低于下限拒绝；设置为 1 可排除没有文字的样本 |
| `max_text_chars` | 超过上限拒绝 |
| `forbidden_text_patterns` | 任一 Python 正则表达式 `re.search` 命中即拒绝，默认区分大小写 |

例如配置 `"forbidden_text_patterns": ["(?i)buy\\s+now", "点击购买"]` 可匹配部分广告文本。这是启发式文本规则，不能判断图片与文本是否语义对应。

### 4.6 去重

质量规则通过后再进行全局去重，以避免不合格图片挤掉合格代表。

| 指标/类型 | 判定 | 配置 |
|---|---|---|
| `file_sha256`、`exact_file` | 原文件字节 SHA-256 一致 | `deduplicate=true` |
| `pixel_sha256`、`exact_pixels` | 标准化 RGB 尺寸和像素一致 | `deduplicate=true` |
| pHash 汉明距离 | 64 位低频 DCT 特征哈希差异 | 不超过 `phash_distance`，默认 4，可设 0–8 |
| dHash 汉明距离 | 64 位相邻灰度梯度特征差异 | 不超过 `dhash_distance`，默认 4，可设 0–64 |
| RGB 缩略图 RMSE | 16×16 RGB 图像 sqrt(mean((a−b)²)) | 不超过 `near_rgb_rmse`，默认 8.0 |
| 宽高比差异 | abs(log(ratio_a/ratio_b)) | 不超过 `near_aspect_log_tolerance`，默认 0.03 |
| `near_perceptual` | 上述近重复条件均满足 | `near_deduplicate=true` 且总去重开启 |

pHash 使用相对 AC 系数容差抑制极小系数，与普通 pHash 数值不完全相同。所有近重复复核是启发式，不能代替语义 embedding 去重。

默认 `duplicate_scope=image_text`：规范化文本也相同才归并，保留同图不同描述/问答；没有文本时按图片归并。`image` 会忽略文本差异。代表优先级为像素数降序、噪声升序、清晰度降序、清单序号升序，不是人工审美排序。

`duplicate.jsonl` 给出 `duplicate_of/duplicate_kind/duplicate_metrics`。近重复记录目前保存 pHash 距离和 RGB RMSE，dHash 与比例条件用于判断但未单独写入距离字段。每个重复直接匹配一个保留代表，不通过无限传递链扩大分组。

### 4.7 阶段评测指标

令 N 为输入条数、D 为处理成功条数、M 为指定分辨率匹配条数、E 为全部规则通过条数、U 为重复条数、K 为最终保留条数：

| `report.json` 字段 | 定义 |
|---|---|
| `input_count` | N |
| `error_count` | N−D |
| `stage_metrics.processing_success_count/rate` | D、D/N |
| `stage_metrics.resolution_matched_count` | M，单独按尺寸条件计算，不含其他质量限制 |
| `stage_metrics.resolution_mismatched_count` | D−M |
| `stage_metrics.resolution_pass_rate` | M/D；分辨率条件关闭时为 1（D>0） |
| `stage_metrics.rules_eligible_count/rules_pass_rate` | E=K+U、E/D |
| `rejected_count` | D−E，至少一个规则不合格 |
| `duplicate_count` | U |
| `stage_metrics.duplicate_rate_among_eligible` | U/E |
| `kept_count/stage_metrics.final_keep_rate` | K、K/N |
| `before.reasons` | 最终各原因的出现次数；一条拒绝记录可有多个原因，不可直接加总当成样本数 |

分母为零时比率为 null。比率是 0–1 的数值；乘 100 得百分比。每次完整输出满足 `N=K+rejected_count+U+error_count`。关闭去重时 U=0。

这些指标衡量清洗流程和数据组成，不是模型性能或人工标注准确率。若要评估误删/漏删，按图片类型和规则分层抽取人工审核样本，统计“被拒绝但实际可用”的比例和“保留但实际不合格”的比例。后续训练收益需要固定训练预算、随机种子与验证集做对照实验。

### 4.8 宽高比桶筛选评测指标

打开输出目录的 `report.html` 查看新增宽高比桶部分；`report.json` 提供机器可读指标，`kept.jsonl`、`rejected.jsonl` 等提供逐条结果。

| 指标/字段 | 定义与用途 |
| --- | --- |
| `stage_metrics.aspect_bucket_matched_count` | 成功解码记录中，单独满足桶条件的数量；不受其他阈值和去重影响 |
| `aspect_bucket_mismatched_count` | 成功解码数量减去桶匹配数量 |
| `aspect_bucket_pass_rate` | 桶匹配数 / 成功解码数；无成功解码记录为 null；关闭桶条件时成功解码记录全部通过 |
| `aspect_bucket_filter.counts_before` | 所有成功解码图片的匹配桶归属计数，包含因其他条件拒绝或重复的图片 |
| `aspect_bucket_filter.counts_after` | 最终 kept 图片的桶归属计数，已应用全部规则及去重 |
| `aspect_bucket_filter` 其余字段 | 是否启用、选中桶、模式、容差、尺寸计算依据、偏差公式 |
| 每条记录 `aspect_bucket` | 匹配且分配的桶；没有通过桶条件或关闭条件时为 null |
| `aspect_bucket_nearest` | 最近的选中桶，即使不通过也可用来排查；关闭条件时为 null |
| `aspect_bucket_deviation` | 到最近桶的对称相对偏差；例如 0.05 是 5%；关闭条件时为 null |
| `aspect_bucket_match` | true/false 表示通过/未通过；关闭条件时为 null |
| 拒绝原因 `aspect_bucket_mismatch` | 不满足任何选中桶；同一记录可同时包含多个拒绝原因 |

上述逐条字段针对成功解码记录；错误记录可能没有尺寸与桶字段。`counts_before` 总和等于独立桶匹配数（条件启用时），`counts_after` 总和等于最终保留数。选中但没有图片的桶也输出 0；关闭条件时桶计数字典为空。质量评测可同时检查桶通过率、各桶覆盖量、最终保留率，并人工抽查 rejected 中临界偏差图片；各桶不执行均衡采样或配额。

## 5. 配置文件和参数

项目已有：

| 配置 | 用途 |
|---|---|
| `configs/statistics_only.json` | 全量统计，关闭可选阈值与去重 |
| `configs/above_256p.json` | 短边大于或等于 256，关闭去重 |
| `configs/exact_256p.json` | 兼容旧文件名，内容与 above_256p 相同，采用大于或等于的新规则 |
| `configs/audit.json` | 无可选技术质量阈值，开启默认去重 |
| `configs/aspect_strict.json` | 严格选择 1:1、2:3、3:2，关闭去重 |
| `configs/aspect_loose.json` | 宽松选择八桶，默认容差 5%，关闭去重 |
| `configs/generation_example.json` | 示例：短边至少 512、比例 0.25–4、文本非空，并开启默认去重 |

以下为可自定义的完整字段表，数值是默认值，不是推荐质量阈值：

```json
{
  "resolution": null,
  "min_short_edge": null,
  "max_long_edge": null,
  "min_pixels": null,
  "max_pixels": null,
  "min_aspect_ratio": null,
  "aspect_buckets": null,
  "aspect_mode": "strict",
  "aspect_tolerance": 0.05,
  "max_aspect_ratio": null,
  "min_blur_score": null,
  "max_noise_sigma": null,
  "min_snr_proxy_db": null,
  "max_blockiness": null,
  "min_text_chars": null,
  "max_text_chars": null,
  "forbidden_text_patterns": [],
  "reject_animated": true,
  "max_decode_pixels": 100000000,
  "metric_patch_size": 256,
  "alpha_background": [255, 255, 255],
  "convert_icc": true,
  "text_unicode_form": "NFC",
  "deduplicate": true,
  "near_deduplicate": true,
  "duplicate_scope": "image_text",
  "phash_distance": 4,
  "dhash_distance": 4,
  "near_rgb_rmse": 8.0,
  "near_aspect_log_tolerance": 0.03,
  "export_max_edge": null,
  "export_format": "png",
  "export_jpeg_quality": 95
}
```

`export_format` 可选 png/jpeg；JPEG `export_jpeg_quality` 为 1–100，采用无色度子采样，仍是有损输出。图片规范化不会自动统一为方形，固定裁剪和张量归一化由训练数据加载器执行。

`resolution` 支持 `256p`（短边>=256）、`256x256`（精确宽高）等正整数格式；大小写与首尾空白会规范化，`256×256` 也接受。`any/none/null` 关闭分辨率条件。无后缀的 `256`、`0p`、负数、逗号多个值都非法；多目标请分别筛选到不同输出目录。

各子命令参数：

| 命令 | 主要必填参数 | 可选参数 |
|---|---|---|
| `prepare` | `--input --manifest` | `--root --sidecar-text` |
| `analyze` | `--manifest --output` | `--root --config --resolution --aspect-buckets --aspect-mode --aspect-tolerance --workers --rank --world-size --resume` |
| `filter/merge` | `--shards --output` | `--config --resolution --aspect-buckets --aspect-mode --aspect-tolerance` |
| `export` | `--curated --output` | `--root --workers --resume` |
| `run` | `--input --output` | `--root --config --resolution --aspect-buckets --aspect-mode --aspect-tolerance --workers --sidecar-text --export --resume` |

`workers` 默认 min(8,CPU数量)。配置未知字段、非法类型、范围错误会报错；各子命令的 `--help` 可查看在线说明。

## 6. 多机分片流程

先创建全局固定清单：

```powershell
.\.venv\Scripts\python.exe -m image_cleaner prepare --input "D:\datasets\images" --manifest runs/distributed/input.jsonl --sidecar-text
```

将 `input.jsonl`、`input.jsonl.meta.json`、相同代码和配置分发到节点。每台机器都必须能访问对应原图，可以使用不同本地根目录。

分别在两台机器执行：

```powershell
# 节点 0
.\.venv\Scripts\python.exe -m image_cleaner analyze --manifest runs/distributed/input.jsonl --root "D:\node0\images" --output runs/distributed/shards --config configs/statistics_only.json --workers 8 --world-size 2 --rank 0

# 节点 1
.\.venv\Scripts\python.exe -m image_cleaner analyze --manifest runs/distributed/input.jsonl --root "E:\node1\images" --output runs/distributed/shards --config configs/statistics_only.json --workers 8 --world-size 2 --rank 1
```

每条清单按 `seq % world_size` 分配。每个节点本地写不同 rank 的 SQLite，任务退出后收集全部 `shard-*.sqlite` 到协调节点同一目录；不用让不同机器写同一个 SQLite。

协调节点先汇总统计，然后按分辨率条件筛选：

```powershell
.\.venv\Scripts\python.exe -m image_cleaner merge --shards runs/distributed/shards --output runs/distributed/statistics
.\.venv\Scripts\python.exe -m image_cleaner filter --shards runs/distributed/shards --output runs/distributed/only_256p --resolution 256p
```

若希望跨分片去重，在第二条命令增加 `--config configs/audit.json`。合并会验证全部 rank 完整、清单/配置/软件版本一致、序号不重复。筛选结果与使用同一清单的单机处理一致，去重代表顺序不依赖 worker 完成顺序。

这是静态分片处理，没有自动节点调度或故障迁移。分析并行，最终筛选和全局去重由一个协调节点完成；数据和索引落磁盘，没有百万级运行性能保证。大图处理的解码内存与图片尺寸有关，可降低 workers 或最大解码像素限制。

## 7. 输出、断点续跑和复核

统计 `run` 输出清单、分片数据库，以及 `curated` 报告；`filter/merge` 的报告直接在其 `--output` 目录内，**不会再额外套一层 curated**。

| 文件 | 用途 |
|---|---|
| `input.jsonl/.meta.json` | 固定输入与 SHA-256、条数、数据根目录 |
| `shard-*.sqlite` | 完整分析记录与断点检查点，是重复筛选的数据源 |
| `shard-*.jsonl` | 方便检查的分片记录 |
| `kept.jsonl` | 最终保留的路径、文本、指标 |
| `rejected.jsonl` | 各规则拒绝原因，包括 `resolution_mismatch` |
| `duplicate.jsonl` | 重复类型、代表 ID 和近重复评分 |
| `error.jsonl` | 单图处理失败及异常信息 |
| `report.json/.html` | 配置、分辨率定义、阶段指标和前后分布 |
| `exported.jsonl` | 标准化图片相对路径及文本 |
| `export_errors.jsonl` | 导出失败清单 |
| `export.meta.json` | 导出完成状态、计数及身份校验 |

分析续跑：

```powershell
.\.venv\Scripts\python.exe -m image_cleaner analyze --manifest runs/distributed/input.jsonl --root "D:\node0\images" --output runs/distributed/shards --config configs/statistics_only.json --workers 8 --world-size 2 --rank 0 --resume
```

导出续跑：

```powershell
.\.venv\Scripts\python.exe -m image_cleaner export --curated runs/only_256p --output runs/export_256p --workers 8 --resume
```

`run --resume` 要求相同输入、配置、分辨率等身份设置；修改规则应使用 `filter` 输出到新目录。分析每 100 条提交一次；已提交项跳过。清单固定后保持原始文件不变。残留锁文件只能在确认任务退出后移除。

合并/筛选暂不支持中途续跑，失败时用新输出目录重跑，不必重新分析。导出失败会记录清单并返回退出码 1；参数/文件操作等任务错误返回 2；分析阶段的个别图片错误不使整批命令失败，需检查 `error_count`。

复核建议：检查总数守恒、目标尺寸匹配率、各原因抽样、清洗前后图片类别/宽高比分布变化，以及近重复误合并。为每轮保存独立目录及配置；后续训练/验证划分应让同图及其变体落在同一侧，本工具未自动划分数据集。

## 8. 验证与功能边界

在全新示例目录运行以下命令，可复现分辨率筛选，不依赖外部数据。示例是六张合成图片和一条损坏图片；256p 及以上预期保留五张（256×256、384×256、256×384、257×384、512×512）、尺寸拒绝一张、错误一张。重复执行请复用已生成输入并为任务更换新的输出目录。旧版筛选报告不会自动改写；重新 filter 到新目录，旧分析分片仍可复用。

```powershell
.\.venv\Scripts\python.exe examples/make_resolution_demo.py
.\.venv\Scripts\python.exe -m image_cleaner run --input runs/resolution_demo_input --output runs/resolution_demo_statistics --config configs/statistics_only.json --workers 2 --sidecar-text
.\.venv\Scripts\python.exe -m image_cleaner filter --shards runs/resolution_demo_statistics/shards --output runs/resolution_demo_256p --resolution 256p
.\.venv\Scripts\python.exe -m image_cleaner export --curated runs/resolution_demo_256p --output runs/resolution_demo_export --workers 2
```

单元与集成测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试包括包含边界的短边下限、精确宽高、255/256/257 边界、EXIF 后方向、配置覆盖、禁用尺寸条件、规则交集、无原图重复筛选，以及既有多进程/跨分片去重与导出测试。

未包含 CLIP 图文语义对齐、VLM 事实核验、审美模型、OCR、水印分类器、语义 embedding 去重或训练模型性能评测。当前技术质量指标无法判断图片里的描述是否事实正确。
