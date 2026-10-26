"""Synthetic artwork keeps tests independent of third-party theme licenses/network."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from PIL import Image
from e16toomarchy.decorations import export_decorations

RENDERER = Path(__file__).resolve().parents[1] / "build/e16-frame-preview"
CORE_TEST = RENDERER.with_name("e16-frame-test")


def theme(root):
    (root / "borders").mkdir(parents=True)
    (root / "borders.cfg").write_text('#include <borders/default.cfg>\n')
    (root / "borders/default.cfg").write_text('''__E_CFG_VERSION 1
__BORDER __BGN
__NAME DEFAULT
__BORDER_SIZE_LEFT 2
__BORDER_SIZE_RIGHT 2
__BORDER_SIZE_TOP 12
__BORDER_SIZE_BOTTOM 2
__BORDER_PART __BGN
__ICLASS FRAME
__ACLASS ACTION_MOVE
__MIN_WIDTH 0
__MAX_WIDTH 99999
__MIN_HEIGHT 12
__MAX_HEIGHT 12
__TOPLEFT_ORIGIN -1
__BOTTOMRIGHT_ORIGIN -1
__BOTTOMRIGHT_X_PERCENTAGE 1024
__BOTTOMRIGHT_X_ABSOLUTE -1
__BOTTOMRIGHT_Y_ABSOLUTE 11
__END
__END
''')
    configs = ['__ICLASS __BGN', '__NAME FRAME']
    for state, color in [('NORMAL', (180, 40, 40)), ('NORMAL_ACTIVE', (40, 180, 40)),
                         ('HILITED_ACTIVE', (180, 180, 40)), ('CLICKED_ACTIVE', (40, 40, 180))]:
        name = state.lower() + '.png'
        image = Image.new('RGB', (5, 5), color)
        image.putpixel((0, 0), (255, 0, 255))
        image.putpixel((4, 0), (255, 255, 255))
        image.save(root / name)
        configs += [f'__{state} "{name}"', '__EDGE_SCALING 1 1 1 1']
    (root / 'imageclasses.cfg').write_text('\n'.join(configs + ['__END']))


class DecorationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'source'
        self.output = Path(self.tmp.name) / 'out'
        theme(self.root)

    def export(self):
        report = export_decorations(self.root, self.output)
        manifest = self.output / 'e16/decoration.json'
        return report, manifest

    def test_exports_states_geometry_and_transparency(self):
        report, path = self.export()
        self.assertEqual(report['border'], 'DEFAULT')
        self.assertEqual(report['parts'], 1)
        self.assertEqual(report['assets'], 4)
        data = json.loads(path.read_text())
        self.assertEqual(data['parts'][0]['geometry']['bottomright']['xp'], 1024)
        state = data['images']['FRAME']['states']['normal_active']
        self.assertEqual(state['edges'], [1, 1, 1, 1])
        with Image.open(path.parent / state['file']) as image:
            self.assertEqual(image.getpixel((0, 0))[3], 0)
        self.assertEqual(data['parts'][0]['action'], 'move')

    def test_unknown_border_is_error(self):
        with self.assertRaisesRegex(ValueError, 'available: DEFAULT'):
            export_decorations(self.root, self.output, 'missing')

    def test_custom_commands_are_never_exported_as_executable_actions(self):
        cfg = self.root / 'borders/default.cfg'
        cfg.write_text(cfg.read_text().replace('ACTION_MOVE', 'EXEC_MY_SCRIPT'))
        report, path = self.export()
        self.assertEqual(json.loads(path.read_text())['parts'][0]['action'], '')
        self.assertTrue(any('no Hyprland equivalent' in warning for warning in report['warnings']))

    @unittest.skipUnless(RENDERER.is_file(), 'Run make preview for C++ renderer checks')
    def test_shared_renderer_pixels_for_all_states(self):
        _, path = self.export()
        for state, color in [('active', (40, 180, 40)), ('inactive', (180, 40, 40)),
                             ('hover:0', (180, 180, 40)), ('pressed:0', (40, 40, 180))]:
            with self.subTest(state=state):
                png = self.output / (state.replace(':', '-') + '.png')
                subprocess.run([str(RENDERER), str(path), str(png), '100', '60', state], check=True, capture_output=True)
                with Image.open(png) as image:
                    self.assertEqual(image.size, (104, 74))
                    self.assertEqual(image.convert('RGB').getpixel((52, 6)), color)
                    self.assertEqual(image.convert('RGBA').getpixel((0, 0))[3], 0)
                    self.assertEqual(image.convert('RGB').getpixel((103, 0)), (255, 255, 255))

    @unittest.skipUnless(RENDERER.is_file(), 'Run make preview for C++ renderer checks')
    def test_renderer_rejects_cycles_and_asset_traversal(self):
        _, path = self.export()
        data = json.loads(path.read_text())
        cyclic = json.loads(path.read_text())
        cyclic['parts'][0]['geometry']['topleft']['origin'] = 0
        path.write_text(json.dumps(cyclic))
        run = subprocess.run([str(RENDERER), str(path), str(self.output / 'bad.png')], capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('Cyclic', run.stderr)
        Image.new('RGB', (2, 2)).save(self.output / 'external.png')
        data['images']['FRAME']['states']['normal']['file'] = '../external.png'
        path.write_text(json.dumps(data))
        run = subprocess.run([str(RENDERER), str(path), str(self.output / 'bad.png')], capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('outside theme', run.stderr)

    @unittest.skipUnless(CORE_TEST.is_file(), 'Run make test for C++ hit-testing checks')
    def test_core_geometry_and_shape_hit_testing(self):
        _, path = self.export()
        subprocess.run([str(CORE_TEST), str(path)], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
