from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from PIL import Image
from e16toomarchy import gallery, picker


class GalleryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / 'cache'
        self.mock_paths = patch.object(gallery, 'paths', return_value={'cache': self.cache})
        self.mock_paths.start()
        self.addCleanup(self.mock_paths.stop)

    def fake_image(self, url, target, limit, timeout):
        self.assertEqual(url, 'https://themes.effx.us/preview/e16/BrushedMetal.png')
        self.assertEqual(limit, gallery.MAX_IMAGE_BYTES)
        self.assertEqual(timeout, 8)
        Image.new('RGB', (1000, 700), (40, 60, 80)).save(target, 'PNG')

    def test_downloads_only_gallery_image_and_reuses_local_cache(self):
        with patch.object(gallery, 'fetch', side_effect=self.fake_image) as fetch:
            result = gallery.thumbnail('BrushedMetal')
            again = gallery.thumbnail('BrushedMetal')
        fetch.assert_called_once()
        self.assertEqual(result, again)
        self.assertTrue(result['available'])
        with Image.open(result['thumbnail']) as image:
            self.assertLessEqual(image.width, 144)
            self.assertLessEqual(image.height, 100)
            self.assertEqual(image.format, 'JPEG')
        with Image.open(result['preview']) as image:
            self.assertLessEqual(image.width, 800)
            self.assertLessEqual(image.height, 600)
        self.assertFalse(any(self.cache.rglob('*.etheme')))

    def test_missing_screenshot_is_cached_without_blocking_conversion(self):
        error = HTTPError(gallery.gallery_url('Aqua'), 404, 'Not found', {}, None)
        with patch.object(gallery, 'fetch', side_effect=error) as fetch:
            first = gallery.thumbnail('Aqua')
            second = gallery.thumbnail('Aqua')
        fetch.assert_called_once()
        self.assertFalse(first['available'])
        self.assertEqual(first, second)
        self.assertEqual(first['preview'], '')

    def test_corrupt_and_oversized_images_are_not_exposed_to_qml(self):
        def invalid(url, target, limit, timeout):
            target.write_text('<img src="https://evil.example/tracker">')
        with patch.object(gallery, 'fetch', side_effect=invalid):
            self.assertFalse(gallery.thumbnail('Corrupt')['available'])
        def too_large(url, target, limit, timeout):
            Image.new('RGB', (20, 20)).save(target, 'PNG')
        with patch.object(gallery, 'MAX_IMAGE_PIXELS', 100), patch.object(gallery, 'fetch', side_effect=too_large):
            self.assertFalse(gallery.thumbnail('Oversized')['available'])
        self.assertFalse(any(self.cache.rglob('*.jpg')))

    def test_remote_paths_and_redirects_remain_restricted(self):
        self.assertTrue(picker.allowed_source(gallery.gallery_url('Aqua-Graphite')))
        self.assertTrue(picker.allowed_source(picker.theme_url('Aqua')))
        for url in ('http://themes.effx.us/preview/e16/Aqua.png',
                    'https://evil.example/preview/e16/Aqua.png',
                    'https://themes.effx.us/preview/e16/../../secret',
                    'https://themes.effx.us/preview/e16/%2e%2e/secret',
                    'https://themes.effx.us/preview/e16/Aqua.png?redirect=https://evil.example'):
            with self.subTest(url=url):
                self.assertFalse(picker.allowed_source(url))
        for name in ('../a', '<img src=x>', 'Aqua/other'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                gallery.gallery_url(name)

    def test_batch_is_bounded(self):
        with self.assertRaises(ValueError):
            gallery.thumbnails(['Aqua'] * 13)

    def test_fetch_enforces_byte_limit_before_image_decoding(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return b'x' * 32
        class Opener:
            def open(self, *args, **kwargs): return Response()
        with patch.object(picker, 'build_opener', return_value=Opener()):
            with self.assertRaisesRegex(ValueError, 'size limit'):
                picker.fetch(gallery.gallery_url('Aqua'), Path(self.tmp.name) / 'oversized.bin', limit=16)
