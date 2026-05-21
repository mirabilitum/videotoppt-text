# Gensee 录播处理流水线

本项目用于处理 Gensee 录播课程，把播放页里的音视频整理成可复用的学习资料：

- 下载课程音频和视频
- 按固定时长切分音频
- 用 FunASR 生成逐段转写
- 合并转写结果
- 从视频里提取 PPT 关键帧
- 生成联系表和课程大纲

## 固定依赖

运行环境为 Python 3.13。

本地依赖包括：

- `bin/ffmpeg.exe`，优先作为 ffmpeg
- `models/`，本地 FunASR / ModelScope 模型缓存
- `packages/`，本地 wheel 和离线依赖
- `.env`，运行配置、路径、API Key 和阈值配置

Python 依赖见 `requirements.txt`，当前包含：

- `funasr`
- `modelscope`
- `openai`
- `requests`
- `opencv-python-headless`
- `imageio-ffmpeg`
- `python-dotenv`
- `openpyxl`
- `playwright`

其中：

- `openai` 主要用于大纲生成步骤
- `playwright` 作为抓取播放页里 `m3u8` 的备用方式
- `openpyxl` 只用于批量 Excel 读取

## 支持格式

### 输入

- 播放页：Gensee 播放页 URL
- 批量清单：`xlsx`，首行是表头
- 批量表头：代码按列名关键字匹配 `序号`、`课程名称`、`视频链接`

当前代码只按 `xlsx` 方式读取批量清单，不直接读取 `xls` 或 `csv`。

### 媒体源

当前流程以 Gensee 的 HLS 地址为准，主要识别：

- `record1.m3u8`
- `recordaudioonly.m3u8`

也就是说，这条流水线的核心输入是 Gensee 播放页解析出来的流地址，不是通用的本地视频导入工具。

### 输出

当前产物主要是这些格式：

- `wav`：原始音频和分段音频
- `json`：课程信息、m3u8 缓存、转写结果、流水线状态
- `txt`：合并后的转写文本和分段转写文本
- `ts`：下载后的完整视频和切分后的视频片段
- `jpg`：提取的关键帧和联系表
- `md`：课程大纲、章节大纲和中间草稿

当前流水线不自动生成 `docx`，历史目录里出现的 `outline.docx` 不是这版代码的标准输出。

## 目录说明

- `scripts/`：所有可执行脚本
- `scripts/common.py`：通用配置和工具函数
- `tests/`：单元测试
- `output/`：运行产物，全部是生成数据
- `models/`、`packages/`：本地大模型和离线依赖
- `bin/ffmpeg.exe`：本地 ffmpeg
- `.env`：本地配置，不应提交

## 常用命令

环境检查：

```powershell
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe scripts\check_env.py
```

跑单元测试：

```powershell
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -m unittest discover -s tests -v
```

单个课程流程：

```powershell
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe scripts\run_pipeline.py --page-url <URL> --title "课程标题" --seq 1 --output-dir D:\video\output\001_课程标题 --part-seconds 900
```

批量运行：

```powershell
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe scripts\run_batch.py --dry-run
```

## 版本控制约定

以下内容不应上传到 git：

- `.env` 及其他环境文件
- `output/` 下的全部生成数据
- 批量 Excel，如 `*.xlsx`
- 本地模型、离线包和缓存
- Python 缓存和测试临时目录

