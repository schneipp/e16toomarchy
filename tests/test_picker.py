import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from e16toomarchy import picker


class PickerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.locations = {key: root / key for key in ('cache', 'state', 'runtime', 'config')}
        for path in self.locations.values():
            path.mkdir()
        self.paths_patch = patch.object(picker, 'paths', return_value=self.locations)
        self.paths_patch.start()
        self.addCleanup(self.paths_patch.stop)

    def test_catalog_restricts_network_sources_and_markup(self):
        html = '''<a href="Aqua.etheme">Aqua</a>
<a href="https://evil.example/Evil.etheme">bad</a>
<a href="../Other.etheme">bad</a>
<a href="%3Cimg%20src=x%3E.etheme">bad</a>
<a href="..%2Fescape.etheme">bad</a>
<a href="BrushedMetal.etheme">good</a>'''
        self.assertEqual(picker.parse_catalog(html), ['Aqua', 'BrushedMetal'])
        for name in ('../bad', '<img src=x>', '$(touch /tmp/no)', 'a/b', 'a\\b', 'a\x00b'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                picker.theme_url(name)

    def test_redirects_reject_other_hosts_and_http(self):
        handler = picker.CatalogRedirects()
        for url in ('https://evil.example/a', 'http://themes.effx.us/packages/e16/A.etheme', 'https://themes.effx.us/outside/a'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                handler.redirect_request(None, None, 302, '', {}, url)

    def test_cache_keys_distinguish_slug_collisions_and_modes(self):
        self.assertNotEqual(picker.key_for('Some_Theme', 'auto'), picker.key_for('Some-Theme', 'auto'))
        self.assertNotEqual(picker.key_for('Aqua', 'auto'), picker.key_for('Aqua', 'dark'))
        self.assertNotIn('/', picker.key_for('Aqua', 'auto'))

    def test_lua_paths_are_escaped_and_integration_is_conditional(self):
        value = '/tmp/a"; os.execute("bad") --\n'
        literal = picker.lua_string(value)
        self.assertIn('\\"', literal)
        self.assertNotIn('\n', literal)
        self.assertIn('\\010', literal)
        full = picker.integration_text('/tmp/frame.json', '/tmp/plugin.so', 'full')
        frames = picker.integration_text('/tmp/frame.json', '/tmp/plugin.so', 'frames')
        self.assertIn('if check then', full)
        self.assertIn('omarchy/current/theme/e16/decoration.json', full)
        self.assertIn('local manifest = "/tmp/frame.json"', frames)
        self.assertIn('hyprbars = { enabled = false }', frames)
        self.assertNotIn('#include', frames)

    def test_lock_prevents_concurrent_jobs(self):
        fd = picker.lock_fd()
        try:
            with self.assertRaises(picker.BusyError):
                picker.lock_fd()
        finally:
            os.close(fd)

    def test_status_recovers_interrupted_worker(self):
        path = self.locations['state'] / 'status.json'
        picker.atomic_json(path, {'busy': True, 'stage': 'converting'})
        result = picker.status()
        self.assertFalse(result['busy'])
        self.assertEqual(result['stage'], 'failed')
        self.assertIn('interrupted', result['error'])

    def test_config_loader_preserves_existing_content_and_is_idempotent(self):
        main = self.locations['config'] / 'hypr/hyprland.lua'
        main.parent.mkdir()
        original = '-- user config\nhl.config({general={gaps_in=7}})\n'
        main.write_text(original)
        picker.ensure_config_loader(self.locations['config'], self.locations['state'])
        first = main.read_text()
        picker.ensure_config_loader(self.locations['config'], self.locations['state'])
        self.assertEqual(main.read_text(), first)
        self.assertTrue(first.startswith(original))
        self.assertEqual(len(list(main.parent.glob('*.e16-backup-*'))), 1)

    def test_failed_activation_restores_integration_and_previous_theme(self):
        state = self.locations['state']
        integration = state / 'integration.lua'
        integration.write_text('-- previous working configuration\n')
        with patch.object(picker, 'prepare', return_value=Path('/cache/theme')), \
             patch.object(picker, 'ensure_native', return_value=Path('/runtime/plugin.so')), \
             patch.object(picker, 'install_theme', return_value=Path('/themes/e16-test')), \
             patch.object(picker, 'current_theme', return_value='nord'), \
             patch.object(picker, 'ensure_config_loader'), \
             patch.object(picker, 'reload_hyprland'), \
             patch.object(picker, 'hypr_eval', side_effect=RuntimeError('ABI mismatch')), \
             patch.object(picker, 'run_command') as command:
            with self.assertRaisesRegex(RuntimeError, 'ABI mismatch'):
                picker.apply_theme('Aqua', 'auto', 'full', lambda _: None)
            calls = [args.args[0] for args in command.call_args_list]
            self.assertIn(['omarchy', 'theme', 'set', 'nord'], calls)
        self.assertEqual(integration.read_text(), '-- previous working configuration\n')
        self.assertFalse((state / 'active.json').exists())

    def test_frames_only_does_not_change_colors_or_wallpaper(self):
        with patch.object(picker, 'prepare', return_value=Path('/cache/theme')), \
             patch.object(picker, 'ensure_native', return_value=Path('/runtime/plugin.so')), \
             patch.object(picker, 'install_theme', return_value=Path('/themes/e16-test')), \
             patch.object(picker, 'current_theme', return_value='nord'), \
             patch.object(picker, 'ensure_config_loader'), \
             patch.object(picker, 'reload_hyprland'), \
             patch.object(picker, 'hypr_eval'), \
             patch.object(picker, 'run_command') as command:
            picker.apply_theme('Aqua', 'auto', 'frames', lambda _: None)
            command.assert_not_called()
        active = picker.read_json(self.locations['state'] / 'active.json')
        self.assertEqual(active['original_theme'], 'nord')
        self.assertEqual(active['kind'], 'frames')

    def test_restore_preserves_theme_selected_elsewhere(self):
        state = self.locations['state']
        picker.atomic_json(state / 'active.json', {'enabled': True, 'kind': 'full', 'theme': 'e16-test', 'original_theme': 'nord'})
        with patch.object(picker, 'current_theme', return_value='gruvbox'), \
             patch.object(picker, 'reload_hyprland'), patch.object(picker, 'run_command') as command:
            picker.restore(lambda _: None)
            command.assert_not_called()
        self.assertFalse(picker.read_json(state / 'active.json')['enabled'])

    def test_installed_ui_uses_new_urls_when_qml_or_model_changes(self):
        from e16toomarchy.picker_install import version_ui
        entries = []
        for index, code in enumerate(('Item {}', 'Item { property bool fresh: true }')):
            stage = Path(self.tmp.name) / str(index)
            stage.mkdir()
            (stage / 'manifest.json').write_text(json.dumps({'entryPoints': {'barWidget': 'Picker.qml'}}))
            (stage / 'Picker.qml').write_text(code)
            (stage / 'Model.js').write_text('function safe() { return true }')
            version_ui(stage)
            entry = json.loads((stage / 'manifest.json').read_text())['entryPoints']['barWidget']
            self.assertEqual((stage / entry).read_text(), code)
            self.assertTrue((stage / entry).with_name('Model.js').is_file())
            entries.append(entry)
        self.assertNotEqual(entries[0], entries[1])

    def test_current_theme_normalizes_cli_display_names(self):
        with patch.object(picker, 'run_command', return_value='Tokyo Night'):
            self.assertEqual(picker.current_theme(), 'tokyo-night')

    def test_restore_after_full_then_frames_also_restores_the_palette(self):
        state = self.locations['state']
        picker.atomic_json(state / 'active.json', {'enabled': True, 'kind': 'frames', 'theme': 'e16-frames',
                                                 'palette_theme': 'e16-colors', 'original_theme': 'nord'})
        with patch.object(picker, 'current_theme', return_value='e16-colors'), \
             patch.object(picker, 'reload_hyprland'), patch.object(picker, 'run_command') as command:
            picker.restore(lambda _: None)
            command.assert_called_once_with(['omarchy', 'theme', 'set', 'nord'], timeout=180)

    def test_qml_uses_plain_text_and_static_shared_tooltips(self):
        source = Path(__file__).resolve().parents[1] / 'omarchy-plugin/Picker.qml'
        qml = source.read_text()
        self.assertEqual(qml.count('Text {'), qml.count('textFormat: Text.PlainText'))
        tooltips = [line.strip() for line in qml.splitlines() if 'tooltipText:' in line]
        self.assertTrue(tooltips)
        self.assertTrue(all('strings.t(' in line for line in tooltips))
        self.assertNotIn('bash', qml)


if __name__ == '__main__':
    unittest.main()
