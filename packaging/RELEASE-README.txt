格式工坊 v1.2.0 完整包 — 本地格式转换

Windows x64：解压完整目录到可写位置，双击“启动格式工坊.cmd”。
macOS arm64 / x64：下载匹配芯片架构的 ZIP，使用系统归档工具完整解压，双击“启动格式工坊.command”。无需 Python、Homebrew 或系统 Office。
两个平台均内置 Python 依赖、LibreOffice、FFmpeg、LibreDWG、Cairo 和中文字体，启动器使用 --offline，不下载任何引擎。浏览器地址为 http://127.0.0.1:8765 。Ctrl+C 停止。

签名：Windows 未配置代码签名证书；macOS FormatHub 可执行文件只做本机 ad-hoc 签名，未配置 Apple Developer ID 或 notarization，系统可能要求在“隐私与安全性”确认打开。LibreOffice.app 保留官方原始签名，未修改其内部内容。

Linux v1.1.0 完整包仍在独立旧版本入口，无需重新构建；不将其版本标为 v1.2.0。

数据与升级：分发包不含制作者 data、备份或令牌。Windows 默认 %LOCALAPPDATA%/FormatHub；macOS 默认 ~/Library/Application Support/FormatHub；可用 --data-dir 指定其他位置。升级时解压到新目录，先停旧程序再启动新程序；回滚时启动旧包，数据目录由用户自行保留。发行 smoke 使用独立临时目录，不修改用户数据。

来源与许可：engine-sources.json 保留官方下载地址、版本及 SHA256；release-manifest.json 记录包内文件大小与 SHA256；corresponding-source.zip 包含项目对应源码与构建脚本；licenses 保留 Python 和 native 许可，runtime/LibreOffice.app 或 runtime/libreoffice 保留 Office 原始许可。LibreDWG 对应上游源代码下载地址见 engine-sources.json，Homebrew 依赖的精确版本、source URL 与校验值见 licenses/native/homebrew-installed.json。

Windows 通过官方 MSI 管理映像展开到 runtime/libreoffice，不注册系统安装项；macOS 官方 DMG 只读挂载后复制到 runtime/LibreOffice.app，不安装到 /Applications。各附件须通过 CI 解压后四类真实转换与离线启动测试后才发布。
