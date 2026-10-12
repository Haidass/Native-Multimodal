# 代码结构、功能与实现边界

## 1. 当前版本与数据流

当前清洗库版本 0.3.1，面向图片—文本对。0.3.1 修复透明图片导出时 JSON 数组背景色回读的类型问题。主要流程：

```text
图片文件夹 / 图文 JSONL
        │ prepare：稳定编号、固定清单与 SHA-256
        ▼
input.jsonl + input.jsonl.meta.json
        │ analyze：rank 分片、本机进程池、规范化、测量
        ▼
各 rank 的 SQLite + JSONL
        │ merge/filter：重算规则、全局去重、流式统计
        ▼
kept / rejected / duplicate / error + HTML/JSON 报告
        │ export：源哈希校验、输出规范化 RGB
        ▼
标准化图片 + exported.jsonl → 训练数据加载器
```

`run` 将单机 prepare、analyze、merge 顺序串联，可选 export。静态 Space 仅展示文档与命令，算法在用户机器运行。

## 2. 功能矩阵

| 环节 | 已实现 | 输出/限制 |
| --- | --- | --- |
| 批量导入 | 递归图片目录、同名 txt、图文 JSONL | 固定清单，路径和原始 ID 可追溯 |
| 解码检查 | 坏文件、截断、像素容量、动画/多页 | 单图失败进入 error，不删除源图 |
| 标准化 | EXIF、ICC→sRGB、RGB、透明合成、Unicode/空白 | 无模型推理，无默认裁剪 |
| 尺寸统计 | 宽高、短/长边、像素、比例、方向分类 | 全量直方图，最多 20000 样本估计分位数 |
| 尺寸筛选 | 短边 >=Np、精确 WxH、其他尺寸阈值 | 条件取交集，包含边界 |
| 宽高比桶 | 八桶、多选/全选、严格/宽松、可调容差 | 固定桶，不做桶均衡采样 |
| 技术质量 | 模糊、噪声、SNR、块效应代理 | 最多五个原生像素区域，不是人工质量结论 |
| 文本检查 | 长度、Unicode、广告等正则 | 不评估图文语义一致性 |
| 精确去重 | 原字节、规范化像素 | 跨全部分片全局处理 |
| 近重复 | pHash、dHash、比例、RGB RMSE 联合 | 启发式，不是 embedding 语义去重 |
| 并行/续跑 | 本机进程池、多机静态 rank、SQLite 检查点 | 合并由单协调节点，无自动调度 |
| 导出 | 独立 RGB PNG/JPEG、缩小长边、清单、续跑校验 | 原图不改动，固定裁剪由训练端完成 |

用户提到的“正则化”在此实现中是数据规范化和规则校验；不包含训练损失正则化、数据增强或张量标准化。

## 3. 源码文件说明

| 文件 | 主要职责 |
| --- | --- |
| `image_cleaner/cli.py` | argparse 子命令、CLI 覆盖、run 串联与返回码 |
| `image_cleaner/config.py` | 不可变 Config、JSON 加载、字段与数值校验、配置指纹 |
| `image_cleaner/io.py` | 目录导入、JSONL、清单身份、原子写入、锁、SQLite、限流进程池 |
| `image_cleaner/images.py` | EXIF/色彩/透明度/文本规范化、单图分析与筛选原因 |
| `image_cleaner/aspect.py` | 精确分数比较、对称偏差、确定性桶分配 |
| `image_cleaner/metrics.py` | 原生区域质量测量、pHash/dHash、缩略图与 RMSE |
| `image_cleaner/jobs.py` | 分片分析、全局过滤、落盘去重索引、导出与续跑 |
| `image_cleaner/stats.py` | 增量统计、蓄水池分位数、内嵌 SVG HTML 报告 |
| `tests/test_pipeline.py` | 导入/清洗/去重/导出/分片/续跑/容量等测试 |
| `tests/test_aspect.py` | 容差边界、横竖桶、重叠分配、CLI、缓存重筛选 |
| `configs/` | 统计、尺寸、桶、去重、生成数据示例配置 |
| `examples/` | 生成合成演示样本 |
| `docker/Dockerfile` | CLI 容器部署，不启动 Web 服务 |
| `tools/build_space.py` | 文档转换、静态页面、源码 ZIP、发布文件校验清单 |
| `tools/publish_space.py` | 从环境变量读取 Token，检查账号并发布白名单文件 |
| `web/` | Space 首页模板、样式与交互脚本 |

`index.html`、`manual/*.html` 与 `downloads/*.zip` 是发布构建产物。运行结果在 runs，虚拟环境在 .venv，两者不发布。

## 4. 记录格式与状态

外部 JSONL 每条必须有字符串 path；text 为字符串或 null，metadata 为对象。输入 id 会保存为 source_id，程序生成稳定 `sample-000000000000` 等 ID 与 seq。其他外部字段放入 metadata，避免覆盖计算指标。

分析记录包含源路径、规范化文本、原始/校正宽高、文件/像素哈希、质量指标、预处理元数据、警告和筛选原因。最终状态：

| 状态 | 含义 |
| --- | --- |
| kept | 所有规则通过，且没有归并到其他代表 |
| rejected | 至少一个尺寸/桶/质量/文本规则不通过 |
| duplicate | 规则通过，但精确或近重复判定归并到代表 |
| error | 无法成功解码、规范化或测量 |

最终满足 `input_count=kept+rejected+duplicate+error`。同一拒绝记录可以有多个原因，原因计数不可当成样本数相加。

## 5. 为什么统计后能反复筛选

analyze 保存成功处理的完整指标，即使当时被阈值拒绝也写入 SQLite。filter 从全部记录重新计算规则，而不是仅读取上一次 kept。因此放宽阈值能恢复以前被拒绝的记录，收紧阈值也无需读源图片。

六项测量设置（reject_animated、max_decode_pixels、metric_patch_size、alpha_background、convert_icc、text_unicode_form）不允许在重筛选中改变。变化会使缓存指标不再对应新处理逻辑，需要重新分析。

## 6. 并行、去重和内存

进程池并发队列约为 workers 的两倍，结果逐条写入 SQLite。rank 使用 `seq % world_size`，每个 rank 写单独数据库。全局 merge 校验完整分片并按稳定优先级处理候选。

精确去重索引基于哈希，近重复使用 pHash 的 r+1 分段索引召回候选，再复核联合条件。索引和排序主要落磁盘，不将全量图片或全量指标载入内存；没有全数据两两比较。

合并批量处理 500 条候选，SQLite 缓存约 32 MiB，分位数抽样上限 20000。图片解码内存仍随原图像素与 workers 增长。大量相似图片、纯色图或高半径设置会增加复核成本，不能据此承诺百万/亿级吞吐量。

## 7. 当前没有覆盖的训练前审核

当前不包含 OCR、水印/人脸/隐私识别、NSFW/版权判定、CLIP 语义一致性、美学模型、语义 embedding 去重、训练/验证集防泄漏、WebDataset/Parquet 输入、视频/音频、训练采样配额、动态集群调度。

初筛保留仅代表通过已配置规则；训练前仍应按来源、内容类别和任务需求抽查，确认图文对齐及样本可用性。若后续扩展，应保留现有 source_id、稳定清单和每条决策原因便于追溯。

## 8. 验证与版本

现有 22 项清洗测试覆盖正好 5% 边界、零容差、桶重叠、源文件删除后的缓存筛选、多进程/双分片一致性、去重、多文本、断点续跑、透明图导出与配置回读、导出校验、分辨率包含边界等。发布时另核验文档参数与代码、ZIP 白名单和页面链接。

恢复分析要求软件版本与配置指纹一致。合并旧分片需要同一批分片身份一致且当前代码能够读取其字段；不要混用不同软件版本、不同清单或不同 world-size 的分片。版本号不代表数据清洗标准或模型效果保证。
