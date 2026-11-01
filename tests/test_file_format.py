import pathlib
import tempfile
import unittest

from pxr import Sdf, Tf, Usd


class FileFormatTest(unittest.TestCase):
    def test_registration_and_capabilities(self):
        for extension in ("gltf", "glb"):
            with self.subTest(extension=extension):
                fmt = Sdf.FileFormat.FindByExtension(extension)
                self.assertIsNotNone(fmt)
                self.assertEqual(fmt.formatId, "gltf")
                self.assertTrue(Sdf.FileFormat.FormatSupportsReading(extension))
                self.assertFalse(Sdf.FileFormat.FormatSupportsWriting(extension))
                self.assertFalse(Sdf.FileFormat.FormatSupportsEditing(extension))

    def test_unimplemented_import_fails_with_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = pathlib.Path(directory) / ("asset." + extension)
                    path.write_bytes(b'{"asset":{"version":"2.0"}}')
                    self.assertFalse(Sdf.FileFormat.FindByExtension(extension).CanRead(str(path)))
                    with self.assertRaisesRegex(Tf.ErrorException, "not implemented"):
                        Usd.Stage.Open(str(path))


if __name__ == "__main__":
    unittest.main()
