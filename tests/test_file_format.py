import base64
import json
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest
import urllib.parse

from pxr import Gf, Sdf, Tf, Usd, UsdGeom


def write_asset(path, document, binary=None):
    data = json.dumps(document, separators=(",", ":")).encode()
    if path.suffix == ".glb":
        data += b" " * (-len(data) % 4)
        bin_chunk = b""
        if binary is not None:
            binary += b"\0" * (-len(binary) % 4)
            bin_chunk = struct.pack("<II", len(binary), 0x004E4942) + binary
        data = (struct.pack("<4sIIII", b"glTF", 2, 20 + len(data) + len(bin_chunk),
                            len(data), 0x4E4F534A) + data + bin_chunk)
    path.write_bytes(data)


def scene_document():
    return {
        "asset": {"version": "2.0"},
        "scene": 1,
        "scenes": [{"nodes": [2]}, {"nodes": [0]}],
        "nodes": [
            {
                "name": "Root / invalid name",
                "translation": [1, 2, 3],
                "rotation": [0, 0, 0.7071067811865476, 0.7071067811865476],
                "scale": [-2, 3, 1],
                "children": [1],
            },
            {"name": "Child", "matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 1, 0, 0, 1]},
            {"name": "Other scene"},
        ],
    }


def mesh_document(extension, index_type=5123):
    vertex_data = b"".join(struct.pack("<6f", *vertex) for vertex in (
        (0, 0, 0, 0, 0, 1),
        (1, 0, 0, 0, 0, 1),
        (0, 1, 0, 0, 0, 1),
    ))
    index_data = struct.pack({5121: "<3B", 5123: "<3H", 5125: "<3I"}[index_type], 2, 0, 1)
    binary = vertex_data + index_data
    buffer = {"byteLength": len(binary)}
    if extension == "gltf":
        buffer["uri"] = "data:application/octet-stream;base64," + base64.b64encode(binary).decode()
    document = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0, 1]}],
        "nodes": [{"mesh": 0, "translation": [2, 0, 0]}, {"mesh": 0}],
        "meshes": [{"name": "Example mesh", "primitives": [
            {"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2},
            {"attributes": {"POSITION": 0}},
        ]}],
        "buffers": [buffer],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(vertex_data), "byteStride": 24},
            {"buffer": 0, "byteOffset": len(vertex_data), "byteLength": len(index_data)},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
             "min": [0, 0, 0], "max": [1, 1, 0]},
            {"bufferView": 0, "byteOffset": 12, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 1, "componentType": index_type, "count": 3, "type": "SCALAR"},
        ],
    }
    return document, binary if extension == "glb" else None


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

    def test_scene_hierarchy_and_transforms(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = pathlib.Path(directory) / ("asset." + extension)
                    write_asset(path, scene_document())
                    self.assertTrue(Sdf.FileFormat.FindByExtension(extension).CanRead(str(path)))
                    stage = Usd.Stage.Open(str(path))
                    self.assertEqual(stage.GetDefaultPrim().GetPath(), Sdf.Path("/Asset"))
                    self.assertEqual(UsdGeom.GetStageUpAxis(stage), UsdGeom.Tokens.y)
                    self.assertEqual(UsdGeom.GetStageMetersPerUnit(stage), 1.0)
                    self.assertTrue(stage.GetPrimAtPath("/Asset/geo").IsA(UsdGeom.Scope))
                    self.assertTrue(stage.GetPrimAtPath("/Asset/mtl").IsA(UsdGeom.Scope))
                    root = stage.GetPrimAtPath("/Asset/geo/Node_0")
                    child = stage.GetPrimAtPath("/Asset/geo/Node_0/Node_1")
                    self.assertTrue(root.IsA(UsdGeom.Xform))
                    self.assertTrue(child.IsA(UsdGeom.Xform))
                    self.assertEqual(root.GetDisplayName(), "Root / invalid name")
                    self.assertEqual(child.GetDisplayName(), "Child")
                    self.assertFalse(stage.GetPrimAtPath("/Asset/geo/Node_2"))
                    world = UsdGeom.XformCache().GetLocalToWorldTransform(child)
                    point = world.Transform(Gf.Vec3d(0, 0, 0))
                    self.assertTrue(Gf.IsClose(point, Gf.Vec3d(1, 0, 3), 1e-5), str(point))

    def test_triangle_meshes(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                for index_type in (5121, 5123, 5125):
                    with self.subTest(extension=extension, index_type=index_type):
                        path = pathlib.Path(directory) / f"mesh_{index_type}.{extension}"
                        document, binary = mesh_document(extension, index_type)
                        write_asset(path, document, binary)
                        stage = Usd.Stage.Open(str(path))
                        for node in (0, 1):
                            prefix = f"/Asset/geo/Node_{node}/Mesh_0"
                            self.assertEqual(stage.GetPrimAtPath(prefix).GetDisplayName(), "Example mesh")
                            mesh = UsdGeom.Mesh.Get(stage, prefix + "/Primitive_0")
                            self.assertTrue(mesh)
                            self.assertEqual(list(mesh.GetPointsAttr().Get()),
                                             [Gf.Vec3f(0, 0, 0), Gf.Vec3f(1, 0, 0), Gf.Vec3f(0, 1, 0)])
                            self.assertEqual(list(mesh.GetFaceVertexCountsAttr().Get()), [3])
                            self.assertEqual(list(mesh.GetFaceVertexIndicesAttr().Get()), [2, 0, 1])
                            self.assertEqual(list(mesh.GetNormalsAttr().Get()), [Gf.Vec3f(0, 0, 1)] * 3)
                            self.assertEqual(mesh.GetNormalsInterpolation(), UsdGeom.Tokens.vertex)
                            self.assertEqual(mesh.GetSubdivisionSchemeAttr().Get(), UsdGeom.Tokens.none)
                            self.assertEqual(mesh.GetOrientationAttr().Get(), UsdGeom.Tokens.rightHanded)
                            other = UsdGeom.Mesh.Get(stage, prefix + "/Primitive_1")
                            self.assertEqual(list(other.GetFaceVertexIndicesAttr().Get()), [0, 1, 2])
                            self.assertEqual(list(other.GetNormalsAttr().Get()), [Gf.Vec3f(0, 0, 1)])
                            self.assertEqual(other.GetNormalsInterpolation(), UsdGeom.Tokens.uniform)
                        point = UsdGeom.XformCache().GetLocalToWorldTransform(mesh.GetPrim()).Transform(
                            Gf.Vec3d(0, 0, 0))
                        self.assertEqual(point, Gf.Vec3d(0, 0, 0))
                        first = stage.GetPrimAtPath("/Asset/geo/Node_0/Mesh_0/Primitive_0")
                        self.assertEqual(UsdGeom.XformCache().GetLocalToWorldTransform(first).Transform(
                            Gf.Vec3d(0, 0, 0)), Gf.Vec3d(2, 0, 0))

    def test_mesh_failures_and_reload_preservation(self):
        def mutate(document, name):
            primitive = document["meshes"][0]["primitives"][0]
            if name == "accessor":
                primitive["attributes"]["POSITION"] = 99
            elif name == "view bounds":
                document["bufferViews"][0]["byteLength"] = 20
            elif name == "buffer bounds":
                document["bufferViews"][0]["byteLength"] = 999
            elif name == "buffer index":
                document["bufferViews"][0]["buffer"] = 99
            elif name == "bufferView index":
                document["accessors"][0]["bufferView"] = 99
            elif name == "component type":
                document["accessors"][0]["componentType"] = 5123
            elif name == "alignment":
                document["accessors"][0]["byteOffset"] = 1
            elif name == "normalized":
                document["accessors"][0]["normalized"] = True
            elif name == "sparse":
                document["accessors"][0]["sparse"] = {
                    "count": 1,
                    "indices": {"bufferView": 1, "componentType": 5121},
                    "values": {"bufferView": 0},
                }
            elif name == "index count":
                document["accessors"][2]["count"] = 2
            elif name == "normal count":
                document["accessors"][1]["count"] = 2
            elif name == "stride":
                document["bufferViews"][0]["byteStride"] = 8
            elif name == "material":
                primitive["material"] = 0
                document["materials"] = [{}]
            elif name == "attribute":
                primitive["attributes"]["TEXCOORD_0"] = 0
            elif name == "mode":
                primitive["mode"] = 1

        cases = {
            "accessor": "accessor index",
            "view bounds": "bufferView",
            "buffer bounds": "bufferView",
            "buffer index": "buffer index",
            "bufferView index": "bufferView index",
            "component type": "invalid type",
            "alignment": "alignment",
            "normalized": "cannot parse",
            "sparse": "sparse",
            "index count": "triangle index count",
            "normal count": "normal and position counts",
            "stride": "bufferView",
            "material": "not supported",
            "attribute": "unsupported or repeated vertex attribute",
            "mode": "not supported",
        }
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                path = pathlib.Path(directory) / ("mesh." + extension)
                document, binary = mesh_document(extension)
                write_asset(path, document, binary)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer.ExportToString()
                for name, message in cases.items():
                    with self.subTest(extension=extension, name=name):
                        invalid, invalid_binary = mesh_document(extension)
                        mutate(invalid, name)
                        write_asset(path, invalid, invalid_binary)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer.ExportToString(), before)
                        self.assertTrue(stage.GetPrimAtPath("/Asset/geo/Node_0/Mesh_0/Primitive_0"))
                invalid, invalid_binary = mesh_document(extension)
                if extension == "glb":
                    invalid_binary = invalid_binary[:72] + struct.pack("<3H", 3, 0, 1)
                else:
                    data = base64.b64decode(invalid["buffers"][0]["uri"].split(",", 1)[1])
                    invalid["buffers"][0]["uri"] = "data:application/octet-stream;base64," + base64.b64encode(
                        data[:72] + struct.pack("<3H", 3, 0, 1)).decode()
                write_asset(path, invalid, invalid_binary)
                with self.assertRaisesRegex(Tf.ErrorException, "index is out of range"):
                    layer.Reload(True)
                self.assertEqual(layer.ExportToString(), before)
                write_asset(path, document, binary)
                self.assertTrue(layer.Reload(True))

    def test_external_buffers_and_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "nested").mkdir()
            buffer_path = root / "nested" / "mesh data.bin"
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = root / ("external." + extension)
                    document, binary = mesh_document("glb")
                    document["buffers"][0]["uri"] = "nested/mesh%20data.bin"
                    buffer_path.write_bytes(binary)
                    write_asset(path, document)
                    stage = Usd.Stage.Open(str(path))
                    layer = stage.GetRootLayer()
                    mesh = UsdGeom.Mesh.Get(stage, "/Asset/geo/Node_0/Mesh_0/Primitive_0")
                    self.assertEqual(list(mesh.GetPointsAttr().Get()),
                                     [Gf.Vec3f(0, 0, 0), Gf.Vec3f(1, 0, 0), Gf.Vec3f(0, 1, 0)])
                    self.assertEqual(list(mesh.GetFaceVertexIndicesAttr().Get()), [2, 0, 1])
                    self.assertEqual(list(mesh.GetNormalsAttr().Get()), [Gf.Vec3f(0, 0, 1)] * 3)

                    before = layer.ExportToString()
                    buffer_path.write_bytes(binary[:-1])
                    with self.assertRaisesRegex(Tf.ErrorException, "shorter than its declared byteLength"):
                        layer.Reload(True)
                    self.assertEqual(layer.ExportToString(), before)
                    buffer_path.write_bytes(struct.pack("<f", 4) + binary[4:])
                    self.assertTrue(layer.Reload(True))
                    self.assertEqual(mesh.GetPointsAttr().Get()[0], Gf.Vec3f(4, 0, 0))

    def test_external_buffer_uri_rejections(self):
        uris = (
            "../escape.bin", "%2e%2e/escape.bin", "nested/%2e%2e/escape.bin",
            "/absolute.bin", "%2fabsolute.bin", "//host/file.bin", "C:/file.bin",
            "https://example.invalid/file.bin", "file:///file.bin", "x%5cy.bin",
            "x%3ay.bin", "x%00.bin", "x%0a.bin", "x%zz.bin", "%ff.bin",
            "%c0%af.bin", "%ed%a0%80.bin", "%f4%90%80%80.bin", "%c3.bin",
            "mesh.bin?query", "mesh.bin#fragment", "mesh.bin?", "mesh.bin#",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                path = root / ("invalid." + extension)
                document, binary = mesh_document("glb")
                document["buffers"][0]["uri"] = "mesh.bin"
                (root / "mesh.bin").write_bytes(binary)
                write_asset(path, document)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer.ExportToString()
                for uri in uris:
                    with self.subTest(extension=extension, uri=uri):
                        document["buffers"][0]["uri"] = uri
                        write_asset(path, document)
                        with self.assertRaisesRegex(Tf.ErrorException, "unsafe or unsupported|cannot parse"):
                            layer.Reload(True)
                        self.assertEqual(layer.ExportToString(), before)
                document["buffers"][0]["uri"] = "missing.bin"
                write_asset(path, document)
                with self.assertRaisesRegex(Tf.ErrorException, "cannot resolve external buffer"):
                    layer.Reload(True)
                self.assertEqual(layer.ExportToString(), before)

    def test_external_buffers_with_unicode_and_mixed_glb_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory) / "モデル"
            root.mkdir()
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = root / ("mixed." + extension)
                    document, binary = mesh_document("glb")
                    document["buffers"][0]["byteLength"] = 72
                    if extension == "gltf":
                        document["buffers"][0]["uri"] = "data:application/octet-stream;base64," + (
                            base64.b64encode(binary[:72]).decode())
                    uri = urllib.parse.quote("indices_日本.bin")
                    document["buffers"].append({"byteLength": len(binary) - 72, "uri": uri})
                    document["bufferViews"][1]["buffer"] = 1
                    document["bufferViews"][1]["byteOffset"] = 0
                    (root / "indices_日本.bin").write_bytes(binary[72:])
                    write_asset(path, document, binary[:72] if extension == "glb" else None)
                    stage = Usd.Stage.Open(str(path))
                    mesh = UsdGeom.Mesh.Get(stage, "/Asset/geo/Node_0/Mesh_0/Primitive_0")
                    self.assertEqual(list(mesh.GetFaceVertexIndicesAttr().Get()), [2, 0, 1])

    def test_unselected_external_buffer_is_not_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = pathlib.Path(directory) / ("unselected." + extension)
                    document = scene_document()
                    mesh, _ = mesh_document("glb")
                    document["nodes"][2]["mesh"] = 0
                    for key in ("meshes", "buffers", "bufferViews", "accessors"):
                        document[key] = mesh[key]
                    document["buffers"][0]["uri"] = "missing.bin"
                    write_asset(path, document)
                    stage = Usd.Stage.Open(str(path))
                    self.assertTrue(stage.GetPrimAtPath("/Asset/geo/Node_0"))
                    self.assertFalse(stage.GetPrimAtPath("/Asset/geo/Node_2"))

    def test_external_buffer_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            asset_dir = root / "asset"
            asset_dir.mkdir()
            document, binary = mesh_document("glb")
            outside = root / "outside"
            outside.mkdir()
            (outside / "buffer.bin").write_bytes(binary)
            link = asset_dir / "link"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError as error:
                if sys.platform != "win32":
                    self.skipTest(f"directory symlinks unavailable: {error}")
                result = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                    capture_output=True, text=True, check=False,
                )
                if result.returncode:
                    self.skipTest(f"directory links unavailable: {result.stderr}")
            document["buffers"][0]["uri"] = "link/buffer.bin"
            path = asset_dir / "asset.gltf"
            write_asset(path, document)
            with self.assertRaisesRegex(Tf.ErrorException, "escapes the source directory"):
                Usd.Stage.Open(str(path))

    def test_flat_normals_and_negative_scale(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = pathlib.Path(directory) / ("mesh." + extension)
                    document, binary = mesh_document(extension)
                    del document["meshes"][0]["primitives"][0]["attributes"]["NORMAL"]
                    document["nodes"][0]["scale"] = [-1, 1, 1]
                    if extension == "gltf":
                        data = base64.b64decode(document["buffers"][0]["uri"].split(",", 1)[1])
                    else:
                        data = binary
                    data = data[:72] + struct.pack("<3H", 0, 2, 1)
                    if extension == "gltf":
                        document["buffers"][0]["uri"] = "data:application/octet-stream;base64," + (
                            base64.b64encode(data).decode())
                    else:
                        binary = data
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    mesh = UsdGeom.Mesh.Get(stage, "/Asset/geo/Node_0/Mesh_0/Primitive_0")
                    self.assertEqual(list(mesh.GetFaceVertexIndicesAttr().Get()), [0, 2, 1])
                    self.assertEqual(list(mesh.GetNormalsAttr().Get()), [Gf.Vec3f(0, 0, -1)])
                    self.assertEqual(mesh.GetNormalsInterpolation(), UsdGeom.Tokens.uniform)
                    transform = UsdGeom.XformCache().GetLocalToWorldTransform(mesh.GetPrim())
                    self.assertLess(transform.GetDeterminant(), 0)

    def test_nonfinite_position_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = pathlib.Path(directory) / ("mesh." + extension)
                    document, binary = mesh_document(extension)
                    if extension == "gltf":
                        data = base64.b64decode(document["buffers"][0]["uri"].split(",", 1)[1])
                    else:
                        data = binary
                    data = struct.pack("<f", float("nan")) + data[4:]
                    if extension == "gltf":
                        document["buffers"][0]["uri"] = "data:application/octet-stream;base64," + (
                            base64.b64encode(data).decode())
                    else:
                        binary = data
                    write_asset(path, document, binary)
                    with self.assertRaisesRegex(Tf.ErrorException, "non-finite"):
                        Usd.Stage.Open(str(path))

    def test_first_scene_fallback_warns(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = pathlib.Path(directory) / ("asset." + extension)
                    document = scene_document()
                    del document["scene"]
                    write_asset(path, document)
                    result = subprocess.run(
                        [sys.executable, "-c",
                         "from pxr import Usd; import sys; "
                         "s = Usd.Stage.Open(sys.argv[1]); "
                         "assert s.GetPrimAtPath('/Asset/geo/Node_2'); "
                         "assert not s.GetPrimAtPath('/Asset/geo/Node_0')",
                         str(path)],
                        capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("no default scene", result.stderr)

    def test_node_order_and_optional_extension_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = pathlib.Path(directory) / ("asset." + extension)
                    document = {
                        "asset": {"version": "2.0"},
                        "extensionsUsed": ["UNKNOWN_optional"],
                        "scene": 0,
                        "scenes": [{"nodes": [2, 0]}],
                        "nodes": [{"children": [3, 1]}, {}, {}, {}],
                    }
                    write_asset(path, document)
                    result = subprocess.run(
                        [sys.executable, "-c",
                         "from pxr import Usd; import sys; "
                         "s = Usd.Stage.Open(sys.argv[1]); "
                         "assert [p.GetName() for p in s.GetPrimAtPath('/Asset/geo').GetChildren()] == ['Node_2', 'Node_0']; "
                         "assert [p.GetName() for p in s.GetPrimAtPath('/Asset/geo/Node_0').GetChildren()] == ['Node_3', 'Node_1']",
                         str(path)],
                        capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("UNKNOWN_optional", result.stderr)

    def test_failures_report_diagnostics(self):
        cases = {
            "no scenes": ({"asset": {"version": "2.0"}}, "no scenes"),
            "invalid scene": ({"asset": {"version": "2.0"}, "scene": 2, "scenes": [{}]}, "out of range"),
            "invalid node": ({"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [1]}]}, "node index"),
            "cycle": ({"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
                       "nodes": [{"children": [0]}]}, "cyclic node"),
            "duplicate node": ({"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0, 0]}],
                                "nodes": [{}]}, "repeated"),
            "invalid mesh accessor": ({"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
                                  "nodes": [{"mesh": 0}], "meshes": [{"primitives": [
                                      {"attributes": {"POSITION": 0}}]}]}, "accessor index"),
            "bad quaternion": ({"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
                                "nodes": [{"rotation": [0, 0, 0, 0]}]}, "invalid transform"),
            "perspective matrix": ({"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
                                    "nodes": [{"matrix": [1, 0, 0, 0.1, 0, 1, 0, 0,
                                                          0, 0, 1, 0, 0, 0, 0, 1]}]}, "invalid transform"),
            "required extension": ({"asset": {"version": "2.0"}, "scenes": [{}],
                                    "extensionsUsed": ["UNKNOWN_extension"],
                                    "extensionsRequired": ["UNKNOWN_extension"]}, "cannot parse"),
            "known required extension": ({"asset": {"version": "2.0"}, "scenes": [{}],
                                          "extensionsUsed": ["KHR_lights_punctual"],
                                          "extensionsRequired": ["KHR_lights_punctual"]}, "cannot parse"),
        }
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                for name, (document, message) in cases.items():
                    with self.subTest(extension=extension, name=name):
                        path = pathlib.Path(directory) / (name + "." + extension)
                        write_asset(path, document)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            Usd.Stage.Open(str(path))
                path = pathlib.Path(directory) / ("malformed." + extension)
                path.write_bytes(b"{")
                with self.assertRaisesRegex(Tf.ErrorException, "cannot parse"):
                    Usd.Stage.Open(str(path))

    def test_failed_reload_does_not_publish_partial_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = pathlib.Path(directory) / ("asset." + extension)
                    write_asset(path, scene_document())
                    stage = Usd.Stage.Open(str(path))
                    layer = stage.GetRootLayer()
                    before = layer.ExportToString()
                    document = scene_document()
                    document["nodes"][1]["children"] = [0]
                    write_asset(path, document)
                    with self.assertRaisesRegex(Tf.ErrorException, "cyclic node"):
                        layer.Reload(True)
                    self.assertEqual(layer.ExportToString(), before)
                    self.assertTrue(stage.GetPrimAtPath("/Asset/geo/Node_0/Node_1"))
                    changed = scene_document()
                    changed["nodes"][0]["children"] = []
                    write_asset(path, changed)
                    self.assertTrue(layer.Reload(True))
                    self.assertFalse(stage.GetPrimAtPath("/Asset/geo/Node_0/Node_1"))
                    self.assertTrue(stage.GetPrimAtPath("/Asset/geo/Node_0"))


if __name__ == "__main__":
    unittest.main()
