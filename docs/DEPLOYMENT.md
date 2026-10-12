# 部署、分布式运行与发布指南

## 1. 部署形式如何选择

| 场景 | 方式 |
| --- | --- |
| 初次使用、少量验证 | Windows / Linux 虚拟环境 + CLI |
| 单台服务器大批量 | Linux CLI，多进程 analyze，磁盘上的 SQLite |
| 环境一致性与隔离 | Docker CLI 容器，挂载原图与任务目录 |
| 多机数据分析 | 同一 manifest，静态 rank/world-size，汇总完整分片 |
| 在线共享代码和教程 | Hugging Face 静态 Space；不执行清洗后台 |

清洗不依赖 GPU，也不加载大模型。核心依赖 Python>=3.10、NumPy>=1.26,<3、Pillow>=10.4,<13。实际解码格式取决于 Pillow 构建与可用编解码器，文件扩展名支持不保证所有格式均能解码。

## 2. Windows 部署

```powershell
git clone https://github.com/Haidass/Native-Multimodal.git multimodal_training
cd multimodal_training
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m image_cleaner --help
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

不需要激活虚拟环境或修改 PowerShell 执行策略。路径有空格时加双引号。不要把 output 放到图片输入树中，以免后续重新导入导出文件。

默认 workers 为 min(8,CPU 数量)，大图先设 2 或 4，观察内存与吞吐量再上调。配置 max_decode_pixels 控制解码容量；降低上限会把超限图片记入 error，不能替代缩图。

## 3. Linux 部署

```bash
git clone https://github.com/Haidass/Native-Multimodal.git multimodal_training
cd multimodal_training
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
.venv/bin/python -m image_cleaner --help
.venv/bin/python -m unittest discover -s tests -v
```

```bash
.venv/bin/python -m image_cleaner run --input /data/images --output /jobs/statistics --config configs/statistics_only.json --workers 8 --sidecar-text
.venv/bin/python -m image_cleaner filter --shards /jobs/statistics/shards --output /jobs/filtered --config configs/audit.json --resolution 256p --aspect-buckets all --aspect-mode loose --aspect-tolerance 0.05
.venv/bin/python -m image_cleaner export --curated /jobs/filtered --output /jobs/normalized --workers 8
```

离线运行前应在对应平台准备 Python、包安装文件和代码。可以执行 `pip download . --dest wheels` 收集核心依赖；打包方式和平台不同，应在目标环境验证。原图、任务文件建议放在可靠本地盘或受控共享存储，优先使用快盘承载 SQLite。

## 4. Docker CLI 部署

从项目根目录构建：

```bash
docker build -f docker/Dockerfile -t nmm-data-preprocess:0.3.1 .
docker run --rm nmm-data-preprocess:0.3.1 --help
```

以下为 Linux 主机路径示例。先创建输出目录，并确保容器 UID 1000 对其有写权限；数据目录只读挂载：

```bash
mkdir -p /jobs/nmm
docker run --rm \
  -v /data/images:/data/images:ro \
  -v /jobs/nmm:/jobs \
  -v "$PWD/configs:/configs:ro" \
  nmm-data-preprocess:0.3.1 \
  run --input /data/images --output /jobs/statistics --config /configs/statistics_only.json --workers 4 --sidecar-text

docker run --rm \
  -v /jobs/nmm:/jobs \
  -v "$PWD/configs:/configs:ro" \
  nmm-data-preprocess:0.3.1 \
  filter --shards /jobs/statistics/shards --output /jobs/filtered --config /configs/audit.json --resolution 256p --aspect-buckets all --aspect-mode loose

docker run --rm \
  -v /data/images:/data/images:ro \
  -v /jobs/nmm:/jobs \
  nmm-data-preprocess:0.3.1 \
  export --curated /jobs/filtered --output /jobs/normalized --workers 4
```

第二条仅使用缓存分片，无须挂载源图；第三条 export 必须能访问原图，路径保持与分析时相同，或通过 `--root` 改写。容器不是 HF Docker Space 应用，没有监听 Web 端口。

Windows Docker Desktop 中将 Linux 主机路径替换为实际 Windows 路径；PowerShell 多行续行用反引号，或把命令写成一行，不使用 Bash 的反斜线续行。Dockerfile 是提供的部署方案，Docker 是否可用需在用户环境实际构建验证。

## 5. 两台服务器的完整流程

### 5.1 制作一份全局清单

在协调机器运行：

```bash
.venv/bin/python -m image_cleaner prepare --input /data/images --manifest /jobs/input.jsonl --sidecar-text
```

把 `input.jsonl`、`input.jsonl.meta.json`、同一代码版本和统计配置分发到所有分析节点。每台节点都要能访问属于其 rank 的图片，图片根目录可以不同；相对路径结构必须保持一致。

### 5.2 各节点分析

节点 0：

```bash
.venv/bin/python -m image_cleaner analyze --manifest /jobs/input.jsonl --root /mnt/images --output /jobs/node0 --config configs/statistics_only.json --rank 0 --world-size 2 --workers 8
```

节点 1：

```bash
.venv/bin/python -m image_cleaner analyze --manifest /jobs/input.jsonl --root /dataset/images --output /jobs/node1 --config configs/statistics_only.json --rank 1 --world-size 2 --workers 8
```

每条记录按 `seq % 2` 分配。不要让两个节点使用同一 rank，也不要向同一个 SQLite 并发写入。节点失败后在原节点增加 `--resume`，其余参数不变。

### 5.3 收集分片

分析进程正常退出后，将各节点的 `shard-00000-of-00002.sqlite` 与 `shard-00001-of-00002.sqlite` 放到协调节点 `/jobs/all_shards`；JSONL 可同时保留用于检查，但 merge 读取 SQLite。必须等分析完成再复制，避免只复制仍依赖 WAL 的数据库。

先形成统计报告，然后全局筛选去重：

```bash
.venv/bin/python -m image_cleaner merge --shards /jobs/all_shards --output /jobs/global_statistics
.venv/bin/python -m image_cleaner filter --shards /jobs/all_shards --output /jobs/global_filtered --config configs/audit.json --resolution 256p --aspect-buckets all --aspect-mode loose --aspect-tolerance 0.05
```

两轮都不需要原图。协调节点会验证所有 rank 完整、配置/清单/身份一致、总记录数匹配；缺失 rank 时拒绝合并，不能把局部去重当全局去重。

### 5.4 统一导出

协调节点必须能访问全部保留原图，以统一根目录覆盖各节点不同的绝对 source_path：

```bash
.venv/bin/python -m image_cleaner export --curated /jobs/global_filtered --root /coordinator/images --output /jobs/global_export --workers 8
```

相对图片路径依据原清单保持不变。当前 CLI 不把导出任务再分配到多个 rank，多机能力主要在 analyze 阶段。

## 6. 续跑、重筛选与故障处理

| 情况 | 操作 |
| --- | --- |
| analyze 正常中断 | 相同命令加 --resume；同 manifest/config/version/rank |
| run 需要继续 | 相同命令加 --resume，不改输入、配置、root、sidecar 设置 |
| export 中断 | 相同命令加 --resume；会检查源和已有输出哈希 |
| 想调整尺寸、桶或质量阈值 | 从原 shards filter 到新的输出目录 |
| 想调整颜色、测量区域等 | 新输出目录重新 analyze，再 merge |
| merge 中途失败 | 无中途续跑支持；选新输出目录重新 merge，复用 shards |
| 出现 .lock | 先确认对应进程已退出，才删除该特定锁；运行中不能删 |
| 源文件在分析后变更 | export 拒绝哈希不一致的样本；重新制作/分析数据 |
| 缺少 rank | 补齐原任务的分片，不修改 world-size 去掩盖缺失 |

analyze 每 100 条提交一次检查点，异常退出最多重算尚未提交的记录。改变软件版本会影响 resume 身份，生产作业应固定版本与配置，保存元数据。

代码读取整个 manifest 再挑选本 rank，不是自动调度器；超大清单、偏斜大图、共享盘拥塞可能限制速度。建议先测 1–10 万代表性图片，记录图片/秒、CPU、峰值内存、磁盘占用、错误率和各规则保留率，再确定节点与进程数。已有测试不等于百万级吞吐量基准。

## 7. Hugging Face Space 的发布方式

本项目使用静态 Space：根 README YAML 指定 `sdk: static` 与 `app_file: index.html`。页面包含教程、参数、代码下载、命令生成器和比例匹配演示；不连接用户文件系统、不提交训练图片、不运行服务器清洗。

选择静态托管使文档入口无需清洗计算资源。官方规则参见 [Static HTML Spaces](https://huggingface.co/docs/hub/spaces-sdks-static) 和 [配置参考](https://huggingface.co/docs/hub/spaces-config-reference)。

构建发布文件：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[publish]"
.\.venv\Scripts\python.exe tools/build_space.py
```

本地预览：

```powershell
.\.venv\Scripts\python.exe -m http.server 8000 --bind 127.0.0.1
```

打开 `http://127.0.0.1:8000/index.html`。这是预览服务器，不是清洗 API。终端 Ctrl+C 停止。

发布脚本仅从当前进程的 `HF_TOKEN` 读取认证，不使用账号密码，不调用持久化 login，不把 Token 写到任何文件。可在安全终端设置环境变量，或从秘密管理器注入；不要在脚本和配置中硬编码真实 Token。

```bash
# HF_TOKEN 已通过环境或秘密管理器注入。
.venv/bin/python tools/publish_space.py --repo-id HanWangTJU/NMM-data-preprocess
```

脚本检查登录身份与仓库命名空间；创建不存在的 public static Space，或更新已有 static Space。它不删除远端文件、不变更现有私有性，也不申请付费硬件。先构建再发布，使用 release/publish-manifest.json 指定白名单与 SHA-256；发布前校验哈希、路径和凭据模式。

发布内容包括库、测试、配置、示例、教程、页面、Dockerfile 和源码 ZIP；排除 .venv、runs、个人原图、运行日志、认证信息与系统路径。README 和 ZIP 保持同一版本，发布收据保存 repo ID、提交 ID、文件数量和页面地址。

如果网络直连失败，可按实际网络环境设置 HTTPS_PROXY 连接官方 huggingface.co；不要为上传将认证 Token 发给未经确认的镜像站。权限不足时需要具有目标 Space 写入/创建权限的 Token。

## 8. 部署后的验收

1. `--help` 可用，测试通过，合成样例能统计/筛选/导出。
2. 报告四类数量守恒，错误清单包含合成坏文件。
3. 256p 和宽高比 5% 临界行为符合说明。
4. 双 rank 与单机结果一致，源图删除后的缓存 filter 正常。
5. 在线文档和源码 ZIP 可打开，远端文件没有凭据或个人数据。
6. 实际数据上再做人工抽查与性能试运行，确认阈值和资源设置合适。
