# 格式工坊 Format Hub

一台机器部署，整个团队通过浏览器处理文件。文件在部署机器本地转换，支持 Windows、macOS、Linux、Android、iOS 浏览器客户端。

[下载发行包](https://github.com/huanxiang666/format-hub/releases) · [问题反馈](https://github.com/huanxiang666/format-hub/issues) · [引擎来源清单](engine-sources.json)

## 开始使用

**优先下载 Releases 中与你系统匹配的发行包，解压到可写目录。**

- Linux x64：运行 `./format-hub`。完整包包含 Python、FFmpeg、LibreDWG、中文字体和 LibreOffice。
- Windows x64 完整引擎包：双击 `启动格式工坊.cmd`。包内包含 Python、LibreOffice、FFmpeg、LibreDWG和字体，首次启动无需联网下载引擎。
- macOS：新增 Apple Silicon（arm64）与 Intel（x64）完整引擎构建，包内包含 LibreOffice.app 和应用运行环境，使用包内 `.command` 启动。以 Releases 实际附件和对应平台验收记录为准；没有附件的平台仍视为待验证，不把构建脚本当成成品。
- 完整包体积更大，用一次下载换取离线开箱即用。v1.1.0精简Windows包仍保留，其首次下载行为与完整包不同。

打开 `http://127.0.0.1:8765`；同一局域网的同事打开 `http://部署机器IP:8765`。启动窗口会显示地址。电脑需要保持开机；公网部署请使用 HTTPS 反向代理并设置团队口令。

Linux 完整包当前构建环境为 **Ubuntu 26.04 x64 / glibc 2.43**，转换隔离需要 **Linux 6.7+、Landlock ABI 4+、libseccomp**。其他发行版不承诺二进制兼容；可使用下述源码或 Docker 构建。Windows发行包须以Release附带的CI验收结果为准，不将脚本存在当作实机通过。

## 这次完善的交互

- **图片实时预览与裁剪**：鼠标/触屏选区、拖动、数字选区、重置；一寸、二寸、头像预设；尺寸模式、底色、质量档位中文显示。
- **图片输出参数**：像素尺寸、旋转、画布颜色、DPI、JPEG/WebP 文件大小上限。裁剪发生在EXIF方向校正之后，服务端再次检查坐标。
- **PDF合并**：文件选择器一次选多份，也可继续追加，支持上移下移，显示每份PDF页数。
- **PDF拆分**：上传后先读取真实总页数和前6页缩略图，再填写 `1-3,4-6` 等范围；加密文件填写密码后预览。
- **CAD 字体**：自带开源中文字体作为缺失字体替代；可上传本次任务的 TTF/TTC/OTF/SHX，真实字体优先。支持快速、均衡、精细三档曲线质量。
- **相关文件名**：下载结果保留原上传文件名线索；合并结果带首份名称和数量。服务器内部仍用随机任务目录与序号文件名，避免路径穿越和同名覆盖。

图片预览用于确认选区和布局，压缩后的精确字节数以下载结果为准。红蓝白底是画布填色，不包含自动人像抠图；动画文件的首帧/多帧规则会明确提示。

## 工具一览

| 类别 | 功能 |
|---|---|
| 图片 | PNG/JPG/WebP/BMP/TIFF/ICO/GIF/AVIF互转、尺寸/选区裁剪、质量与大小上限、旋转、水印、去EXIF、DPI、多图PDF |
| PDF | 合并、拆分、页码提取重排、旋转、无损整理、文字提取、图片、水印、页码、AES256加密与使用正确密码解密 |
| PDF与Word | 可编辑DOCX、整页图片型DOCX两种模式 |
| Office/ODF | DOC/DOCX/ODT/RTF/TXT、XLS/XLSX/ODS/CSV、PPT/PPTX/ODP各自同类转换及PDF输出 |
| 音视频 | MP4/MKV/MOV/AVI/WebM；MP3/WAV/FLAC/OGG/M4A/AAC/Opus转换与音轨提取 |
| CAD | DWG→DXF/PDF/SVG；DXF→PDF/SVG/规范化DXF；中文字体替代、任务字库和曲线质量 |
| OFD | PDF与图片型OFD双向转换，严格检查不支持对象 |
| SVG | 自包含SVG→PNG/PDF |
| 压缩包 | ZIP/TAR/TAR.GZ安全重打包 |

**能力边界**：扫描PDF没有自动OCR；复杂Office版式/宏/动画/外链需复核；CAD为模型空间二维输出，未实现DXF→DWG或版本互转；OFD只支持图片型，文字/矢量发票与签名等明确报错；无损PDF整理未保证体积缩小；字幕/附件/多音轨不自动保留；暂不支持RAR/7Z。

## 团队、数据与限制

默认单文件200MB、每任务30个文件、2个转换进程、40个等待任务，文件保留24小时并定期清理。任务和结果按浏览器会话隔离，同一浏览器返回可继续下载；清除Cookie或换浏览器不显示原会话记录。本版不含跨设备账户同步和SSO。

源码部署默认数据在项目 `data/`。原生发行包默认使用用户数据目录：

- Windows：`%LOCALAPPDATA%\format-hub\data`
- Linux：`~/.local/share/format-hub/data`（遵循XDG_DATA_HOME）
- macOS源码默认仍为项目data，若打包则为`~/Library/Application Support/format-hub/data`

可用 `--data-dir` 指定独立数据目录。不要把原生发行包数据放进应用程序/引擎目录。Linux任务子进程继承文件与网络隔离、超时、并发及容量约束；字体和PDF预览也在隔离子进程内处理。Windows/macOS原生没有Linux Landlock隔离，公网不可信上传建议放入受限容器。

启用团队口令：设置 `FORMAT_HUB_TOKEN` 环境变量后启动。不要把口令写入URL、源码或提交到GitHub。

## 引擎与国内网络

小型引擎随包提供。完整引擎版将较大的Office引擎也装入包内，不再在用户首次启动时下载。构建时仍使用**固定HTTPS源、固定SHA256、下载/解包预算、原子落盘**准备引擎，不写用户系统Office安装。精简/源码部署保留可选引擎准备命令。源与许可证见 [engine-sources.json](engine-sources.json)。源码安装优先清华PyPI，失败回落官方PyPI。

```bash
# 完整包离线启动，不下载引擎
format-hub --offline
# 精简/源码部署保留固定白名单引擎命令
format-hub --install-engine windows-office
format-hub --install-engine windows-dwg
```

所有下载源都可能受网络环境影响，下载器失败会显示具体源和原因，不把失败标成已安装。GitHub发布页有源码、引擎来源、校验文件和平台验收说明。未把不明镜像设为自动执行来源。

## 源码启动

Python 3.12，Linux/macOS：

```bash
python3 bootstrap.py --source
bash start.sh
```

Windows：安装Python后可使用源码 `start.bat`；普通用户优先下载发行包。源码版Office需可用`soffice`，或使用`SOFFICE`指定引擎。当前Ubuntu的项目内Office组装可用`python3 setup_office.py --download`，该脚本非通用发行版安装器。

```bash
python run.py --host 0.0.0.0 --port 8765 --data-dir /独立数据目录
```

Linux源码启动会编译Office IPC适配库，需要C编译器；完整发行包已经编译好。不能满足Landlock/系统库条件时任务明确失败，不会静默关闭隔离。

## Docker

```bash
docker compose up -d --build
```

镜像包含Office、FFmpeg和字体，DWG引擎需为目标架构配置`DWG2DXF`。宿主内核及容器系统调用策略必须支持Landlock/seccomp；Docker路径仍以实际CI/目标机验证说明为准。

## 主要配置

| 环境变量 | 默认 | 含义 |
|---|---:|---|
| FORMAT_HUB_HOST / PORT | 0.0.0.0 / 8765 | 共享监听地址与端口 |
| FORMAT_HUB_DATA | 见数据目录 | 独立任务数据根目录 |
| FORMAT_HUB_TOKEN | 空 | 团队访问口令 |
| FORMAT_HUB_WORKERS | 2 | 并发1–8 |
| FORMAT_HUB_TIMEOUT | 600 | 单任务秒数上限 |
| FORMAT_HUB_MAX_FILE_MB / MAX_FILES | 200 / 30 | 上传限制 |
| FORMAT_HUB_RETENTION_HOURS | 24 | 文件保留时间 |
| FORMAT_HUB_MEMORY_MB | 2048 | Linux worker虚拟内存上限 |
| FORMAT_HUB_JOB_MB / STORAGE_MB | 1024 / 10240 | 任务/全部文件预算 |
| FORMAT_HUB_MIN_FREE_MB | 512 | 磁盘保留空间 |
| SOFFICE / DWG2DXF / FFMPEG | 自动检测 | 对应引擎可执行路径 |

只启动一个HTTP进程，内部队列管理转换worker，勿配置多个Uvicorn进程。应用层容量预算有运行采样和完成检查，不等于文件系统硬配额。

## 开发和验证

```bash
python -m pytest tests -q
python -m unittest discover -s packaging -p test_bootstrap.py -v
node --check app/static/app.js
node --check app/static/editor.js
```

`GET /api/catalog`给出实际能力；`POST /api/inspect`读取PDF页数与预览；`POST /api/jobs`上传转换，`fonts`为可选CAD字体文件；`GET /api/jobs`仅返回本会话。完整契约在`/openapi.json`。

发布前验证真实文件、错误/边界、并发/会话、裁剪结果、字体和原视频，并从无关工作目录启动打包产物走HTTP转换。测试数据使用隔离临时目录，不进入发行包。

## 许可与来源

AGPL-3.0；第三方引擎和字体保持各自许可，见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) 和包内licenses。参考 [pdf2word](https://github.com/huanxiang666/pdf2word)、[pdftoolbox](https://github.com/huanxiang666/pdftoolbox)、[dwg2pdf](https://github.com/huanxiang666/dwg2pdf)、[pdf-to-ofd](https://github.com/huanxiang666/pdf-to-ofd)。用户文件不上传第三方转换网站。
