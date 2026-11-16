"""Bounded PDF metadata/thumbnail inspection in a confined subprocess."""
import base64
import json
from pathlib import Path


def inspect_files(directory: Path):
    import fitz
    spec = json.loads((directory / 'spec.json').read_text(encoding='utf-8'))
    results = []
    for item in spec['file_metadata']:
        entry = {'name': item['name'], 'kind': 'pdf', 'pages': 0, 'encrypted': False, 'previews': []}
        try:
            with fitz.open(directory / 'input' / item['stored']) as doc:
                if not doc.is_pdf:
                    raise ValueError('不是有效的 PDF 文件')
                entry['encrypted'] = bool(doc.needs_pass)
                if doc.needs_pass and not doc.authenticate(spec['options'].get('password', '')):
                    entry['error'] = '此 PDF 已加密，请填写正确密码后重新预览'
                else:
                    entry['pages'] = len(doc)
                    if len(doc) > 10000:
                        raise ValueError('PDF 超过10000页，请拆小后处理')
                    for index in range(min(len(doc), 6)):
                        page = doc[index]
                        scale = min(240 / max(page.rect.width, 1), 300 / max(page.rect.height, 1))
                        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                        entry['previews'].append({'page': index + 1, 'data_url': 'data:image/png;base64,' + base64.b64encode(pix.tobytes('png')).decode('ascii')})
        except Exception as exc:
            entry['error'] = str(exc).replace(str(directory), '[预览文件]')[:200]
            entry['previews'] = []
            entry['pages'] = 0
        results.append(entry)
    (directory / 'inspection.json').write_text(json.dumps({'files': results}, ensure_ascii=False), encoding='utf-8')
    return []
