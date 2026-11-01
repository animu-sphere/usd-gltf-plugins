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


def uv_document(extension, external=False):
    document, binary = mesh_document("glb")
    binary += b"\0" * (-len(binary) % 4)
    uv_offset = len(binary)
    for uv, byte_uv, short_uv in (
        ((0.25, -0.5), (0, 255), (0, 65535)),
        ((1.5, 0), (128, 64), (32768, 16384)),
        ((0, 1.25), (255, 0), (65535, 0)),
    ):
        binary += struct.pack("<2f2B2x2H", *uv, *byte_uv, *short_uv)
    document["buffers"][0]["byteLength"] = len(binary)
    document["bufferViews"].append({
        "buffer": 0, "byteOffset": uv_offset, "byteLength": 48, "byteStride": 16,
    })
    for offset, component_type, normalized in ((0, 5126, False), (8, 5121, True), (12, 5123, True)):
        document["accessors"].append({
            "bufferView": 2, "byteOffset": offset, "componentType": component_type,
            "normalized": normalized, "count": 3, "type": "VEC2",
        })
    document["meshes"][0]["primitives"][0]["attributes"].update({
        "TEXCOORD_0": 3, "TEXCOORD_1": 4, "TEXCOORD_2": 5,
    })
    document["meshes"][0]["primitives"][1]["attributes"]["TEXCOORD_0"] = 3
    if external:
        document["buffers"][0]["uri"] = "mesh.bin"
    elif extension == "gltf":
        document["buffers"][0]["uri"] = "data:application/octet-stream;base64," + (
            base64.b64encode(binary).decode())
    return document, binary


def color_document(extension, component_type, components, external=False):
    document, binary = mesh_document("glb")
    binary += b"\0" * (-len(binary) % 4)
    color_offset = len(binary)
    fmt, maximum = {5126: ("f", 1), 5121: ("B", 255), 5123: ("H", 65535)}[component_type]
    stride = (struct.calcsize(fmt) * components + 3) & ~3
    raw_colors = ((0, maximum, 0, maximum),
                  (maximum, 0, maximum, 0),
                  (maximum // 2 if maximum > 1 else 0.5, maximum, 0,
                   maximum // 2 if maximum > 1 else 0.5))
    for color in raw_colors:
        packed = struct.pack("<" + fmt * components, *color[:components])
        binary += packed + b"\0" * (stride - len(packed))
    document["buffers"][0]["byteLength"] = len(binary)
    document["bufferViews"].append({
        "buffer": 0, "byteOffset": color_offset, "byteLength": stride * 3, "byteStride": stride,
    })
    document["accessors"].append({
        "bufferView": 2, "componentType": component_type, "count": 3,
        "type": f"VEC{components}", "normalized": component_type != 5126,
    })
    document["meshes"][0]["primitives"][0]["attributes"]["COLOR_0"] = 3
    document["meshes"][0]["primitives"][1]["attributes"]["COLOR_0"] = 3
    if external:
        document["buffers"][0]["uri"] = "mesh.bin"
    elif extension == "gltf":
        document["buffers"][0]["uri"] = "data:application/octet-stream;base64," + (
            base64.b64encode(binary).decode())
    return document, binary


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

    def test_coordinate_system_and_reference_composition(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = pathlib.Path(directory) / ("coordinates." + extension)
                    document, binary = mesh_document(extension)
                    document["scenes"][0]["nodes"] = [0]
                    document["nodes"] = [
                        {
                            "translation": [1, 2, 3],
                            "rotation": [0, 0, 0.7071067811865476, 0.7071067811865476],
                            "scale": [2, 3, 4],
                            "children": [1],
                        },
                        {"mesh": 0, "matrix": [
                            1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 1, 1,
                        ]},
                    ]
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    layer = stage.GetRootLayer()
                    self.assertTrue(layer.pseudoRoot.HasInfo("upAxis"))
                    self.assertTrue(layer.pseudoRoot.HasInfo("metersPerUnit"))
                    self.assertEqual(UsdGeom.GetStageUpAxis(stage), UsdGeom.Tokens.y)
                    self.assertEqual(UsdGeom.GetStageMetersPerUnit(stage), 1.0)

                    mesh_path = "/Asset/geo/Node_0/Node_1/Mesh_0/Primitive_0"
                    mesh = UsdGeom.Mesh.Get(stage, mesh_path)
                    points = list(mesh.GetPointsAttr().Get())
                    self.assertEqual(points, [
                        Gf.Vec3f(0, 0, 0), Gf.Vec3f(1, 0, 0), Gf.Vec3f(0, 1, 0),
                    ])
                    self.assertEqual(mesh.GetOrientationAttr().Get(), UsdGeom.Tokens.rightHanded)
                    transform = UsdGeom.XformCache().GetLocalToWorldTransform(mesh.GetPrim())
                    expected = ((1, 2, 7), (1, 4, 7), (-2, 2, 7))
                    for point, wanted in zip(points, expected):
                        actual = transform.Transform(Gf.Vec3d(point))
                        self.assertTrue(Gf.IsClose(actual, Gf.Vec3d(*wanted), 1e-5), str(actual))

                    host = Usd.Stage.CreateInMemory()
                    UsdGeom.SetStageUpAxis(host, UsdGeom.Tokens.z)
                    UsdGeom.SetStageMetersPerUnit(host, 0.01)
                    reference = host.DefinePrim("/Model", "Xform")
                    self.assertTrue(reference.GetReferences().AddReference(str(path)))
                    self.assertEqual(UsdGeom.GetStageUpAxis(host), UsdGeom.Tokens.z)
                    self.assertEqual(UsdGeom.GetStageMetersPerUnit(host), 0.01)
                    referenced = UsdGeom.Mesh.Get(host, mesh_path.replace("/Asset", "/Model", 1))
                    self.assertTrue(referenced)
                    self.assertEqual(list(referenced.GetPointsAttr().Get()), points)
                    referenced_transform = UsdGeom.XformCache().GetLocalToWorldTransform(
                        referenced.GetPrim())
                    for point, wanted in zip(referenced.GetPointsAttr().Get(), expected):
                        actual = referenced_transform.Transform(Gf.Vec3d(point))
                        self.assertTrue(Gf.IsClose(actual, Gf.Vec3d(*wanted), 1e-5), str(actual))

                    corrective = UsdGeom.Xformable(reference)
                    corrective.AddRotateXOp().Set(90)
                    corrective.AddScaleOp().Set(Gf.Vec3f(100))
                    corrected_transform = UsdGeom.XformCache().GetLocalToWorldTransform(
                        referenced.GetPrim())
                    corrected = ((100, -700, 200), (100, -700, 400), (-200, -700, 200))
                    for point, wanted in zip(referenced.GetPointsAttr().Get(), corrected):
                        actual = corrected_transform.Transform(Gf.Vec3d(point))
                        self.assertTrue(Gf.IsClose(actual, Gf.Vec3d(*wanted), 1e-3), str(actual))

                    self.assertTrue(layer.Reload(True))
                    self.assertTrue(layer.pseudoRoot.HasInfo("upAxis"))
                    self.assertTrue(layer.pseudoRoot.HasInfo("metersPerUnit"))
                    self.assertEqual(UsdGeom.GetStageUpAxis(stage), UsdGeom.Tokens.y)
                    self.assertEqual(UsdGeom.GetStageMetersPerUnit(stage), 1.0)
                    self.assertEqual(list(mesh.GetPointsAttr().Get()), points)
                    transform = UsdGeom.XformCache().GetLocalToWorldTransform(mesh.GetPrim())
                    for point, wanted in zip(points, expected):
                        actual = transform.Transform(Gf.Vec3d(point))
                        self.assertTrue(Gf.IsClose(actual, Gf.Vec3d(*wanted), 1e-5), str(actual))

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
                            self.assertFalse(UsdGeom.PrimvarsAPI(mesh).GetPrimvar("displayColor").HasAuthoredValue())
                            self.assertFalse(UsdGeom.PrimvarsAPI(mesh).GetPrimvar("gltf:colorAlpha"))
                            self.assertEqual(mesh.GetSubdivisionSchemeAttr().Get(), UsdGeom.Tokens.none)
                            self.assertEqual(mesh.GetOrientationAttr().Get(), UsdGeom.Tokens.rightHanded)
                            self.assertFalse(UsdGeom.PrimvarsAPI(mesh).GetPrimvar("st"))
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

    def test_texture_coordinates(self):
        expected = (
            ((0.25, 1.5), (1.5, 1), (0, -0.25)),
            ((0, 0), (128 / 255, 1 - 64 / 255), (1, 1)),
            ((0, 0), (32768 / 65535, 1 - 16384 / 65535), (1, 1)),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                for external in (False, True):
                    with self.subTest(extension=extension, external=external):
                        path = root / f"uv_{external}.{extension}"
                        document, binary = uv_document(extension, external)
                        if external:
                            (root / "mesh.bin").write_bytes(binary)
                        write_asset(path, document, binary if extension == "glb" and not external else None)
                        stage = Usd.Stage.Open(str(path))
                        for node in (0, 1):
                            mesh = UsdGeom.Mesh.Get(stage, f"/Asset/geo/Node_{node}/Mesh_0/Primitive_0")
                            api = UsdGeom.PrimvarsAPI(mesh)
                            self.assertEqual(list(mesh.GetFaceVertexIndicesAttr().Get()), [2, 0, 1])
                            for name, coordinates in zip(("st", "st1", "st2"), expected):
                                primvar = api.GetPrimvar(name)
                                self.assertTrue(primvar)
                                self.assertEqual(primvar.GetTypeName(), Sdf.ValueTypeNames.TexCoord2fArray)
                                self.assertEqual(primvar.GetInterpolation(), UsdGeom.Tokens.vertex)
                                self.assertFalse(primvar.IsIndexed())
                                values = primvar.Get()
                                self.assertEqual(len(values), len(coordinates))
                                for actual, wanted in zip(values, coordinates):
                                    self.assertTrue(Gf.IsClose(actual, Gf.Vec2f(*wanted), 1e-6), str(actual))
                            other = UsdGeom.Mesh.Get(stage, f"/Asset/geo/Node_{node}/Mesh_0/Primitive_1")
                            self.assertEqual(list(other.GetFaceVertexIndicesAttr().Get()), [0, 1, 2])
                            self.assertEqual(list(UsdGeom.PrimvarsAPI(other).GetPrimvar("st").Get()),
                                             [Gf.Vec2f(*uv) for uv in expected[0]])

    def test_texture_coordinate_failures_preserve_layer(self):
        def mutate(document, name):
            attributes = document["meshes"][0]["primitives"][0]["attributes"]
            if name == "count":
                document["accessors"][3]["count"] = 2
            elif name == "type":
                document["accessors"][3]["type"] = "VEC3"
            elif name == "normalized integer":
                document["accessors"][4]["normalized"] = False
            elif name == "component":
                document["accessors"][4]["componentType"] = 5122
            elif name == "missing set":
                del attributes["TEXCOORD_1"]
            elif name == "accessor":
                attributes["TEXCOORD_0"] = 99
            elif name == "bounds":
                document["bufferViews"][2]["byteLength"] = 8
            elif name == "packed byte alignment":
                document["bufferViews"].append({
                    "buffer": 0, "byteOffset": document["bufferViews"][2]["byteOffset"] + 8,
                    "byteLength": 6,
                })
                document["accessors"][4]["bufferView"] = 3
                document["accessors"][4]["byteOffset"] = 0
            elif name == "sparse":
                document["accessors"][3]["sparse"] = {
                    "count": 1, "indices": {"bufferView": 1, "componentType": 5121},
                    "values": {"bufferView": 2},
                }

        cases = {
            "count": "texture coordinate and position counts differ",
            "type": "invalid type",
            "normalized integer": "invalid type",
            "component": "unsupported component type",
            "missing set": "consecutive",
            "accessor": "accessor index",
            "bounds": "bufferView",
            "packed byte alignment": "invalid stride",
            "sparse": "sparse",
        }
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                path = pathlib.Path(directory) / ("uv." + extension)
                document, binary = uv_document(extension, external=True)
                (path.parent / "mesh.bin").write_bytes(binary)
                write_asset(path, document)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer.ExportToString()
                for name, message in cases.items():
                    with self.subTest(extension=extension, name=name):
                        invalid, _ = uv_document(extension, external=True)
                        mutate(invalid, name)
                        write_asset(path, invalid)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer.ExportToString(), before)
                invalid = bytearray(binary)
                uv_offset = document["bufferViews"][2]["byteOffset"]
                invalid[uv_offset:uv_offset + 4] = struct.pack("<f", float("nan"))
                (path.parent / "mesh.bin").write_bytes(invalid)
                write_asset(path, document)
                with self.assertRaisesRegex(Tf.ErrorException, "non-finite"):
                    layer.Reload(True)
                self.assertEqual(layer.ExportToString(), before)
                updated = bytearray(binary)
                updated[uv_offset:uv_offset + 4] = struct.pack("<f", 0.75)
                (path.parent / "mesh.bin").write_bytes(updated)
                self.assertTrue(layer.Reload(True))
                mesh = stage.GetPrimAtPath("/Asset/geo/Node_0/Mesh_0/Primitive_0")
                self.assertEqual(UsdGeom.PrimvarsAPI(mesh).GetPrimvar("st").Get()[0],
                                 Gf.Vec2f(0.75, 1.5))

    def test_vertex_colors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                for external in (False, True):
                    for component_type in (5126, 5121, 5123):
                        for components in (3, 4):
                            with self.subTest(extension=extension, external=external,
                                              component_type=component_type, components=components):
                                path = root / f"color_{external}_{component_type}_{components}.{extension}"
                                document, binary = color_document(extension, component_type, components, external)
                                if external:
                                    (root / "mesh.bin").write_bytes(binary)
                                write_asset(path, document, binary if extension == "glb" and not external else None)
                                stage = Usd.Stage.Open(str(path))
                                half = {5126: 0.5, 5121: 127 / 255, 5123: 32767 / 65535}[component_type]
                                expected = (Gf.Vec3f(0, 1, 0), Gf.Vec3f(1, 0, 1), Gf.Vec3f(half, 1, 0))
                                for node in (0, 1):
                                    for primitive in (0, 1):
                                        mesh = UsdGeom.Mesh.Get(
                                            stage, f"/Asset/geo/Node_{node}/Mesh_0/Primitive_{primitive}")
                                        api = UsdGeom.PrimvarsAPI(mesh)
                                        self.assertEqual(list(mesh.GetFaceVertexIndicesAttr().Get()),
                                                         [2, 0, 1] if primitive == 0 else [0, 1, 2])
                                        color = api.GetPrimvar("displayColor")
                                        self.assertEqual(color.GetTypeName(), Sdf.ValueTypeNames.Color3fArray)
                                        self.assertEqual(color.GetInterpolation(), UsdGeom.Tokens.vertex)
                                        self.assertFalse(color.IsIndexed())
                                        self.assertEqual(list(color.Get()), list(expected))
                                        alpha = api.GetPrimvar("gltf:colorAlpha")
                                        if components == 4:
                                            self.assertEqual(alpha.GetTypeName(), Sdf.ValueTypeNames.FloatArray)
                                            self.assertEqual(alpha.GetInterpolation(), UsdGeom.Tokens.vertex)
                                            self.assertFalse(alpha.IsIndexed())
                                            for actual, wanted in zip(alpha.Get(), (1, 0, half)):
                                                self.assertAlmostEqual(actual, wanted, places=6)
                                        else:
                                            self.assertFalse(alpha)
                                        self.assertFalse(api.GetPrimvar("displayOpacity").HasAuthoredValue())

    def test_vertex_color_failures_preserve_layer(self):
        def mutate(document, name):
            accessor = document["accessors"][3]
            if name == "count":
                accessor["count"] = 2
            elif name == "type":
                accessor["type"] = "VEC2"
            elif name == "normalized integer":
                accessor["normalized"] = False
            elif name == "component":
                accessor["componentType"] = 5122
            elif name == "accessor":
                document["meshes"][0]["primitives"][0]["attributes"]["COLOR_0"] = 99
            elif name == "bounds":
                document["bufferViews"][2]["byteLength"] = 4
            elif name == "sparse":
                accessor["sparse"] = {
                    "count": 1, "indices": {"bufferView": 1, "componentType": 5121},
                    "values": {"bufferView": 2},
                }

        cases = {
            "count": "vertex color and position counts differ",
            "type": "vertex color accessor has an invalid type",
            "normalized integer": "invalid type",
            "component": "unsupported component type",
            "accessor": "accessor index",
            "bounds": "bufferView",
            "sparse": "sparse",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                path = root / ("color." + extension)
                document, binary = color_document(extension, 5121, 4, external=True)
                (root / "mesh.bin").write_bytes(binary)
                write_asset(path, document)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer.ExportToString()
                for name, message in cases.items():
                    with self.subTest(extension=extension, name=name):
                        invalid, _ = color_document(extension, 5121, 4, external=True)
                        mutate(invalid, name)
                        write_asset(path, invalid)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer.ExportToString(), before)
                floats, float_binary = color_document(extension, 5126, 4, external=True)
                offset = floats["bufferViews"][2]["byteOffset"]
                for component in (0, 3):
                    for value in (float("nan"), float("inf"), -0.01, 1.01):
                        with self.subTest(extension=extension, component=component, value=value):
                            invalid = bytearray(float_binary)
                            position = offset + component * 4
                            invalid[position:position + 4] = struct.pack("<f", value)
                            (root / "mesh.bin").write_bytes(invalid)
                            write_asset(path, floats)
                            with self.assertRaisesRegex(Tf.ErrorException, "non-finite or out-of-range"):
                                layer.Reload(True)
                            self.assertEqual(layer.ExportToString(), before)
                updated = bytearray(binary)
                updated[document["bufferViews"][2]["byteOffset"]] = 128
                updated[document["bufferViews"][2]["byteOffset"] + 3] = 64
                (root / "mesh.bin").write_bytes(updated)
                write_asset(path, document)
                self.assertTrue(layer.Reload(True))
                mesh = stage.GetPrimAtPath("/Asset/geo/Node_0/Mesh_0/Primitive_0")
                self.assertAlmostEqual(
                    UsdGeom.PrimvarsAPI(mesh).GetPrimvar("displayColor").Get()[0][0], 128 / 255, places=6)
                self.assertAlmostEqual(
                    UsdGeom.PrimvarsAPI(mesh).GetPrimvar("gltf:colorAlpha").Get()[0], 64 / 255, places=6)

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
                primitive["attributes"]["COLOR_1"] = 0
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
