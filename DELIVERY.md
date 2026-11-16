# 格式工坊交付与验收

## 决策者摘要

当前 Linux 机器上的成品已部署运行，团队访问 **http://192.168.0.35:8765**；本机访问 **http://127.0.0.1:8765**。不依赖本对话继续运行。

已实现24类工具、批量上传、参数设置、隔离任务队列、取消、下载、自动到期清理和可选团队口令。62项整体验收通过，最后新增的父进程/快速输出预算/交错上传预算3项定向回归通过。活服务8类转换、PPTX转PDF、真实DWG转PDF以及浏览器两条完整下载流程通过。

管理员现在可分享局域网地址；若需要访问口令，按README创建本机service.env并重启服务。客户端均使用浏览器。Windows/macOS原生服务与Docker为交付的部署路径，未实机/镜像验收，不计作已验证平台。

阅读顺序：先 [README.md](README.md) 使用；以下表格看证据和限制；维护时查源码与evidence。源码在当前目录，未推送GitHub。

## 需求与证据

| 需求 | 实现入口 | 真实验证 | 结果与状态 |
|---|---|---|---|
| 一机团队共享 | run.py / app/server.py / format-hub.service | 活服务LAN健康探针；systemctl用户服务 | 0.0.0.0:8765，用户服务已enabled/active；已实现且验证 |
| PDF与Word | engines.py PDF工具 | tests/test_engines.py；浏览器上传PDF并下载DOCX，python-docx读回文本 | 可编辑与图片型Word分开；已验证基础样本 |
| PDF合并拆分等 | engines.py 13项PDF工具 | test_engines.py真实多页PDF，页数/旋转/水印/加解密/提取读回 | 已实现且验证测试覆盖的行为 |
| Office与ODF | _office / runtime/soffice | DOCX→PDF、XLSX→ODS→XLSX值对账、DOCX→DOC→PDF；PPTX→PDF活HTTP | 已实现且验证基础样本，复杂文档保真部分验证 |
| 图片处理 | _images / _images_pdf / _svg | 多格式、动画、防帧丢失、中文长名；浏览器设480×640下载JPG并Pillow读回 | 已实现且验证 |
| OFD双向 | _pdf_ofd / _ofd_pdf | PDF→OFD→PDF尺寸读回；DocBody签名与Visible负控独立复核 | 图片型已验证；文字/矢量OFD不支持且明确报错 |
| 音视频 | _media | 真实WAV→MP3、MP4→WebM；活HTTP音轨转换 | 已实现且验证基础编码，长视频与全编解码器矩阵未测 |
| CAD | _cad / runtime/dwg2dxf | DXF矢量PDF；既有真实sample.dwg活HTTP→PDF：1页7矢量路径4226字节 | DWG→DXF/PDF/SVG、DXF→PDF/SVG已验证样本；DXF→DWG未实现 |
| SVG与压缩包 | _svg / _archive | 自包含SVG渲染；ZIP/TAR重打包与路径/链接负控 | 已实现且验证 |
| 并发与会话隔离 | server.py / SQLite WAL | 4个同名图片并发不同像素；不同会话下载/删除访问404 | 已实现且验证 |
| 取消、重启、资源边界 | server.py / worker.py / sandbox.py | 真sleep进程启动窗口取消、正常关闭回收；NOFILE256；父PID门禁；容量负控 | 独立复核发现项已修复，回归通过 |
| 浏览器交互 | app/static | agent-browser真实上传、点击、参数、转换、单文件和ZIP下载；1440/390视口DOM几何 | 已实现且部分验证；桌面/手机无横向溢出，视觉读图未验证 |
| 跨平台部署 | Dockerfile / compose.yaml / start.sh / start.bat | 当前Linux运行；脚本/文档检查 | Windows/macOS实机、Docker构建未验证 |

## 测试证据

- `./.venv/bin/python -m pytest tests -q` → **62 passed, 2 warnings in 10.31s**，`evidence/all-tests.txt`。此轮含45项引擎、10项API、7项边界。
- 随后新增 `-k 'fast_output or expected_parent'` → **2 passed**，`evidence/final-budget-parent-tests.txt`。
- 随后新增 `-k storage_budget` → **1 passed**，`evidence/interleaved-upload-test.txt`。当前测试总数65，本轮没有重复跑65项全套，不将分轮结果伪称同一完整命令输出。
- `node --check app/static/app.js`、`python -m py_compile app/server.py app/worker.py app/sandbox.py`退出0。
- `evidence/live-smoke.json`：常驻服务HTTP转换8类全部succeeded，输出204至36764字节。
- `evidence/presentation-smoke.txt`：PPTX→PDF，1页，标题文字读回，11786字节。
- `evidence/dwg-live.json`：真实DWG→矢量PDF，1页7路径4226字节。
- `evidence/browser-result.docx`：浏览器下载，可读文字包含`Format Hub browser acceptance`。
- `evidence/browser-resized.jpg`与`browser-bundle.zip`：真实浏览器下载，JPEG480×640，ZIP含唯一001_resized.jpg。
- `evidence/browser-state.json`和`browser-errors.txt`：实际页面状态与无前端错误记录；`desktop-final.png`、`desktop.png`1440×1000，`mobile.png`390×844。模型读图工具明确不支持图像，未做截图人工视觉复核。
- 旧失败日志保留为调试证据；其失败已由后续`all-tests.txt`与定向回归覆盖，不代表当前验收状态。

## 独立复核结论与修复

独立上下文从原始源码和隔离复现出发，发现并修复：进程登记提前移除、启动取消竞态、排队容量归还、流式文件限额、并发存储快照漏计、NOFILE上限兼容、中文物理名过长、OFD签名/隐藏对象、Office外部资源读取、异常父退出以及快速输出容量漏检。

Office增加格式真实容器预检与外部关系拦截；Linux转换进程继承Landlock文件访问限制和seccomp IP socket限制。任务外私有文件读取errno13、IP socket errno1及继承子进程负控已独立验证。LibreOffice内部硬编码IPC由随项目交付的C适配库导向任务私有目录，未放开整个系统临时目录，真实Office隔离正控成功。

预算是应用层近似限额，非磁盘文件系统配额：运行期0.5秒采样、最终同步检查；瞬态写入可能超过目标。Windows/macOS原生进程没有Linux内核隔离。浏览器会话并非多账户权限系统。没有企业SSO、跨设备任务同步或多人压测容量承诺。

## 改动、依赖、回滚

本轮在独立根目录 `/home/huanxiang455/桌面/DSH/out/20261116-format-hub` 新增成品；未覆盖当前工作区已有业务项目。应用/测试/部署文本文件行数及字节记录见 `evidence/file-inventory.json`（生成时快照）。主实现：engines.py、server.py、worker.py、sandbox.py与三个静态文件；依赖锁见requirements.lock。

系统侧只新增用户级服务 `/home/huanxiang455/.config/systemd/user/format-hub.service`及enabled链接；无root系统软件安装、无防火墙改动。项目内独立Python3.12环境、FFmpeg、LibreDWG0.14、LibreOffice26.2.6.3和54个官方deb在runtime中；Office源URL/版本/SHA256见runtime/packages/manifest.json。许可见THIRD-PARTY-NOTICES.md。

回滚：`systemctl --user disable --now format-hub.service`，移除新增用户服务配置后执行`systemctl --user daemon-reload`。保留/归档data中的团队文件。项目与运行时均为本轮新建，删除项目目录即可卸载应用；没有旧版本备份可恢复，因为未覆盖既有项目。

## 明确未交付或未验证

未实现：扫描OCR、人像抠图、精确目标KB压缩、任意文字/矢量OFD发票、DXF→DWG/版本互转、RAR/7Z、多账户与跨设备同步。未验证：Windows/macOS原生运行、Docker镜像、另一台团队设备网络、复杂Office/大型CAD/长音视频保真、截图人工视觉检查、生产规模长时压力测试。用户级服务在用户登录后启动；当前Linger=no，注销后常驻与无人登录自启动未配置。
