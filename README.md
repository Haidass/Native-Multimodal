---
title: NMM-data-preprocess
emoji: 🧹
colorFrom: blue
colorTo: green
sdk: static
app_file: index.html
pinned: false
short_description: 图片文本初筛：代码、参数、部署、分布统计与清洗教程
---

# Native Multimodal：图片—文本数据初筛

GitHub 源码仓库：`Haidass/Native-Multimodal`。在线中文教程与命令生成器：[Hugging Face Space](https://huggingface.co/spaces/HanWangTJU/NMM-data-preprocess)。本仓库同步当前图片—文本初筛工具，不包含模型训练代码、个人数据或认证凭据。

版本 **0.3.2**。面向原生图片—文本模型训练前的数据初筛。Space 提供中文教程、参数查询、命令生成和源码下载；数据处理在本地或服务器通过 CLI 执行。静态 Space 不接收图片、不运行后台清洗作业。

0.3.2 新增 `1:3` 和 `3:1`，共十个预设桶；支持单独选择、多选和 `all`，严格匹配及默认 5% 宽松匹配均适用。清洗回归测试增至 24 项。

| 入口 | 内容 |
| --- | --- |
| [新手逐步教程](docs/QUICKSTART.md) | 下载、安装、合成示例、先统计再筛选再导出 |
| [完整使用与评测手册](docs/USAGE.md) | 每个阶段的操作、指标公式、宽高比规则、报告解读 |
| [参数参考](docs/PARAMETERS.md) | 所有 CLI 参数与 JSON 配置字段、默认值、边界和优先级 |
| [部署指南](docs/DEPLOYMENT.md) | Windows、Linux、Docker、多机、断点续跑、HF Space 发布 |
| [代码与功能说明](docs/ARCHITECTURE.md) | 模块职责、数据流、文件格式、功能矩阵、能力边界 |

下载：`git clone https://github.com/Haidass/Native-Multimodal.git multimodal_training`，或在 Space 首页下载源码 ZIP。所有命令均在下载后的项目根目录执行。

Python 3.10+，Windows / Linux。支持文件夹递归导入和 JSONL 图文清单；单机多进程和多机静态分片；SQLite 断点续跑、全局去重、原始/保留分布报告，以及标准化图片导出。原始图片不修改、不删除。

详细的逐阶段指标、公式、配置字段、单机/多机命令和评测方法见 **[完整使用手册](docs/USAGE.md)**。

## 统计后按宽高比桶筛选

支持十个宽:高预设桶 `1:1,2:3,3:2,3:4,4:3,9:16,16:9,21:9,1:3,3:1`，可多选或使用 `all`。默认不启用桶筛选；启用后默认严格匹配。宽松模式偏差默认 5%，用 `--aspect-tolerance 0.03` 等参数调整。完整规则和评测指标见 [宽高比筛选说明](docs/USAGE.md#36-宽高比桶筛选)。

```powershell
# 严格筛选三个桶，同时要求短边至少 256 像素。
.\.venv\Scripts\python.exe -m image_cleaner filter --shards runs/statistics/shards --output runs/aspect_strict --resolution 256p --aspect-buckets "1:1,2:3,3:2" --aspect-mode strict

# 全部十个桶，允许包含边界的 5% 对称偏差；无须重新解码图片。
.\.venv\Scripts\python.exe -m image_cleaner filter --shards runs/statistics/shards --output runs/aspect_loose --aspect-buckets all --aspect-mode loose --aspect-tolerance 0.05
```

## 统计后按分辨率筛选

```powershell
# 第一次全量统计，不启用可选阈值和去重。
.\.venv\Scripts\python.exe -m image_cleaner run --input "D:\datasets\images" --output runs/statistics --config configs/statistics_only.json --workers 8

# 查看 runs/statistics/curated/report.html 后，保留短边大于或等于 256 像素的图片。
.\.venv\Scripts\python.exe -m image_cleaner filter --shards runs/statistics/shards --output runs/only_256p --resolution 256p

# 或只保留方向校正后的宽=256、高=256；其他尺寸全部进入 rejected.jsonl。
.\.venv\Scripts\python.exe -m image_cleaner filter --shards runs/statistics/shards --output runs/only_256_square --resolution 256x256

# 导出筛选后的图片与文本。
.\.venv\Scripts\python.exe -m image_cleaner export --curated runs/only_256p --output runs/export_256p --workers 8
```

`filter` 是 `merge` 的别名；它从全部分析分片重新筛选，复用已保存的尺寸与质量指标，不重新读图，也不会把不匹配的图片缩放到目标尺寸。

`256p` 在本工具中明确指 **短边大于或等于 256**：256×256、384×256、256×384、257×384、512×512 均符合；255×384 不符合。256 本身包含在内。`384x256` 指精确宽高，与 `256x384` 不同。尺寸取 EXIF 校正后的方向，筛选在导出缩放之前执行。`--resolution any` 关闭分辨率条件。

配置文件也可写 `"resolution": "256p"`；命令行覆盖该字段。所有其他启用规则继续取交集。使用 `statistics_only.json` 做第一轮后，单独传 `--resolution` 就只做尺寸筛选；需要加去重时同时指定 `--config configs/audit.json`。

`configs/above_256p.json` 可直接一键筛选。旧文件名 `configs/exact_256p.json` 保留为兼容配置，但其中的 `256p` 也采用大于或等于的新规则。

```powershell
.\.venv\Scripts\python.exe -m image_cleaner run --input "D:\datasets\images" --output runs/direct_256p --config configs/above_256p.json --workers 8 --export
```

## 安装与第一次运行

首次使用时，在下载后的项目根目录建立 `.venv`：

```powershell
cd D:\projects\multimodal_training
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

推荐第一次使用审计配置：先了解分布、查看重复样本，再设置质量阈值。输出必须在输入图片目录之外。

```powershell
.\.venv\Scripts\python.exe -m image_cleaner run --input "D:\datasets\images" --output "runs\first_audit" --config configs/audit.json --workers 8
```

如果每张图片有同名 UTF-8 文本，如 `001.jpg` 与 `001.txt`，加 `--sidecar-text`。同一目录递归支持 JPG、PNG、WebP、BMP、TIFF、GIF、AVIF；实际解码格式支持取决于 Pillow。默认拒绝动画和多页文件，不会悄悄把所有帧当成一张图。

若需要输出 RGB 标准化图片，加 `--export`。只导出保留样本，默认无损 PNG；默认不缩放、不裁剪、不去噪。配置 `export_max_edge` 后保持宽高比缩小，不放大。固定方形裁剪和图像张量均值/标准差归一化应由模型训练的数据加载器负责。

```powershell
.\.venv\Scripts\python.exe -m image_cleaner run --input "D:\datasets\images" --output "runs\first_audit" --config configs/audit.json --workers 8 --export --resume
```

`--resume` 必须使用相同输入、配置和分片规模。清单固定后请保持原始文件不变；分析续跑会跳过已记录样本，导出时另行校验源文件 SHA-256，发现变更会报告失败。

## 图文 JSONL 导入

大规模多机处理最适合使用固定 JSONL 清单。每行至少有 `path`；`text`、原始 `id` 和任意元数据可选：

```json
{"id":"origin-001","path":"animals/001.jpg","text":"一只猫坐在窗边。","metadata":{"source":"dataset-a","license":"your-source-license"}}
{"id":"origin-002","path":"charts/002.png","text":"柱状图显示不同月份的销量。"}
```

`path` 可以是绝对路径，或相对于 `--root` 的路径。为了跨机器迁移，推荐相对路径。同图多个描述/问答可使用多条记录；工具生成内部唯一 ID，原始 ID 存入 `source_id`，其他输入字段放入 `metadata`。

```powershell
.\.venv\Scripts\python.exe -m image_cleaner run --input "D:\datasets\pairs.jsonl" --root "D:\datasets\images" --output runs/pairs --config configs/audit.json --workers 8
```

不自动下载 URL、不解压任意压缩包；请先将图片落到本地磁盘、共享文件系统或对象存储挂载目录。

## 已实现的方法

| 方法 | 实现与输出 |
|---|---|
| 分辨率分布 | 宽、高、短边、长边、总像素数的直方图、均值、标准差、分位数；清洗前后对比 |
| 宽高比分布 | W/H 分桶，以及横图/竖图/近方图占比；可设置最小/最大比例 |
| 图片规范化 | EXIF 方向校正、ICC 转 sRGB（有配置文件时）、RGB 转换、透明背景合成；可选等比例缩小 |
| 文本规范化 | NFC 或 NFKC Unicode 规范化、换行统一、清除行末及首尾空白；保留段落；字符数和正则表达式筛选 |
| 解码与容量检查 | 损坏、截断、缺失文件、动画/多页图片，以及最大解码像素数检查 |
| 清晰度 | 原生像素区域上的拉普拉斯方差 `blur_score`；越低通常越模糊，但依赖图片内容 |
| 噪声估计 | 低梯度区域高通残差 MAD 估计 `noise_sigma`，像素范围 0–255 |
| 信噪比代理 | `snr_proxy_db = 20 log10(contrast / noise_sigma)`，无参考估计，不是真实 SNR 或 PSNR |
| 压缩块效应 | 在 8 像素网格不同相位上测量边界与非边界的梯度差 `blockiness`；是代理指标 |
| 完全重复 | 文件 SHA-256，以及方向/颜色/透明度规范化后的 RGB 像素 SHA-256 |
| 近似重复 | 64 位 pHash 候选检索 + dHash + 宽高比 + RGB 缩略图 RMSE 复核 |
| 可追溯筛选 | 每条记录保存指标、筛选原因、重复类型、代表样本 ID；配置及版本写入报告 |
| 标准化输出 | 独立 PNG/JPEG 图片及对应文本清单；支持导出续跑、源文件与输出完整性校验 |

此处“正则化”实施为数据规范化和文本正则筛选。Dropout、weight decay 等模型正则化属于训练代码，未混入清洗过程。

这版实现的是图片技术质量与基础文本清洗，**未实现 CLIP 图文语义对齐、VLM 事实核验、审美模型、OCR、水印分类器和语义 embedding 去重**。统计/感知指标不能替代这些模型的判断。

## 指标解释与阈值配置

`configs/audit.json` 默认只统计技术质量，开启去重与格式有效性检查；不设置模糊、噪声、信噪比和块效应阈值。`configs/generation_example.json` 是示例，采用短边至少 512、宽高比 0.25–4、文本非空；不要将示例当成适用于所有领域的标准。

可选字段如下，未列出的字段使用代码默认值；未知字段和非法范围会报错：

```json
{
  "min_short_edge": 512,
  "max_long_edge": null,
  "min_pixels": null,
  "max_pixels": null,
  "min_aspect_ratio": 0.25,
  "max_aspect_ratio": 4.0,
  "min_blur_score": null,
  "max_noise_sigma": null,
  "min_snr_proxy_db": null,
  "max_blockiness": null,
  "min_text_chars": 1,
  "max_text_chars": null,
  "forbidden_text_patterns": [],
  "duplicate_scope": "image_text",
  "deduplicate": true,
  "near_deduplicate": true,
  "phash_distance": 4,
  "dhash_distance": 4,
  "near_rgb_rmse": 8.0,
  "near_aspect_log_tolerance": 0.03,
  "metric_patch_size": 256,
  "max_decode_pixels": 100000000,
  "reject_animated": true,
  "alpha_background": [255, 255, 255],
  "convert_icc": true,
  "text_unicode_form": "NFC",
  "export_max_edge": null,
  "export_format": "png",
  "export_jpeg_quality": 95
}
```

质量指标使用最多五个原生像素区域（四角和中心，默认 256×256），取区域指标中位数。不会先缩图再估计噪声，但采样可能漏掉局部缺陷。高通噪声估计仍会受纹理、边缘、非高斯噪声影响；锐化、噪声也可能抬高清晰度分数。

信噪比代理的“信号”是局部亮度标准差。纯色、低对比图或噪声估计为零时结果为 `null`，附带 `snr_proxy_undefined`；缺失指标不触发对应阈值筛选。极小图片的部分质量指标也为 `null`。报告包含各指标缺失数，避免将未测到的指标误当成高质量。

ICC 转换失败会记录处理错误；16 位/浮点等输入最终转为 8 位 RGB，工具主要面向常规网页图像，不适合需要保留原始动态范围的科学影像。

建议查看 `report.html`，抽查保留和删除样本后再设置质量阈值。清洗前后直方图、计数、均值和标准差是全量计算；分位数在不超过 20,000 条时精确，更多时使用固定随机种子的蓄水池抽样，报告明确标记近似。

## 去重策略

默认 `duplicate_scope=image_text`：仅当规范化文本也相同时删除重复记录，保留同图的不同有效描述/问答；只导入图片时文本为空，等价于按图片去重。改为 `image` 则不区分文本，图片重复记录都会归并。

先应用质量阈值，再从合格样本选择代表，按 `像素数降序 → 噪声升序 → 清晰度降序 → 清单序号升序` 排序。被质量规则拒绝的样本不会压掉合格样本。每个近重复都直接匹配一个保留代表，不通过长链相似关系无限扩大分组。

近重复候选通过 SQLite 多索引哈希分带检索：将 64 位 pHash 分成 r+1 段，汉明距离不超过 r 的候选必然共享至少一个段。之后严格复核 pHash、dHash、宽高比和 RGB 缩略图误差。允许 pHash 半径 0–8。pHash 抑制相对于最大 AC 系数极小的系数，减少平滑/纯色图片重编码后的数值波动；它与普通 pHash 分数不可直接互换。

近重复仍是启发式，可能误合并细节仅有微小差异的图片，也可能漏掉大裁剪、翻转、文字覆盖或强颜色变化。先抽查 `duplicate.jsonl`；如只需要确定重复，设置 `near_deduplicate=false`。重复清单不会被删除，方便重新评估。

若后续划分训练/验证集，需要把同图及其变体放在同一侧；本工具尚未自动做数据集划分。

## 多机分布式处理

采用静态 map/reduce 分片，无需 Ray、Redis 或 GPU。每台机器独立运行多进程 worker；每条清单按 `seq % world_size` 分配到唯一 rank。所有机器必须使用同一份冻结后的清单、元数据和配置。

1. 在协调节点准备清单：

```bash
python -m image_cleaner prepare --input /data/images --manifest /jobs/input.jsonl --sidecar-text
```

将 `input.jsonl` 与 `input.jsonl.meta.json` 分发到节点。

2. 以下两条命令分别在两台机器上运行（也可用于同一机器模拟两节点）：

```bash
# 节点 0，本机目录可以不同；相对路径须对应同一份图片内容。
python -m image_cleaner analyze --manifest /jobs/input.jsonl --root /node0/images --output /jobs/shards --config configs/audit.json --workers 8 --world-size 2 --rank 0

# 节点 1
python -m image_cleaner analyze --manifest /jobs/input.jsonl --root /node1/images --output /jobs/shards --config configs/audit.json --workers 8 --world-size 2 --rank 1
```

每个节点也可以写本地目录。任务完成、进程退出后，把各自 `shard-*.sqlite` 收集到一个协调节点的目录。不要让多个机器写同一个 SQLite 文件；共享文件系统上不同 rank 的文件互不重叠，但更推荐节点本地磁盘后收集。

3. 在协调节点执行全局合并：

```bash
python -m image_cleaner merge --shards /jobs/shards --output /jobs/curated
```

缺少任一 rank、未完成分片、不同清单/配置/版本、重复序号都会报错。去重是在所有分片合并后完成，因此能找到跨节点重复。

4. 如需要物理输出标准化图片：

```bash
python -m image_cleaner export --curated /jobs/curated --root /data/images --output /jobs/normalized --workers 8
```

分析和导出均支持 `--resume`。中断后如果遗留 `.lock`，只有确认相关进程已经退出，才可删除对应锁文件后续跑。分析每 100 条提交 SQLite，最多重新分析尚未提交的记录。全局合并暂不支持中途续跑；合并失败时在新的输出目录重新执行，分析结果可复用。

静态分片不包含自动调度、节点故障迁移或动态负载均衡。多机分析可并行，最终全局合并去重由单个协调节点执行；索引、排序与中间结果落磁盘，不在内存保存全部样本，也不做全量两两比较。大量相似/纯色图片或过宽的哈希半径会增加候选复核成本。每个节点需读取整份清单来选择自己的 rank；超大清单可预先拆分调度，但当前 CLI 不接受不同清单直接合并。

并发任务队列最多约 `2 × workers`；统计抽样大小固定；SQLite 页缓存约 32 MiB。图片解码仍需原图大小的内存，因此高分辨率数据请适当降低 `--workers` 与 `max_decode_pixels`。这里提供的是规模化处理机制，没有宣称已完成百万/亿级性能基准。

## 输出文件

```text
runs/first_audit/
  input.jsonl                       # 固定清单
  input.jsonl.meta.json             # 清单 SHA-256、数量、默认数据根目录
  shards/
    shard-00000-of-00001.sqlite     # 分析结果与续跑检查点
    shard-00000-of-00001.jsonl      # 分片分析结果，方便查看
  curated/
    curation.sqlite                # 全局最终结果与哈希索引
    kept.jsonl                     # 保留记录，包含源路径与文本
    rejected.jsonl                 # 技术质量/文本规则拒绝记录
    duplicate.jsonl                # 重复类型、代表 ID、近重复距离
    error.jsonl                    # 解码或文件处理错误
    report.json                    # 配置、计数、前后分布
    report.html                    # 无外部依赖的 SVG 分布图
  normalized/                      # 仅 --export 时生成
    images/000000/sample-....png
    exported.jsonl                 # 图片相对路径与文本，可用于后续训练
    export_errors.jsonl
    export.meta.json
```

导出 `exported.jsonl` 的顺序可能随 worker 完成时间变化；每条 ID 和输出路径稳定。最终筛选清单均按固定输入序号输出。导出失败会写错误清单并返回非零退出码；单张图片分析失败记录到错误清单，不中止整批分析。

修改阈值后不必重复解码：

```bash
python -m image_cleaner merge --shards runs/first_audit/shards --output runs/filtered_v2 --config configs/generation_example.json
```

必须输出到新目录，图片预处理与质量测量设置不能更改；更改这些设置需要重新分析。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试涵盖多进程/两分片与单机一致性、跨分片重复、缺失分片拒绝、断点续跑、源文件变更、像素重复与近重复、同图多文本、EXIF/透明度、噪声估计、模糊指标、筛选后再统计，以及超过一批次的全局去重。

技术参考：[Pillow ImageOps](https://pillow.readthedocs.io/en/stable/reference/ImageOps.html)、[Python ProcessPoolExecutor](https://docs.python.org/3/library/concurrent.futures.html)。本工具的噪声实现采用原生像素高通残差估计，未调用 `skimage.estimate_sigma`，两者分数不可直接互换。
