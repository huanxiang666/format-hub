"""Real-file engine acceptance tests; no converters are mocked."""
from pathlib import Path
import io
import zipfile

import pytest
from PIL import Image
import fitz

from app.engines import catalog, execute


@pytest.fixture
def picture(tmp_path):
    p = tmp_path / '照片.png'
    Image.new('RGBA', (120, 80), (200, 30, 40, 128)).save(p)
    return p


@pytest.fixture
def pdf(tmp_path):
    p = tmp_path / 'sample.pdf'
    with fitz.open() as d:
        for i, size in enumerate([(240, 320), (320, 240), (180, 300)]):
            page = d.new_page(width=size[0], height=size[1])
            page.insert_text((20, 40), f'Page {i+1} sample')
        d.save(p)
    return p


def run(tmp_path, op, files, **opts):
    out = tmp_path / f'out_{op}'
    results = execute(op, files, out, opts)
    assert results
    for p in results:
        assert p.is_file() and p.stat().st_size > 0
        assert p.resolve().is_relative_to(out.resolve())
    return results


def test_cad_corrupt_sort_table_preserves_geometry(tmp_path):
    import ezdxf
    source = tmp_path / 'broken-order.dxf'
    doc = ezdxf.new('R2010')
    doc.modelspace().add_line((0, 0), (100, 50))
    doc.saveas(source)
    lines = source.read_text(encoding='utf-8').splitlines()
    start = next(i for i in range(len(lines)-1) if lines[i].strip() == '2' and lines[i+1].strip() == 'OBJECTS')
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == 'ENDSEC')
    broken = ['0','SORTENTSTABLE','5','FFFFF001','100','AcDbSortentsTable','331','AAAA','331','BBBB','5','CCCC']
    source.write_text('\n'.join(lines[:end-1]+broken+lines[end-1:])+'\n',encoding='utf-8')
    with pytest.raises(ezdxf.DXFStructureError, match='sort handle'):
        ezdxf.readfile(source)
    import warnings
    with pytest.warns(UserWarning, match='绘制排序'):
        result = run(tmp_path,'cad_convert',[source],target='pdf')[0]
    with fitz.open(result) as pdf:
        assert len(pdf)==1 and len(pdf[0].get_drawings())>0
    # The compatibility override is scoped to one read and must not leak.
    with pytest.raises(ezdxf.DXFStructureError, match='sort handle'):
        ezdxf.readfile(source)


def test_cad_batched_pdf_matches_original_renderer():
    import ezdxf
    from ezdxf.addons.drawing import Frontend, RenderContext, layout, config, pymupdf
    from app.engines import _cad_pdf_backend
    doc = ezdxf.new()
    msp = doc.modelspace()
    for i in range(25):
        msp.add_line((i, 0), (25-i, 30), dxfattribs={'color': i % 7 + 1})
    msp.add_solid([(2,2),(15,2),(2,15),(15,15)],dxfattribs={'color':2})
    cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE)
    pdfs = []
    for backend in [pymupdf.PyMuPdfBackend(), _cad_pdf_backend(batch_size=3)]:
        Frontend(RenderContext(doc),backend,config=cfg).draw_layout(msp,finalize=True)
        pdfs.append(backend.get_pdf_bytes(layout.Page(100,100,layout.Units.mm)))
    with fitz.open(stream=pdfs[0],filetype='pdf') as original, fitz.open(stream=pdfs[1],filetype='pdf') as batched:
        assert len(original)==len(batched)==1
        assert original[0].rect==batched[0].rect
        assert len(original[0].get_drawings())==len(batched[0].get_drawings())
        assert original[0].get_pixmap().samples==batched[0].get_pixmap().samples


def test_cad_oversized_pdf_fits_a0_without_rasterizing():
    from app.engines import _fit_cad_pdf
    with fitz.open() as source:
        page = source.new_page(width=50000,height=100000)
        page.draw_line((100,100),(49000,99000),color=(1,0,0))
        data = source.tobytes()
    with fitz.open(stream=_fit_cad_pdf(data),filetype='pdf') as result:
        assert len(result)==1
        assert max(result[0].rect.width,result[0].rect.height) < 3371
        assert len(result[0].get_drawings())==1
        assert not result[0].get_images()


def test_catalog_contract():
    rows = catalog()
    assert len({r['id'] for r in rows}) == len(rows)
    assert len(rows) >= 22
    for row in rows:
        assert set(row) == {'id','name','category','description','inputs','targets','available','reason','options','multiple'}
        assert isinstance(row['available'], bool)
        assert all('.' not in s for s in row['inputs'])
        for opt in row['options']:
            assert {'name','label','type','default','min','max','choices'} <= set(opt)
            assert opt['type'] in ['number','select','text','checkbox']
            assert isinstance(opt['choices'], list)
    assert '矩形' in next(r for r in rows if r['id']=='image_resize')['description']
    assert '图片型' in next(r for r in rows if r['id']=='ofd_pdf')['description']


@pytest.mark.parametrize('target', ['png','jpg','webp','bmp','tiff','ico','gif'])
def test_image_formats(tmp_path, picture, target):
    out = run(tmp_path, 'image_convert', [picture], target=target)[0]
    with Image.open(out) as im:
        assert im.width > 0 and im.height > 0


def test_resize_canvas_rotate_watermark(tmp_path, picture):
    out = run(tmp_path,'image_resize',[picture],width=200,height=200,mode='contain',background='blue',rotate=90,watermark='DEMO',target='png')[0]
    with Image.open(out) as im:
        assert im.size == (200,200)
        assert im.convert('RGB').getpixel((0,0)) == (67,142,219)


def test_resize_crop_after_exif_before_rotation(tmp_path):
    from PIL import ImageOps
    source=tmp_path/'oriented.png'
    image=Image.new('RGB',(12,8))
    image.putdata([(x*20,y*30,50) for y in range(8) for x in range(12)])
    exif=Image.Exif(); exif[274]=6
    image.save(source,exif=exif)
    with Image.open(source) as raw:
        oriented=ImageOps.exif_transpose(raw).convert('RGBA')
        expected=oriented.crop((0,0,4,12)).transpose(Image.Transpose.ROTATE_270)
    result=run(tmp_path,'image_resize',[source],target='png',crop={'x':0,'y':0,'width':0.5,'height':1},rotate=90,width=12,height=4,mode='stretch',background='transparent',dpi=300)[0]
    with Image.open(result) as actual:
        assert actual.size==expected.size
        assert actual.convert('RGBA').tobytes()==expected.tobytes()
        assert actual.getexif().get(274,1)==1
        assert actual.info['dpi']==pytest.approx((300,300),abs=.02)


@pytest.mark.parametrize('crop',[None,{}, {'x':0,'y':0,'width':1,'height':1,'extra':0}, {'x':-0.1,'y':0,'width':1,'height':1}, {'x':0.5,'y':0,'width':0.6,'height':1}, {'x':0,'y':0,'width':0,'height':1}, {'x':0,'y':0,'width':float('nan'),'height':1}, {'x':False,'y':0,'width':1,'height':1}, {'x':'0','y':0,'width':1,'height':1}, {'x':0,'y':0,'width':.000001,'height':1}])
def test_resize_crop_strict_validation(tmp_path,picture,crop):
    with pytest.raises(ValueError,match='crop|有效范围'):
        run(tmp_path,'image_resize',[picture],crop=crop)


@pytest.mark.parametrize('target',['jpg','png','tiff','webp'])
def test_resize_dpi_metadata(tmp_path,picture,target):
    result=run(tmp_path,'image_resize',[picture],target=target,dpi=240)[0]
    with Image.open(result) as image:
        if target=='webp':
            metadata=image.getexif()
            assert float(metadata[282])==240 and float(metadata[283])==240 and metadata[296]==2
        else:
            assert image.info['dpi']==pytest.approx((240,240),abs=.02)


@pytest.mark.parametrize('dpi',[71,601,True,float('inf')])
def test_resize_dpi_invalid(tmp_path,picture,dpi):
    with pytest.raises(ValueError,match='dpi'):
        run(tmp_path,'image_resize',[picture],dpi=dpi)


@pytest.mark.parametrize('target',['jpg','webp'])
def test_resize_target_kb_real_encoded_bytes(tmp_path,target):
    import random
    source=tmp_path/'noise.png'
    image=Image.frombytes('RGB',(256,256),random.Random(132).randbytes(256*256*3)); image.save(source)
    result=run(tmp_path,'image_resize',[source],target=target,width=256,height=256,max_kb=12,quality=95,dpi=150)[0]
    assert result.stat().st_size<=12*1024
    with Image.open(result) as encoded:
        assert encoded.size==(256,256)
    with pytest.raises(ValueError,match='最低质量'):
        run(tmp_path,'image_resize',[source],target=target,width=256,height=256,max_kb=.01)
    with pytest.raises(ValueError,match='仅支持'):
        run(tmp_path,'image_resize',[source],target='png',max_kb=12)


def test_resize_transparent_canvas(tmp_path,picture):
    result=run(tmp_path,'image_resize',[picture],width=200,height=200,target='png',background='transparent')[0]
    with Image.open(result) as image:
        assert image.getpixel((0,0))[3]==0
    with pytest.raises(ValueError,match='transparent'):
        run(tmp_path,'image_resize',[picture],target='jpg',background='transparent')


def test_animation_preserved(tmp_path):
    p = tmp_path / 'anim.gif'
    Image.new('RGB',(30,30),'red').save(p,save_all=True,append_images=[Image.new('RGB',(30,30),'blue')],duration=[100,200],loop=0)
    out = run(tmp_path,'image_convert',[p],target='webp')[0]
    with Image.open(out) as im:
        assert im.n_frames == 2
    with pytest.raises(ValueError, match='动画'):
        run(tmp_path,'image_convert',[p],target='jpg')


def test_images_pdf_and_render(tmp_path,picture):
    out = run(tmp_path,'images_pdf',[picture,picture])[0]
    with fitz.open(out) as d:
        assert len(d)==2
    images = run(tmp_path,'pdf_images',[out],dpi=72,target='png')
    assert len(images)==2


def test_pdf_merge_extract_split(tmp_path,pdf):
    merged = run(tmp_path,'pdf_merge',[pdf,pdf])[0]
    with fitz.open(merged) as d:
        assert len(d)==6
    out = run(tmp_path,'pdf_extract',[pdf],pages='3,1-2')[0]
    with fitz.open(out) as d:
        assert len(d)==3 and 'Page 3' in d[0].get_text()
    pieces = run(tmp_path,'pdf_split',[pdf],pages='1-2,3')
    assert len(pieces)==2
    with fitz.open(pieces[0]) as d:
        assert len(d)==2


@pytest.mark.parametrize('pages',['0','4','3-1','1,,2','nonsense'])
def test_bad_page_ranges(tmp_path,pdf,pages):
    with pytest.raises(ValueError, match='页'):
        run(tmp_path,'pdf_extract',[pdf],pages=pages)


def test_pdf_edits_text_encryption(tmp_path,pdf):
    rotated = run(tmp_path,'pdf_rotate',[pdf],angle=90,pages='2')[0]
    with fitz.open(rotated) as d:
        assert d[0].rotation==0 and d[1].rotation==90
    numbered=run(tmp_path,'pdf_numbers',[pdf],start=7)[0]
    with fitz.open(numbered) as d:
        assert '7' in d[0].get_text()
    marked=run(tmp_path,'pdf_watermark',[pdf],text='CONFIDENTIAL')[0]
    with fitz.open(marked) as d:
        assert 'CONFIDENTIAL' in d[0].get_text()
    text=run(tmp_path,'pdf_text',[pdf])[0]
    assert 'Page 3 sample' in text.read_text(encoding='utf-8')
    compressed=run(tmp_path,'pdf_compress',[pdf])[0]
    with fitz.open(compressed) as d:
        assert len(d)==3 and 'Page 2' in d[1].get_text()
    locked=run(tmp_path,'pdf_encrypt',[pdf],password='test-user',owner_password='test-owner')[0]  # gau:allow public PDF test fixture
    with fitz.open(locked) as d:
        assert d.needs_pass
    with pytest.raises(ValueError, match='密码'):
        run(tmp_path,'pdf_decrypt',[locked],password='wrong')
    clear=run(tmp_path,'pdf_decrypt',[locked],password='test-user')[0]  # gau:allow public PDF test fixture
    with fitz.open(clear) as d:
        assert not d.needs_pass and len(d)==3


def test_pdf_docx_image(tmp_path,pdf):
    from docx import Document
    out=run(tmp_path,'pdf_docx_image',[pdf],dpi=72)[0]
    d=Document(out)
    assert len(d.inline_shapes)==3
    assert len(d.sections)==3


def test_pdf_docx_editable(tmp_path,pdf):
    from docx import Document
    out=run(tmp_path,'pdf_docx',[pdf])[0]
    assert 'Page 1' in '\n'.join(p.text for p in Document(out).paragraphs)


def test_ofd_roundtrip_mixed_sizes(tmp_path,pdf):
    ofd=run(tmp_path,'pdf_ofd',[pdf],dpi=72)[0]
    assert zipfile.is_zipfile(ofd)
    import xml.etree.ElementTree as ET
    ids=[]
    with zipfile.ZipFile(ofd) as z:
        for name in z.namelist():
            if name.endswith('.xml'):
                ids.extend(n.attrib['ID'] for n in ET.fromstring(z.read(name)).iter() if 'ID' in n.attrib)
    assert len(ids)==len(set(ids))
    out=run(tmp_path,'ofd_pdf',[ofd])[0]
    with fitz.open(pdf) as src, fitz.open(out) as dst:
        assert len(src)==len(dst)
        for a,b in zip(src,dst):
            assert abs(a.rect.width-b.rect.width)<0.1
            assert abs(a.rect.height-b.rect.height)<0.1
            pix=b.get_pixmap()
            assert min(pix.samples)<100


def test_ofd_text_object_is_explicit_error(tmp_path,pdf):
    ofd=run(tmp_path,'pdf_ofd',[pdf],dpi=72)[0]
    altered=tmp_path/'text.ofd'
    with zipfile.ZipFile(ofd) as src, zipfile.ZipFile(altered,'w') as dst:
        for name in src.namelist():
            data=src.read(name)
            if name.endswith('Content.xml'):
                data=data.replace(b'</ofd:Layer>',b'<ofd:TextObject ID="999"/></ofd:Layer>')
            dst.writestr(name,data)
    with pytest.raises(ValueError,match='TextObject'):
        run(tmp_path,'ofd_pdf',[altered])


@pytest.mark.parametrize('variant',['signature','visible_image','visible_layer'])
def test_ofd_signature_and_hidden_objects_fail(tmp_path,pdf,variant):
    ofd=run(tmp_path,'pdf_ofd',[pdf],dpi=72)[0]
    altered=tmp_path/f'{variant}.ofd'
    with zipfile.ZipFile(ofd) as src, zipfile.ZipFile(altered,'w') as dst:
        for name in src.namelist():
            data=src.read(name)
            if variant=='signature' and name=='OFD.xml':
                data=data.replace(b'</ofd:DocBody>',b'<ofd:Signatures>Signs/Signatures.xml</ofd:Signatures></ofd:DocBody>')
            if name.endswith('Content.xml'):
                if variant=='visible_image':
                    data=data.replace(b'<ofd:ImageObject ',b'<ofd:ImageObject Visible="false" ')
                if variant=='visible_layer':
                    data=data.replace(b'<ofd:Layer ',b'<ofd:Layer Visible="false" ')
            dst.writestr(name,data)
    with pytest.raises(ValueError,match='Signatures|Visible'):
        run(tmp_path,'ofd_pdf',[altered])


def test_cad_real_dxf(tmp_path):
    import ezdxf
    p=tmp_path/'drawing.dxf'
    d=ezdxf.new()
    d.modelspace().add_lwpolyline([(0,0),(50,0),(50,30),(0,30)],close=True)
    d.modelspace().add_text('CAD TEST',dxfattribs={'height':4})
    d.saveas(p)
    svg=run(tmp_path,'cad_convert',[p],target='svg')[0]
    assert '<svg' in svg.read_text(encoding='utf-8')
    out=run(tmp_path,'cad_convert',[p],target='pdf')[0]
    with fitz.open(out) as d:
        assert len(d)==1 and len(d[0].get_drawings())>0
    out=run(tmp_path,'cad_convert',[p],target='dxf')[0]
    assert len(ezdxf.readfile(out).modelspace())==2


def test_cad_quality_changes_real_arc_tessellation(tmp_path):
    import ezdxf
    source=tmp_path/'arc.dxf'; doc=ezdxf.new()
    doc.modelspace().add_lwpolyline([(0,0,2,2,1),(30,0,2,2,0)],format='xyseb')
    doc.saveas(source); original=source.read_bytes(); counts=[]
    for quality in ['fast','balanced','high']:
        result=run(tmp_path,'cad_convert',[source],target='pdf',cad_quality=quality)[0]
        with fitz.open(result) as pdf:
            assert not pdf[0].get_images()
            counts.append(sum(len(d['items']) for d in pdf[0].get_drawings()))
    assert counts[0]<counts[1]<counts[2]
    assert source.read_bytes()==original
    with pytest.raises(ValueError,match='cad_quality'):
        run(tmp_path,'cad_convert',[source],cad_quality='invalid')


def test_cad_missing_cjk_font_matches_bundled_glyphs(tmp_path):
    import ezdxf
    from app.engines import ROOT
    assert (ROOT/'runtime/fonts/DroidSansFallback.ttf').is_file()
    images=[]
    for font in ['missing-cjk.ttf','DroidSansFallback.ttf','missing-cjk.shx']:
        source=tmp_path/(font+'.dxf');doc=ezdxf.new()
        doc.styles.new('Chinese',dxfattribs={'font':font})
        doc.modelspace().add_text('中文图纸',dxfattribs={'style':'Chinese','height':10})
        doc.saveas(source); original=source.read_bytes()
        with pytest.warns(UserWarning,match='缺少字体'):
            result=run(tmp_path,'cad_convert',[source],target='pdf')[0]
        with fitz.open(result) as pdf:
            # The backend batches glyph outlines into one filled path.
            assert sum(len(d['items']) for d in pdf[0].get_drawings())>50
            pix=pdf[0].get_pixmap()
            images.append((pix.width,pix.height,pix.samples))
        assert source.read_bytes()==original
    assert images[0]==images[1]==images[2]


def test_cad_user_font_wins_and_internal_directory_bounded(tmp_path):
    import ezdxf,shutil
    from app.engines import _cad_fonts,ROOT
    doc=ezdxf.new();doc.styles.new('Chinese',dxfattribs={'font':'simsun.ttf'})
    fonts=tmp_path/'fonts';fonts.mkdir()
    supplied=fonts/'simsun.ttf';shutil.copyfile(ROOT/'runtime/fonts/DroidSansFallback.ttf',supplied)
    manager=_cad_fonts(doc,tmp_path/'output',{'_font_dir':str(fonts)})
    assert manager.get_font_face('simsun.ttf').filename=='simsun.ttf'
    with pytest.raises(ValueError,match='_font_dir'):
        _cad_fonts(doc,tmp_path/'output',{'_font_dir':str(ROOT/'runtime/fonts')})
    (fonts/'escape.ttf').symlink_to(supplied)
    with pytest.raises(ValueError,match='链接'):
        _cad_fonts(doc,tmp_path/'output',{'_font_dir':str(fonts)})


def test_media_original_sample_worker_2gb(tmp_path):
    import json,os,shutil,subprocess,sys
    from app.engines import ROOT,_ffmpeg
    source=ROOT/'data/jobs/632753bc1093b8df01871d1d6fe72594/input/001.mp4'
    assert source.is_file(), '原视频验收样本缺失'
    task=tmp_path/'video';(task/'input').mkdir(parents=True)
    shutil.copyfile(source,task/'input/001.mp4')
    (task/'spec.json').write_text(json.dumps({'operation':'media_convert','files':['001.mp4'],'options':{'target':'mov'}}),encoding='utf-8')
    completed=subprocess.run([sys.executable,'-m','app.worker',str(task)],cwd=ROOT,env={**os.environ,'FORMAT_HUB_MEMORY_MB':'2048'},capture_output=True,timeout=120)
    assert completed.returncode==0,completed.stderr.decode()
    result=json.loads((task/'result.json').read_text(encoding='utf-8'))
    assert result['ok'] and len(result['outputs'])==1
    output=task/'output'/result['outputs'][0]
    decoded=subprocess.run([_ffmpeg(),'-v','error','-threads','2','-i',str(output),'-f','null','-'],capture_output=True,timeout=120)
    assert decoded.returncode==0,decoded.stderr.decode()


def test_media_real_audio(tmp_path):
    import wave
    p=tmp_path/'tone.wav'
    with wave.open(str(p),'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(b'\x00\x00'*800)
    out=run(tmp_path,'media_audio',[p],target='mp3')[0]
    assert out.read_bytes()[:3] in [b'ID3',b'\xff\xfb\x90',b'\xff\xf3\x40'] or out.stat().st_size>100


def test_media_video_real(tmp_path):
    import subprocess
    import imageio_ffmpeg
    src=tmp_path/'clip.mp4'
    result=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=blue:s=64x48:d=0.2','-f','lavfi','-i','sine=frequency=440:duration=0.2','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(src)],capture_output=True)
    assert result.returncode==0,result.stderr.decode()
    out=run(tmp_path,'media_convert',[src],target='webm')[0]
    checked=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-i',str(out),'-f','null','-'],capture_output=True)
    assert checked.returncode==0,checked.stderr.decode()


def test_validation_and_no_overwrite(tmp_path,picture,pdf):
    with pytest.raises(ValueError,match='操作'):
        execute('unknown',[picture],tmp_path/'o',{})
    with pytest.raises(ValueError,match='输入'):
        execute('image_convert',[],tmp_path/'o',{})
    with pytest.raises(ValueError,match='格式'):
        run(tmp_path,'image_convert',[picture],target='../escape')
    with pytest.raises(ValueError,match='width'):
        run(tmp_path,'image_resize',[picture],width=-1)
    original=picture.read_bytes()
    outputs=execute('image_convert',[picture],picture.parent,{'target':'png'})
    assert outputs[0]!=picture and picture.read_bytes()==original
    target=tmp_path/'symlink_out'; target.mkdir()
    outside=tmp_path/'outside'; outside.mkdir()
    (target/'照片_converted.png').symlink_to(outside/'escaped.png')
    outputs=execute('image_convert',[picture],target,{'target':'png'})
    assert not (outside/'escaped.png').exists()
    assert outputs[0].resolve().is_relative_to(target.resolve())


def test_long_chinese_output_name(tmp_path):
    src=tmp_path/(('文'*83)+'.png')
    Image.new('RGB',(16,16),'blue').save(src)
    output=run(tmp_path,'image_convert',[src],target='png')[0]
    assert len(output.name.encode('utf-8'))<255
    with Image.open(output) as image:
        assert image.size==(16,16)


def test_office_target_validation_before_external_process(tmp_path):
    row=next(r for r in catalog() if r['id']=='office_convert')
    if row['available']:
        p=tmp_path/'bad.docx'; p.write_bytes(b'not an office file')
        with pytest.raises(ValueError,match='目标'):
            run(tmp_path,'office_convert',[p],target='xlsx')
    else:
        p=tmp_path/'bad.docx'; p.write_bytes(b'not an office file')
        with pytest.raises(ValueError,match='soffice|LibreOffice'):
            run(tmp_path,'office_convert',[p],target='pdf')


@pytest.mark.parametrize('target',['zip','tar','tar.gz'])
def test_archive_repack(tmp_path,target):
    import tarfile
    src=tmp_path/'archive.zip'
    with zipfile.ZipFile(src,'w') as z:
        z.writestr('中文/data.txt','hello 世界')
    out=run(tmp_path,'archive_convert',[src],target=target)[0]
    if target=='zip':
        with zipfile.ZipFile(out) as z:
            assert z.read('中文/data.txt').decode()=='hello 世界'
    else:
        with tarfile.open(out) as t:
            assert t.extractfile('中文/data.txt').read().decode()=='hello 世界'


def test_archive_traversal_and_symlink(tmp_path):
    import tarfile
    for kind in ['path','symlink']:
        src=tmp_path/f'{kind}.tar'
        with tarfile.open(src,'w') as t:
            item=tarfile.TarInfo('../escape' if kind=='path' else 'link')
            if kind=='symlink':
                item.type=tarfile.SYMTYPE;item.linkname='/etc/passwd'
            t.addfile(item)
        with pytest.raises(ValueError,match='路径|链接'):
            run(tmp_path,'archive_convert',[src],target='zip')


def test_svg_real_and_external_blocked(tmp_path):
    p=tmp_path/'vector.svg'
    p.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80"><rect width="100" height="60" fill="red"/></svg>',encoding='utf-8')
    png=run(tmp_path,'svg_convert',[p],target='png')[0]
    with Image.open(png) as im:
        assert im.size==(120,80)
    pdf=run(tmp_path,'svg_convert',[p],target='pdf')[0]
    with fitz.open(pdf) as d:
        assert len(d)==1
    p.write_text('<svg xmlns="http://www.w3.org/2000/svg"><image href="file:///etc/passwd"/></svg>',encoding='utf-8')
    with pytest.raises(ValueError,match='引用'):
        run(tmp_path,'svg_convert',[p])


def test_office_real_roundtrip(tmp_path):
    if not next(r for r in catalog() if r['id']=='office_convert')['available']:
        pytest.fail('Office runtime 未就绪，真实办公转换验收尚未完成')
    from docx import Document
    from openpyxl import Workbook,load_workbook
    doc=Document();doc.add_paragraph('Office editable sample');src=tmp_path/'office.docx';doc.save(src)
    pdf=run(tmp_path,'office_convert',[src],target='pdf')[0]
    with fitz.open(pdf) as d:
        assert 'Office editable sample' in d[0].get_text()
    sheet=Workbook();sheet.active['A1']='中文';sheet.active['B1']=42
    src=tmp_path/'sheet.xlsx';sheet.save(src)
    converted=run(tmp_path,'office_convert',[src],target='ods')[0]
    returned=run(tmp_path,'office_convert',[converted],target='xlsx')[0]
    wb=load_workbook(returned)
    assert wb.active['A1'].value=='中文' and wb.active['B1'].value==42


@pytest.mark.parametrize('variant',['html_doc','rtf_field','docx_external','odf_external'])
def test_office_external_preflight(tmp_path,variant):
    from docx import Document
    if variant=='html_doc':
        src=tmp_path/'fake.doc';src.write_text('<html><img src="http://127.0.0.1:9/private"></html>',encoding='utf-8')
    elif variant=='rtf_field':
        src=tmp_path/'field.rtf';src.write_bytes(b'{\\rtf1 {\\field {\\*\\fldinst INCLUDEPICTURE "http://127.0.0.1:9/private"}}}')
    elif variant=='docx_external':
        valid=tmp_path/'valid.docx';d=Document();d.add_paragraph('sample');d.save(valid)
        src=tmp_path/'external.docx'
        with zipfile.ZipFile(valid) as original,zipfile.ZipFile(src,'w') as modified:
            for name in original.namelist():
                data=original.read(name)
                if name=='word/_rels/document.xml.rels':
                    data=data.replace(b'</Relationships>',b'<Relationship Id="rId99" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="http://127.0.0.1:9/private" TargetMode="External"/></Relationships>')
                modified.writestr(name,data)
    else:
        src=tmp_path/'external.odt'
        with zipfile.ZipFile(src,'w') as z:
            z.writestr('mimetype','application/vnd.oasis.opendocument.text')
            z.writestr('content.xml','<document xmlns:xlink="http://www.w3.org/1999/xlink"><image xlink:href="../private"/></document>')
    with pytest.raises(ValueError,match='签名|外部|字段|包外'):
        run(tmp_path,'office_convert',[src],target='pdf')


@pytest.mark.parametrize('ext',['txt','csv'])
def test_office_text_forced_import(tmp_path,ext):
    src=tmp_path/f'literal.{ext}'
    src.write_text('<html><img src="http://127.0.0.1:9/private">LITERAL</html>',encoding='utf-8')
    pdf=run(tmp_path,'office_convert',[src],target='pdf')[0]
    with fitz.open(pdf) as d:
        assert '<html>' in ''.join(p.get_text() for p in d)


def test_office_legacy_real(tmp_path):
    from docx import Document
    src=tmp_path/'legacy_source.docx';d=Document();d.add_paragraph('Legacy valid sample');d.save(src)
    legacy=run(tmp_path,'office_convert',[src],target='doc')[0]
    pdf=run(tmp_path,'office_convert',[legacy],target='pdf')[0]
    with fitz.open(pdf) as d:
        assert 'Legacy valid sample' in d[0].get_text()


def test_image_word_real_pagination(tmp_path,pdf):
    if not next(r for r in catalog() if r['id']=='office_convert')['available']:
        pytest.fail('Office runtime 未就绪，图片型 Word 分页验收尚未完成')
    word=run(tmp_path,'pdf_docx_image',[pdf],dpi=72)[0]
    rendered=run(tmp_path,'office_convert',[word],target='pdf')[0]
    with fitz.open(rendered) as d:
        assert len(d)==3
        assert [round(p.rect.width) for p in d]==[240,320,180]

