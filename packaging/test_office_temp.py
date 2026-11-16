import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import engines


class OfficeTemporaryPathTests(unittest.TestCase):
    def test_windows_short_directory_and_linux_job_local_default(self):
        original = tempfile.TemporaryDirectory
        for platform in ('win32', 'linux'):
            with self.subTest(platform=platform), original(prefix='format-hub-office-regression-') as base:
                base = Path(base).resolve()
                source = base / 'input.docx'; source.write_bytes(b'container')
                output = base / 'output'; output.mkdir()
                profile = base / 'office-tmp'; profile.mkdir()
                with patch.object(engines.sys, 'platform', platform), patch.dict(os.environ, {'TEMP': str(base)}), patch.object(engines, '_soffice', return_value='soffice.com'), patch.object(engines, '_office_preflight'), patch.object(engines.tempfile, 'TemporaryDirectory') as temporary, patch.object(engines, '_run') as run:
                    temporary.return_value.__enter__.return_value = str(profile)
                    def convert(command):
                        self.assertIn('-env:UserInstallation=' + (profile / 'profile').as_uri(), command)
                        (profile / 'input.pdf').write_bytes(b'%PDF-1.7\n')
                        return subprocess.CompletedProcess(command, 0, '', '')
                    run.side_effect = convert
                    result = engines._office([source], output, {'target': 'pdf'})
                    self.assertEqual(result[0].read_bytes(), b'%PDF-1.7\n')
                    if platform == 'win32':
                        temporary.assert_called_once_with(prefix='fh-', dir=str(base))
                    else:
                        temporary.assert_called_once_with(prefix='format-hub-office-')
                    temporary.return_value.__exit__.assert_called_once()


if __name__ == '__main__':
    unittest.main()
