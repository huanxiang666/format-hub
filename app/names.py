"""Display names are bounded metadata; internal paths never come from uploads."""
from pathlib import Path
import re
import unicodedata


def safe_stem(name, budget=140):
    value = unicodedata.normalize('NFKC', Path(str(name).replace('\\', '/')).name)
    value = Path(value).stem
    value = ''.join(c for c in value if not unicodedata.category(c).startswith('C'))
    value = re.sub(r'[<>:"/\\|?*#%]', '_', value).strip(' .')
    value = value.encode('utf-8')[:budget].decode('utf-8', errors='ignore').strip(' .') or '文件'
    if value.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}:
        value = '_' + value
    return value


def output_names(operation, files, outputs):
    """Map trusted engine basenames to user-related, unique download basenames."""
    lookup = {Path(f['stored']).stem: safe_stem(f['name']) for f in files}
    first = safe_stem(files[0]['name'])
    names = []
    used = set()
    for raw in outputs:
        basename = Path(raw).name
        if basename != raw:
            raise ValueError('转换结果名称错误')
        stem, suffix = Path(raw).stem, Path(raw).suffix.lower()
        if operation == 'pdf_merge':
            stem = first + f'_等{len(files)}份_合并'
        elif operation == 'images_pdf':
            stem = first + f'_等{len(files)}张_合成'
        else:
            for physical, original in lookup.items():
                if stem == physical or stem.startswith(physical + '_'):
                    stem = original + stem[len(physical):]
                    break
            else:
                stem = first + '_' + stem
        base = safe_stem(stem + suffix, 180)
        candidate = base + suffix
        index = 2
        while candidate.casefold() in used:
            candidate = f'{base}_{index}{suffix}'
            index += 1
        used.add(candidate.casefold())
        names.append(candidate)
    return names
