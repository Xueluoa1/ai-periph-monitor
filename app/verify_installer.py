"""Offline distribution checks; never install, launch, or change Windows settings."""
import ast
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import installer


class InstallerTests(unittest.TestCase):
    def test_runtime_manifest_has_all_local_imports(self):
        source = Path(installer.__file__).resolve().parent
        files = installer.source_install_files(str(source))
        names = {path.name for path in files}
        self.assertTrue({'gravastar_k1.py', 'shared_readings.py', 'tray_font.py',
                         'launcher_main.py', 'ui_mouse_pages.py'} <= names)
        for path in files:
            if path.suffix != '.py':
                continue
            tree = ast.parse(path.read_text(encoding='utf-8-sig'))
            for node in ast.walk(tree):
                imports = ([alias.name.split('.')[0] for alias in node.names]
                           if isinstance(node, ast.Import) else
                           [node.module.split('.')[0]]
                           if isinstance(node, ast.ImportFrom) and node.module else [])
                for name in imports:
                    if (source / (name + '.py')).is_file():
                        self.assertIn(name + '.py', names, f'{path.name} imports {name}')
        self.assertFalse(any(name.startswith(('verify_', 'build_', 'qa_', 'test_'))
                             for name in names))

    def test_source_copy_contains_runtime_fonts_and_icons(self):
        source = Path(installer.__file__).resolve().parent
        with tempfile.TemporaryDirectory() as destination:
            installer.copy_source_distribution(str(source), destination)
            target = Path(destination)
            for path in installer.source_install_files(str(source)):
                self.assertEqual(path.read_bytes(), (target / path.name).read_bytes())
            for name in installer.SOURCE_ASSET_DIRS:
                for path in (source / name).rglob('*'):
                    if path.is_file():
                        self.assertEqual(path.read_bytes(),
                                         (target / path.relative_to(source)).read_bytes())
            self.assertTrue((target / 'fonts' / 'ZCOOLQingKeHuangYou-Regular.ttf').is_file())

    def test_missing_required_assets_fail_before_copy(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as target:
            (Path(source) / 'app.py').write_text('')
            with self.assertRaises(FileNotFoundError):
                installer.copy_source_distribution(source, target)
            self.assertEqual(list(Path(target).iterdir()), [])

    def test_failed_executable_copy_never_registers_original_location(self):
        with tempfile.TemporaryDirectory() as target, ExitStack() as stack:
            stack.enter_context(patch.object(installer, 'install_dir', return_value=target))
            stack.enter_context(patch.object(installer.tc, 'is_frozen', return_value=True))
            for name in ('post_to_tray', 'stop_widget_app', 'is_tray_running', 'is_widget_running'):
                stack.enter_context(patch.object(installer.tc, name, return_value=False))
            stack.enter_context(patch('time.sleep'))
            stack.enter_context(patch.object(installer.shutil, 'copy2', side_effect=PermissionError('locked')))
            shortcut = stack.enter_context(patch.object(installer.win_shell, 'create_shortcut'))
            registry = stack.enter_context(patch.object(installer.winreg, 'CreateKeyEx'))
            launch = stack.enter_context(patch.object(installer.subprocess, 'Popen'))
            with self.assertRaisesRegex(OSError, '安装未完成'):
                installer.install_to_windows(start_now=True)
            shortcut.assert_not_called()
            registry.assert_not_called()
            launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
