import io
import json
from pathlib import Path
import tarfile
import tempfile
import tomllib
import unittest

from PIL import Image
from e16toomarchy.cli import contrast, convert, make_palette, unpack


class ConverterTests(unittest.TestCase):
    def test_realistic_theme(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            (root / "artwork").mkdir(parents=True)
            Image.new("RGB", (20, 20), (45, 55, 65)).save(root / "artwork/ui.png")
            Image.new("RGB", (20, 20), (20, 180, 160)).save(root / "artwork/button.png")
            Image.new("RGB", (80, 50), (80, 90, 100)).save(root / "wall.png")
            (root / "imageclasses.cfg").write_text('''__E_CFG_VERSION 1
__ICLASS __BGN
__NAME DIALOG
__NORMAL "artwork/ui.png"
__END
__ICLASS __BGN
__NAME BUTTON
__NORMAL "artwork/button.png"
__END
''')
            (root / "desktops.cfg").write_text('BEGIN_BACKGROUND("test")\nADD_BACKGROUND_SCALED("wall.png")')
            (root / "textclasses.cfg").write_text('__FORGROUND_COLOR 230 230 230')
            output = Path(tmp) / "out"
            report = convert(root, output, "Test", "fixture")
            palette = tomllib.loads((output / "colors.toml").read_text())
            self.assertEqual(palette["background"], "#2d3741")
            self.assertEqual(report["wallpapers"], 1)
            self.assertTrue((output / "preview.png").is_file())
            self.assertEqual(json.loads((output / "conversion.json").read_text())["name"], "Test")
            for mode in ("light", "dark"):
                result = make_palette({(20, 180, 160): 10}, (45, 55, 65), (80, 80, 80), mode)
                rgb = lambda v: tuple(bytes.fromhex(v[1:]))
                self.assertGreaterEqual(contrast(rgb(result["foreground"]), rgb(result["background"])), 4.5)
                self.assertEqual(result["mode"], mode)

    def test_archive_rejects_traversal_links_and_devices(self):
        for name, kind in (("../escape", tarfile.REGTYPE), ("/absolute", tarfile.REGTYPE),
                           ("link", tarfile.SYMTYPE), ("hard", tarfile.LNKTYPE), ("device", tarfile.CHRTYPE)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                archive = Path(tmp) / "theme.tar"
                with tarfile.open(archive, "w") as tar:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    member.linkname = "/tmp"
                    tar.addfile(member, io.BytesIO())
                with self.assertRaises(ValueError):
                    unpack(archive, Path(tmp) / "out")

    def test_regular_archive_and_no_config_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "theme.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                payload = b'__E_CFG_VERSION 1\n#include "/etc/passwd"\n'
                member = tarfile.TarInfo("nested/init.cfg")
                member.size = len(payload)
                tar.addfile(member, io.BytesIO(payload))
            root = Path(tmp) / "input"
            unpack(archive, root)
            report = convert(root, Path(tmp) / "output", "Empty", "fixture")
            self.assertTrue(report["warnings"])
            self.assertTrue((Path(tmp) / "output/backgrounds/01-solid.png").is_file())

    def test_refuses_symlink_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            root.mkdir()
            (root / "external.cfg").symlink_to("/etc/passwd")
            with self.assertRaises(ValueError):
                convert(root, Path(tmp) / "output", "Bad", "fixture")


if __name__ == "__main__":
    unittest.main()
