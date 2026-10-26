"""Exercise the real POSIX curl-pipe-sh entry point with isolated system tools."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'install.sh'
MOCK = r'''import json, os, pathlib, shlex, shutil, subprocess, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['TEST_LOG'], 'a') as log:
    log.write(json.dumps([name, *args]) + '\n')
if name == 'id':
    print(os.environ.get('TEST_UID', '1000'))
elif name == 'hyprctl':
    version = os.environ.get('TEST_VERSION', '0.56.2')
    if args == ['version']: print('Hyprland ' + version + ' built from test')
    else: print(json.dumps({'version': version, 'commit': 'abcdef'}))
elif name == 'pacman':
    missing = json.loads(pathlib.Path(os.environ['TEST_MISSING']).read_text())
    sys.exit(1 if args[-1] in missing else 0)
elif name == 'omarchy':
    if args[:2] == ['pkg', 'add']:
        pathlib.Path(os.environ['TEST_MISSING']).write_text('[]')
elif name == 'pkg-config':
    if args[0] == '--modversion': print('0.56.2')
    else: print(shlex.quote('-I' + os.environ['TEST_HEADERS']))
elif name == 'curl':
    if os.environ.get('TEST_DOWNLOAD_FAIL'): sys.exit(22)
    shutil.copyfile(os.environ['TEST_ARCHIVE'], args[args.index('--output') + 1])
elif name == 'python':
    if args == ['-']:
        sys.exit(subprocess.run([os.environ['TEST_REAL_PYTHON'], '-'], input=sys.stdin.read(), text=True).returncode)
    if args[:2] == ['-m', 'venv']:
        target = pathlib.Path(args[2]) / 'bin'
        target.mkdir(parents=True)
        (target / 'python').symlink_to(pathlib.Path(os.environ['TEST_BIN']) / 'python')
    elif args[:2] == ['-m', 'e16toomarchy.picker_install']:
        assert '--build-native' in args
        source = pathlib.Path(args[args.index('--source') + 1])
        assert (source / 'e16toomarchy/picker_install.py').is_file()
        if os.environ.get('TEST_INSTALL_FAIL'): sys.exit(1)
'''


class ShellInstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='e16 installer test ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.scratch = self.root / 'scratch'
        self.scratch.mkdir()
        self.log = self.root / 'commands.jsonl'
        self.missing = self.root / 'missing.json'
        self.missing.write_text('[]')
        headers = self.root / 'headers'
        (headers / 'src').mkdir(parents=True)
        self.header = headers / 'src/version.h'
        self.header.write_text('#define GIT_COMMIT_HASH "abcdef"\n')
        archive = self.root / 'source.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            data = b'# fixture\n'
            entry = tarfile.TarInfo('e16toomarchy-main/e16toomarchy/picker_install.py')
            entry.size = len(data)
            tar.addfile(entry, io.BytesIO(data))
        mock = self.root / 'mock.py'
        mock.write_text('#!' + sys.executable + '\n' + MOCK)
        mock.chmod(0o755)
        for name in ['id', 'omarchy', 'omarchy-shell', 'hyprctl', 'pacman', 'pkg-config', 'curl', 'python']:
            (self.bin / name).symlink_to(mock)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        TMPDIR=str(self.scratch), HOME=str(self.root / 'home'), TEST_LOG=str(self.log),
                        TEST_MISSING=str(self.missing), TEST_HEADERS=str(headers), TEST_ARCHIVE=str(archive),
                        TEST_REAL_PYTHON=sys.executable, TEST_BIN=str(self.bin))

    def run_installer(self, *args):
        return subprocess.run(['sh', '-s', '--', *args], input=SCRIPT.read_text(), text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=self.env, timeout=30)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_pipe_install_builds_native_and_cleans_bootstrap(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stdout)
        install = next(call for call in self.calls() if call[:3] == ['python', '-m', 'e16toomarchy.picker_install'])
        self.assertIn('--build-native', install)
        self.assertNotIn('--no-enable', install)
        curl = next(call for call in self.calls() if call[0] == 'curl')
        self.assertIn('https://codeload.github.com/schneipp/e16toomarchy/tar.gz/refs/heads/main', curl)
        self.assertIn('=https', curl)
        self.assertEqual(list(self.scratch.iterdir()), [])
        self.assertIn('No theme was applied', result.stdout)

    def test_only_missing_packages_are_installed(self):
        self.missing.write_text('["gcc"]')
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn(['omarchy', 'pkg', 'add', 'gcc'], self.calls())
        self.assertFalse(any(call[0] == 'sudo' for call in self.calls()))

    def test_check_is_read_only(self):
        result = self.run_installer('--check')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(any(call[0] == 'curl' or call[:3] == ['omarchy', 'pkg', 'add'] for call in self.calls()))
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_no_packages_reports_missing_tools_without_installing(self):
        self.missing.write_text('["make"]')
        result = self.run_installer('--no-packages')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing packages: make', result.stdout)
        self.assertFalse(any(call[:3] == ['omarchy', 'pkg', 'add'] for call in self.calls()))
        self.assertFalse(any(call[0] == 'curl' for call in self.calls()))

    def test_no_enable_is_forwarded(self):
        result = self.run_installer('--no-enable')
        self.assertEqual(result.returncode, 0, result.stdout)
        install = next(call for call in self.calls() if call[:3] == ['python', '-m', 'e16toomarchy.picker_install'])
        self.assertIn('--no-enable', install)

    def test_wrong_version_and_wrong_headers_fail_before_download(self):
        self.env['TEST_VERSION'] = '0.57.0'
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('supports Hyprland 0.56.2', result.stdout)
        self.assertFalse(any(call[0] == 'curl' for call in self.calls()))
        self.env['TEST_VERSION'] = '0.56.2'
        self.header.write_text('#define GIT_COMMIT_HASH "different"\n')
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('headers differ', result.stdout)
        self.assertFalse(any(call[0] == 'curl' for call in self.calls()))

    def test_download_and_install_failure_clean_temporary_files(self):
        for flag in ('TEST_DOWNLOAD_FAIL', 'TEST_INSTALL_FAIL'):
            with self.subTest(flag=flag):
                self.env[flag] = '1'
                result = self.run_installer()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(list(self.scratch.iterdir()), [])
                self.env.pop(flag)

    def test_root_and_unknown_options_are_rejected(self):
        self.env['TEST_UID'] = '0'
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('without sudo', result.stdout)
        result = self.run_installer('--run-arbitrary-code')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unknown option', result.stdout)
        self.assertFalse(any(call[0] == 'curl' for call in self.calls()))
