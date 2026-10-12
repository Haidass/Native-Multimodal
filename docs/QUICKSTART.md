# 从下载到第一轮图片—文本初筛

## 1. 下载代码

安装 Python 3.10 或更新版本，建议 Python 3.12。使用 Git 下载，或者在 Space 首页下载源码 ZIP，解压到 `multimodal_training`。

```bash
git clone https://github.com/Haidass/Native-Multimodal.git multimodal_training
cd multimodal_training
```

后续命令都在此目录运行。安装过程需要联网下载 NumPy 和 Pillow；实际清洗不需要联网或 GPU。

## 2. Windows 安装

在 PowerShell 执行，无需激活环境或修改执行策略：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m image_cleaner --help
```

如果没有 Python 3.12，将第一条改为 `python -m venv .venv`，并先确认 `python --version` 至少为 3.10。

## 3. Linux 安装

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
.venv/bin/python -m image_cleaner --help
```

后文 Windows 命令中的 `.\.venv\Scripts\python.exe` 在 Linux 替换为 `.venv/bin/python`，路径也换成实际 Linux 路径。

## 4. 先用合成样例跑通

```powershell
.\.venv\Scripts\python.exe examples/make_demo.py
.\.venv\Scripts\python.exe -m image_cleaner run --input runs/demo_input --output runs/tutorial_statistics --config configs/statistics_only.json --workers 2 --sidecar-text
```

合成示例包括重压缩、同像素不同格式、模糊、噪声、低分辨率、透明图、极端宽高比和坏文件。示例不是真实训练数据。生成脚本发现目标目录已存在时会停止，避免覆盖。

预期输入 12 条，成功处理 11 条、错误 1 条；统计配置关闭可选阈值和去重，所以 11 条成功记录全部保留。打开 `runs/tutorial_statistics/curated/report.html`，查看原始分布与质量代理指标。坏文件应出现在 `error.jsonl`，不会使全批分析终止。

## 5. 实际数据怎样输入

最直接的方式是递归扫描图片文件夹：

```text
D:/datasets/images/
  animal/cat.jpg
  animal/cat.txt
  scene/road.png
  scene/road.txt
```

添加 `--sidecar-text` 会读取同名 `.txt` 文本（UTF-8）。没有文本的图片仍可导入；后续可用 `min_text_chars` 条件筛掉。

已有图文配对时更推荐 JSONL，一行一条，路径相对 `--root`：

```json
{"id":"cat-001","path":"animal/cat.jpg","text":"一只猫坐在窗边。","metadata":{"source":"my_corpus"}}
{"id":"road-002","path":"scene/road.png","text":"雨后的城市道路。"}
```

```powershell
.\.venv\Scripts\python.exe -m image_cleaner run --input "D:\datasets\pairs.jsonl" --root "D:\datasets\images" --output runs/my_statistics --config configs/statistics_only.json --workers 8
```

输出目录应位于原始图片文件夹之外。工具不修改或删除原始图片。文件夹导入未显式启用 `--sidecar-text` 时，不会自动配对文本。

## 6. 在统计后筛选尺寸与宽高比

```powershell
# 短边 >=256；宽高比在八桶内，允许 5% 对称偏差。
.\.venv\Scripts\python.exe -m image_cleaner filter --shards runs/tutorial_statistics/shards --output runs/tutorial_filtered --resolution 256p --aspect-buckets all --aspect-mode loose --aspect-tolerance 0.05

# 严格只留方图或 3:2 横图，输出到另一个目录。
.\.venv\Scripts\python.exe -m image_cleaner filter --shards runs/tutorial_statistics/shards --output runs/tutorial_strict --resolution 256p --aspect-buckets "1:1,3:2" --aspect-mode strict
```

`256p` 的定义是 `min(width,height)>=256`，包括正好 256；不要求短边必须等于 256。`256x256` 才是精确尺寸。宽高比始终按方向校正后的宽:高计算。

宽松偏差为 `max(r/b,b/r)-1`，`0.05` 表示 5%。参数说明和完整边界例子见 [完整手册](USAGE.md)。重筛选读取原分析分片，不重复解码；规则取交集。

## 7. 再加入文本与去重规则

在项目根目录新建 `my_rules.json`：

```json
{
  "resolution": "256p",
  "aspect_buckets": "all",
  "aspect_mode": "loose",
  "aspect_tolerance": 0.05,
  "min_text_chars": 1,
  "deduplicate": true,
  "near_deduplicate": true,
  "duplicate_scope": "image_text"
}
```

```powershell
.\.venv\Scripts\python.exe -m image_cleaner filter --shards runs/tutorial_statistics/shards --output runs/tutorial_dedup --config my_rules.json
```

显式提供 JSON 配置文件时，未写字段使用 Config 默认值，而不是与分析分片逐项合并。默认开启精确与近重复去重；纯统计配置特意关闭了它们。同图不同规范化文本默认保留；改为 `duplicate_scope=image` 会忽略文本差异。

质量阈值如 `min_blur_score`、`max_noise_sigma`、`min_snr_proxy_db`、`max_blockiness` 应先在代表性样本上校准，再写入配置；没有跨数据域通用的推荐数值。

## 8. 查看报告、导出训练清单

```powershell
.\.venv\Scripts\python.exe -m image_cleaner export --curated runs/tutorial_dedup --output runs/tutorial_export --workers 2
```

清洗目录有 `kept/rejected/duplicate/error.jsonl`、`report.json`、`report.html`、`curation.sqlite`。导出目录有 RGB 图片、相对路径与文本组成的 `exported.jsonl`、失败清单和续跑元数据。

优先核对以下项目：

1. 输入数等于保留、拒绝、重复、错误四类数量之和。
2. 错误数及错误原因；质量缺失指标并不等于通过人工质量检验。
3. 分辨率与桶的独立通过率，以及全部规则通过率。
4. 各桶前后样本数量；抽查被拒绝的临界图片和近重复判定。
5. 最终图文是否对应。当前工具没有图文语义一致性模型。

导出的文本已做 Unicode 与空白规范化；图片做 EXIF、色彩、透明度与 RGB 标准化。训练时的张量归一化、裁剪和多模态对齐仍由训练数据加载器负责。

## 9. 测试和后续阅读

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

基础版本包含 22 项清洗测试。详细参数见 [参数参考](PARAMETERS.md)，多机与续跑见 [部署指南](DEPLOYMENT.md)，逐阶段指标见 [完整手册](USAGE.md)，实现细节见 [代码说明](ARCHITECTURE.md)。
