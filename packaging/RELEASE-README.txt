格式工坊 v1.1.0 — 本地格式转换

Windows x64
解压完整目录到自己可写的位置（不要只复制 EXE）。双击“启动格式工坊.cmd”。首次启动自动下载并校验 LibreOffice 官方引擎，装入当前目录 runtime/libreoffice，无需安装 Python、FFmpeg、LibreDWG 或系统 Office。
Office 下载超过 350MB，请保持网络连接。失败时可继续使用图片/PDF/音视频/CAD 功能，稍后再次双击重试。也可在命令行执行 format-hub.exe --install-engine windows-office。
程序地址默认 http://127.0.0.1:8765 ，浏览器访问即可。Ctrl+C 停止。
本地构建未配置代码签名；Windows CI 实测记录由发布者单独提供。

Linux x64 完整包
在 Ubuntu 26.04 x64 上解压后运行 ./format-hub --host 127.0.0.1 。本包携带 Python、应用依赖、FFmpeg、LibreDWG、中文字体和 Office。无需 Python 虚拟环境。
本次成品使用 glibc 2.43、ICU 78 构建并在原生 Linux 7.0 验证；转换隔离需要内核 >=6.7 / Landlock ABI>=4。较旧发行版可能缺共享库，Linux 内核版本符合并不代表所有用户态库兼容。

macOS
当前提供源码方案：先具备 Python 3.12+，执行 python3 bootstrap.py --run。依赖使用清华固定源，失败后使用官方 PyPI。无 macOS 实机二进制验收，不将其他平台产物标为 macOS binary。macOS Office 与 DWG 引擎尚无自动安装器；图片/PDF/音视频等依赖能力由源码环境检测后显示。

数据与升级
成品包首次运行将任务数据写到用户数据目录：Linux 为 ~/.local/share/format-hub，Windows 为 %LOCALAPPDATA%/FormatHub，macOS 为 ~/Library/Application Support/FormatHub。源码版默认项目 data，均可通过 --data-dir 指定独立可写目录。分发包从未携带制作者的 data、备份、证据、访问口令或令牌。请定期清理自己的任务数据。
升级时解压新版本到新目录，停旧程序后从新目录启动。需要保留数据时用 --data-dir 指向原数据目录；回滚时停新版本、用旧程序指向同一数据目录。本版本不自动覆盖其他版本。
如需团队访问，明确设置 --host 0.0.0.0，并配置 FORMAT_HUB_TOKEN；默认启动器仅本机访问。

来源与校验
engine-sources.json 固定引擎版本、HTTPS 官方源、SHA256 和预算。Windows Office 通过官方 MSI 管理映像展开，不注册系统安装项；下载失败不保留不完整引擎。仅允许用户显式选用 HTTPS 正向代理，默认不执行国内第三方镜像软件。
对应源码在 corresponding-source.zip；Python 与引擎许可保留在 licenses、runtime/office/usr/share/doc 等位置。修改分发应遵守各组件原始许可。

发行负责者需完成 Windows Actions 实测、签名及适用平台校验后再公开上传。本项目构建脚本不执行发布。
