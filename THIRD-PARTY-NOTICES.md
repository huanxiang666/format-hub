# 来源与第三方组件

本项目源码按 GNU Affero General Public License v3.0（AGPL-3.0）提供。协议全文：https://www.gnu.org/licenses/agpl-3.0.html 。通过网络修改运行时，保留协议要求的对应源码获取方式。本交付包含完整应用源码。

| 组件 | 用途 | 上游许可/来源 |
|---|---|---|
| PyMuPDF | PDF读取写入和渲染 | AGPL-3.0 / 商业双许可，https://github.com/pymupdf/PyMuPDF |
| pdf2docx | PDF重建DOCX | MIT，https://github.com/ArtifexSoftware/pdf2docx |
| Pillow | 位图格式和处理 | HPND，https://github.com/python-pillow/Pillow |
| python-docx / openpyxl | OOXML文档与表格 | MIT |
| ezdxf | DXF读取和绘制 | MIT，https://github.com/mozman/ezdxf |
| LibreDWG 0.14 | 本机DWG→DXF外部进程 | GPL-3.0，https://www.gnu.org/software/libredwg/ ；本交付二进制复用当前工作区既有dwg2pdf项目 |
| LibreOffice | Office外部转换引擎 | MPL-2.0，https://www.libreoffice.org/ |
| FFmpeg | 音视频外部进程 | 本机imageio-ffmpeg自带构建enable-gpl/enable-version3，GPLv3构建；https://ffmpeg.org/ |
| FastAPI / Uvicorn / Starlette | HTTP服务 | MIT / BSD-3-Clause |
| CairoSVG / Cairo | SVG渲染 | LGPL-3.0 / LGPL-2.1或MPL-1.1 |
| defusedxml | XML实体保护 | PSF |
| libseccomp | Linux系统调用约束 | LGPL-2.1 |

应用中的Office IPC重定向C代码和静态页面为本次新增源码；不修改系统库。下载的Office .deb保持自身copyright文件，来源、SHA256在runtime/packages/manifest.json。requirements.lock记录当前解析依赖，实际许可证以各包自带声明为准。

用户仓库调研：

- https://github.com/huanxiang666/pdf2word （MIT，图片型Word功能参考）
- https://github.com/huanxiang666/pdftoolbox （AGPL-3.0，功能调研）
- https://github.com/huanxiang666/dwg2pdf （AGPL-3.0，功能与本机LibreDWG引擎来源）
- https://github.com/huanxiang666/pdf-to-ofd （MIT，图片型OFD结构参考）

未把第三方网站的在线服务当作本项目后端；转换均在部署机器本地完成。打包的源码部署包不含当前机器的Python虚拟环境、Office下载包或用户任务数据。部署时按目标平台安装依赖。第三方组件升级应重新跑真实文件验收。
