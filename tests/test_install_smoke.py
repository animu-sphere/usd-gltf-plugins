# SPDX-License-Identifier: Apache-2.0
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


OPEN_INSTALLED_PLUGIN = """
import pathlib
import sys
from pxr import Plug, Usd

plugin = Plug.Registry().GetPluginWithName("UsdGltfFileFormat")
assert plugin is not None
assert pathlib.Path(sys.argv[1]).resolve() in pathlib.Path(plugin.path).resolve().parents, plugin.path
stage = Usd.Stage.Open(sys.argv[2])
assert stage.GetDefaultPrim().GetPath() == "/Root"
assert stage.GetPrimAtPath("/Root/Scenes/Scene_0")
assert any(prim.GetTypeName() == "Mesh" for prim in stage.Traverse())
"""


class InstallSmokeTest(unittest.TestCase):
    def test_installed_plugin_registers_and_opens_a_stage(self):
        fixture = pathlib.Path(__file__).parent / "fixtures" / "reflections.gltf"
        with tempfile.TemporaryDirectory() as directory:
            prefix = pathlib.Path(directory) / "installed"
            command = [os.environ["USD_GLTF_TEST_CMAKE"], "--install", os.environ["USD_GLTF_TEST_BUILD_DIR"],
                       "--prefix", str(prefix)]
            if os.environ.get("USD_GLTF_TEST_CONFIG"):
                command += ["--config", os.environ["USD_GLTF_TEST_CONFIG"]]
            subprocess.run(command, check=True, capture_output=True, text=True)
            registration = prefix / "plugin" / "resources" / "usdGltfFileFormat"
            licenses = prefix / "share" / "licenses" / "usd-gltf-plugins"
            for path in (registration / "plugInfo.json", registration / "NOTICES.txt", registration / "LICENSE",
                         licenses / "LICENSE", licenses / "NOTICES.txt"):
                self.assertTrue(path.is_file(), path)

            environment = dict(os.environ, PXR_PLUGINPATH_NAME=str(registration))
            result = subprocess.run(
                [sys.executable, "-c", OPEN_INSTALLED_PLUGIN, str(prefix), str(fixture)],
                cwd=directory, env=environment, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
