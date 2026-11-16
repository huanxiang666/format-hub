"""Release download boundary tests: python -m unittest discover -s packaging."""
import hashlib
import io
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap


class Response(io.BytesIO):
    headers = {}
    def geturl(self):
        return 'https://example.org/engine'


class BootstrapTests(unittest.TestCase):
    def test_http_and_credentials_rejected(self):
        for value in ('http://example.org/a', 'file:///tmp/a', 'https://user:pass@example.org/a'):
            with self.assertRaises(ValueError):
                bootstrap.https_url(value)

    def test_redirect_downgrade_rejected(self):
        with self.assertRaises(ValueError):
            bootstrap.HTTPSRedirect().redirect_request(None, None, 302, '', {}, 'http://example.org/file')

    def test_atomic_download_and_digest_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'engine'
            target.write_bytes(b'original')
            source = dict(sha256=hashlib.sha256(b'valid').hexdigest(), max_download_bytes=10, urls=['https://example.org/engine'])
            with patch('urllib.request.build_opener') as factory:
                factory.return_value.open.return_value = Response(b'bad')
                with self.assertRaises(RuntimeError):
                    bootstrap.download_verified(source, target)
                self.assertEqual(target.read_bytes(), b'original')
                factory.return_value.open.return_value = Response(b'valid')
                bootstrap.download_verified(source, target)
                self.assertEqual(target.read_bytes(), b'valid')
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ['engine'])

    def test_download_size_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.build_opener') as factory:
            factory.return_value.open.return_value = Response(b'oversize')
            source = dict(sha256='0' * 64, max_download_bytes=3, urls=['https://example.org/engine'])
            with self.assertRaises(RuntimeError):
                bootstrap.download_verified(source, Path(tmp) / 'engine')
            self.assertFalse(list(Path(tmp).iterdir()))

    def test_zip_valid_and_bad_paths(self):
        for member, valid in [('program/test.txt', True), ('../outside', False), ('/absolute', False), ('C:\\bad', False), ('..\\outside', False)]:
            with self.subTest(member=member), tempfile.TemporaryDirectory() as tmp:
                archive = Path(tmp) / 'test.zip'
                with zipfile.ZipFile(archive, 'w') as z:
                    z.writestr(member, 'content')
                if valid:
                    bootstrap.extract_verified(archive, Path(tmp) / 'out', 100)
                    self.assertEqual((Path(tmp) / 'out/program/test.txt').read_text(), 'content')
                else:
                    with self.assertRaises(ValueError):
                        bootstrap.extract_verified(archive, Path(tmp) / 'out', 100)

    def test_zip_symlink_size_duplicate(self):
        for mode in ('link', 'size', 'duplicate'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                archive = Path(tmp) / 'test.zip'
                with zipfile.ZipFile(archive, 'w') as z:
                    if mode == 'link':
                        item = zipfile.ZipInfo('link'); item.external_attr = (stat.S_IFLNK | 0o777) << 16
                        z.writestr(item, '/etc/passwd')
                    else:
                        z.writestr('file', 'large content')
                        if mode == 'duplicate':
                            z.writestr('FILE', 'second')
                with self.assertRaises(ValueError):
                    bootstrap.extract_verified(archive, Path(tmp) / 'out', 3 if mode == 'size' else 100)

    def test_tar_link_and_traversal(self):
        for mode in ('link', 'path'):
            with tempfile.TemporaryDirectory() as tmp:
                archive = Path(tmp) / 'test.tar'
                with tarfile.open(archive, 'w') as t:
                    item = tarfile.TarInfo('link' if mode == 'link' else '../outside')
                    if mode == 'link':
                        item.type = tarfile.SYMTYPE; item.linkname = '/etc/passwd'; t.addfile(item)
                    else:
                        item.size = 1; t.addfile(item, io.BytesIO(b'x'))
                with self.assertRaises(ValueError):
                    bootstrap.extract_verified(archive, Path(tmp) / 'out', 100)


if __name__ == '__main__':
    unittest.main()
