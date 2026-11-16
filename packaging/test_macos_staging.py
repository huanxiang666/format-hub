import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('stage_macos', Path(__file__).with_name('stage_macos.py'))
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


class MacOSStagingTests(unittest.TestCase):
    def test_load_commands_ignore_self_id_and_parse_rpath(self):
        output = '''Load command 0
          cmd LC_ID_DYLIB
      cmdsize 64
         name @rpath/libbrotlidec.1.dylib (offset 24)
Load command 1
          cmd LC_RPATH
      cmdsize 48
         path @loader_path (offset 12)
Load command 2
          cmd LC_LOAD_DYLIB
      cmdsize 64
         name @rpath/libbrotlicommon.1.dylib (offset 24)
Load command 3
          cmd LC_LOAD_WEAK_DYLIB
         name /usr/lib/libSystem.B.dylib (offset 24)
'''
        with patch.object(stage, 'run', return_value=output):
            self.assertEqual(stage.dependencies(Path('/fixture/lib.dylib')), ['@rpath/libbrotlicommon.1.dylib', '/usr/lib/libSystem.B.dylib'])
            self.assertEqual(stage.load_commands(Path('/fixture/lib.dylib'))[1], ['@loader_path'])

    def test_java_class_is_not_fat_macho(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'JREProperties.class'
            source.write_bytes(bytes.fromhex('cafebabe000000340020') + bytes(80))
            self.assertFalse(stage.macho(source))
            source.write_bytes(bytes.fromhex('cafebabe00000001') + bytes(20))
            self.assertTrue(stage.macho(source))
            source.write_bytes(bytes.fromhex('cffaedfe') + bytes(28))
            self.assertTrue(stage.macho(source))

    def test_rpath_resolution_and_missing_dependency(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'libbrotlidec.dylib'
            source.touch()
            common = root / 'libbrotlicommon.1.dylib'
            common.touch()
            self.assertEqual(stage.resolve_dependency(source, '@rpath/'+common.name, ['@loader_path'], root), common)
            with self.assertRaisesRegex(RuntimeError, 'missing'):
                stage.resolve_dependency(source, '@rpath/missing.dylib', ['@loader_path'], root)


if __name__ == '__main__':
    unittest.main()
