import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from e16toomarchy import picker
from e16toomarchy.picker_install import install_cli


class RuntimeInstallTests(unittest.TestCase):
    def test_cli_wrapper_quotes_paths_and_preserves_arguments(self):
        with tempfile.TemporaryDirectory(prefix="E16 runtime's ") as tmp:
            root = Path(tmp)
            runtime = root / 'runtime'
            python = runtime / 'venv/bin/python'
            python.parent.mkdir(parents=True)
            python.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            python.chmod(0o755)
            command = install_cli(runtime, root / 'bin')
            args = ['convert', 'Some Theme', '$(touch never)']
            result = subprocess.check_output([str(command), *args], text=True).splitlines()
            self.assertEqual(result, [str(runtime / 'converter.py'), *args])
            command.write_text('#!/bin/sh\n# My own launcher\n')
            self.assertIsNone(install_cli(runtime, root / 'bin'))
            self.assertIn('My own launcher', command.read_text())

    def test_native_build_is_reused_until_sources_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            for name in ('plugin/plugin.cpp', 'plugin/frame.hpp', 'Makefile'):
                path = runtime / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('original')
            def build(_):
                (runtime / 'build').mkdir(exist_ok=True)
                (runtime / 'build/e16-decorations.so').write_bytes(b'fixture')
            def command(args):
                return json.dumps({'version': '0.56.2', 'commit': 'abcdef', 'abiHash': 'test-abi'}) if args[0] == 'hyprctl' else '0.56.2'
            with patch.object(picker, 'paths', return_value={'runtime': runtime}), \
                 patch.object(picker, 'run_command', side_effect=command), \
                 patch.object(picker, 'run_build', side_effect=build) as compiler:
                picker.ensure_native(lambda _: None)
                picker.ensure_native(lambda _: None)
                self.assertEqual(compiler.call_count, 1)
                (runtime / 'plugin/plugin.cpp').write_text('changed implementation')
                picker.ensure_native(lambda _: None)
                self.assertEqual(compiler.call_count, 2)
