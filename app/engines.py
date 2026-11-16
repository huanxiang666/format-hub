"""Local conversion engines. Each execute call is designed for a dedicated worker process.

PDF↔OFD uses the image-package structure documented by huanxiang666/pdf-to-ofd
(https://github.com/huanxiang666/pdf-to-ofd, MIT); this implementation is independent
and fails explicitly on unsupported OFD content instead of silently dropping it.
"""
from __future__ import annotations

import importlib.util
import io
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import time
import subprocess
import tempfile
import uuid
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
IMAGE_FORMATS = ['png', 'jpg', 'webp', 'bmp', 'tiff', 'ico', 'gif']
OFFICE_GROUPS = [
    ['doc', 'docx', 'odt', 'rtf', 'txt', 'pdf'],
    ['xls', 'xlsx', 'ods', 'csv', 'pdf'],
    ['ppt', 'pptx', 'odp', 'pdf'],
]
MEDIA_INPUTS = ['mp4','mkv','mov','avi','webm','m4v','mpg','mpeg','mp3','wav','flac','ogg','m4a','aac','wma','opus']
NS = 'http://www.ofdspec.org/2016'
ET.register_namespace('ofd', NS)


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _program(env: str, names: list[str], relative: list[str]) -> str | None:
    candidates = ([os.environ[env]] if os.environ.get(env) else [])
    candidates += [str(ROOT / p) for p in relative]
    candidates += [shutil.which(name) or '' for name in names]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    return None


_PROBES = {}


def _soffice() -> str | None:
    program = _program('SOFFICE',['soffice','libreoffice'],['runtime/soffice','runtime/office/usr/lib/libreoffice/program/soffice','runtime/libreoffice/program/soffice','runtime/libreoffice/program/soffice.com','runtime/libreoffice/program/soffice.exe'])
    if not program:
        return None
    # A wrapper existing on disk does not prove that its shared libraries work.
    key=(program,Path(program).stat().st_mtime_ns)
    cached=_PROBES.get(key)
    if cached and time.monotonic()-cached[0]<10:
        return program if cached[1] else None
    try:
        result=subprocess.run([program,'--version'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=8)
        valid=result.returncode==0 and b'LibreOffice' in result.stdout
    except (OSError,subprocess.TimeoutExpired):
        valid=False
    _PROBES[key]=(time.monotonic(),valid)
    return program if valid else None


def _dwg() -> str | None:
    return _program('DWG2DXF',['dwg2dxf','ODAFileConverter'],['runtime/dwg2dxf','runtime/dwg2dxf.exe','runtime/dwg/dwg2dxf.exe','runtime/ODAFileConverter','runtime/oda/ODAFileConverter'])


def _ffmpeg() -> str | None:
    found = _program('FFMPEG',['ffmpeg'],['runtime/ffmpeg','runtime/ffmpeg.exe'])
    if found:
        return found
    if _has('imageio_ffmpeg'):
        try:
            import imageio_ffmpeg
            candidate = imageio_ffmpeg.get_ffmpeg_exe()
            if Path(candidate).is_file() and os.access(candidate, os.X_OK):
                return candidate
        except (RuntimeError, OSError):
            return None
    return None


def _image_formats() -> list[str]:
    result = list(IMAGE_FORMATS)
    if _has('PIL'):
        from PIL import Image
        Image.init()
        if 'AVIF' in Image.SAVE:
            result.append('avif')
    return result


def _field(name, label, kind='text', default='', minimum=None, maximum=None, choices=None, choice_labels=None):
    field=dict(name=name,label=label,type=kind,default=default,min=minimum,max=maximum,choices=choices or [])
    if choice_labels:
        field['choice_labels']=choice_labels
    return field


def catalog() -> list[dict]:
    """Return UI-ready capabilities, including unavailable engines and exact reasons."""
    images = _image_formats()
    pdf_ok, pillow_ok = _has('fitz'), _has('PIL')
    docx_ok = _has('docx')
    pages = _field('pages','页码范围（如 1-3,5；留空为全部）')
    dpi = _field('dpi','渲染 DPI','number',150,36,600)
    quality = _field('quality','图片质量','number',90,1,100)
    password = _field('password','PDF 密码')
    target = lambda choices, default: _field('target','目标格式','select',default,choices=choices)
    rows = []

    def add(id,name,category,description,inputs,targets,available,reason,options=None,multiple=True):
        rows.append(dict(id=id,name=name,category=category,description=description,inputs=inputs,targets=targets,available=bool(available),reason='' if available else reason,options=options or [],multiple=multiple))

    add('image_convert','图片格式转换','图片','支持透明背景；GIF/WebP 动画保留帧，转静态格式请明确选择首帧。',images,images,pillow_ok,'缺少 Pillow',[target(images,'png'),quality,_field('first_frame','仅保留动画首帧','checkbox',False),_field('strip_exif','移除 EXIF','checkbox',True)])
    add('image_resize','图片尺寸与证件照画布','图片','尺寸、选区裁剪（crop 为 EXIF 校正后归一化 x/y/width/height，先裁后转）、旋转、DPI、JPEG/WebP 大小上限；白/红/蓝底仅为矩形画布，transparent 保留透明，不做人像抠图。',images,images,pillow_ok,'缺少 Pillow',[target(images,'jpg'),_field('width','宽度 px（0=按比例）','number',600,0,12000),_field('height','高度 px（0=按比例）','number',800,0,12000),_field('mode','尺寸模式','select','contain',choices=['contain','cover','stretch']),_field('background','画布底色','select','white',choices=['white','red','blue','transparent']),_field('dpi','输出 DPI（JPEG/PNG/TIFF/WebP）','number',300,72,600),_field('max_kb','大小上限 KB（0=不限制，仅 JPEG/WebP）','number',0,0,1048576),_field('rotate','旋转角度','number',0,-360,360),_field('watermark','文字水印'),quality,_field('strip_exif','移除 EXIF','checkbox',True),_field('first_frame','仅保留动画首帧','checkbox',False)])
    add('images_pdf','多图合成 PDF','图片','每张图片及动画每帧各生成一页，按上传顺序排列。',images,['pdf'],pdf_ok and pillow_ok,'缺少 PyMuPDF 或 Pillow',[dpi])
    add('pdf_images','PDF 转图片','PDF','逐页渲染为图片，可选页码与分辨率。',['pdf'],['png','jpg','webp'],pdf_ok and pillow_ok,'缺少 PyMuPDF 或 Pillow',[target(['png','jpg','webp'],'png'),pages,dpi,quality,password])
    add('pdf_merge','合并 PDF','PDF','按上传顺序合并全部页面；加密输入使用统一密码。',['pdf'],['pdf'],pdf_ok,'缺少 PyMuPDF',[password])
    add('pdf_split','拆分 PDF','PDF','留空逐页拆分；输入 1-3,4-6 时每个逗号分组生成独立 PDF。',['pdf'],['pdf'],pdf_ok,'缺少 PyMuPDF',[pages,password])
    add('pdf_extract','提取 PDF 页面','PDF','把指定页码按给定顺序组合为一个 PDF，支持重复页。',['pdf'],['pdf'],pdf_ok,'缺少 PyMuPDF',[pages,password])
    add('pdf_rotate','旋转 PDF','PDF','仅旋转选中页面，支持 90/180/270 度。',['pdf'],['pdf'],pdf_ok,'缺少 PyMuPDF',[pages,_field('angle','旋转角度','select','90',choices=['90','180','270']),password])
    add('pdf_compress','PDF 无损整理压缩','PDF','移除未使用对象并压缩内容流，保留文本与矢量；已优化文件未必变小。',['pdf'],['pdf'],pdf_ok,'缺少 PyMuPDF',[password])
    add('pdf_text','PDF 提取文字','PDF','导出已有文字层；扫描图片不执行 OCR。',['pdf'],['txt'],pdf_ok,'缺少 PyMuPDF',[pages,password])
    add('pdf_watermark','PDF 文字水印','PDF','添加半透明文字水印，支持中文。',['pdf'],['pdf'],pdf_ok,'缺少 PyMuPDF',[pages,_field('text','水印文字','text','内部资料'),_field('font_size','字号','number',28,6,160),_field('opacity','不透明度','number',0.25,0.01,1),password])
    add('pdf_numbers','PDF 添加页码','PDF','页面底部添加数字页码。',['pdf'],['pdf'],pdf_ok,'缺少 PyMuPDF',[pages,_field('start','起始页码','number',1,0,1000000),password])
    add('pdf_encrypt','PDF 密码保护','PDF','AES-256 加密；设置打开密码和可选所有者密码。',['pdf'],['pdf'],pdf_ok,'缺少 PyMuPDF',[_field('password','新的打开密码'),_field('owner_password','所有者密码（留空随机生成）'),_field('input_password','原 PDF 密码')])
    add('pdf_decrypt','PDF 移除密码','PDF','使用正确密码解密并另存。',['pdf'],['pdf'],pdf_ok,'缺少 PyMuPDF',[password])
    add('pdf_docx','PDF 转可编辑 Word','PDF','pdf2docx 重建文字与版式；复杂排版可能变化，扫描页不自动 OCR。',['pdf'],['docx'],pdf_ok and _has('pdf2docx'),'缺少 pdf2docx 或 PyMuPDF',[password])
    add('pdf_docx_image','PDF 转保真图片 Word','PDF','每页作为整页图片嵌入 Word；图片内容不可编辑。',['pdf'],['docx'],pdf_ok and docx_ok,'缺少 python-docx 或 PyMuPDF',[dpi,password])
    office_targets = list(dict.fromkeys(x for group in OFFICE_GROUPS for x in group))
    add('office_convert','Office 文档转换','办公','Word 文档仅转文档类、表格仅转表格类、演示仅转演示类；均可导出 PDF。宏、动画、复杂字体可能变化。',[x for x in office_targets if x!='pdf'],office_targets,bool(_soffice()),'未检测到 LibreOffice/soffice；设置 SOFFICE 或安装项目 runtime',[target(office_targets,'pdf')])
    media_targets=['mp4','mkv','mov','avi','webm','mp3','wav','flac','ogg','m4a','aac','opus']
    add('media_convert','音视频格式转换','音视频','使用 FFmpeg 重新编码主音视频流；字幕与附件不自动保留。',MEDIA_INPUTS,media_targets,bool(_ffmpeg()),'未检测到 FFmpeg；安装 imageio-ffmpeg 或设置 FFMPEG',[target(media_targets,'mp4'),_field('crf','视频 CRF（越小质量越高）','number',23,0,51),_field('audio_bitrate','音频码率 kbps','number',192,32,512)])
    add('media_audio','提取/转换音频','音视频','提取第一条音轨并转换；无音轨会返回明确错误。',MEDIA_INPUTS,['mp3','wav','flac','ogg','m4a','aac','opus'],bool(_ffmpeg()),'未检测到 FFmpeg；安装 imageio-ffmpeg 或设置 FFMPEG',[target(['mp3','wav','flac','ogg','m4a','aac','opus'],'mp3'),_field('audio_bitrate','码率 kbps','number',192,32,512)])
    cad_ok=_has('ezdxf') and pdf_ok
    add('cad_convert','CAD 图纸转换','CAD','DXF 模型空间导出 SVG/PDF 或规范化 DXF；三维模型为二维投影。支持上传 TTF/TTC/OTF/SHX 字体，真实字体优先，缺失字体使用内置中文替代并提示复核。DWG 另需 DWG2DXF/ODA；当前 '+('已检测 DWG 转换器。' if _dwg() else '未检测 DWG 转换器，DXF 可用。'),['dxf','dwg'],['pdf','svg','dxf'],cad_ok,'缺少 ezdxf 或 PyMuPDF',[target(['pdf','svg','dxf'],'pdf'),_field('cad_quality','曲线精度','select','balanced',choices=['fast','balanced','high'],choice_labels={'fast':'快速','balanced':'均衡','high':'精细'})])
    add('pdf_ofd','PDF 转图片型 OFD','OFD','逐页栅格化封装 OFD；保留页面尺寸，文字不再可搜索，不保留签名及表单。',['pdf'],['ofd'],pdf_ok,'缺少 PyMuPDF',[dpi,quality,password])
    add('ofd_pdf','图片型 OFD 转 PDF','OFD','仅图片型 OFD 的 ImageObject 内容；TextObject/PathObject/CompositeObject、模板、签名、复杂裁切会明确报错，避免内容丢失。',['ofd'],['pdf'],pdf_ok and pillow_ok and _has('defusedxml'),'缺少 PyMuPDF、Pillow 或 defusedxml')
    add('svg_convert','SVG 转图片/PDF','图片','仅处理自包含 SVG；外部链接、脚本、DOCTYPE 与实体一律报错。',['svg'],['png','pdf'],_has('cairosvg') and _has('defusedxml'),'缺少 CairoSVG 或 defusedxml',[target(['png','pdf'],'png'),_field('width','输出宽度 px（0=原尺寸）','number',0,0,12000)])
    add('archive_convert','压缩包格式转换','压缩包','ZIP/TAR/TAR.GZ 内存重打包；拒绝路径越界、链接、重复名称及超预算压缩包，不解压到文件系统。',['zip','tar','gz'],['zip','tar','tar.gz'],True,'',[target(['zip','tar','tar.gz'],'zip')])
    return rows


def _num(options, name, default, low, high, integer=False):
    value=options.get(name,default)
    if isinstance(value,bool):
        raise ValueError(f'{name} 必须是数值')
    try:
        n=float(value)
    except (TypeError,ValueError) as exc:
        raise ValueError(f'{name} 必须是数值') from exc
    if not math.isfinite(n) or not low<=n<=high or (integer and n!=int(n)):
        raise ValueError(f'{name} 超出有效范围 {low}–{high}')
    return int(n) if integer else n


def _bool(options,name,default=False):
    v=options.get(name,default)
    if isinstance(v,bool):
        return v
    if v in [0,1,'true','false','0','1']:
        return v in [1,'true','1']
    raise ValueError(f'{name} 必须是布尔值')


def _target(options, choices, default):
    value=str(options.get('target',default)).lower().lstrip('.')
    if value not in choices:
        raise ValueError(f'目标格式 {value!r} 不受支持；可选：{", ".join(choices)}')
    return value


def _output(directory: Path, stem: str, suffix: str) -> Path:
    # Never follow a pre-existing output symlink and never overwrite an input/output.
    stem=re.sub(r'[^\w\-.\u4e00-\u9fff]+','_',Path(stem).name)
    stem=stem.encode('utf-8')[:160].decode('utf-8',errors='ignore') or 'output'
    p=directory/f'{stem}.{suffix}'
    i=2
    while p.exists() or p.is_symlink():
        p=directory/f'{stem}_{i}.{suffix}'; i+=1
    if not p.resolve().is_relative_to(directory):
        raise ValueError('输出路径越出任务目录')
    return p


def _pages(expression, count):
    if expression is None or str(expression).strip()=='':
        return list(range(count))
    output=[]
    for group in str(expression).replace('，',',').split(','):
        group=group.strip()
        if not re.fullmatch(r'\d+(?:\s*-\s*\d+)?',group):
            raise ValueError(f'页码格式错误：{group!r}；示例 1-3,5')
        ends=[int(s) for s in group.split('-')]
        start,end=ends[0],ends[-1]
        if start<1 or end<start or end>count:
            raise ValueError(f'页码范围 {group} 越界；文档共有 {count} 页')
        output.extend(range(start-1,end))
    return output


def _open_pdf(path, password=''):
    import fitz
    doc=fitz.open(path)
    if not doc.is_pdf:
        doc.close(); raise ValueError(f'{path.name} 不是有效 PDF')
    if doc.needs_pass and not doc.authenticate(str(password)):
        doc.close(); raise ValueError(f'{path.name} 密码错误或缺少密码')
    if not len(doc):
        doc.close(); raise ValueError(f'{path.name} 没有页面')
    return doc


def _save_pdf(doc,path,**kwargs):
    import fitz
    kwargs.setdefault('encryption',fitz.PDF_ENCRYPT_NONE)
    doc.save(path,garbage=4,deflate=True,**kwargs)


def _run(args,cwd=None):
    from app.processes import run_external
    result=run_external([str(s) for s in args],cwd=cwd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors='replace')
    if result.returncode:
        details=(result.stderr or result.stdout).strip()[-4000:]
        raise ValueError(f'{Path(args[0]).name} 转换失败（退出码 {result.returncode}）：{details}')
    return result


def _rgb(image, background='white'):
    from PIL import Image
    rgba=image.convert('RGBA')
    canvas=Image.new('RGBA',rgba.size,background)
    canvas.alpha_composite(rgba)
    return canvas.convert('RGB')


def _save_image(frames,path,target,quality,durations=None,loop=0,exif=None,dpi=None,max_kb=0):
    from PIL import Image
    format_name={'jpg':'JPEG','tiff':'TIFF','ico':'ICO'}.get(target,target.upper())
    kw={}
    if target in ['jpg','webp','avif']:
        kw['quality']=quality
    if exif and target in ['jpg','png','webp','tiff']:
        kw['exif']=exif
    if target in ['jpg','bmp']:
        frames=[_rgb(im) for im in frames]
    if target=='gif':
        frames=[im.convert('RGBA') for im in frames]
    if target=='ico':
        im=frames[0].copy(); im.thumbnail((256,256),Image.Resampling.LANCZOS)
        frames=[im]; kw['sizes']=[im.size]
    if len(frames)>1:
        kw.update(save_all=True,append_images=frames[1:])
        if target in ['gif','webp','avif']:
            kw.update(duration=durations or 100,loop=loop)
    if dpi is not None:
        if target in ['jpg','png','tiff']:
            kw['dpi']=(dpi,dpi)
        if target in ['jpg','png','webp','tiff']:
            metadata=Image.Exif()
            if exif:
                metadata.load(exif)
            metadata[282]=dpi; metadata[283]=dpi; metadata[296]=2
            metadata.pop(274,None)  # Pixels were already EXIF-transposed.
            kw['exif']=metadata.tobytes()
    if max_kb:
        if target not in ['jpg','webp']:
            raise ValueError('max_kb 仅支持 JPEG/WebP；其它格式请关闭大小上限')
        limit=int(max_kb*1024)
        best=None; low=1; high=quality
        while low<=high:
            trial=(low+high)//2
            buffer=io.BytesIO()
            frames[0].save(buffer,format=format_name,**{**kw,'quality':trial})
            data=buffer.getvalue()
            if len(data)<=limit:
                best=data; low=trial+1
            else:
                high=trial-1
        if best is None:
            raise ValueError(f'max_kb={max_kb:g}：最低质量仍超过大小上限；请降低像素尺寸或提高上限')
        path.write_bytes(best)
    else:
        frames[0].save(path,format=format_name,**kw)


def _crop_image(frame, crop):
    if not isinstance(crop,dict) or set(crop)!={'x','y','width','height'}:
        raise ValueError('crop 必须包含且仅包含 x/y/width/height')
    if any(isinstance(v,(str,bool)) or not isinstance(v,(int,float)) for v in crop.values()):
        raise ValueError('crop 坐标必须是 0..1 数值')
    x=_num(crop,'x',0,0,1); y=_num(crop,'y',0,0,1)
    w=_num(crop,'width',0,0,1); h=_num(crop,'height',0,0,1)
    if w<=0 or h<=0 or x+w>1 or y+h>1:
        raise ValueError('crop 选区必须在图像边界 0..1 内且宽高大于 0')
    box=(round(x*frame.width),round(y*frame.height),round((x+w)*frame.width),round((y+h)*frame.height))
    if box[2]<=box[0] or box[3]<=box[1]:
        raise ValueError('crop 选区不足一个像素')
    return frame.crop(box)


def _images(operation,files,out,options):
    from PIL import Image, ImageOps, ImageDraw, ImageFont
    targets=_image_formats()
    target=_target(options,targets,'png' if operation=='image_convert' else 'jpg')
    quality=_num(options,'quality',90,1,100,True)
    dpi=_num(options,'dpi',300,72,600,True) if operation=='image_resize' else None
    max_kb=_num(options,'max_kb',0,0,1048576) if operation=='image_resize' else 0
    outputs=[]
    for src in files:
        with Image.open(src) as source:
            count=getattr(source,'n_frames',1)
            first=_bool(options,'first_frame',False)
            if count>1 and not first and target not in ['gif','webp','tiff','avif']:
                raise ValueError('动画/多页图片转静态格式会丢帧，请勾选“仅保留动画首帧”')
            frames=[]; durations=[]
            exif=source.info.get('exif') if not _bool(options,'strip_exif',True) else None
            loop=source.info.get('loop',0)
            for i in range(1 if first else count):
                source.seek(i)
                frame=ImageOps.exif_transpose(source).convert('RGBA')
                durations.append(source.info.get('duration',100))
                if operation=='image_resize':
                    if 'crop' in options:
                        frame=_crop_image(frame,options['crop'])
                    width=_num(options,'width',600,0,12000,True)
                    height=_num(options,'height',800,0,12000,True)
                    if width==0 and height==0:
                        raise ValueError('width 与 height 至少一个大于 0')
                    angle=_num(options,'rotate',0,-360,360)
                    if angle:
                        frame=frame.rotate(-angle,expand=True,resample=Image.Resampling.BICUBIC)
                    width=width or max(1,round(frame.width*height/frame.height))
                    height=height or max(1,round(frame.height*width/frame.width))
                    if width*height>60_000_000:
                        raise ValueError('目标画布过大，最多 6000 万像素')
                    mode=options.get('mode','contain')
                    background=options.get('background','white')
                    bg={'white':'#FFFFFF','red':'#D9001B','blue':'#438EDB','transparent':(0,0,0,0)}.get(background)
                    if bg is None:
                        raise ValueError('background 仅支持 white/red/blue/transparent')
                    if background=='transparent' and target not in ['png','webp','tiff','gif','ico','avif']:
                        raise ValueError('transparent 背景需要 PNG/WebP/TIFF/GIF/ICO/AVIF 目标格式')
                    if mode=='cover':
                        frame=ImageOps.fit(frame,(width,height),method=Image.Resampling.LANCZOS)
                    elif mode=='contain':
                        frame=ImageOps.contain(frame,(width,height),method=Image.Resampling.LANCZOS)
                    elif mode=='stretch':
                        frame=frame.resize((width,height),Image.Resampling.LANCZOS)
                    else:
                        raise ValueError('mode 仅支持 contain/cover/stretch')
                    canvas=Image.new('RGBA',(width,height),bg)
                    canvas.alpha_composite(frame,((width-frame.width)//2,(height-frame.height)//2))
                    frame=canvas
                    mark=str(options.get('watermark','')).strip()
                    if mark:
                        font_path=_font_path()
                        font=ImageFont.truetype(font_path,max(12,min(width,height)//18)) if font_path else ImageFont.load_default()
                        draw=ImageDraw.Draw(frame)
                        draw.text((width-12,height-12),mark,font=font,fill=(40,40,40,160),anchor='rs')
                frames.append(frame)
            p=_output(out,src.stem+('_resized' if operation=='image_resize' else '_converted'),target)
            _save_image(frames,p,target,quality,durations,loop,exif,dpi,max_kb)
            outputs.append(p)
    return outputs


def _font_path():
    for p in [ROOT/'runtime/fonts/NotoSansCJK-Regular.ttc',ROOT/'runtime/fonts/DroidSansFallback.ttf',Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'),Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]:
        if p.is_file():
            return str(p)
    return None


def _images_pdf(files,out,options):
    import fitz
    from PIL import Image,ImageOps
    dpi=_num(options,'dpi',150,36,600,True)
    p=_output(out,'images','pdf')
    with fitz.open() as doc:
        for src in files:
            with Image.open(src) as image:
                for i in range(getattr(image,'n_frames',1)):
                    image.seek(i)
                    im=ImageOps.exif_transpose(image).convert('RGBA')
                    data=io.BytesIO(); im.save(data,format='PNG')
                    page=doc.new_page(width=im.width*72/dpi,height=im.height*72/dpi)
                    page.insert_image(page.rect,stream=data.getvalue())
        _save_pdf(doc,p)
    return [p]


def _pdf(operation,files,out,options):
    import fitz
    outputs=[]
    if operation=='pdf_merge':
        p=_output(out,'merged','pdf')
        with fitz.open() as merged:
            for src in files:
                with _open_pdf(src,options.get('password','')) as doc:
                    merged.insert_pdf(doc)
            _save_pdf(merged,p)
        return [p]
    for src in files:
        password=options.get('input_password','') if operation=='pdf_encrypt' else options.get('password','')
        with _open_pdf(src,password) as doc:
            pages=_pages(options.get('pages',''),len(doc))
            if operation=='pdf_images':
                from PIL import Image
                target=_target(options,['png','jpg','webp'],'png')
                dpi=_num(options,'dpi',150,36,600,True)
                quality=_num(options,'quality',90,1,100,True)
                for i in pages:
                    pix=doc[i].get_pixmap(dpi=dpi,alpha=False)
                    p=_output(out,f'{src.stem}_page_{i+1:04}',target)
                    im=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
                    _save_image([im],p,target,quality); outputs.append(p)
            elif operation=='pdf_text':
                p=_output(out,src.stem,'txt')
                p.write_text('\n\n'.join(f'--- 第 {i+1} 页 ---\n'+doc[i].get_text(sort=True) for i in pages),encoding='utf-8')
                outputs.append(p)
            elif operation in ['pdf_extract','pdf_split']:
                groups=[pages] if operation=='pdf_extract' else ([_pages(group,len(doc)) for group in str(options['pages']).replace('，',',').split(',')] if str(options.get('pages','')).strip() else [[i] for i in pages])
                for n,group in enumerate(groups,1):
                    p=_output(out,f'{src.stem}_{"extract" if operation=="pdf_extract" else f"part_{n:04}"}','pdf')
                    with fitz.open() as result:
                        for index in group:
                            result.insert_pdf(doc,from_page=index,to_page=index)
                        _save_pdf(result,p)
                    outputs.append(p)
            elif operation=='pdf_docx':
                from pdf2docx import Converter
                p=_output(out,src.stem,'docx')
                cv=Converter(str(src),password=str(password))
                try:
                    cv.convert(str(p),multi_processing=False)
                finally:
                    cv.close()
                outputs.append(p)
            elif operation=='pdf_docx_image':
                outputs.append(_docx_images(doc,src,out,options))
            elif operation=='pdf_ofd':
                outputs.append(_pdf_ofd(doc,src,out,options))
            else:
                if operation=='pdf_rotate':
                    angle=_num(options,'angle',90,0,270,True)
                    if angle not in [90,180,270]:
                        raise ValueError('旋转角度仅支持 90/180/270')
                    for i in pages:
                        doc[i].set_rotation((doc[i].rotation+angle)%360)
                elif operation in ['pdf_watermark','pdf_numbers']:
                    text=str(options.get('text','内部资料')).strip()
                    if operation=='pdf_watermark' and not text:
                        raise ValueError('水印文字为空')
                    size=_num(options,'font_size',28,6,160)
                    opacity=_num(options,'opacity',0.25,0.01,1)
                    start=_num(options,'start',1,0,1000000,True)
                    for n,i in enumerate(pages):
                        page=doc[i]
                        content=str(start+n) if operation=='pdf_numbers' else text
                        fontname='helv' if content.isascii() else 'china-s'
                        actual_size=11 if operation=='pdf_numbers' else size
                        font=fitz.Font(fontname)
                        text_width=font.text_length(content,fontsize=actual_size)
                        if text_width>page.rect.width-20:
                            actual_size*=max(0.05,(page.rect.width-20)/text_width)
                            text_width=font.text_length(content,fontsize=actual_size)
                        x=max(5,(page.rect.width-text_width)/2)
                        y=page.rect.height-16 if operation=='pdf_numbers' else page.rect.height/2
                        page.insert_text((x,y),content,fontname=fontname,fontsize=actual_size,color=(0.4,0.4,0.4),fill_opacity=1 if operation=='pdf_numbers' else opacity,overlay=True)
                p=_output(out,src.stem+'_'+operation.removeprefix('pdf_'),'pdf')
                if operation=='pdf_encrypt':
                    new_password=str(options.get('password',''))
                    if not new_password:
                        raise ValueError('新的打开密码为空')
                    _save_pdf(doc,p,encryption=fitz.PDF_ENCRYPT_AES_256,user_pw=new_password,owner_pw=str(options.get('owner_password','')) or uuid.uuid4().hex,permissions=fitz.PDF_PERM_PRINT|fitz.PDF_PERM_ACCESSIBILITY)
                else:
                    _save_pdf(doc,p)
                outputs.append(p)
    return outputs


def _docx_images(doc,src,out,options):
    from docx import Document
    from docx.shared import Pt
    from docx.enum.section import WD_SECTION_START
    d=Document()
    dpi=_num(options,'dpi',150,36,600,True)
    for i,page in enumerate(doc):
        sec=d.sections[0] if i==0 else d.add_section(WD_SECTION_START.NEW_PAGE)
        sec.page_width=Pt(page.rect.width); sec.page_height=Pt(page.rect.height)
        sec.top_margin=sec.bottom_margin=sec.left_margin=sec.right_margin=Pt(0)
        sec.header_distance=sec.footer_distance=Pt(0)
        p=d.add_paragraph()
        p.paragraph_format.space_before=Pt(0); p.paragraph_format.space_after=Pt(0)
        p.paragraph_format.line_spacing=1
        pix=page.get_pixmap(dpi=dpi,alpha=False)
        # Leave one point for Word's mandatory paragraph baseline to prevent blank pages.
        p.add_run().add_picture(io.BytesIO(pix.tobytes('png')),width=Pt(page.rect.width),height=Pt(max(1,page.rect.height-1)))
    p=_output(out,src.stem+'_image','docx'); d.save(p)
    return p


def _office_preflight(src):
    """Only pass real document containers without active external references to LO."""
    from defusedxml import ElementTree as SafeET
    ext=src.suffix.lower()[1:]
    with src.open('rb') as stream:
        header=stream.read(16)
    if ext in ['txt','csv']:
        if b'\x00' in src.read_bytes()[:65536]:
            raise ValueError('纯文本文件含 NUL，疑似二进制文件')
        return
    if ext in ['doc','xls','ppt']:
        if not header.startswith(bytes.fromhex('d0cf11e0a1b11ae1')):
            raise ValueError('Office 文件签名错误：旧版 doc/xls/ppt 必须是真实 OLE 容器，HTML 伪装文件已拦截')
        data=src.read_bytes()
        # Legacy binary containers have no uniform relationship index. Conservatively
        # reject common external-link/field markers in either narrow or UTF-16 form.
        for marker in ['http://','https://','file://','INCLUDEPICTURE','INCLUDETEXT','DDEAUTO','DDE','LINK ']:
            if marker.lower().encode() in data.lower() or marker.lower().encode('utf-16le') in data.lower():
                raise ValueError('旧版 OLE 文档含外部链接/字段标记；请在本地移除链接后另存再转换')
        return
    if ext=='rtf':
        data=src.read_bytes()
        if not data.lstrip().startswith(b'{\\rtf'):
            raise ValueError('RTF 文件签名错误')
        if re.search(rb'\\(?:field|object|objdata|filetbl)\b|INCLUDEPICTURE|INCLUDETEXT|DDE|https?://|file://',data,re.I):
            raise ValueError('RTF 含字段、嵌入对象或外部内容引用，已停止转换')
        return
    expected={'docx':'word/document.xml','xlsx':'xl/workbook.xml','pptx':'ppt/presentation.xml','odt':'content.xml','ods':'content.xml','odp':'content.xml'}
    if ext not in expected or not zipfile.is_zipfile(src):
        raise ValueError('Office 文件签名错误：需真实 OOXML/ODF ZIP 容器')
    with zipfile.ZipFile(src) as z:
        members=z.infolist()
        if len(members)>20000 or sum(m.file_size for m in members)>256*1024*1024:
            raise ValueError('Office 容器超过 20000 项或 256MB 解包预算')
        names={m.filename for m in members}
        if expected[ext] not in names:
            raise ValueError(f'Office 容器类型不符：缺少 {expected[ext]}')
        if ext in ['odt','ods','odp']:
            mime={'odt':'text','ods':'spreadsheet','odp':'presentation'}[ext]
            if z.read('mimetype').decode().strip()!=f'application/vnd.oasis.opendocument.{mime}':
                raise ValueError('ODF mimetype 与扩展名不符')
        for item in members:
            if item.filename.lower().endswith(('.xml','.rels')):
                data=z.read(item)
                tree=SafeET.fromstring(data)
                for node in tree.iter():
                    for key,value in node.attrib.items():
                        label=key.rsplit('}',1)[-1]
                        if label=='TargetMode' and value.lower()=='external':
                            raise ValueError('Office 文档包含外部关系 TargetMode=External；移除外部链接后再转换')
                        if ext in ['odt','ods','odp'] and label=='href':
                            path=PurePosixPath(value)
                            if re.match(r'^[a-z][a-z0-9+.-]*:',value,re.I) or value.startswith(('/', '\\')) or '..' in path.parts:
                                raise ValueError('ODF 文档包含包外 xlink:href 引用')
                    if node.tag.rsplit('}',1)[-1].lower() in ['ddelink','dde-source','externallink']:
                        raise ValueError('Office 文档包含 DDE/外部链接')


def _office(files,out,options):
    program=_soffice()
    if not program:
        raise ValueError('未检测到 LibreOffice/soffice')
    outputs=[]
    filters={'pdf':None,'doc':'MS Word 97','docx':'Office Open XML Text','odt':'writer8','rtf':'Rich Text Format','txt':'Text (encoded):UTF8','xls':'MS Excel 97','xlsx':'Calc MS Excel 2007 XML','ods':'calc8','csv':'Text - txt - csv (StarCalc):44,34,76','ppt':'MS PowerPoint 97','pptx':'Impress MS PowerPoint 2007 XML','odp':'impress8'}
    for src in files:
        group=next((g for g in OFFICE_GROUPS if src.suffix.lower()[1:] in g[:-1]),None)
        if group is None:
            raise ValueError(f'Office 输入格式不受支持：{src.suffix}')
        target=_target(options,group,'pdf')
        _office_preflight(src)
        with tempfile.TemporaryDirectory(prefix='format-hub-office-') as temp:
            temp=Path(temp)
            spec=target
            if target=='pdf':
                spec='pdf:'+('writer_pdf_Export' if group is OFFICE_GROUPS[0] else 'calc_pdf_Export' if group is OFFICE_GROUPS[1] else 'impress_pdf_Export')
            elif filters[target]:
                spec=f'{target}:{filters[target]}'
            input_filter=[]
            if src.suffix.lower()=='.txt':
                input_filter=['--infilter=Text (encoded):UTF8']
            elif src.suffix.lower()=='.csv':
                input_filter=['--infilter=Text - txt - csv (StarCalc):44,34,76']
            conversion = _run([program,f'-env:UserInstallation={(temp/"profile").as_uri()}','--headless','--norestore','--nofirststartwizard',*input_filter,'--convert-to',spec,'--outdir',temp,src])
            converted=temp/(src.stem+'.'+target)
            if not converted.is_file() or not converted.stat().st_size:
                raise ValueError(f'LibreOffice 未生成 {target}；诊断：{(conversion.stdout + conversion.stderr).strip()[-2000:]}')
            p=_output(out,src.stem,target); shutil.copyfile(converted,p); outputs.append(p)
    return outputs


def _media(operation,files,out,options):
    ffmpeg=_ffmpeg()
    if not ffmpeg:
        raise ValueError('缺少 FFmpeg，请安装 imageio-ffmpeg 或设置 FFMPEG')
    audio=['mp3','wav','flac','ogg','m4a','aac','opus']
    target=_target(options,audio if operation=='media_audio' else ['mp4','mkv','mov','avi','webm']+audio,'mp3' if operation=='media_audio' else 'mp4')
    bitrate=_num(options,'audio_bitrate',192,32,512,True)
    crf=_num(options,'crf',23,0,51,True)
    codecs={'mp3':'libmp3lame','wav':'pcm_s16le','flac':'flac','ogg':'libvorbis','m4a':'aac','aac':'aac','opus':'libopus'}
    outputs=[]
    for src in files:
        p=_output(out,src.stem+'_converted',target)
        # Auto thread counts scale with host CPUs and exhaust the worker's 2GB
        # address-space budget through decoder frames, filter pools and x264 lookahead.
        args=[ffmpeg,'-nostdin','-hide_banner','-loglevel','error','-threads','2','-filter_threads','1','-filter_complex_threads','1','-i',src]
        if target in audio:
            args+=['-map','0:a:0','-vn','-c:a',codecs[target]]
            if target not in ['wav','flac']:
                args+=['-b:a',f'{bitrate}k']
        else:
            args+=['-map','0:v:0','-map','0:a:0?']
            if target=='webm':
                args+=['-c:v','libvpx-vp9','-crf',str(crf),'-b:v','0','-c:a','libopus']
            elif target=='avi':
                args+=['-c:v','mpeg4','-q:v','4','-c:a','libmp3lame']
            else:
                args+=['-c:v','libx264','-crf',str(crf),'-pix_fmt','yuv420p','-vf','pad=ceil(iw/2)*2:ceil(ih/2)*2','-c:a','aac']
                if target in ['mp4','mov']:
                    args+=['-movflags','+faststart']
        _run(args+['-threads','2','-n',p])
        if not p.is_file() or not p.stat().st_size:
            raise ValueError('FFmpeg 输出为空')
        outputs.append(p)
    return outputs


def _read_cad_document(path):
    import ezdxf
    from ezdxf.entities.dxfobj import SortEntsTable
    from ezdxf.lldxf.const import DXFStructureError
    try:
        return ezdxf.readfile(path)
    except DXFStructureError as exc:
        if 'Invalid sort handle code' not in str(exc) and 'Invalid handle code' not in str(exc):
            raise
    original = SortEntsTable.load_table
    dropped = 0
    def load_table(table, tags):
        nonlocal dropped
        table.table = {}
        index = 0
        while index < len(tags):
            handle = tags[index]
            if handle.code == 331 and index + 1 < len(tags) and tags[index + 1].code == 5:
                table.table[handle.value] = tags[index + 1].value
                index += 2
            else:
                dropped += 1
                index += 1
    # Each job owns a dedicated process; restore the library method after this read.
    SortEntsTable.load_table = load_table
    try:
        doc = ezdxf.readfile(path)
    finally:
        SortEntsTable.load_table = original
    if dropped:
        import warnings
        warnings.warn(f'CAD 绘制排序表包含 {dropped} 个无效标签，已保留有效配对并继续转换；请复核重叠图元的显示顺序。', UserWarning)
    return doc


def _cad_pdf_backend(batch_size=1000):
    from ezdxf.addons.drawing import pymupdf
    class BatchedRenderBackend(pymupdf.PyMuPdfRenderBackend):
        pending = 0
        def flush_batch(self):
            self.content_shape.commit()
            self.content_shape = self.page.new_shape()
            self.pending = 0
        def finished_path(self):
            self.pending += 1
            if self.pending >= batch_size:
                self.flush_batch()
        def finish_line(self, shape, properties, close):
            super().finish_line(shape, properties, close)
            self.finished_path()
        def finish_filling(self, shape, properties):
            super().finish_filling(shape, properties)
            self.finished_path()
        def draw_image(self, image_data, properties):
            # Commit preceding vectors before the image to preserve painter order.
            if self.pending:
                self.flush_batch()
            super().draw_image(image_data, properties)
    class BatchedBackend(pymupdf.PyMuPdfBackend):
        @staticmethod
        def make_backend(page, settings):
            return BatchedRenderBackend(page, settings)
    return BatchedBackend()


def _fit_cad_pdf(data):
    import fitz
    with fitz.open(stream=data, filetype='pdf') as source:
        limit = 1189 * 72 / 25.4
        if all(max(page.rect.width, page.rect.height) <= limit for page in source):
            return data
        with fitz.open() as result:
            for page in source:
                portrait = page.rect.height >= page.rect.width
                target = result.new_page(width=(841 if portrait else 1189)*72/25.4, height=(1189 if portrait else 841)*72/25.4)
                target.show_pdf_page(target.rect, source, page.number, keep_proportion=True)
            return result.tobytes(garbage=4, deflate=True)


def _cad_fonts(doc, out, options):
    """Register render-only aliases; real supplied/system fonts always win."""
    import warnings
    from ezdxf.fonts.font_manager import FontManager, FONT_DIRECTORIES, LINUX_FONT_DIRS
    manager=FontManager()
    # Explicit directories avoid ezdxf support_dirs/config paths from the document.
    manager.build(folders=[str(ROOT/'runtime/fonts')]+list(FONT_DIRECTORIES.get(manager.platform,LINUX_FONT_DIRS)),support_dirs=False)
    supplied=options.get('_font_dir')
    if supplied is not None:
        expected=out.parent/'fonts'
        directory=Path(supplied)
        if directory.is_symlink() or directory.resolve()!=expected.resolve() or not directory.is_dir():
            raise ValueError('_font_dir 必须是 worker 当前任务的 fonts 目录')
        for path in directory.rglob('*'):
            if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
                raise ValueError('字体目录含链接或越界路径')
        manager.build(folders=[str(directory)],support_dirs=False)
    names=['NotoSansCJK-Regular.ttc','DroidSansFallback.ttf']
    fallback=next((name for name in names if (ROOT/'runtime/fonts'/name).is_file() and manager.has_font(name)),None)
    fallback=fallback or next((name for name in names if manager.has_font(name)),None)
    if not fallback:
        warnings.warn('CAD 缺少内置中文字体；中文显示可能缺字，请补充字体。',UserWarning)
    aliases=set('simsun.ttf simsun.ttc nsimsun.ttf simhei.ttf simkai.ttf simfang.ttf msyh.ttf msyh.ttc deng.ttf hztxt.shx gbcbig.shx gbcbigt.shx gbenor.shx hzst.shx hzfs.shx tssdeng.shx tssdchn.shx fs.shx st.shx ht.shx kt.shx song.shx china.shx chineset.shx bigfont.shx'.split())
    missing=[]
    for style in doc.styles:
        for attribute in ['font','bigfont']:
            name=str(style.dxf.get(attribute,'')).replace('\\','/').rsplit('/',1)[-1]
            if name and not manager.has_font(name):
                missing.append(name); aliases.add(name)
    if fallback:
        manager._fallback_font_name=fallback
        for alias in aliases:
            manager.add_synonyms({fallback:alias},reverse=False)
    if missing:
        warnings.warn('CAD 缺少字体：'+', '.join(sorted(set(missing)))+('；渲染时使用内置中文字体替代，请复核文字布局。' if fallback else '；请上传所需字体。'),UserWarning)
    # Workers are dedicated processes. Restore this global after rendering so callers
    # in the test/API process never retain another task's uploaded font directory.
    return manager


def _cad_configuration(quality):
    from ezdxf.addons.drawing import config
    presets={'fast':(0.1,32),'balanced':(0.01,128),'high':(0.001,512)}
    if quality not in presets:
        raise ValueError('cad_quality 仅支持 fast/balanced/high')
    distance,segments=presets[quality]
    return config.Configuration(background_policy=config.BackgroundPolicy.WHITE,max_flattening_distance=distance,circle_approximation_count=segments)


def _cad(files,out,options):
    import ezdxf
    from ezdxf.addons.drawing import Frontend,RenderContext,layout,svg,pymupdf,config
    target=_target(options,['pdf','svg','dxf'],'pdf')
    cfg=_cad_configuration(options.get('cad_quality','balanced'))
    outputs=[]
    for src in files:
        with tempfile.TemporaryDirectory(prefix='format-hub-cad-') as tmp:
            dxf=src
            if src.suffix.lower()=='.dwg':
                converter=_dwg()
                if not converter:
                    raise ValueError('DWG 转换需安装 DWG2DXF 或 ODAFileConverter；设置 DWG2DXF 或使用 DXF 输入')
                dxf=Path(tmp)/(src.stem+'.dxf')
                if 'oda' in Path(converter).name.lower():
                    incoming=Path(tmp)/'in'; outgoing=Path(tmp)/'out'
                    incoming.mkdir(); outgoing.mkdir(); shutil.copyfile(src,incoming/src.name)
                    _run([converter,incoming,outgoing,'ACAD2018','DXF','0','1'])
                    candidates=list(outgoing.rglob('*.dxf'))+list(outgoing.rglob('*.DXF'))
                    if len(candidates)!=1:
                        raise ValueError('ODA 未生成唯一 DXF 输出')
                    dxf=candidates[0]
                else:
                    _run([converter,'-o',dxf,src])
                if not dxf.is_file():
                    raise ValueError('DWG 转换器未生成 DXF')
            doc=_read_cad_document(dxf)
            p=_output(out,src.stem,target)
            if target=='dxf':
                doc.saveas(p)
            else:
                msp=doc.modelspace()
                if not len(msp):
                    raise ValueError('DXF 模型空间为空；请把图形放入模型空间后导出')
                backend=svg.SVGBackend() if target=='svg' else _cad_pdf_backend()
                from ezdxf.fonts import fonts
                manager=_cad_fonts(doc,out,options)
                previous=fonts.font_manager
                # Renderer glyph caches key by filename, so isolate them as well.
                caches=[fonts.TrueTypeFont,fonts.ShapeFileFont,fonts.LibreCadFont]
                previous_caches=[font._glyph_caches for font in caches]
                fonts.font_manager=manager
                for font in caches:
                    font._glyph_caches={}
                try:
                    context=RenderContext(doc)
                    # ezdxf defaults to TTF-first SHX mapping; a supplied real SHX
                    # must win over a built-in name synonym such as txt -> txt.ttf.
                    context.shx_resolve_order='stl'
                    context._setup_text_styles(doc)
                    Frontend(context,backend,config=cfg).draw_layout(msp,finalize=True)
                finally:
                    fonts.font_manager=previous
                    for font,cache in zip(caches,previous_caches):
                        font._glyph_caches=cache
                page=layout.Page(0,0,layout.Units.mm,margins=layout.Margins.all(10))
                if target=='svg':
                    p.write_text(backend.get_string(page),encoding='utf-8')
                else:
                    p.write_bytes(_fit_cad_pdf(backend.get_pdf_bytes(page)))
            outputs.append(p)
    return outputs


def _xml(tag,attrs=None,text=None):
    node=ET.Element(f'{{{NS}}}{tag}',attrs or {})
    node.text=text
    return node


def _xml_bytes(node):
    return ET.tostring(node,encoding='utf-8',xml_declaration=True)


def _pdf_ofd(doc,src,out,options):
    dpi=_num(options,'dpi',150,36,600,True)
    quality=_num(options,'quality',90,1,100,True)
    p=_output(out,src.stem,'ofd')
    root=_xml('OFD',{'Version':'1.0','DocType':'OFD'})
    body=ET.SubElement(root,f'{{{NS}}}DocBody')
    info=ET.SubElement(body,f'{{{NS}}}DocInfo')
    ET.SubElement(info,f'{{{NS}}}DocID').text=uuid.uuid4().hex
    ET.SubElement(info,f'{{{NS}}}Creator').text='Format Hub'
    ET.SubElement(body,f'{{{NS}}}DocRoot').text='Doc_0/Document.xml'
    document=_xml('Document')
    common=ET.SubElement(document,f'{{{NS}}}CommonData')
    ET.SubElement(common,f'{{{NS}}}MaxUnitID').text=str(len(doc)*4+3)
    area=ET.SubElement(common,f'{{{NS}}}PageArea')
    first=doc[0].rect
    ET.SubElement(area,f'{{{NS}}}PhysicalBox').text=f'0 0 {first.width*25.4/72:.6f} {first.height*25.4/72:.6f}'
    ET.SubElement(common,f'{{{NS}}}DocumentRes').text='DocumentRes.xml'
    pages=ET.SubElement(document,f'{{{NS}}}Pages')
    resources=_xml('Res',{'BaseLoc':'Res'})
    media=ET.SubElement(resources,f'{{{NS}}}MultiMedias')
    with zipfile.ZipFile(p,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for i,page in enumerate(doc,1):
            w,h=page.rect.width*25.4/72,page.rect.height*25.4/72
            page_id=i*4; resource_id=i*4+1; image_id=i*4+2; layer_id=i*4+3
            ET.SubElement(pages,f'{{{NS}}}Page',{'ID':str(page_id),'BaseLoc':f'Pages/Page_{i}/Content.xml'})
            mm=ET.SubElement(media,f'{{{NS}}}MultiMedia',{'ID':str(resource_id),'Type':'Image','Format':'JPEG'})
            ET.SubElement(mm,f'{{{NS}}}MediaFile').text=f'image_{i}.jpg'
            content=_xml('Page')
            area=ET.SubElement(content,f'{{{NS}}}Area')
            ET.SubElement(area,f'{{{NS}}}PhysicalBox').text=f'0 0 {w:.6f} {h:.6f}'
            layer=ET.SubElement(ET.SubElement(content,f'{{{NS}}}Content'),f'{{{NS}}}Layer',{'ID':str(layer_id),'Type':'Body'})
            ET.SubElement(layer,f'{{{NS}}}ImageObject',{'ID':str(image_id),'ResourceID':str(resource_id),'Boundary':f'0 0 {w:.6f} {h:.6f}','CTM':f'{w:.6f} 0 0 {h:.6f} 0 0'})
            pix=page.get_pixmap(dpi=dpi,alpha=False)
            z.writestr(f'Doc_0/Res/image_{i}.jpg',pix.tobytes('jpeg',jpg_quality=quality))
            z.writestr(f'Doc_0/Pages/Page_{i}/Content.xml',_xml_bytes(content))
        z.writestr('OFD.xml',_xml_bytes(root))
        z.writestr('Doc_0/Document.xml',_xml_bytes(document))
        z.writestr('Doc_0/DocumentRes.xml',_xml_bytes(resources))
    return p


def _ofd_path(base,name):
    value=str(name).replace('\\','/')
    path=PurePosixPath(base)/value
    parts=[]
    for part in path.parts:
        if part in ['', '.', '/']:
            continue
        if part=='..':
            if not parts:
                raise ValueError('OFD 资源路径越出包根目录')
            parts.pop()
        else:
            parts.append(part)
    return '/'.join(parts)


def _ofd_pdf(files,out,options):
    import fitz
    from PIL import Image
    from defusedxml import ElementTree as SafeET
    outputs=[]
    local=lambda n:n.tag.rsplit('}',1)[-1]
    findall=lambda n,tag:[e for e in n.iter() if local(e)==tag]
    text=lambda n,tag:next((e.text.strip() for e in n.iter() if local(e)==tag and e.text),'')
    def numbers(value,size):
        try:
            ns=[float(s) for s in value.replace(',',' ').split()]
        except ValueError as exc:
            raise ValueError(f'OFD 坐标无效：{value}') from exc
        if len(ns)!=size or not all(math.isfinite(n) for n in ns):
            raise ValueError(f'OFD 坐标数量无效：{value}')
        return ns
    for src in files:
        p=_output(out,src.stem,'pdf')
        with zipfile.ZipFile(src) as z, fitz.open() as doc:
            members=z.infolist()
            if len(members)>10000 or sum(f.file_size for f in members)>512*1024*1024:
                raise ValueError('OFD 包超过 10000 项或 512MB 解压预算')
            def xml(name):
                data=z.read(name)
                if len(data)>16*1024*1024:
                    raise ValueError('OFD XML 过大')
                tree=SafeET.fromstring(data)
                for node in tree.iter():
                    kind=local(node)
                    if kind in ['Signatures','Signature','Annotations','Annotation','PageAnnot']:
                        raise ValueError(f'OFD 含未支持的 {kind}，已停止以避免内容丢失')
                    if 'Visible' in node.attrib and node.attrib['Visible'].lower() not in ['true','1']:
                        raise ValueError(f'OFD {kind} 包含隐藏 Visible 属性，当前严格模式停止转换')
                return tree
            # Reject package-level signatures/annotations even when referenced outside
            # Document.xml: otherwise a superficially valid conversion loses them.
            for member in members:
                if member.filename.lower().endswith('.xml'):
                    xml(member.filename)
            root=xml('OFD.xml')
            roots=findall(root,'DocRoot')
            if not roots:
                raise ValueError('OFD.xml 缺少 DocRoot')
            for dr in roots:
                docpath=_ofd_path('',dr.text or '')
                base=str(PurePosixPath(docpath).parent)
                definition=xml(docpath)
                for bad in ['TemplatePage','Signatures','Annotations']:
                    if findall(definition,bad):
                        raise ValueError(f'OFD 含未支持的 {bad}，已停止以避免内容丢失')
                boxtext=text(definition,'PhysicalBox')
                resources={}
                resource_nodes=findall(definition,'DocumentRes')+findall(definition,'PublicRes')
                for rn in resource_nodes:
                    rp=_ofd_path(base,rn.text or '')
                    rs=xml(rp)
                    rb=_ofd_path(str(PurePosixPath(rp).parent),rs.attrib.get('BaseLoc',''))
                    for mm in findall(rs,'MultiMedia'):
                        resources[mm.attrib.get('ID','')]=_ofd_path(rb,text(mm,'MediaFile'))
                for page_ref in findall(definition,'Page'):
                    content=xml(_ofd_path(base,page_ref.attrib.get('BaseLoc','')))
                    for n in content.iter():
                        kind=local(n)
                        if kind in ['TextObject','PathObject','CompositeObject','Template','Clips','Actions','PageAnnot','VideoObject','AudioObject']:
                            raise ValueError(f'OFD 含未支持的 {kind}，已停止以避免内容丢失')
                        if kind in ['Layer','Content'] and ('CTM' in n.attrib or 'Alpha' in n.attrib):
                            raise ValueError('OFD 层变换或透明度尚未支持，已停止以避免内容丢失')
                    box=numbers(text(content,'PhysicalBox') or boxtext,4)
                    if box[2]<=0 or box[3]<=0:
                        raise ValueError('OFD 页面尺寸无效')
                    page=doc.new_page(width=box[2]*72/25.4,height=box[3]*72/25.4)
                    images=findall(content,'ImageObject')
                    for obj in images:
                        resource=obj.attrib.get('ResourceID') or text(obj,'ResourceID')
                        if resource not in resources:
                            raise ValueError(f'OFD 缺少图片资源 {resource}')
                        boundary=numbers(obj.attrib.get('Boundary',''),4)
                        if boundary[2]<=0 or boundary[3]<=0:
                            raise ValueError('OFD 图片 Boundary 尺寸无效')
                        ctm=numbers(obj.attrib.get('CTM',f'{boundary[2]} 0 0 {boundary[3]} 0 0'),6)
                        if abs(ctm[1])>1e-8 or abs(ctm[2])>1e-8 or ctm[0]<=0 or ctm[3]<=0:
                            raise ValueError('OFD 图片含旋转、倾斜或翻转 CTM；当前仅支持正向矩形图片')
                        if obj.attrib.get('Alpha','255')!='255' or obj.attrib.get('SMask'):
                            raise ValueError('OFD 图片透明蒙版/Alpha 尚未支持')
                        # Standard OFD CTM maps the unit image square to page millimetres.
                        # Upstream image OFD uses identity CTM with a full-page Boundary.
                        if all(abs(a-b)<1e-8 for a,b in zip(ctm,[1,0,0,1,0,0])):
                            x,y,w,h=boundary
                        else:
                            x,y=boundary[0]+ctm[4],boundary[1]+ctm[5]
                            w,h=ctm[0],ctm[3]
                        k=72/25.4
                        rect=fitz.Rect((x-box[0])*k,(y-box[1])*k,(x+w-box[0])*k,(y+h-box[1])*k)
                        clip=fitz.Rect((boundary[0]-box[0])*k,(boundary[1]-box[1])*k,(boundary[0]+boundary[2]-box[0])*k,(boundary[1]+boundary[3]-box[1])*k)
                        if not clip.contains(rect):
                            raise ValueError('OFD 图片超出 Boundary，裁切场景尚未支持')
                        data=z.read(resources[resource])
                        with Image.open(io.BytesIO(data)) as im:
                            im.load()
                            buffer=io.BytesIO(); im.convert('RGBA').save(buffer,format='PNG')
                        page.insert_image(rect,stream=buffer.getvalue(),keep_proportion=False)
            if not len(doc):
                raise ValueError('OFD 没有页面')
            _save_pdf(doc,p)
        outputs.append(p)
    return outputs


def _svg(files,out,options):
    import cairosvg
    from defusedxml import ElementTree as SafeET
    target=_target(options,['png','pdf'],'png')
    width=_num(options,'width',0,0,12000,True)
    outputs=[]
    for src in files:
        data=src.read_bytes()
        if len(data)>16*1024*1024 or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
            raise ValueError('SVG 超过 16MB 或含禁止的 DOCTYPE/ENTITY')
        tree=SafeET.fromstring(data)
        for node in tree.iter():
            if node.tag.rsplit('}',1)[-1] in ['script','foreignObject','image','use']:
                # Raster references and use are excluded, including data URLs, to keep
                # the renderer completely independent of network and host files.
                raise ValueError('SVG 包含脚本、foreignObject 或图片/复用引用')
            for key,value in node.attrib.items():
                if key.rsplit('}',1)[-1] in ['href','src'] or key.lower().startswith('on'):
                    raise ValueError('SVG 包含外部引用或事件处理器')
        source=data.decode('utf-8-sig')
        if re.search(r'@import|url\s*\(\s*[\'\"]?\s*(?!#)',source,re.I):
            raise ValueError('SVG 包含外部 CSS URL/import')
        p=_output(out,src.stem,target)
        from cairosvg.surface import PNGSurface, PDFSurface
        def block_fetch(url, resource_type):
            raise ValueError('SVG 渲染请求了外部资源，已停止转换')
        render=PNGSurface.convert if target=='png' else PDFSurface.convert
        render(bytestring=data,write_to=str(p),unsafe=False,url_fetcher=block_fetch,**({'output_width':width} if width else {}))
        outputs.append(p)
    return outputs


def _archive(files,out,options):
    target=_target(options,['zip','tar','tar.gz'],'zip')
    outputs=[]
    for src in files:
        entries=[]; names=set(); total=0
        def record(name,data,directory=False):
            nonlocal total
            if '\\' in name or name.startswith('/') or ':' in name or '..' in PurePosixPath(name).parts:
                raise ValueError(f'压缩包包含危险路径：{name!r}')
            normalized=str(PurePosixPath(name))
            if normalized in ['.',''] or normalized in names:
                raise ValueError('压缩包包含空路径或重复文件名')
            names.add(normalized)
            total+=len(data)
            if len(names)>10000 or total>256*1024*1024:
                raise ValueError('压缩包超过 10000 项或 256MB 解包预算')
            entries.append((normalized,data,directory))
        if zipfile.is_zipfile(src):
            with zipfile.ZipFile(src) as z:
                if len(z.infolist())>10000 or sum(i.file_size for i in z.infolist())>256*1024*1024:
                    raise ValueError('压缩包超过 10000 项或 256MB 解包预算')
                for item in z.infolist():
                    mode=(item.external_attr>>16)&0o170000
                    if mode not in [0,0o100000,0o040000] or item.flag_bits&1:
                        raise ValueError('ZIP 含链接、特殊文件或加密项目')
                    if item.file_size>1024*1024 and item.file_size/max(1,item.compress_size)>1000:
                        raise ValueError('ZIP 压缩比异常，超过压缩炸弹预算')
                    record(item.filename,z.read(item),item.is_dir())
        else:
            with tarfile.open(src,'r:*') as t:
                for item in t:
                    if not (item.isfile() or item.isdir()):
                        raise ValueError('TAR 含链接、设备或特殊文件')
                    if item.size>256*1024*1024-total:
                        raise ValueError('TAR 超过 256MB 解包预算')
                    if item.isdir():
                        record(item.name,b'',True)
                    else:
                        stream=t.extractfile(item)
                        if stream is None:
                            raise ValueError('TAR 文件数据缺失')
                        with stream:
                            record(item.name,stream.read())
        if not entries:
            raise ValueError('压缩包为空')
        stem=src.name[:-7] if src.name.endswith('.tar.gz') else src.stem
        p=_output(out,stem+'_repacked',target)
        if target=='zip':
            with zipfile.ZipFile(p,'w',compression=zipfile.ZIP_DEFLATED) as z:
                for name,data,directory in entries:
                    z.writestr(name+'/' if directory else name,data)
        else:
            with tarfile.open(p,'w:gz' if target=='tar.gz' else 'w') as t:
                for name,data,directory in entries:
                    item=tarfile.TarInfo(name)
                    item.type=tarfile.DIRTYPE if directory else tarfile.REGTYPE
                    item.mode=0o755 if directory else 0o644
                    item.size=len(data)
                    t.addfile(item,io.BytesIO(data) if not directory else None)
        outputs.append(p)
    return outputs


def _execute(operation: str, files: list[Path], output_dir: Path, options: dict) -> list[Path]:
    """Convert files or raise a detailed exception; returned paths stay inside output_dir.

    The queue owns the worker-process timeout. Engines never invoke a shell, modify
    inputs, or trust an output filename supplied through options.
    """
    entries={r['id']:r for r in catalog()}
    if operation not in entries:
        raise ValueError(f'未知转换操作：{operation}')
    row=entries[operation]
    if not row['available']:
        raise ValueError(row['reason'])
    if not files:
        raise ValueError('至少需要一个输入文件')
    if not isinstance(options,dict):
        raise ValueError('options 必须是对象')
    files=[Path(p).resolve() for p in files]
    for src in files:
        if not src.is_file() or not src.stat().st_size:
            raise ValueError(f'输入文件不存在或为空：{src.name}')
        if src.suffix.lower().lstrip('.') not in row['inputs']:
            raise ValueError(f'{operation} 不支持输入格式 {src.suffix}')
    out=Path(output_dir).resolve()
    out.mkdir(parents=True,exist_ok=True)
    if operation in ['image_convert','image_resize']:
        result=_images(operation,files,out,options)
    elif operation=='images_pdf':
        result=_images_pdf(files,out,options)
    elif operation.startswith('pdf_'):
        result=_pdf(operation,files,out,options)
    elif operation=='office_convert':
        result=_office(files,out,options)
    elif operation.startswith('media_'):
        result=_media(operation,files,out,options)
    elif operation=='cad_convert':
        result=_cad(files,out,options)
    elif operation=='ofd_pdf':
        result=_ofd_pdf(files,out,options)
    elif operation=='svg_convert':
        result=_svg(files,out,options)
    elif operation=='archive_convert':
        result=_archive(files,out,options)
    else:
        raise ValueError(f'操作尚未注册处理器：{operation}')
    for p in result:
        if not p.resolve().is_relative_to(out) or not p.is_file() or p.stat().st_size==0:
            raise RuntimeError('转换器未生成有效的任务目录内文件')
        if p.resolve() in files:
            raise RuntimeError('输出与输入发生冲突')
    return result


def execute(operation: str, files: list[Path], output_dir: Path, options: dict) -> list[Path]:
    """Public engine contract, with useful diagnostics stripped of private directories."""
    try:
        return _execute(operation,files,output_dir,options)
    except Exception as exc:
        message=str(exc) or type(exc).__name__
        # Native parsers and converter stderr sometimes echo absolute input paths.
        paths=[str(Path(p).resolve()) for p in files]+[str(Path(output_dir).resolve()),str(ROOT)]
        for private in sorted(paths,key=len,reverse=True):
            message=message.replace(private,Path(private).name)
        message=re.sub(r'/tmp/[^\s\'\"]+','[临时文件]',message)
        raise ValueError(f'{operation}：{message}') from exc
