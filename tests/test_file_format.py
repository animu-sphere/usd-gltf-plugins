# SPDX-License-Identifier: Apache-2.0
import base64
import faulthandler
import json
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
import zlib

from pxr import Ar, Gf, Sdf, Sdr, Tf, Usd, UsdGeom, UsdShade

# A crash in native code would otherwise not say which test caused it.
faulthandler.enable()


def layer_text(layer):
    """Returns a layer's content; ExportToString is empty for a read-only format."""
    text = Sdf.Layer.CreateAnonymous(".usda")
    text.TransferContent(layer)
    return text.ExportToString()


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


def png_chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def png_declaring(width, height, depth=8, color=6):
    """A PNG whose chunks are valid, with a header that need not agree with its data."""
    return (b"\x89PNG\r\n\x1a\n"
            + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, 0))
            + png_chunk(b"IDAT", zlib.compress(b"\0\xff\x80\x40\xff")) + png_chunk(b"IEND", b""))


PNG = png_declaring(1, 1)
JPEG = (pathlib.Path(__file__).parent / "fixtures" / "textures" / "flat.jpg").read_bytes()


def jpeg_marker(data, marker):
    """The offset of the first occurrence of a segment's marker before the scan."""
    return data.index(bytes((0xFF, marker)), 2)


def jpeg_without(data, marker):
    start = jpeg_marker(data, marker)
    return data[:start] + data[start + 2 + struct.unpack_from(">H", data, start + 2)[0]:]


def jpeg_with_frame(data, marker=0xC0, precision=8, width=None, height=None):
    result = bytearray(data)
    start = jpeg_marker(data, 0xC0)
    result[start + 1] = marker
    result[start + 4] = precision
    if height is not None:
        struct.pack_into(">H", result, start + 5, height)
    if width is not None:
        struct.pack_into(">H", result, start + 7, width)
    return bytes(result)


def damaged_images():
    """Returns (name, data, message) for images that a structural check must reject."""
    sof = jpeg_marker(JPEG, 0xC0)
    sos = jpeg_marker(JPEG, 0xDA)
    png_damaged = "PNG image is damaged"
    jpeg_damaged = "JPEG image is damaged"
    too_large = "image is wider or taller than 16384 pixels"
    flipped = bytearray(PNG)
    flipped[45] ^= 0xFF
    no_iend = PNG[:-12]
    return (
        ("png signature only", PNG[:8], png_damaged),
        ("png cut in IHDR", PNG[:20], png_damaged),
        ("png cut after IHDR", PNG[:33], png_damaged),
        ("png cut in IDAT", PNG[:45], png_damaged),
        ("png without IEND", no_iend, png_damaged),
        ("png cut in checksum", PNG[:-2], png_damaged),
        ("png flipped byte", bytes(flipped), "chunk checksum does not match"),
        ("png without IDAT", b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", PNG[16:29])
         + png_chunk(b"IEND", b""), "no image data"),
        ("png IHDR not first", b"\x89PNG\r\n\x1a\n" + png_chunk(b"gAMA", bytes(4)) + PNG[8:],
         "valid IHDR"),
        ("png zero width", png_declaring(0, 1), "declares no pixels"),
        ("png color type", png_declaring(1, 1, color=5), "invalid format"),
        ("png bit depth", png_declaring(1, 1, depth=7), "invalid format"),
        ("png without palette", png_declaring(1, 1, color=3), "palette is missing"),
        ("png too wide", png_declaring(16385, 1), too_large),
        ("png too tall", png_declaring(1, 16385), too_large),
        ("png declares 4 billion", png_declaring(0xFFFFFFFF, 0xFFFFFFFF), too_large),
        ("jpeg signature only", JPEG[:3], jpeg_damaged),
        ("jpeg cut before frame", JPEG[:sof], jpeg_damaged),
        ("jpeg cut in frame", JPEG[:sof + 6], jpeg_damaged),
        ("jpeg cut before scan", JPEG[:sos], jpeg_damaged),
        ("jpeg cut in scan header", JPEG[:sos + 6], jpeg_damaged),
        ("jpeg cut in scan", JPEG[:(sos + len(JPEG)) // 2], "before its end marker"),
        ("jpeg without end marker", JPEG[:-2], "before its end marker"),
        ("jpeg without quantization", jpeg_without(JPEG, 0xDB), "no frame header or tables"),
        ("jpeg without huffman", jpeg_without(JPEG, 0xC4), "no frame header or tables"),
        ("jpeg zero width", jpeg_with_frame(JPEG, width=0), "frame header is invalid"),
        ("jpeg zero height", jpeg_with_frame(JPEG, height=0), "frame header is invalid"),
        ("jpeg lossless", jpeg_with_frame(JPEG, marker=0xC3), "not baseline or progressive"),
        ("jpeg arithmetic", jpeg_with_frame(JPEG, marker=0xC9), "not baseline or progressive"),
        ("jpeg 12-bit", jpeg_with_frame(JPEG, marker=0xC1, precision=12), "not 8-bit"),
        ("jpeg too wide", jpeg_with_frame(JPEG, width=16385), too_large),
        ("jpeg too tall", jpeg_with_frame(JPEG, height=65500), too_large),
    )


def asset_bytes(resolved_path):
    return bytes(Ar.GetResolver().OpenAsset(Ar.ResolvedPath(resolved_path)).GetBuffer())


def append_view(document, binary, extension, data):
    """Appends a bufferView holding `data` and returns the longer binary."""
    binary += b"\0" * (-len(binary) % 4)
    document["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(data)})
    binary += data
    document["buffers"][0]["byteLength"] = len(binary)
    if extension == "gltf":
        document["buffers"][0]["uri"] = "data:application/octet-stream;base64," + (
            base64.b64encode(binary).decode())
    return binary


def textured_document(extension, image, data):
    """A uv_document whose first primitive has a base color texture.

    `image` is the glTF image, or None to embed `data` through a bufferView.
    """
    document, binary = uv_document(extension)
    if image is None:
        binary = append_view(document, binary, extension, data)
        image = {"bufferView": len(document["bufferViews"]) - 1,
                 "mimeType": "image/png" if data == PNG else "image/jpeg"}
    document["images"] = [image]
    document["textures"] = [{"source": 0}]
    document["materials"] = [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}]
    document["meshes"][0]["primitives"][0]["material"] = 0
    return document, binary


def ktx2_fixture(name):
    return (pathlib.Path(__file__).parent / "fixtures" / "textures" / (name + ".ktx2")).read_bytes()


def ktx2_document(extension, image, data):
    """A textured_document whose texture requires KHR_texture_basisu."""
    document, binary = textured_document(extension, image, data)
    if image is None:
        document["images"][0]["mimeType"] = "image/ktx2"
    document["textures"] = [{"extensions": {"KHR_texture_basisu": {"source": 0}}}]
    document["extensionsUsed"] = ["KHR_texture_basisu"]
    document["extensionsRequired"] = ["KHR_texture_basisu"]
    return document, binary


def patch_ktx2_header(data, offset, value):
    return data[:offset] + struct.pack("<I", value) + data[offset + 4:]


def oversized_ktx2():
    """A consistent uncompressed UASTC file wider than the transcoder's limit."""
    data = bytearray(ktx2_fixture("uastc_alpha"))
    level_offset = struct.unpack_from("<Q", data, 80)[0]
    # One row of 4100 blocks of 16 bytes each.
    level_length = 4100 * 16
    struct.pack_into("<II", data, 20, 16400, 4)
    struct.pack_into("<QQQ", data, 80, level_offset, level_length, level_length)
    return bytes(data[:level_offset]) + bytes(level_length)


def ktx2_with_orientation(data, orientation):
    """Inserts a KTXorientation entry, which sorts before the encoder's own keys."""
    entry = b"KTXorientation\0" + orientation + b"\0"
    entry = struct.pack("<I", len(entry)) + entry
    entry += b"\0" * (-len(entry) % 8)
    kvd_offset, kvd_length, sgd_offset, sgd_length = struct.unpack_from("<IIQQ", data, 56)
    level_count = struct.unpack_from("<I", data, 40)[0]
    result = bytearray(data[:kvd_offset] + entry + data[kvd_offset:])
    struct.pack_into("<IIQ", result, 60 - 4, kvd_offset, kvd_length + len(entry),
                     sgd_offset + len(entry) if sgd_length else 0)
    for level in range(max(level_count, 1)):
        offset = 80 + level * 24
        struct.pack_into("<Q", result, offset,
                         struct.unpack_from("<Q", result, offset)[0] + len(entry))
    return bytes(result)


def read_png(data):
    """Returns the width, height and RGBA rows of a PNG without row filters."""
    width = height = None
    compressed = b""
    offset = 8
    while offset < len(data):
        length, kind = struct.unpack_from(">I4s", data, offset)
        chunk = data[offset + 8:offset + 8 + length]
        crc = struct.unpack_from(">I", data, offset + 8 + length)[0]
        if crc != zlib.crc32(kind + chunk):
            raise ValueError("bad PNG chunk checksum")
        if kind == b"IHDR":
            width, height, depth, color = struct.unpack_from(">IIBB", chunk)
            if (depth, color) != (8, 6):
                raise ValueError("PNG is not 8-bit RGBA")
        elif kind == b"IDAT":
            compressed += chunk
        offset += 12 + length
    raw = zlib.decompress(compressed)
    stride = width * 4 + 1
    if len(raw) != stride * height:
        raise ValueError("PNG has the wrong amount of data")
    rows = []
    for y in range(height):
        row = raw[y * stride:(y + 1) * stride]
        if row[0] != 0:
            raise ValueError("PNG row is filtered")
        rows.append([tuple(row[1 + x * 4:5 + x * 4]) for x in range(width)])
    return width, height, rows


def base_color_file(stage):
    """The base color image of a textured_document's material, which must be the same in both paths."""
    mesh = stage.GetPrimAtPath("/Asset/geo/Node_0/Mesh_0/Primitive_0")
    material, _ = UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()
    preview = UsdShade.Shader.Get(
        stage, material.GetPath().AppendPath(Sdf.Path("preview/baseColorTexture")))
    mtlx = UsdShade.Shader.Get(
        stage, material.GetPath().AppendPath(Sdf.Path("mtlx/baseColorImage")))
    asset = preview.GetInput("file").Get()
    if asset.path != mtlx.GetInput("file").Get().path:
        raise ValueError("the shading paths use different images")
    return asset


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


def unsupported_document(extension, feature):
    document, binary = mesh_document("glb")
    del document["nodes"][0]["mesh"]
    node = document["nodes"][1]

    def add_accessor(data, component_type, count, accessor_type, **kwargs):
        nonlocal binary
        binary += b"\0" * (-len(binary) % 4)
        view = len(document["bufferViews"])
        document["bufferViews"].append({
            "buffer": 0, "byteOffset": len(binary), "byteLength": len(data),
        })
        binary += data
        index = len(document["accessors"])
        document["accessors"].append({
            "bufferView": view, "componentType": component_type, "count": count,
            "type": accessor_type, **kwargs,
        })
        return index

    if feature == "skin":
        joints = add_accessor(bytes(12), 5121, 3, "VEC4")
        weights = add_accessor(struct.pack("<12f", *([1, 0, 0, 0] * 3)), 5126, 3, "VEC4")
        for item in document["meshes"][0]["primitives"]:
            item["attributes"].update({"JOINTS_0": joints, "WEIGHTS_0": weights})
        node["skin"] = 0
        document["skins"] = [{"joints": [0]}]
    elif feature == "camera":
        node["camera"] = 0
        document["cameras"] = [{"type": "perspective", "perspective": {
            "yfov": 1.0, "znear": 0.1,
        }}]
    elif feature == "light":
        document["extensionsUsed"] = ["KHR_lights_punctual"]
        document["extensions"] = {"KHR_lights_punctual": {"lights": [{"type": "point"}]}}
        node["extensions"] = {"KHR_lights_punctual": {"light": 0}}
    elif feature in ("morph target", "mesh weights", "node weights"):
        delta = add_accessor(struct.pack("<9f", *([0.0] * 9)), 5126, 3, "VEC3",
                             min=[0, 0, 0], max=[0, 0, 0])
        for item in document["meshes"][0]["primitives"]:
            item["targets"] = [{"POSITION": delta}]
        if feature == "mesh weights":
            document["meshes"][0]["weights"] = [0.5]
        elif feature == "node weights":
            node["weights"] = [0.5]
    elif feature == "animation":
        times = add_accessor(struct.pack("<2f", 0, 1), 5126, 2, "SCALAR", min=[0], max=[1])
        values = add_accessor(struct.pack("<6f", 0, 0, 0, 1, 0, 0), 5126, 2, "VEC3")
        document["animations"] = [{
            "samplers": [{"input": times, "output": values}],
            "channels": [{"sampler": 0, "target": {"node": 0, "path": "translation"}}],
        }]
    else:
        raise ValueError(feature)

    document["buffers"][0]["byteLength"] = len(binary)
    if extension == "gltf":
        document["buffers"][0]["uri"] = "data:application/octet-stream;base64," + (
            base64.b64encode(binary).decode())
        return document, None
    return document, binary


def topology_document(extension, mode, indices, external=False, normal=False, index_type=5123):
    vertices = (
        (0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0), (0, 2, 0),
    )
    binary = b"".join(struct.pack("<3f3f2f3f", *point, 0, 0, 1, 0.25, 0.75, 1, 0, 0)
                      for point in vertices)
    vertex_size = len(binary)
    if indices is not None:
        fmt = {5121: "B", 5123: "H", 5125: "I"}[index_type]
        binary += struct.pack("<" + fmt * len(indices), *indices)
    buffer = {"byteLength": len(binary)}
    if external:
        buffer["uri"] = "topology.bin"
    elif extension == "gltf":
        buffer["uri"] = "data:application/octet-stream;base64," + base64.b64encode(binary).decode()
    attributes = {"POSITION": 0, "TEXCOORD_0": 2, "COLOR_0": 3}
    if normal:
        attributes["NORMAL"] = 1
    primitive = {"attributes": attributes, "mode": mode}
    if indices is not None:
        primitive["indices"] = 4
    document = {
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}], "meshes": [{"primitives": [primitive]}],
        "buffers": [buffer],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": vertex_size, "byteStride": 44}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 5, "type": "VEC3",
             "min": [0, 0, 0], "max": [1, 2, 0]},
            {"bufferView": 0, "byteOffset": 12, "componentType": 5126, "count": 5, "type": "VEC3"},
            {"bufferView": 0, "byteOffset": 24, "componentType": 5126, "count": 5, "type": "VEC2"},
            {"bufferView": 0, "byteOffset": 32, "componentType": 5126, "count": 5, "type": "VEC3"},
        ],
    }
    if indices is not None:
        document["bufferViews"].append({
            "buffer": 0, "byteOffset": vertex_size, "byteLength": len(binary) - vertex_size,
        })
        document["accessors"].append({
            "bufferView": 1, "componentType": index_type, "count": len(indices), "type": "SCALAR",
        })
    return document, binary


class FileFormatTest(unittest.TestCase):
    def assert_material_nodegraphs(self, material, preview, mtlx):
        stage = material.GetPrim().GetStage()
        material_path = material.GetPath()
        self.assertEqual(
            {prim.GetName() for prim in material.GetPrim().GetChildren()}, {"preview", "mtlx"})
        for context, graph_name, shader, shader_output in (
            ("", "preview", preview, "surface"),
            ("mtlx", "mtlx", mtlx, "out"),
        ):
            graph = UsdShade.NodeGraph.Get(stage, material_path.AppendChild(graph_name))
            self.assertTrue(graph)
            self.assertEqual(shader.GetPath(), graph.GetPath().AppendChild("surface"))
            output = graph.GetOutput("surface")
            self.assertTrue(output)
            self.assertEqual(material.GetSurfaceOutput(context).GetAttr().GetConnections(), [
                output.GetAttr().GetPath(),
            ])
            self.assertEqual(output.GetAttr().GetConnections(), [
                shader.GetPath().AppendProperty("outputs:" + shader_output),
            ])
            resolved, name, kind = material.ComputeSurfaceSource(context)
            self.assertEqual((resolved.GetPath(), name, kind),
                             (shader.GetPath(), shader_output, UsdShade.AttributeType.Output))

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
                            self.assertFalse(UsdGeom.PrimvarsAPI(mesh).GetPrimvar("gltf_colorAlpha"))
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

    def test_triangle_strips_and_fans(self):
        cases = (
            (5, None, [0, 1, 2, 2, 1, 3, 2, 3, 4]),
            (6, None, [0, 1, 2, 0, 2, 3, 0, 3, 4]),
            (5, [1, 0, 3, 2, 4], [1, 0, 3, 3, 0, 2, 3, 2, 4]),
            (6, [1, 0, 3, 2, 4], [1, 0, 3, 1, 3, 2, 1, 2, 4]),
            (5, [0, 1, 2], [0, 1, 2]),
            (6, [0, 1, 2], [0, 1, 2]),
            (5, [0, 1, 2, 3], [0, 1, 2, 2, 1, 3]),
            (6, [0, 1, 2, 3], [0, 1, 2, 0, 2, 3]),
            (5, [0, 1, 2, 2, 3, 4], [0, 1, 2, 2, 1, 2, 2, 2, 3, 3, 2, 4]),
            (6, [0, 1, 2, 2, 3, 4], [0, 1, 2, 0, 2, 2, 0, 2, 3, 0, 3, 4]),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                for external in (False, True):
                    for case, (mode, indices, expected) in enumerate(cases):
                        for normal in (False, True):
                            with self.subTest(extension=extension, external=external, case=case, normal=normal):
                                path = root / f"topology_{external}_{case}_{normal}.{extension}"
                                document, binary = topology_document(extension, mode, indices, external, normal)
                                if external:
                                    (root / "topology.bin").write_bytes(binary)
                                write_asset(path, document, binary if extension == "glb" and not external else None)
                                stage = Usd.Stage.Open(str(path))
                                mesh = UsdGeom.Mesh.Get(stage, "/Asset/geo/Node_0/Mesh_0/Primitive_0")
                                self.assertEqual(list(mesh.GetFaceVertexIndicesAttr().Get()), expected)
                                self.assertEqual(list(mesh.GetFaceVertexCountsAttr().Get()), [3] * (len(expected) // 3))
                                self.assertEqual(mesh.GetOrientationAttr().Get(), UsdGeom.Tokens.rightHanded)
                                self.assertEqual(mesh.GetSubdivisionSchemeAttr().Get(), UsdGeom.Tokens.none)
                                points = mesh.GetPointsAttr().Get()
                                if normal:
                                    self.assertEqual(list(mesh.GetNormalsAttr().Get()), [Gf.Vec3f(0, 0, 1)] * 5)
                                    self.assertEqual(mesh.GetNormalsInterpolation(), UsdGeom.Tokens.vertex)
                                else:
                                    expected_normals = []
                                    for i in range(0, len(expected), 3):
                                        a, b, c = (points[index] for index in expected[i:i + 3])
                                        cross = Gf.Cross(b - a, c - a)
                                        expected_normals.append(cross.GetNormalized() if cross.GetLength() else Gf.Vec3f(0))
                                    self.assertEqual(list(mesh.GetNormalsAttr().Get()), expected_normals)
                                    self.assertEqual(mesh.GetNormalsInterpolation(), UsdGeom.Tokens.uniform)
                                primvars = UsdGeom.PrimvarsAPI(mesh)
                                self.assertEqual(list(primvars.GetPrimvar("st").Get()), [Gf.Vec2f(0.25, 0.25)] * 5)
                                self.assertEqual(list(primvars.GetPrimvar("displayColor").Get()),
                                                 [Gf.Vec3f(1, 0, 0)] * 5)

    def test_triangle_strip_and_fan_index_types_and_nonindexed_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                for mode in (5, 6):
                    for index_type in (None, 5121, 5123, 5125):
                        for count in (3, 4):
                            with self.subTest(extension=extension, mode=mode, index_type=index_type, count=count):
                                path = root / f"topology_{mode}_{index_type}_{count}.{extension}"
                                indices = None if index_type is None else list(range(count))
                                document, binary = topology_document(
                                    extension, mode, indices, index_type=index_type or 5123)
                                if indices is None:
                                    for accessor in document["accessors"]:
                                        accessor["count"] = count
                                write_asset(path, document, binary if extension == "glb" else None)
                                stage = Usd.Stage.Open(str(path))
                                mesh = UsdGeom.Mesh.Get(stage, "/Asset/geo/Node_0/Mesh_0/Primitive_0")
                                expected = [0, 1, 2]
                                if count == 4:
                                    expected += [2, 1, 3] if mode == 5 else [0, 2, 3]
                                self.assertEqual(list(mesh.GetFaceVertexIndicesAttr().Get()), expected)

    def test_triangle_strip_and_fan_failures_preserve_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                for mode in (5, 6):
                    path = root / f"topology_{mode}.{extension}"
                    document, binary = topology_document(extension, mode, [0, 1, 2, 3, 4], external=True)
                    buffer_path = root / "topology.bin"
                    buffer_path.write_bytes(binary)
                    write_asset(path, document)
                    stage = Usd.Stage.Open(str(path))
                    layer = stage.GetRootLayer()
                    before = layer_text(layer)
                    for case in ("short indexed", "short nonindexed", "out of range", "reserved"):
                        with self.subTest(extension=extension, mode=mode, case=case):
                            invalid, invalid_binary = topology_document(
                                extension, mode, [0, 1, 2, 3, 4], external=True)
                            if case == "short indexed":
                                invalid["accessors"][4]["count"] = 2
                            elif case == "short nonindexed":
                                del invalid["meshes"][0]["primitives"][0]["indices"]
                                for accessor in invalid["accessors"][:4]:
                                    accessor["count"] = 2
                            else:
                                invalid_binary = invalid_binary[:220] + struct.pack(
                                    "<5H", 0, 1, 2, 3, 5 if case == "out of range" else 65535)
                            buffer_path.write_bytes(invalid_binary)
                            write_asset(path, invalid)
                            message = "index count is invalid" if case.startswith("short") else "index is out of range"
                            with self.assertRaisesRegex(Tf.ErrorException, message):
                                layer.Reload(True)
                            self.assertEqual(layer_text(layer), before)
                    document, binary = topology_document(extension, mode, [4, 3, 2], external=True)
                    buffer_path.write_bytes(binary)
                    write_asset(path, document)
                    self.assertTrue(layer.Reload(True))
                    mesh = UsdGeom.Mesh.Get(stage, "/Asset/geo/Node_0/Mesh_0/Primitive_0")
                    self.assertEqual(list(mesh.GetFaceVertexIndicesAttr().Get()), [4, 3, 2])

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
                before = layer_text(layer)
                for name, message in cases.items():
                    with self.subTest(extension=extension, name=name):
                        invalid, _ = uv_document(extension, external=True)
                        mutate(invalid, name)
                        write_asset(path, invalid)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)
                invalid = bytearray(binary)
                uv_offset = document["bufferViews"][2]["byteOffset"]
                invalid[uv_offset:uv_offset + 4] = struct.pack("<f", float("nan"))
                (path.parent / "mesh.bin").write_bytes(invalid)
                write_asset(path, document)
                with self.assertRaisesRegex(Tf.ErrorException, "non-finite"):
                    layer.Reload(True)
                self.assertEqual(layer_text(layer), before)
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
                                        alpha = api.GetPrimvar("gltf_colorAlpha")
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
                before = layer_text(layer)
                for name, message in cases.items():
                    with self.subTest(extension=extension, name=name):
                        invalid, _ = color_document(extension, 5121, 4, external=True)
                        mutate(invalid, name)
                        write_asset(path, invalid)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)
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
                            self.assertEqual(layer_text(layer), before)
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
                    UsdGeom.PrimvarsAPI(mesh).GetPrimvar("gltf_colorAlpha").Get()[0], 64 / 255, places=6)

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
                document["images"] = [{"uri": "data:image/png;base64,AA=="}]
                document["textures"] = [{"source": 0}]
                document["materials"] = [{"normalTexture": {"index": 0}}]
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
            "material": "texture coordinate set the primitive does not have",
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
                before = layer_text(layer)
                for name, message in cases.items():
                    with self.subTest(extension=extension, name=name):
                        invalid, invalid_binary = mesh_document(extension)
                        mutate(invalid, name)
                        write_asset(path, invalid, invalid_binary)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)
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
                self.assertEqual(layer_text(layer), before)
                write_asset(path, document, binary)
                self.assertTrue(layer.Reload(True))

    def test_factor_materials_and_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = pathlib.Path(directory) / ("materials." + extension)
                    document, binary = mesh_document(extension)
                    document["meshes"][0]["primitives"][0]["material"] = 0
                    document["meshes"][0]["primitives"][1]["material"] = 1
                    document["materials"] = [
                        {
                            "name": "PBR / material",
                            "pbrMetallicRoughness": {
                                "baseColorFactor": [0.2, 0.4, 0.6, 0.25],
                                "metallicFactor": 0.3,
                                "roughnessFactor": 0.7,
                            },
                            "emissiveFactor": [0.1, 0.2, 0.3],
                            "doubleSided": True,
                        },
                        {},
                    ]
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    layer = stage.GetRootLayer()
                    material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                    self.assertTrue(material)
                    self.assertEqual(material.GetPrim().GetDisplayName(), "PBR / material")
                    preview = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/preview/surface")
                    mtlx = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/mtlx/surface")
                    self.assert_material_nodegraphs(material, preview, mtlx)
                    self.assertEqual(preview.GetIdAttr().Get(), "UsdPreviewSurface")
                    self.assertEqual(mtlx.GetIdAttr().Get(), "ND_gltf_pbr_surfaceshader")
                    for context, shader, output in (
                        ("", preview, "surface"), ("mtlx", mtlx, "out"),
                        ("unavailable", preview, "surface"),
                    ):
                        resolved, name, kind = material.ComputeSurfaceSource(context)
                        self.assertEqual((resolved.GetPath(), name, kind),
                                         (shader.GetPath(), output, UsdShade.AttributeType.Output))
                    registry = Sdr.Registry()
                    for shader, inputs, output in (
                        (preview, ("diffuseColor", "metallic", "roughness", "emissiveColor"), "surface"),
                        (mtlx, ("base_color", "metallic", "roughness", "emissive",
                                "alpha", "alpha_mode"), "out"),
                    ):
                        node = registry.GetShaderNodeByIdentifier(shader.GetIdAttr().Get())
                        self.assertTrue(node)
                        self.assertIn(output, node.GetShaderOutputNames())
                        for name in inputs:
                            self.assertIsNotNone(node.GetShaderInput(name))
                    preview_node = registry.GetShaderNodeByIdentifier("UsdPreviewSurface")
                    mtlx_node = registry.GetShaderNodeByIdentifier("ND_gltf_pbr_surfaceshader")
                    self.assertEqual(preview_node.GetShaderInput("useSpecularWorkflow").GetDefaultValue(), 0)
                    self.assertEqual(preview_node.GetShaderInput("opacity").GetDefaultValue(), 1)
                    self.assertEqual(mtlx_node.GetShaderInput("alpha").GetDefaultValue(), 1)
                    self.assertEqual(mtlx_node.GetShaderInput("alpha_mode").GetDefaultValue(), 0)
                    self.assertEqual(mtlx_node.GetShaderInput("emissive_strength").GetDefaultValue(), 1)
                    for shader, color, metallic, roughness, emission in (
                        (preview, "diffuseColor", "metallic", "roughness", "emissiveColor"),
                        (mtlx, "base_color", "metallic", "roughness", "emissive"),
                    ):
                        self.assertTrue(Gf.IsClose(shader.GetInput(color).Get(),
                                                   Gf.Vec3f(0.2, 0.4, 0.6), 1e-6))
                        self.assertAlmostEqual(shader.GetInput(metallic).Get(), 0.3, places=6)
                        self.assertAlmostEqual(shader.GetInput(roughness).Get(), 0.7, places=6)
                        self.assertTrue(Gf.IsClose(shader.GetInput(emission).Get(),
                                                   Gf.Vec3f(0.1, 0.2, 0.3), 1e-6))
                    self.assertEqual(mtlx.GetInput("alpha_mode").Get(), 0)
                    self.assertFalse(preview.GetInput("opacity"))
                    self.assertFalse(mtlx.GetInput("alpha"))
                    for node in (0, 1):
                        for primitive in (0, 1):
                            mesh = UsdGeom.Mesh.Get(
                                stage, f"/Asset/geo/Node_{node}/Mesh_0/Primitive_{primitive}")
                            bound, _ = UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()
                            self.assertEqual(bound.GetPath(), Sdf.Path(f"/Asset/mtl/Material_{primitive}"))
                            self.assertEqual(mesh.GetDoubleSidedAttr().Get(), primitive == 0)
                    host = Usd.Stage.CreateInMemory()
                    host.DefinePrim("/Model").GetReferences().AddReference(str(path))
                    referenced = host.GetPrimAtPath("/Model/geo/Node_0/Mesh_0/Primitive_0")
                    bound, _ = UsdShade.MaterialBindingAPI(referenced).ComputeBoundMaterial()
                    self.assertEqual(bound.GetPath(), Sdf.Path("/Model/mtl/Material_0"))
                    host_preview = UsdShade.Shader.Get(host, "/Model/mtl/Material_0/preview/surface")
                    host_mtlx = UsdShade.Shader.Get(host, "/Model/mtl/Material_0/mtlx/surface")
                    self.assert_material_nodegraphs(bound, host_preview, host_mtlx)
                    default = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_1/preview/surface")
                    self.assertEqual(default.GetInput("diffuseColor").Get(), Gf.Vec3f(1))
                    self.assertEqual(default.GetInput("metallic").Get(), 1)
                    self.assertEqual(default.GetInput("roughness").Get(), 1)
                    document["materials"][0]["pbrMetallicRoughness"]["roughnessFactor"] = 0.9
                    write_asset(path, document, binary)
                    self.assertTrue(layer.Reload(True))
                    self.assert_material_nodegraphs(material, preview, mtlx)
                    self.assertAlmostEqual(preview.GetInput("roughness").Get(), 0.9, places=6)
                    bound, _ = UsdShade.MaterialBindingAPI(
                        stage.GetPrimAtPath("/Asset/geo/Node_0/Mesh_0/Primitive_0")
                    ).ComputeBoundMaterial()
                    self.assertEqual(bound.GetPath(), material.GetPath())

    def test_unselected_materials_are_not_authored(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = pathlib.Path(directory) / ("selection." + extension)
                    document, binary = mesh_document(extension)
                    document["scenes"][0]["nodes"] = [0]
                    document["scenes"].append({"nodes": [1]})
                    document["nodes"][1]["mesh"] = 1
                    document["meshes"].append({"primitives": [{
                        "attributes": {"POSITION": 0}, "material": 1,
                    }]})
                    document["meshes"][0]["primitives"][0]["material"] = 0
                    document["images"] = [{"uri": "data:image/png;base64,AA=="}]
                    document["textures"] = [{"source": 0}]
                    document["materials"] = [{}, {"normalTexture": {"index": 0}}]
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    self.assertTrue(UsdShade.Material.Get(stage, "/Asset/mtl/Material_0"))
                    self.assertFalse(stage.GetPrimAtPath("/Asset/mtl/Material_1"))
                    self.assertFalse(stage.GetPrimAtPath("/Asset/geo/Node_1"))

    def test_material_reload_inside_change_block_preserves_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = pathlib.Path(directory) / ("materials." + extension)
                    document, binary = mesh_document(extension)
                    document["meshes"][0]["primitives"][0]["material"] = 0
                    document["materials"] = [{"pbrMetallicRoughness": {"roughnessFactor": 0.3}}]
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    layer = stage.GetRootLayer()
                    document["materials"][0]["pbrMetallicRoughness"]["roughnessFactor"] = 0.7
                    write_asset(path, document, binary)
                    with Sdf.ChangeBlock():
                        self.assertTrue(layer.Reload(True))
                    shader = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/preview/surface")
                    self.assertAlmostEqual(shader.GetInput("roughness").Get(), 0.7, places=6)
                    before = layer_text(layer)
                    document["meshes"][0]["primitives"][0]["material"] = 5
                    write_asset(path, document, binary)
                    with self.assertRaisesRegex(Tf.ErrorException, "material index is out of range"):
                        with Sdf.ChangeBlock():
                            layer.Reload(True)
                    self.assertEqual(layer_text(layer), before)
                    self.assertAlmostEqual(shader.GetInput("roughness").Get(), 0.7, places=6)

    def test_default_material_with_and_without_vertex_color(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                for component_type, components in ((5126, 3), (5126, 4), (5121, 4), (5123, 4)):
                    with self.subTest(extension=extension, component_type=component_type,
                                      components=components):
                        path = pathlib.Path(directory) / ("default." + extension)
                        document, binary = color_document(extension, component_type, components)
                        del document["meshes"][0]["primitives"][1]["attributes"]["COLOR_0"]
                        write_asset(path, document, binary)
                        stage = Usd.Stage.Open(str(path))
                        for node in (0, 1):
                            for primitive, name in ((0, "DefaultVertexColor"), (1, "Default")):
                                mesh = UsdGeom.Mesh.Get(
                                    stage, f"/Asset/geo/Node_{node}/Mesh_0/Primitive_{primitive}")
                                material, _ = UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()
                                self.assertEqual(material.GetPath(), Sdf.Path("/Asset/mtl/" + name))
                                self.assertFalse(mesh.GetDoubleSidedAttr().Get())
                        for name in ("Default", "DefaultVertexColor"):
                            material = UsdShade.Material.Get(stage, "/Asset/mtl/" + name)
                            preview, _, _ = material.ComputeSurfaceSource()
                            mtlx, _, _ = material.ComputeSurfaceSource("mtlx")
                            self.assert_material_nodegraphs(material, preview, mtlx)
                            self.assertEqual(preview.GetInput("diffuseColor").Get(), Gf.Vec3f(1))
                            self.assertEqual(preview.GetInput("metallic").Get(), 1)
                            self.assertEqual(preview.GetInput("roughness").Get(), 1)
                            self.assertFalse(preview.GetInput("opacity"))
                            self.assertEqual(mtlx.GetIdAttr().Get(), "ND_gltf_pbr_surfaceshader")
                            self.assertEqual(mtlx.GetInput("metallic").Get(), 1)
                            self.assertEqual(mtlx.GetInput("roughness").Get(), 1)
                            self.assertEqual(mtlx.GetInput("alpha_mode").Get(), 0)
                            self.assertFalse(mtlx.GetInput("alpha"))
                            if name == "Default":
                                self.assertFalse(preview.GetInput("diffuseColor").HasConnectedSource())
                                self.assertFalse(mtlx.GetInput("base_color").HasConnectedSource())
                                continue
                            for shader, input_name, reader_name, node_id, output, property_name in (
                                (preview, "diffuseColor", "displayColorReader",
                                 "UsdPrimvarReader_float3", "result", "varname"),
                                (mtlx, "base_color", "displayColorReader",
                                 "ND_geompropvalue_color3", "out", "geomprop"),
                            ):
                                graph_path = shader.GetPrim().GetParent().GetPath()
                                reader = UsdShade.Shader.Get(stage, graph_path.AppendChild(reader_name))
                                self.assertEqual(reader.GetIdAttr().Get(), node_id)
                                self.assertEqual(reader.GetInput(property_name).Get(), "displayColor")
                                self.assertEqual(shader.GetInput(input_name).GetAttr().GetConnections(), [
                                    reader.GetPath().AppendProperty("outputs:" + output),
                                ])
                                node = Sdr.Registry().GetShaderNodeByIdentifier(node_id)
                                self.assertIsNotNone(node)
                                self.assertIsNotNone(node.GetShaderInput(property_name))
                                self.assertIn(output, node.GetShaderOutputNames())
                        layer = stage.GetRootLayer()
                        self.assertTrue(layer.Reload(True))
                        mesh = stage.GetPrimAtPath("/Asset/geo/Node_0/Mesh_0/Primitive_0")
                        bound, _ = UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()
                        self.assertEqual(bound.GetPath(), Sdf.Path("/Asset/mtl/DefaultVertexColor"))

    def test_mesh_schema_attributes(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = pathlib.Path(directory) / ("mesh." + extension)
                    document, binary = mesh_document(extension)
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    mesh = UsdGeom.Mesh.Get(stage, "/Asset/geo/Node_0/Mesh_0/Primitive_0")
                    self.assertTrue(layer_text(stage.GetRootLayer()))
                    points = mesh.GetPointsAttr().Get()
                    self.assertEqual(mesh.GetExtentAttr().Get(),
                                     UsdGeom.PointBased.ComputeExtent(points))
                    self.assertEqual(mesh.GetSubdivisionSchemeAttr().Get(), "none")
                    self.assertEqual(mesh.GetSubdivisionSchemeAttr().GetVariability(),
                                     Sdf.VariabilityUniform)
                    self.assertEqual(stage.GetDefaultPrim().GetPath(), Sdf.Path("/Asset"))

    def test_alpha_modes(self):
        factors = {"baseColorFactor": [0.2, 0.4, 0.6, 0.25]}
        cases = (
            # glTF material, Preview opacity and threshold, MaterialX alpha, mode and cutoff.
            ({"alphaMode": "MASK", "alphaCutoff": 0.75}, 0.25, 0.75, 0.25, 1, 0.75),
            ({"alphaMode": "MASK"}, 0.25, 0.5, 0.25, 1, 0.5),
            ({"alphaMode": "MASK", "alphaCutoff": 0}, None, None, 0.25, 1, 0),
            ({"alphaMode": "BLEND", "alphaCutoff": 0.75}, 0.25, None, 0.25, 2, None),
            ({"alphaMode": "OPAQUE", "alphaCutoff": 0.75}, None, None, None, 0, None),
        )
        node = Sdr.Registry().GetShaderNodeByIdentifier("UsdPreviewSurface")
        self.assertIsNotNone(node.GetShaderInput("opacityThreshold"))
        self.assertEqual(node.GetShaderInput("opacityThreshold").GetDefaultValue(), 0)
        node = Sdr.Registry().GetShaderNodeByIdentifier("ND_gltf_pbr_surfaceshader")
        self.assertIsNotNone(node.GetShaderInput("alpha_cutoff"))
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                path = pathlib.Path(directory) / ("alpha." + extension)
                for source, opacity, threshold, alpha, mode, cutoff in cases:
                    with self.subTest(extension=extension, source=source):
                        document, binary = mesh_document(extension)
                        document["meshes"][0]["primitives"][0]["material"] = 0
                        document["materials"] = [dict(source, pbrMetallicRoughness=factors)]
                        write_asset(path, document, binary)
                        stage = Usd.Stage.Open(str(path))
                        self.assertTrue(stage.GetRootLayer().Reload(True))
                        material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                        preview, _, _ = material.ComputeSurfaceSource()
                        mtlx, _, _ = material.ComputeSurfaceSource("mtlx")
                        self.assert_material_nodegraphs(material, preview, mtlx)
                        for shader, name, expected in (
                            (preview, "opacity", opacity),
                            (preview, "opacityThreshold", threshold),
                            (mtlx, "alpha", alpha),
                            (mtlx, "alpha_cutoff", cutoff),
                        ):
                            if expected is None:
                                self.assertFalse(shader.GetInput(name), name)
                            else:
                                self.assertAlmostEqual(shader.GetInput(name).Get(), expected, places=6)
                        self.assertEqual(mtlx.GetInput("alpha_mode").Get(), mode)

    def test_explicit_material_vertex_colors(self):
        registry = Sdr.Registry()
        for node_id, inputs, output in (
            ("UsdPrimvarReader_float", ("varname", "fallback"), "result"),
            ("ND_geompropvalue_float", ("geomprop", "default"), "out"),
            ("ND_multiply_color3", ("in1", "in2"), "out"),
            ("ND_multiply_float", ("in1", "in2"), "out"),
        ):
            node = registry.GetShaderNodeByIdentifier(node_id)
            self.assertIsNotNone(node, node_id)
            self.assertIn(output, node.GetShaderOutputNames())
            for name in inputs:
                self.assertIsNotNone(node.GetShaderInput(name), name)

        def reader_of(shader, input_name):
            connections = shader.GetInput(input_name).GetAttr().GetConnections()
            self.assertEqual(len(connections), 1)
            return UsdShade.Shader.Get(shader.GetPrim().GetStage(), connections[0].GetPrimPath())

        vertex_rgb = (Gf.Vec3f(0, 1, 0), Gf.Vec3f(1, 0, 1), Gf.Vec3f(0.5, 1, 0))
        vertex_alpha = (1, 0, 0.5)
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                for case, (components, mode, rgb, alpha) in enumerate((
                    (4, "BLEND", (0.5, 0.25, 1), 0.5),
                    (3, "BLEND", (0.5, 0.25, 1), 0.5),
                    (4, "MASK", (1, 1, 1), 1),
                    (3, "MASK", (1, 1, 1), 0.5),
                    (4, "OPAQUE", (0.5, 0.25, 1), 0.5),
                )):
                    with self.subTest(extension=extension, components=components, mode=mode,
                                      rgb=rgb, alpha=alpha):
                        path = pathlib.Path(directory) / f"colors{case}.{extension}"
                        document, binary = color_document(extension, 5126, components)
                        primitives = document["meshes"][0]["primitives"]
                        primitives[0]["material"] = 0
                        primitives[1]["material"] = 0
                        del primitives[1]["attributes"]["COLOR_0"]
                        document["materials"] = [{
                            "name": "Tinted",
                            "alphaMode": mode,
                            "alphaCutoff": 0.25,
                            "doubleSided": True,
                            "pbrMetallicRoughness": {"baseColorFactor": [*rgb, alpha]},
                        }]
                        write_asset(path, document, binary)
                        stage = Usd.Stage.Open(str(path))

                        # A primitive without COLOR_0 keeps the plain factor material.
                        plain = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                        material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0_VertexColor")
                        self.assertEqual(material.GetPrim().GetDisplayName(), "Tinted")
                        plain_preview, _, _ = plain.ComputeSurfaceSource()
                        self.assertFalse(plain_preview.GetInput("diffuseColor").HasConnectedSource())
                        for node in (0, 1):
                            for primitive, name in ((0, "Material_0_VertexColor"), (1, "Material_0")):
                                mesh = UsdGeom.Mesh.Get(
                                    stage, f"/Asset/geo/Node_{node}/Mesh_0/Primitive_{primitive}")
                                bound, _ = UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()
                                self.assertEqual(bound.GetPath(), Sdf.Path("/Asset/mtl/" + name))
                                self.assertTrue(mesh.GetDoubleSidedAttr().Get())

                        preview, _, _ = material.ComputeSurfaceSource()
                        mtlx, _, _ = material.ComputeSurfaceSource("mtlx")
                        tinted = rgb != (1, 1, 1)
                        translucent = mode != "OPAQUE"
                        primvars = UsdGeom.PrimvarsAPI(
                            stage.GetPrimAtPath("/Asset/geo/Node_0/Mesh_0/Primitive_0"))

                        # Preview has no multiply node and reads a per-mesh product.
                        reader = reader_of(preview, "diffuseColor")
                        self.assertEqual(reader.GetIdAttr().Get(), "UsdPrimvarReader_float3")
                        self.assertEqual(reader.GetInput("varname").Get(),
                                         "gltf_previewBaseColor" if tinted else "displayColor")
                        self.assertTrue(Gf.IsClose(reader.GetInput("fallback").Get(),
                                                   Gf.Vec3f(*rgb), 1e-6))
                        product = primvars.GetPrimvar("gltf_previewBaseColor")
                        self.assertEqual(bool(product), tinted)
                        if tinted:
                            self.assertEqual(product.GetInterpolation(), "vertex")
                            for value, source in zip(product.Get(), vertex_rgb):
                                self.assertTrue(Gf.IsClose(
                                    value, Gf.CompMult(source, Gf.Vec3f(*rgb)), 1e-6))

                        # MaterialX multiplies in the graph, only when the factor is not 1.
                        node = reader_of(mtlx, "base_color")
                        if tinted:
                            self.assertEqual(node.GetIdAttr().Get(), "ND_multiply_color3")
                            self.assertTrue(Gf.IsClose(node.GetInput("in2").Get(),
                                                       Gf.Vec3f(*rgb), 1e-6))
                            node = reader_of(node, "in1")
                        self.assertEqual(node.GetIdAttr().Get(), "ND_geompropvalue_color3")
                        self.assertEqual(node.GetInput("geomprop").Get(), "displayColor")
                        self.assertEqual(node.GetInput("default").Get(), Gf.Vec3f(1))

                        if not translucent:
                            self.assertFalse(preview.GetInput("opacity"))
                            self.assertFalse(mtlx.GetInput("alpha"))
                            self.assertFalse(primvars.GetPrimvar("gltf_previewAlpha"))
                            continue
                        reader = reader_of(preview, "opacity")
                        self.assertEqual(reader.GetIdAttr().Get(), "UsdPrimvarReader_float")
                        self.assertEqual(reader.GetInput("varname").Get(),
                                         "gltf_colorAlpha" if alpha == 1 else "gltf_previewAlpha")
                        self.assertAlmostEqual(reader.GetInput("fallback").Get(), alpha, places=6)
                        product = primvars.GetPrimvar("gltf_previewAlpha")
                        self.assertEqual(bool(product), components == 4 and alpha != 1)
                        if product:
                            self.assertEqual(product.GetInterpolation(), "vertex")
                            for value, source in zip(product.Get(), vertex_alpha):
                                self.assertAlmostEqual(value, source * alpha, places=6)
                        self.assertEqual(primvars.GetPrimvar("gltf_colorAlpha").IsDefined(),
                                         components == 4)
                        node = reader_of(mtlx, "alpha")
                        if alpha != 1:
                            self.assertEqual(node.GetIdAttr().Get(), "ND_multiply_float")
                            self.assertAlmostEqual(node.GetInput("in2").Get(), alpha, places=6)
                            node = reader_of(node, "in1")
                        self.assertEqual(node.GetIdAttr().Get(), "ND_geompropvalue_float")
                        self.assertEqual(node.GetInput("geomprop").Get(), "gltf_colorAlpha")
                        self.assertEqual(node.GetInput("default").Get(), 1)
                        threshold = preview.GetInput("opacityThreshold")
                        if mode == "MASK":
                            self.assertAlmostEqual(threshold.Get(), 0.25, places=6)
                        else:
                            self.assertFalse(threshold)

                        self.assertTrue(stage.GetRootLayer().Reload(True))
                        self.assertTrue(stage.GetPrimAtPath("/Asset/mtl/Material_0_VertexColor"))
                        host = Usd.Stage.CreateInMemory()
                        host.DefinePrim("/Model").GetReferences().AddReference(str(path))
                        mesh = host.GetPrimAtPath("/Model/geo/Node_0/Mesh_0/Primitive_0")
                        bound, _ = UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()
                        self.assertEqual(bound.GetPath(), Sdf.Path("/Model/mtl/Material_0_VertexColor"))

    def test_alpha_modes_view_fixture(self):
        path = pathlib.Path(__file__).parent / "fixtures" / "alpha_modes.gltf"
        stage = Usd.Stage.Open(str(path))
        modes = []
        for primitive in range(7):
            mesh = stage.GetPrimAtPath(f"/Asset/geo/Node_0/Mesh_0/Primitive_{primitive}")
            material, _ = UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()
            self.assertEqual(material.GetPath(), Sdf.Path(f"/Asset/mtl/Material_{primitive}_VertexColor"))
            mtlx, _, _ = material.ComputeSurfaceSource("mtlx")
            modes.append(mtlx.GetInput("alpha_mode").Get())
            for prim in material.GetPrim().GetChildren():
                for shader in prim.GetChildren():
                    for name in ("varname", "geomprop"):
                        attribute = UsdShade.Shader(shader).GetInput(name)
                        if attribute:
                            self.assertNotIn(":", attribute.Get())
        self.assertEqual(modes, [1, 1, 1, 2, 2, 0, 0])

    def test_base_color_image_sources_and_networks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "maps").mkdir()
            (root / "maps" / "a b.png").write_bytes(PNG)
            for extension in ("gltf", "glb"):
                for kind, image, data, entry in (
                    ("data", {"uri": "data:image/png;base64," + base64.b64encode(PNG).decode()},
                     PNG, "images/0.png"),
                    ("view", None, JPEG, "images/0.jpg"),
                    ("external", {"uri": "maps/a%20b.png"}, PNG, None),
                ):
                    with self.subTest(extension=extension, kind=kind):
                        path = root / f"{kind}.{extension}"
                        document, binary = textured_document(extension, image, data)
                        write_asset(path, document, binary)
                        stage = Usd.Stage.Open(str(path))
                        expected = f"./{path.name}[{entry}]" if entry else "./maps/a b.png"

                        material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                        preview, _, _ = material.ComputeSurfaceSource()
                        self.assertEqual(preview.GetPath().name, "surface")
                        source = preview.GetInput("diffuseColor").GetConnectedSource()[0]
                        texture = UsdShade.Shader(source.GetPrim())
                        self.assertEqual(texture.GetIdAttr().Get(), "UsdUVTexture")
                        self.assertEqual(texture.GetPath().name, "baseColorTexture")
                        self.assertEqual(texture.GetInput("sourceColorSpace").Get(), "sRGB")
                        self.assertEqual(texture.GetInput("wrapS").Get(), "repeat")
                        self.assertEqual(texture.GetInput("wrapT").Get(), "repeat")
                        self.assertEqual(texture.GetInput("scale").Get(), Gf.Vec4f(1))
                        reader = texture.GetInput("st").GetConnectedSource()[0]
                        self.assertEqual(UsdShade.Shader(reader.GetPrim()).GetInput("varname").Get(), "st")
                        self.assertFalse(preview.GetInput("opacity"))

                        mtlx, _, _ = material.ComputeSurfaceSource("mtlx")
                        rgb = UsdShade.Shader(mtlx.GetInput("base_color").GetConnectedSource()[0].GetPrim())
                        self.assertEqual(rgb.GetIdAttr().Get(), "ND_convert_color4_color3")
                        node = UsdShade.Shader(rgb.GetInput("in").GetConnectedSource()[0].GetPrim())
                        self.assertEqual(node.GetIdAttr().Get(), "ND_image_color4")
                        self.assertEqual(node.GetInput("file").GetAttr().GetColorSpace(), "srgb_texture")
                        self.assertEqual(node.GetInput("uaddressmode").Get(), "periodic")
                        self.assertEqual(node.GetInput("vaddressmode").Get(), "periodic")
                        coordinates = UsdShade.Shader(node.GetInput("texcoord").GetConnectedSource()[0].GetPrim())
                        self.assertEqual(coordinates.GetIdAttr().Get(), "ND_geompropvalue_vector2")
                        self.assertEqual(coordinates.GetInput("geomprop").Get(), "st")
                        self.assertFalse(mtlx.GetInput("alpha"))

                        for shader in (texture, node):
                            asset = shader.GetInput("file").Get()
                            self.assertEqual(asset.path, expected)
                            self.assertEqual(asset_bytes(asset.resolvedPath), data)

                        stage.GetRootLayer().Reload(True)
                        asset = texture.GetInput("file").Get()
                        self.assertEqual(asset_bytes(asset.resolvedPath), data)

    def test_base_color_sampler_uv_set_factor_and_alpha(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = root / ("sampler." + extension)
                    document, binary = textured_document(extension, None, PNG)
                    document["samplers"] = [{"wrapS": 33648, "wrapT": 33071}]
                    document["textures"][0]["sampler"] = 0
                    material = document["materials"][0]
                    material["alphaMode"] = "MASK"
                    material["alphaCutoff"] = 0.25
                    pbr = material["pbrMetallicRoughness"]
                    pbr["baseColorFactor"] = [0.5, 0.25, 0.75, 0.5]
                    pbr["baseColorTexture"]["texCoord"] = 2
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    usd_material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")

                    preview, _, _ = usd_material.ComputeSurfaceSource()
                    self.assertAlmostEqual(preview.GetInput("opacityThreshold").Get(), 0.25)
                    alpha_source, alpha_output, _ = preview.GetInput("opacity").GetConnectedSource()
                    self.assertEqual(alpha_output, "a")
                    texture = UsdShade.Shader(alpha_source.GetPrim())
                    self.assertEqual(texture.GetInput("wrapS").Get(), "mirror")
                    self.assertEqual(texture.GetInput("wrapT").Get(), "clamp")
                    self.assertEqual(texture.GetInput("scale").Get(), Gf.Vec4f(0.5, 0.25, 0.75, 0.5))
                    reader = texture.GetInput("st").GetConnectedSource()[0]
                    self.assertEqual(UsdShade.Shader(reader.GetPrim()).GetInput("varname").Get(), "st2")

                    mtlx, _, _ = usd_material.ComputeSurfaceSource("mtlx")
                    factor = UsdShade.Shader(mtlx.GetInput("base_color").GetConnectedSource()[0].GetPrim())
                    self.assertEqual(factor.GetPath().name, "baseColorFactor")
                    self.assertEqual(factor.GetInput("in2").Get(), Gf.Vec3f(0.5, 0.25, 0.75))
                    alpha_factor = UsdShade.Shader(mtlx.GetInput("alpha").GetConnectedSource()[0].GetPrim())
                    self.assertEqual(alpha_factor.GetPath().name, "alphaFactor")
                    self.assertAlmostEqual(alpha_factor.GetInput("in2").Get(), 0.5)
                    extract = UsdShade.Shader(alpha_factor.GetInput("in1").GetConnectedSource()[0].GetPrim())
                    self.assertEqual(extract.GetIdAttr().Get(), "ND_extract_color4")
                    self.assertEqual(extract.GetInput("index").Get(), 3)
                    image = UsdShade.Shader(extract.GetInput("in").GetConnectedSource()[0].GetPrim())
                    self.assertEqual(image.GetInput("uaddressmode").Get(), "mirror")
                    self.assertEqual(image.GetInput("vaddressmode").Get(), "clamp")
                    coordinates = UsdShade.Shader(image.GetInput("texcoord").GetConnectedSource()[0].GetPrim())
                    self.assertEqual(coordinates.GetInput("geomprop").Get(), "st2")

    def test_base_color_texture_with_vertex_colors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = root / ("colors." + extension)
                    document, binary = textured_document(extension, None, PNG)
                    primitive = document["meshes"][0]["primitives"][0]
                    # The UV accessors are not colors, so build a VEC3 color accessor.
                    colors = struct.pack("<9f", 1, 0, 0, 0, 1, 0, 0, 0, 1)
                    binary = append_view(document, binary, extension, colors)
                    document["accessors"].append({
                        "bufferView": len(document["bufferViews"]) - 1,
                        "componentType": 5126, "count": 3, "type": "VEC3",
                    })
                    primitive["attributes"]["COLOR_0"] = len(document["accessors"]) - 1
                    document["materials"][0]["alphaMode"] = "BLEND"
                    document["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"] = [1, 0.5, 1, 1]
                    write_asset(path, document, binary)

                    stage = Usd.Stage.Open(str(path))
                    material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0_VertexColor")
                    preview, _, _ = material.ComputeSurfaceSource()
                    source = preview.GetInput("diffuseColor").GetConnectedSource()[0]
                    self.assertEqual(source.GetPath().name, "baseColorTexture")
                    mesh = stage.GetPrimAtPath("/Asset/geo/Node_0/Mesh_0/Primitive_0")
                    self.assertFalse(UsdGeom.PrimvarsAPI(mesh).HasPrimvar("gltf_previewBaseColor"))

                    mtlx, _, _ = material.ComputeSurfaceSource("mtlx")
                    factor = UsdShade.Shader(mtlx.GetInput("base_color").GetConnectedSource()[0].GetPrim())
                    self.assertEqual(factor.GetPath().name, "baseColorFactor")
                    product = UsdShade.Shader(factor.GetInput("in1").GetConnectedSource()[0].GetPrim())
                    self.assertEqual(product.GetPath().name, "baseColorVertex")
                    self.assertEqual(product.GetInput("in1").GetConnectedSource()[0].GetPath().name, "baseColorRgb")
                    self.assertEqual(product.GetInput("in2").GetConnectedSource()[0].GetPath().name, "displayColorReader")
                    alpha = UsdShade.Shader(mtlx.GetInput("alpha").GetConnectedSource()[0].GetPrim())
                    self.assertEqual(alpha.GetPath().name, "alphaVertex")
                    self.assertEqual(alpha.GetInput("in1").GetConnectedSource()[0].GetPath().name, "baseColorA")
                    self.assertEqual(alpha.GetInput("in2").GetConnectedSource()[0].GetPath().name, "colorAlphaReader")

                    result = subprocess.run(
                        [sys.executable, "-c",
                         "from pxr import Usd; import sys; assert Usd.Stage.Open(sys.argv[1])", str(path)],
                        capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("ignores COLOR_0", result.stderr)

    def test_images_resolve_after_moving_and_through_references(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                for number, name in enumerate(("model", "a b", "モデル", "bracket[1]")):
                    with self.subTest(extension=extension, name=name):
                        source = root / "source" / extension
                        source.mkdir(parents=True, exist_ok=True)
                        document, binary = textured_document(extension, None, PNG)
                        original = source / f"{name}.{extension}"
                        write_asset(original, document, binary)

                        moved = root / "moved" / f"{extension}-{name}"
                        moved.mkdir(parents=True)
                        target = moved / original.name
                        target.write_bytes(original.read_bytes())
                        original.unlink()

                        stage = Usd.Stage.Open(str(target))
                        texture = UsdShade.Shader.Get(
                            stage, "/Asset/mtl/Material_0/preview/baseColorTexture")
                        asset = texture.GetInput("file").Get()
                        self.assertEqual(asset.path, Ar.JoinPackageRelativePath(
                            f"./{target.name}", "images/0.png"))
                        self.assertEqual(asset_bytes(asset.resolvedPath), PNG)

                        host = root / f"host-{extension}-{number}.usda"
                        host.write_text(
                            '#usda 1.0\n(\n    defaultPrim = "Root"\n)\n\n'
                            'def Xform "Root" (\n'
                            f'    references = @./{target.relative_to(root).as_posix()}@\n'
                            ')\n{\n}\n',
                            encoding="utf-8",
                        )
                        composed = Usd.Stage.Open(str(host))
                        texture = UsdShade.Shader.Get(
                            composed, "/Root/mtl/Material_0/preview/baseColorTexture")
                        self.assertEqual(asset_bytes(texture.GetInput("file").Get().resolvedPath), PNG)

    def test_metallic_roughness_occlusion_and_emissive_networks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = root / ("pbr." + extension)
                    document, binary = textured_document(extension, None, PNG)
                    document["samplers"] = [{"wrapS": 33648, "wrapT": 33071}]
                    document["textures"].append({"source": 0, "sampler": 0})
                    material = document["materials"][0]
                    material["pbrMetallicRoughness"].update(
                        metallicFactor=0.5, roughnessFactor=0.25,
                        metallicRoughnessTexture={"index": 1, "texCoord": 1})
                    material["occlusionTexture"] = {"index": 0, "texCoord": 2, "strength": 0.25}
                    material["emissiveTexture"] = {"index": 0, "texCoord": 1}
                    material["emissiveFactor"] = [0.5, 0.25, 1]
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    usd_material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                    expected = f"./{path.name}[images/0.png]"

                    def source_of(shader, name):
                        source, output, _ = shader.GetInput(name).GetConnectedSource()
                        return UsdShade.Shader(source.GetPrim()), output

                    preview, _, _ = usd_material.ComputeSurfaceSource()
                    metallic, metallic_output = source_of(preview, "metallic")
                    roughness, roughness_output = source_of(preview, "roughness")
                    self.assertEqual(metallic.GetPath(), roughness.GetPath())
                    self.assertEqual(metallic.GetPath().name, "metallicRoughnessTexture")
                    self.assertEqual((roughness_output, metallic_output), ("g", "b"))
                    self.assertEqual(metallic.GetInput("sourceColorSpace").Get(), "raw")
                    self.assertEqual(metallic.GetInput("scale").Get(), Gf.Vec4f(1, 0.25, 0.5, 1))
                    self.assertFalse(metallic.GetInput("bias"))
                    self.assertEqual(metallic.GetInput("wrapS").Get(), "mirror")
                    self.assertEqual(metallic.GetInput("wrapT").Get(), "clamp")
                    self.assertEqual(source_of(metallic, "st")[0].GetInput("varname").Get(), "st1")

                    occlusion, occlusion_output = source_of(preview, "occlusion")
                    self.assertEqual(occlusion.GetPath().name, "occlusionTexture")
                    self.assertEqual(occlusion_output, "r")
                    self.assertEqual(occlusion.GetInput("sourceColorSpace").Get(), "raw")
                    self.assertEqual(occlusion.GetInput("scale").Get(), Gf.Vec4f(0.25))
                    self.assertEqual(occlusion.GetInput("bias").Get(), Gf.Vec4f(0.75))
                    self.assertEqual(occlusion.GetInput("wrapS").Get(), "repeat")
                    self.assertEqual(source_of(occlusion, "st")[0].GetInput("varname").Get(), "st2")

                    emissive, emissive_output = source_of(preview, "emissiveColor")
                    self.assertEqual(emissive.GetPath().name, "emissiveTexture")
                    self.assertEqual(emissive_output, "rgb")
                    self.assertEqual(emissive.GetInput("sourceColorSpace").Get(), "sRGB")
                    self.assertEqual(emissive.GetInput("scale").Get(), Gf.Vec4f(0.5, 0.25, 1, 1))
                    # Sampling one UV set twice reads it through one node.
                    self.assertEqual(source_of(emissive, "st")[0].GetPath(), source_of(metallic, "st")[0].GetPath())

                    base, _ = source_of(preview, "diffuseColor")
                    self.assertEqual(base.GetInput("sourceColorSpace").Get(), "sRGB")
                    self.assertEqual(source_of(base, "st")[0].GetInput("varname").Get(), "st")

                    mtlx, _, _ = usd_material.ComputeSurfaceSource("mtlx")
                    metallic_factor, _ = source_of(mtlx, "metallic")
                    self.assertEqual(metallic_factor.GetPath().name, "metallicFactor")
                    self.assertEqual(metallic_factor.GetIdAttr().Get(), "ND_multiply_float")
                    self.assertAlmostEqual(metallic_factor.GetInput("in2").Get(), 0.5)
                    metallic_channel, _ = source_of(metallic_factor, "in1")
                    self.assertEqual(metallic_channel.GetIdAttr().Get(), "ND_extract_vector4")
                    self.assertEqual(metallic_channel.GetInput("index").Get(), 2)
                    roughness_factor, _ = source_of(mtlx, "roughness")
                    self.assertAlmostEqual(roughness_factor.GetInput("in2").Get(), 0.25)
                    roughness_channel, _ = source_of(roughness_factor, "in1")
                    self.assertEqual(roughness_channel.GetInput("index").Get(), 1)
                    image, _ = source_of(roughness_channel, "in")
                    self.assertEqual(image.GetPath().name, "metallicRoughnessImage")
                    self.assertEqual(image.GetIdAttr().Get(), "ND_image_vector4")
                    self.assertEqual(image.GetPath(), source_of(metallic_channel, "in")[0].GetPath())
                    self.assertFalse(image.GetInput("file").GetAttr().HasColorSpace())
                    self.assertEqual(image.GetInput("uaddressmode").Get(), "mirror")
                    self.assertEqual(image.GetInput("vaddressmode").Get(), "clamp")
                    self.assertEqual(source_of(image, "texcoord")[0].GetInput("geomprop").Get(), "st1")

                    mix, _ = source_of(mtlx, "occlusion")
                    self.assertEqual(mix.GetIdAttr().Get(), "ND_mix_float")
                    self.assertAlmostEqual(mix.GetInput("bg").Get(), 1)
                    self.assertAlmostEqual(mix.GetInput("mix").Get(), 0.25)
                    occlusion_channel, _ = source_of(mix, "fg")
                    self.assertEqual(occlusion_channel.GetInput("index").Get(), 0)
                    occlusion_image, _ = source_of(occlusion_channel, "in")
                    self.assertEqual(occlusion_image.GetIdAttr().Get(), "ND_image_vector4")
                    self.assertFalse(occlusion_image.GetInput("file").GetAttr().HasColorSpace())
                    self.assertEqual(source_of(occlusion_image, "texcoord")[0].GetInput("geomprop").Get(), "st2")

                    emissive_factor, _ = source_of(mtlx, "emissive")
                    self.assertEqual(emissive_factor.GetIdAttr().Get(), "ND_multiply_color3")
                    self.assertEqual(emissive_factor.GetInput("in2").Get(), Gf.Vec3f(0.5, 0.25, 1))
                    emissive_rgb, _ = source_of(emissive_factor, "in1")
                    self.assertEqual(emissive_rgb.GetIdAttr().Get(), "ND_convert_color4_color3")
                    emissive_image, _ = source_of(emissive_rgb, "in")
                    self.assertEqual(emissive_image.GetIdAttr().Get(), "ND_image_color4")
                    self.assertEqual(emissive_image.GetInput("file").GetAttr().GetColorSpace(), "srgb_texture")

                    for shader in (metallic, occlusion, emissive, image, occlusion_image, emissive_image):
                        asset = shader.GetInput("file").Get()
                        self.assertEqual(asset.path, expected)
                        self.assertEqual(asset_bytes(asset.resolvedPath), PNG)

                    stage.GetRootLayer().Reload(True)
                    self.assertEqual(asset_bytes(metallic.GetInput("file").Get().resolvedPath), PNG)

    def test_pbr_textures_without_terms_they_do_not_need(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            path = root / "pbr.gltf"
            document, binary = textured_document("gltf", None, PNG)
            material = document["materials"][0]
            material["pbrMetallicRoughness"] = {"metallicRoughnessTexture": {"index": 0}}
            material["occlusionTexture"] = {"index": 0}
            # The default emissive factor is black, and a texture does not change that.
            material["emissiveTexture"] = {"index": 0}
            document["materials"].append({})
            document["meshes"][0]["primitives"][1]["material"] = 1
            write_asset(path, document, binary)
            stage = Usd.Stage.Open(str(path))
            usd_material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")

            preview, _, _ = usd_material.ComputeSurfaceSource()
            self.assertFalse(preview.GetInput("diffuseColor").HasConnectedSource())
            occlusion = UsdShade.Shader(preview.GetInput("occlusion").GetConnectedSource()[0].GetPrim())
            self.assertEqual(occlusion.GetInput("scale").Get(), Gf.Vec4f(1))
            self.assertFalse(occlusion.GetInput("bias"))
            emissive = UsdShade.Shader(preview.GetInput("emissiveColor").GetConnectedSource()[0].GetPrim())
            self.assertEqual(emissive.GetInput("scale").Get(), Gf.Vec4f(0, 0, 0, 1))

            mtlx, _, _ = usd_material.ComputeSurfaceSource("mtlx")
            self.assertEqual(mtlx.GetInput("metallic").GetConnectedSource()[0].GetPath().name, "metallicChannel")
            self.assertEqual(mtlx.GetInput("roughness").GetConnectedSource()[0].GetPath().name, "roughnessChannel")
            self.assertEqual(mtlx.GetInput("occlusion").GetConnectedSource()[0].GetPath().name, "occlusionChannel")
            self.assertEqual(mtlx.GetInput("emissive").GetConnectedSource()[0].GetPath().name, "emissiveFactor")

            # A material without these textures authors no occlusion input.
            plain = UsdShade.Material.Get(stage, "/Asset/mtl/Material_1")
            self.assertFalse(plain.ComputeSurfaceSource()[0].GetInput("occlusion"))
            self.assertFalse(plain.ComputeSurfaceSource("mtlx")[0].GetInput("occlusion"))

    def test_pbr_textures_view_fixture(self):
        path = pathlib.Path(__file__).parent / "fixtures" / "pbr_textures.gltf"
        stage = Usd.Stage.Open(str(path))
        expected = ("metallicRoughness",) * 2 + ("occlusion",) * 2 + ("emissive",) * 2
        for primitive, slot in enumerate(expected):
            mesh = stage.GetPrimAtPath(f"/Asset/geo/Node_0/Mesh_0/Primitive_{primitive}")
            material, _ = UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()
            texture = UsdShade.Shader.Get(
                stage, material.GetPath().AppendPath(Sdf.Path(f"preview/{slot}Texture")))
            image = UsdShade.Shader.Get(
                stage, material.GetPath().AppendPath(Sdf.Path(f"mtlx/{slot}Image")))
            for shader in (texture, image):
                asset = shader.GetInput("file").Get()
                self.assertTrue(asset.resolvedPath, asset.path)
                self.assertTrue(asset_bytes(asset.resolvedPath))

    def test_pbr_texture_failures_preserve_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                path = root / ("asset." + extension)

                def pbr_document():
                    document, binary = uv_document(extension)
                    document["images"] = [{"uri": "data:image/png;base64," + base64.b64encode(PNG).decode()},
                                          {"uri": "data:image/png;base64,AAAAAAAAAAAA"}]
                    document["textures"] = [{"source": 0}, {"source": 1}]
                    document["materials"] = [{
                        "pbrMetallicRoughness": {"metallicRoughnessTexture": {"index": 0}},
                        "normalTexture": {"index": 0},
                        "occlusionTexture": {"index": 0},
                        "emissiveTexture": {"index": 0},
                    }]
                    document["meshes"][0]["primitives"][0]["material"] = 0
                    return document, binary

                document, binary = pbr_document()
                write_asset(path, document, binary)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer_text(layer)

                def slots(material):
                    return (("metallic", material["pbrMetallicRoughness"]["metallicRoughnessTexture"]),
                            ("normal", material["normalTexture"]),
                            ("occlusion", material["occlusionTexture"]),
                            ("emissive", material["emissiveTexture"]))

                for index in range(4):
                    name = slots(pbr_document()[0]["materials"][0])[index][0]
                    for case, change, message in (
                        ("uv set", {"texCoord": 3}, "texture coordinate set the primitive does not have"),
                        ("corrupt image", {"index": 1}, "embedded image is not PNG, JPEG or KTX2"),
                    ):
                        with self.subTest(extension=extension, slot=name, case=case):
                            invalid, invalid_binary = pbr_document()
                            slots(invalid["materials"][0])[index][1].update(change)
                            write_asset(path, invalid, invalid_binary)
                            with self.assertRaisesRegex(Tf.ErrorException, message):
                                layer.Reload(True)
                            self.assertEqual(layer_text(layer), before)

                for strength in (-0.1, 1.1):
                    with self.subTest(extension=extension, strength=strength):
                        invalid, invalid_binary = pbr_document()
                        invalid["materials"][0]["occlusionTexture"]["strength"] = strength
                        write_asset(path, invalid, invalid_binary)
                        with self.assertRaisesRegex(Tf.ErrorException, "occlusion strength"):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)

                with self.subTest(extension=extension, scale="overflow"):
                    invalid, invalid_binary = pbr_document()
                    invalid["materials"][0]["normalTexture"]["scale"] = 1e39
                    write_asset(path, invalid, invalid_binary)
                    with self.assertRaisesRegex(Tf.ErrorException, "normal scale is non-finite|cannot parse"):
                        layer.Reload(True)
                    self.assertEqual(layer_text(layer), before)

    def test_normal_texture_networks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = root / ("normal." + extension)
                    document, binary = textured_document(extension, None, PNG)
                    document["samplers"] = [{"wrapS": 33648, "wrapT": 33071}]
                    document["textures"].append({"source": 0, "sampler": 0})
                    document["materials"][0]["normalTexture"] = {"index": 1, "texCoord": 1, "scale": 0.5}
                    document["materials"].append({"normalTexture": {"index": 0}})
                    document["meshes"][0]["primitives"][1]["material"] = 1
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    usd_material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")

                    def source_of(shader, name):
                        source, output, _ = shader.GetInput(name).GetConnectedSource()
                        return UsdShade.Shader(source.GetPrim()), output

                    preview, _, _ = usd_material.ComputeSurfaceSource()
                    self.assertEqual(preview.GetInput("normal").GetTypeName(), Sdf.ValueTypeNames.Normal3f)
                    self.assertEqual(preview.GetInput("normal").Get(), Gf.Vec3f(0, 0, 1))
                    texture, output = source_of(preview, "normal")
                    self.assertEqual(texture.GetPath().name, "normalTexture")
                    self.assertEqual(output, "rgb")
                    self.assertEqual(texture.GetInput("sourceColorSpace").Get(), "raw")
                    self.assertEqual(texture.GetInput("scale").Get(), Gf.Vec4f(1, 1, 2, 1))
                    self.assertEqual(texture.GetInput("bias").Get(), Gf.Vec4f(-0.5, -0.5, -1, 0))
                    self.assertEqual(texture.GetInput("wrapS").Get(), "mirror")
                    self.assertEqual(texture.GetInput("wrapT").Get(), "clamp")
                    self.assertEqual(source_of(texture, "st")[0].GetInput("varname").Get(), "st1")
                    asset = texture.GetInput("file").Get()
                    self.assertEqual(asset_bytes(asset.resolvedPath), PNG)

                    mtlx, _, _ = usd_material.ComputeSurfaceSource("mtlx")
                    self.assertEqual(mtlx.GetInput("normal").GetTypeName(), Sdf.ValueTypeNames.Vector3f)
                    normal_map, _ = source_of(mtlx, "normal")
                    self.assertEqual(normal_map.GetPath().name, "normalMap")
                    self.assertEqual(normal_map.GetIdAttr().Get(), "ND_normalmap_float")
                    self.assertAlmostEqual(normal_map.GetInput("scale").Get(), 0.5)
                    xyz, _ = source_of(normal_map, "in")
                    self.assertEqual(xyz.GetIdAttr().Get(), "ND_convert_vector4_vector3")
                    image, _ = source_of(xyz, "in")
                    self.assertEqual(image.GetPath().name, "normalImage")
                    self.assertEqual(image.GetIdAttr().Get(), "ND_image_vector4")
                    self.assertFalse(image.GetInput("file").GetAttr().HasColorSpace())
                    self.assertEqual(image.GetInput("uaddressmode").Get(), "mirror")
                    self.assertEqual(image.GetInput("vaddressmode").Get(), "clamp")
                    self.assertEqual(source_of(image, "texcoord")[0].GetInput("geomprop").Get(), "st1")
                    self.assertEqual(asset_bytes(image.GetInput("file").Get().resolvedPath), PNG)

                    # The default scale needs no terms of its own.
                    plain = UsdShade.Material.Get(stage, "/Asset/mtl/Material_1")
                    plain_texture, _ = source_of(plain.ComputeSurfaceSource()[0], "normal")
                    self.assertEqual(plain_texture.GetInput("scale").Get(), Gf.Vec4f(2, 2, 2, 1))
                    self.assertEqual(plain_texture.GetInput("bias").Get(), Gf.Vec4f(-1, -1, -1, 0))
                    plain_map, _ = source_of(plain.ComputeSurfaceSource("mtlx")[0], "normal")
                    self.assertFalse(plain_map.GetInput("scale"))

                    stage.GetRootLayer().Reload(True)
                    self.assertEqual(asset_bytes(texture.GetInput("file").Get().resolvedPath), PNG)

    def test_materials_without_a_normal_texture_author_no_normal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "plain.gltf"
            document, binary = textured_document("gltf", None, PNG)
            write_asset(path, document, binary)
            stage = Usd.Stage.Open(str(path))
            material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
            self.assertFalse(material.ComputeSurfaceSource()[0].GetInput("normal"))
            self.assertFalse(material.ComputeSurfaceSource("mtlx")[0].GetInput("normal"))

    def test_normal_textures_view_fixture(self):
        fixtures = pathlib.Path(__file__).parent / "fixtures"
        stage = Usd.Stage.Open(str(fixtures / "normal_textures.gltf"))
        reference = Usd.Stage.Open(str(fixtures / "normal_textures_reference.gltf"))
        count = len(stage.GetPrimAtPath("/Asset/mtl").GetChildren())
        self.assertEqual(len(reference.GetPrimAtPath("/Asset/mtl").GetChildren()), count)
        for index in range(count):
            material = stage.GetPrimAtPath(f"/Asset/mtl/Material_{index}").GetPath()
            texture = UsdShade.Shader.Get(stage, material.AppendPath(Sdf.Path("preview/normalTexture")))
            image = UsdShade.Shader.Get(stage, material.AppendPath(Sdf.Path("mtlx/normalImage")))
            for shader in (texture, image):
                asset = shader.GetInput("file").Get()
                self.assertTrue(asset.resolvedPath, asset.path)
                self.assertTrue(asset_bytes(asset.resolvedPath))
            self.assertFalse(reference.GetPrimAtPath(material.AppendPath(Sdf.Path("preview/normalTexture"))))

    def test_tangents_are_retained(self):
        values = ((1, 0, 0, 1), (0, 1, 0, -1), (0, 0, 1, 1))
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = root / ("tangent." + extension)
                    document, binary = uv_document(extension)
                    binary = append_view(document, binary, extension,
                                         struct.pack("<12f", *[c for v in values for c in v]))
                    document["accessors"].append({"bufferView": len(document["bufferViews"]) - 1,
                                                  "componentType": 5126, "count": 3, "type": "VEC4"})
                    primitives = document["meshes"][0]["primitives"]
                    primitives[0]["attributes"]["TANGENT"] = len(document["accessors"]) - 1
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    mesh = stage.GetPrimAtPath("/Asset/geo/Node_0/Mesh_0/Primitive_0")
                    primvar = UsdGeom.PrimvarsAPI(mesh).GetPrimvar("gltf_tangent")
                    self.assertEqual(primvar.GetTypeName(), Sdf.ValueTypeNames.Float4Array)
                    self.assertEqual(primvar.GetInterpolation(), UsdGeom.Tokens.vertex)
                    self.assertEqual([tuple(v) for v in primvar.Get()], list(values))
                    other = stage.GetPrimAtPath("/Asset/geo/Node_0/Mesh_0/Primitive_1")
                    self.assertFalse(UsdGeom.PrimvarsAPI(other).GetPrimvar("gltf_tangent"))
                    self.assertTrue(stage.GetRootLayer().Reload(True))
                    self.assertEqual([tuple(v) for v in primvar.Get()], list(values))

    def test_tangent_failures_preserve_layer(self):
        good = struct.pack("<12f", *((1, 0, 0, 1) * 3))
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                path = root / ("tangent." + extension)

                def tangent_document(data=good, **accessor):
                    document, binary = uv_document(extension)
                    binary = append_view(document, binary, extension, data)
                    document["accessors"].append({"bufferView": len(document["bufferViews"]) - 1,
                                                  "componentType": 5126, "count": 3, "type": "VEC4", **accessor})
                    document["meshes"][0]["primitives"][0]["attributes"]["TANGENT"] = (
                        len(document["accessors"]) - 1)
                    return document, binary

                write_asset(path, *tangent_document())
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer_text(layer)
                for case, arguments, message in (
                    ("type", {"type": "VEC3"}, "invalid type"),
                    ("component", {"componentType": 5123}, "invalid type"),
                    ("count", {"count": 2}, "tangent and position counts differ"),
                    ("non-finite", {"data": struct.pack("<12f", *((1, 0, 0, 1) * 2), float("nan"), 0, 0, 1)},
                     "tangent contains a non-finite value"),
                ):
                    with self.subTest(extension=extension, case=case):
                        write_asset(path, *tangent_document(**arguments))
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)

    def test_package_resolver_rejects_invalid_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "external.png").write_bytes(PNG)
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = root / ("package." + extension)
                    document, binary = textured_document(extension, None, PNG)
                    document["images"].append({"uri": "external.png"})
                    write_asset(path, document, binary)
                    resolver = Ar.GetResolver()
                    self.assertTrue(resolver.Resolve(f"{path}[images/0.png]"))
                    for entry in ("images/0.jpg", "images/1.png", "images/2.png", "images/00.png",
                                  "images/0.png.png", "images/-1.png", "images/.png", "0.png",
                                  "../external.png", "images/../external.png"):
                        self.assertFalse(resolver.Resolve(f"{path}[{entry}]"), entry)

    def test_base_color_textures_view_fixture(self):
        path = pathlib.Path(__file__).parent / "fixtures" / "base_color_textures.gltf"
        stage = Usd.Stage.Open(str(path))
        for primitive in range(9):
            mesh = stage.GetPrimAtPath(f"/Asset/geo/Node_0/Mesh_0/Primitive_{primitive}")
            material, _ = UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()
            texture = UsdShade.Shader.Get(
                stage, material.GetPath().AppendPath(Sdf.Path("preview/baseColorTexture")))
            image = UsdShade.Shader.Get(
                stage, material.GetPath().AppendPath(Sdf.Path("mtlx/baseColorImage")))
            for shader in (texture, image):
                asset = shader.GetInput("file").Get()
                self.assertTrue(asset.resolvedPath, asset.path)
                self.assertTrue(asset_bytes(asset.resolvedPath))

    def test_ktx2_textures_view_fixture(self):
        path = pathlib.Path(__file__).parent / "fixtures" / "ktx2_textures.gltf"
        stage = Usd.Stage.Open(str(path))
        for primitive in range(6):
            mesh = stage.GetPrimAtPath(f"/Asset/geo/Node_0/Mesh_0/Primitive_{primitive}")
            material, _ = UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()
            texture = UsdShade.Shader.Get(
                stage, material.GetPath().AppendPath(Sdf.Path("preview/baseColorTexture")))
            asset = texture.GetInput("file").Get()
            self.assertTrue(asset.resolvedPath, asset.path)
            self.assertTrue(asset_bytes(asset.resolvedPath))

    def test_ktx2_images_are_served_as_png(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for name, alpha in (("etc1s", False), ("etc1s_alpha", True), ("etc1s_mips", False),
                                ("uastc_zstd", False), ("uastc_alpha", True)):
                data = ktx2_fixture(name)
                (root / (name + ".ktx2")).write_bytes(data)
                encoded = base64.b64encode(data).decode()
                for extension in ("gltf", "glb"):
                    for source, image in (
                        ("bufferView", None),
                        ("data URI", {"uri": "data:image/ktx2;base64," + encoded}),
                        ("file", {"uri": name + ".ktx2", "mimeType": "image/ktx2"}),
                    ):
                        with self.subTest(name=name, extension=extension, source=source):
                            path = root / f"{name}_{source[0]}.{extension}"
                            document, binary = ktx2_document(extension, image, data)
                            write_asset(path, document, binary)
                            stage = Usd.Stage.Open(str(path))
                            asset = base_color_file(stage)
                            self.assertEqual(asset.path, f"./{path.name}[images/0.png]")
                            self.assert_quadrants(asset_bytes(asset.resolvedPath), alpha)

    def assert_quadrants(self, png, alpha):
        width, height, pixels = read_png(png)
        self.assertEqual((width, height), (16, 16))
        low_alpha = 64 if alpha else 255
        for (x, y), expected in (((4, 4), (255, 0, 0, 255)), ((12, 4), (0, 255, 0, 255)),
                                 ((4, 12), (0, 0, 255, low_alpha)),
                                 ((12, 12), (255, 255, 255, low_alpha))):
            actual = pixels[y][x]
            for channel in range(4):
                self.assertAlmostEqual(actual[channel], expected[channel], delta=16,
                                       msg=f"pixel {x}, {y}: {actual}")

    def test_ktx2_is_preferred_over_its_fallback_and_does_not_warn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = root / ("asset." + extension)
                    document, binary = ktx2_document(extension, None, ktx2_fixture("etc1s"))
                    # Optional, with a plain source for clients without the extension.
                    del document["extensionsRequired"]
                    document["images"].append({"uri": "data:image/png;base64,"
                                               + base64.b64encode(PNG).decode()})
                    document["textures"][0]["source"] = 1
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    self.assertEqual(base_color_file(stage).path, f"./{path.name}[images/0.png]")
                    result = subprocess.run(
                        [sys.executable, "-c",
                         "import sys\nfrom pxr import Usd\nUsd.Stage.Open(sys.argv[1])",
                         str(path)],
                        capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn("KHR_texture_basisu", result.stderr)

    def test_ktx2_file_resolves_after_moving_and_on_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "image.ktx2").write_bytes(ktx2_fixture("etc1s"))
            document, binary = ktx2_document("gltf", {"uri": "image.ktx2"}, b"")
            write_asset(source / "asset.gltf", document, binary)
            moved = root / "moved"
            source.rename(moved)
            stage = Usd.Stage.Open(str(moved / "asset.gltf"))
            asset = base_color_file(stage)
            self.assert_quadrants(asset_bytes(asset.resolvedPath), False)

            # The same request is answered from the file's current content.
            (moved / "image.ktx2").write_bytes(ktx2_fixture("uastc_alpha"))
            self.assert_quadrants(asset_bytes(asset.resolvedPath), True)

    def test_ktx2_failures_preserve_layer(self):
        valid = ktx2_fixture("etc1s")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                path = root / ("asset." + extension)
                document, binary = ktx2_document(extension, None, valid)
                write_asset(path, document, binary)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer_text(layer)
                for case, data, message in (
                    ("signature only", b"\xabKTX 20\xbb\r\n\x1a\n", "KTX2 image is damaged"),
                    ("truncated", valid[:len(valid) // 2], "KTX2 image is damaged"),
                    ("other codec", ktx2_fixture("xuastc"), "KTX2 image is not ETC1S or UASTC"),
                    ("cubemap", patch_ktx2_header(valid, 36, 6), "not a single 2D image"),
                    ("array", patch_ktx2_header(valid, 32, 2), "not a single 2D image"),
                    ("volume", patch_ktx2_header(valid, 28, 2), "KTX2 image is damaged"),
                    ("inconsistent size", patch_ktx2_header(valid, 20, 20000), "KTX2 image is damaged"),
                    ("too wide", oversized_ktx2(), "KTX2 image is damaged"),
                    ("orientation", ktx2_with_orientation(valid, b"ru"), "orientation is not supported"),
                ):
                    with self.subTest(extension=extension, case=case):
                        invalid, invalid_binary = ktx2_document(extension, None, data)
                        write_asset(path, invalid, invalid_binary)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)
                with self.subTest(extension=extension, case="plain source"):
                    invalid, invalid_binary = ktx2_document(extension, None, valid)
                    invalid["textures"][0] = {"source": 0}
                    write_asset(path, invalid, invalid_binary)
                    with self.assertRaisesRegex(Tf.ErrorException, "requires KHR_texture_basisu"):
                        layer.Reload(True)
                    self.assertEqual(layer_text(layer), before)

    def test_ktx2_orientation_required_by_the_extension_is_accepted(self):
        data = ktx2_with_orientation(ktx2_fixture("etc1s"), b"rd")
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "asset.glb"
            document, binary = ktx2_document("glb", None, data)
            write_asset(path, document, binary)
            asset = base_color_file(Usd.Stage.Open(str(path)))
            self.assert_quadrants(asset_bytes(asset.resolvedPath), False)

    def test_damaged_ktx2_body_fails_when_read(self):
        # The header is valid, so import succeeds and the image's reader reports it.
        valid = ktx2_fixture("uastc_zstd")
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "asset.glb"
            data = bytearray(valid)
            data[-8:] = b"\xff" * 8
            document, binary = ktx2_document("glb", None, bytes(data))
            write_asset(path, document, binary)
            asset = base_color_file(Usd.Stage.Open(str(path)))
            self.assertIsNone(Ar.GetResolver().OpenAsset(Ar.ResolvedPath(asset.resolvedPath)))

    def test_damaged_and_oversized_images_fail_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                path = root / ("asset." + extension)
                document, binary = textured_document(extension, None, PNG)
                write_asset(path, document, binary)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer_text(layer)
                for case, data, message in damaged_images():
                    suffix = ".png" if data.startswith(b"\x89PNG") else ".jpg"
                    (root / ("image" + suffix)).write_bytes(data)
                    encoded = base64.b64encode(data).decode()
                    for source, image in (
                        ("bufferView", None),
                        ("data URI", {"uri": "data:image/png;base64," + encoded}),
                        ("file", {"uri": "image" + suffix}),
                    ):
                        with self.subTest(extension=extension, case=case, source=source):
                            write_asset(path, *textured_document(extension, image, data))
                            with self.assertRaisesRegex(Tf.ErrorException, message):
                                layer.Reload(True)
                            self.assertEqual(layer_text(layer), before)

    def test_unusual_but_valid_images_are_accepted(self):
        split = png_chunk(b"IDAT", PNG[41:43]) + png_chunk(b"IDAT", PNG[43:-16])
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "asset.glb"
            for case, data in (
                ("png trailing bytes", PNG + b"trailing"),
                ("png split IDAT", PNG[:33] + split + PNG[-12:]),
                ("png at the limit", png_declaring(16384, 16384)),
                ("png palette", png_declaring(1, 1, color=3)[:33] + png_chunk(b"PLTE", bytes(3))
                 + png_declaring(1, 1, color=3)[33:]),
                ("jpeg trailing bytes", JPEG + b"trailing"),
                ("jpeg fill bytes", JPEG[:2] + b"\xff\xff" + JPEG[2:]),
                ("jpeg progressive", jpeg_with_frame(JPEG, marker=0xC2)),
                ("jpeg extended", jpeg_with_frame(JPEG, marker=0xC1)),
                ("jpeg at the limit", jpeg_with_frame(JPEG, width=16384, height=16384)),
            ):
                with self.subTest(case=case):
                    write_asset(path, *textured_document("glb", None, data))
                    asset = base_color_file(Usd.Stage.Open(str(path)))
                    self.assertTrue(asset.resolvedPath)
                    self.assertEqual(asset_bytes(asset.resolvedPath), data)

    def test_checks_do_not_decode_image_data(self):
        # The known limit: valid chunks around invalid data pass, and a JPEG scan has no
        # checksum. See docs/adr/0006-image-failures.md.
        garbage = bytes(range(256))
        sos = jpeg_marker(JPEG, 0xDA)
        scan = sos + 2 + struct.unpack_from(">H", JPEG, sos + 2)[0]
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "asset.glb"
            for case, data in (
                ("png data", PNG[:33] + png_chunk(b"IDAT", garbage) + png_chunk(b"IEND", b"")),
                ("jpeg scan", JPEG[:scan] + bytes(16) + JPEG[-2:]),
            ):
                with self.subTest(case=case):
                    write_asset(path, *textured_document("glb", None, data))
                    self.assertTrue(base_color_file(Usd.Stage.Open(str(path))).resolvedPath)

    def test_image_damaged_after_import_is_not_served(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "asset.glb"
            for case, data, message in (("png", PNG, "PNG image is damaged"),
                                        ("jpeg", JPEG, "JPEG image is damaged")):
                with self.subTest(case=case):
                    write_asset(path, *textured_document("glb", None, data))
                    asset = base_color_file(Usd.Stage.Open(str(path)))
                    self.assertEqual(asset_bytes(asset.resolvedPath), data)
                    resolved = asset.resolvedPath

                    # The image keeps its length and offset, so only its content differs.
                    write_asset(path, *textured_document("glb", None, data[:8] + bytes(len(data) - 8)))
                    self.assertFalse(Ar.GetResolver().Resolve(resolved))
                    result = subprocess.run(
                        [sys.executable, "-c",
                         "import sys\nfrom pxr import Ar\n"
                         "print(Ar.GetResolver().OpenAsset(Ar.ResolvedPath(sys.argv[1])))",
                         resolved],
                        capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("None", result.stdout)
                    self.assertIn(message, result.stderr)

    def test_external_image_file_size_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            # A sparse file on the tested filesystems, and never read past its size check.
            with open(root / "large.png", "wb") as large:
                large.write(PNG)
                large.truncate((2 << 30) + 1)
            path = root / "asset.gltf"
            write_asset(path, *textured_document("gltf", {"uri": "large.png"}, b""))
            with self.assertRaisesRegex(Tf.ErrorException, "external image is larger than 2 GiB"):
                Usd.Stage.Open(str(path))

    def test_texture_failures_preserve_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            outside = root / "outside.png"
            outside.write_bytes(PNG)
            source = root / "source"
            source.mkdir()
            (source / "not-image.png").write_bytes(b"not an image")
            (source / "mismatch.png").write_bytes(JPEG)
            (source / "image.bmp").write_bytes(PNG)
            (source / "image.ktx2").write_bytes(b"\xabKTX 20\xbb\r\n\x1a\n")
            for extension in ("gltf", "glb"):
                path = source / ("asset." + extension)
                document, binary = textured_document(extension, None, PNG)
                write_asset(path, document, binary)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer_text(layer)
                for case, change, message in (
                    ("corrupt embedded", lambda d: d["images"].__setitem__(0, {
                        "uri": "data:image/png;base64,AAAAAAAAAAAA"}), "embedded image is not PNG, JPEG or KTX2"),
                    ("webp data", lambda d: d["images"].__setitem__(0, {
                        "uri": "data:image/webp;base64,UklGRg=="}), "embedded image is not PNG, JPEG or KTX2"),
                    ("missing file", lambda d: d["images"].__setitem__(0, {"uri": "absent.png"}),
                     "cannot resolve external image"),
                    ("parent path", lambda d: d["images"].__setitem__(0, {"uri": "../outside.png"}),
                     "unsafe or unsupported external image URI"),
                    ("absolute path", lambda d: d["images"].__setitem__(0, {"uri": "/outside.png"}),
                     "unsafe or unsupported external image URI"),
                    ("remote", lambda d: d["images"].__setitem__(0, {"uri": "https://example.com/a.png"}),
                     "unsafe or unsupported external image URI"),
                    ("not an image", lambda d: d["images"].__setitem__(0, {"uri": "not-image.png"}),
                     "external image is not PNG, JPEG or KTX2"),
                    ("damaged ktx2 file", lambda d: d["images"].__setitem__(0, {"uri": "image.ktx2"}),
                     "KTX2 image is damaged"),
                    ("basisu png", lambda d: d["textures"][0].__setitem__("extensions", {
                        "KHR_texture_basisu": {"source": 0}}), "KHR_texture_basisu image is not KTX2"),
                    ("wrong content", lambda d: d["images"].__setitem__(0, {"uri": "mismatch.png"}),
                     "file extension does not match"),
                    ("wrong extension", lambda d: d["images"].__setitem__(0, {"uri": "image.bmp"}),
                     "file extension does not match"),
                    ("image index", lambda d: d["textures"][0].__setitem__("source", 9),
                     "image index is out of range|cannot parse"),
                    ("no source", lambda d: d["textures"][0].pop("source"),
                     "texture has no image source|cannot parse"),
                    ("texture index", lambda d: d["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"]
                     .__setitem__("index", 9), "texture index is out of range|cannot parse"),
                    ("sampler index", lambda d: d["textures"][0].__setitem__("sampler", 9),
                     "sampler index is out of range|cannot parse"),
                    ("uv set", lambda d: d["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"]
                     .__setitem__("texCoord", 3), "texture coordinate set the primitive does not have"),
                    ("bad view", lambda d: d["images"].__setitem__(0, {
                        "bufferView": 99, "mimeType": "image/png"}), "bufferView index is out of range|cannot parse"),
                ):
                    with self.subTest(extension=extension, case=case):
                        invalid, invalid_binary = textured_document(extension, None, PNG)
                        change(invalid)
                        write_asset(path, invalid, invalid_binary)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)

                # Only images the selected scene uses are read.
                unused, unused_binary = textured_document(extension, None, PNG)
                unused["images"].append({"uri": "absent.png"})
                write_asset(path, unused, unused_binary)
                self.assertTrue(layer.Reload(True))

    def test_material_failures_preserve_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                path = pathlib.Path(directory) / ("materials." + extension)
                document, binary = mesh_document(extension)
                document["materials"] = [{}]
                document["meshes"][0]["primitives"][0]["material"] = 0
                write_asset(path, document, binary)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer_text(layer)
                for case in ("index", "base color", "metallic", "roughness", "emissive",
                             "alpha cutoff", "base texture", "metallic texture",
                             "normal texture", "occlusion texture", "emissive texture"):
                    with self.subTest(extension=extension, case=case):
                        invalid, invalid_binary = mesh_document(extension)
                        primitive = invalid["meshes"][0]["primitives"][0]
                        primitive["material"] = 0
                        material = {}
                        invalid["materials"] = [material]
                        if case == "index":
                            primitive["material"] = 5
                        elif case in ("base color", "metallic", "roughness"):
                            values = {
                                "base color": {"baseColorFactor": [1.1, 1, 1, 1]},
                                "metallic": {"metallicFactor": -0.1},
                                "roughness": {"roughnessFactor": 1.1},
                            }
                            material["pbrMetallicRoughness"] = values[case]
                        elif case == "emissive":
                            material["emissiveFactor"] = [0, -0.1, 0]
                        elif case == "alpha cutoff":
                            material.update(alphaMode="MASK", alphaCutoff=-0.5)
                        else:
                            invalid["images"] = [{"uri": "data:image/png;base64,AA=="}]
                            invalid["textures"] = [{"source": 0}]
                            info = {"index": 0}
                            if case == "base texture":
                                material["pbrMetallicRoughness"] = {"baseColorTexture": info}
                            elif case == "metallic texture":
                                material["pbrMetallicRoughness"] = {"metallicRoughnessTexture": info}
                            else:
                                material[{"normal texture": "normalTexture",
                                          "occlusion texture": "occlusionTexture",
                                          "emissive texture": "emissiveTexture"}[case]] = info
                        write_asset(path, invalid, invalid_binary)
                        if case == "index":
                            message = "material index is out of range"
                        elif case in ("base color", "metallic", "roughness", "emissive"):
                            message = "material .* factor is non-finite or out of range"
                        elif case == "alpha cutoff":
                            message = "material alpha cutoff is non-finite or negative"
                        else:
                            message = "texture coordinate set the primitive does not have"
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)
                        self.assertTrue(UsdShade.Material.Get(stage, "/Asset/mtl/Material_0"))

    def test_optional_lights_outside_selected_scene_and_required_lights(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    document, binary = unsupported_document(extension, "light")
                    document["scenes"][0]["nodes"] = [0]
                    document["nodes"][0]["mesh"] = 0
                    path = root / ("optional." + extension)
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    self.assertTrue(stage.GetPrimAtPath("/Asset/geo/Node_0"))
                    self.assertFalse(stage.GetPrimAtPath("/Asset/geo/Node_1"))
                    layer = stage.GetRootLayer()
                    before = layer_text(layer)

                    document["extensionsRequired"] = ["KHR_lights_punctual"]
                    required_path = root / ("required." + extension)
                    write_asset(required_path, document, binary)
                    with self.assertRaisesRegex(Tf.ErrorException, "cannot parse"):
                        Usd.Stage.Open(str(required_path))
                    write_asset(path, document, binary)
                    with self.assertRaisesRegex(Tf.ErrorException, "cannot parse"):
                        layer.Reload(True)
                    self.assertEqual(layer_text(layer), before)
                    self.assertTrue(stage.GetPrimAtPath("/Asset/geo/Node_0"))

    def test_animation_outside_selected_scene_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    document, binary = unsupported_document(extension, "animation")
                    document["scenes"][0]["nodes"] = [1]
                    path = pathlib.Path(directory) / ("animation." + extension)
                    write_asset(path, document, binary)
                    with self.assertRaisesRegex(Tf.ErrorException, "animations are not supported"):
                        Usd.Stage.Open(str(path))

    def test_unsupported_features_fail_without_publishing(self):
        cases = {
            "skin": "skin, camera, light, or morph",
            "camera": "skin, camera, light, or morph",
            "light": "skin, camera, light, or morph",
            "morph target": "morph targets",
            "mesh weights": "morph weights",
            "node weights": "skin, camera, light, or morph",
            "animation": "animations are not supported",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                path = root / ("reload." + extension)
                document, binary = mesh_document(extension)
                write_asset(path, document, binary)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer_text(layer)
                for feature, message in cases.items():
                    with self.subTest(extension=extension, feature=feature):
                        invalid, invalid_binary = unsupported_document(extension, feature)
                        initial_path = root / (feature + "." + extension)
                        write_asset(initial_path, invalid, invalid_binary)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            Usd.Stage.Open(str(initial_path))
                        write_asset(path, invalid, invalid_binary)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)
                        self.assertTrue(stage.GetPrimAtPath("/Asset/geo/Node_0/Mesh_0/Primitive_0"))
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

                    before = layer_text(layer)
                    buffer_path.write_bytes(binary[:-1])
                    with self.assertRaisesRegex(Tf.ErrorException, "shorter than its declared byteLength"):
                        layer.Reload(True)
                    self.assertEqual(layer_text(layer), before)
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
                before = layer_text(layer)
                for uri in uris:
                    with self.subTest(extension=extension, uri=uri):
                        document["buffers"][0]["uri"] = uri
                        write_asset(path, document)
                        with self.assertRaisesRegex(Tf.ErrorException, "unsafe or unsupported|cannot parse"):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)
                document["buffers"][0]["uri"] = "missing.bin"
                write_asset(path, document)
                with self.assertRaisesRegex(Tf.ErrorException, "cannot resolve external buffer"):
                    layer.Reload(True)
                self.assertEqual(layer_text(layer), before)

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

    def test_hierarchical_reflection_winding_and_normals(self):
        cases = (
            (2, 1, ((1, -2, 3), (3, -2, 3), (1, -2, 6)), 1),
            (-2, 1, ((1, -2, 3), (-1, -2, 3), (1, -2, 6)), -1),
            (2, -1, ((1, -2, 3), (3, -2, 3), (1, -2, 0)), -1),
            (-2, -1, ((1, -2, 3), (-1, -2, 3), (1, -2, 0)), 1),
        )
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                for parent_x, child_y, expected_points, parity in cases:
                    with self.subTest(extension=extension, parent_x=parent_x, child_y=child_y):
                        path = pathlib.Path(directory) / f"reflection_{parent_x}_{child_y}.{extension}"
                        document, binary = mesh_document(extension)
                        document["scenes"][0]["nodes"] = [0]
                        document["nodes"] = [
                            {
                                "translation": [1, 2, 3],
                                "rotation": [0.7071067811865476, 0, 0, 0.7071067811865476],
                                "scale": [parent_x, 3, 4],
                                "children": [1],
                            },
                            {"mesh": 0, "matrix": [
                                1, 0, 0, 0, 0, child_y, 0, 0, 0, 0, 1, 0, 0, 0, 1, 1,
                            ]},
                        ]
                        write_asset(path, document, binary)
                        stage = Usd.Stage.Open(str(path))
                        mesh_prefix = "/Asset/geo/Node_0/Node_1/Mesh_0"

                        host = Usd.Stage.CreateInMemory()
                        reference = host.DefinePrim("/Model", "Xform")
                        self.assertTrue(reference.GetReferences().AddReference(str(path)))
                        UsdGeom.Xformable(reference).AddScaleOp().Set(Gf.Vec3f(-1, 1, 1))

                        for reload in (False, True):
                            if reload:
                                self.assertTrue(stage.GetRootLayer().Reload(True))
                            for target, prefix, reflected in (
                                (stage, mesh_prefix, False),
                                (host, mesh_prefix.replace("/Asset", "/Model", 1), True),
                            ):
                                for primitive, indices, interpolation in (
                                    (0, [2, 0, 1], UsdGeom.Tokens.vertex),
                                    (1, [0, 1, 2], UsdGeom.Tokens.uniform),
                                ):
                                    mesh = UsdGeom.Mesh.Get(target, f"{prefix}/Primitive_{primitive}")
                                    self.assertTrue(mesh)
                                    self.assertEqual(mesh.GetOrientationAttr().Get(), UsdGeom.Tokens.rightHanded)
                                    self.assertEqual(list(mesh.GetFaceVertexCountsAttr().Get()), [3])
                                    self.assertEqual(list(mesh.GetFaceVertexIndicesAttr().Get()), indices)
                                    self.assertEqual(mesh.GetNormalsInterpolation(), interpolation)
                                    normals = list(mesh.GetNormalsAttr().Get())
                                    self.assertEqual(normals, [Gf.Vec3f(0, 0, 1)] *
                                                     (3 if primitive == 0 else 1))

                                    transform = UsdGeom.XformCache().GetLocalToWorldTransform(mesh.GetPrim())
                                    world_parity = -parity if reflected else parity
                                    self.assertEqual(1 if transform.GetDeterminant() > 0 else -1, world_parity)
                                    points = [transform.Transform(Gf.Vec3d(p))
                                              for p in mesh.GetPointsAttr().Get()]
                                    self.assertEqual(len(points), 3)
                                    for actual, expected in zip(points, expected_points):
                                        wanted = Gf.Vec3d(-expected[0] if reflected else expected[0],
                                                          expected[1], expected[2])
                                        self.assertTrue(Gf.IsClose(actual, wanted, 1e-5), str(actual))

                                    normal = transform.GetInverse().GetTranspose().TransformDir(
                                        Gf.Vec3d(normals[0])).GetNormalized()
                                    self.assertTrue(Gf.IsClose(normal, Gf.Vec3d(0, -1, 0), 1e-5), str(normal))
                                    a, b, c = (points[index] for index in indices)
                                    face_normal = Gf.Cross(b - a, c - a).GetNormalized()
                                    self.assertTrue(Gf.IsClose(face_normal, world_parity * normal, 1e-5),
                                                    str(face_normal))

    def test_reflection_view_fixture(self):
        path = pathlib.Path(__file__).parent / "fixtures" / "reflections.gltf"
        stage = Usd.Stage.Open(str(path))
        cases = (
            ("Node_0/Mesh_0", 1, UsdGeom.Tokens.vertex, [0, 1, 2]),
            ("Node_1/Mesh_0", -1, UsdGeom.Tokens.vertex, [0, 1, 2]),
            ("Node_2/Node_4/Mesh_1", -1, UsdGeom.Tokens.uniform, [0, 1, 2]),
            ("Node_3/Node_5/Mesh_1", 1, UsdGeom.Tokens.uniform, [0, 1, 2]),
            ("Node_6/Mesh_2", 1, UsdGeom.Tokens.vertex, [0, 2, 1]),
        )
        for path, parity, interpolation, indices in cases:
            with self.subTest(path=path):
                mesh = UsdGeom.Mesh.Get(stage, f"/Asset/geo/{path}/Primitive_0")
                self.assertTrue(mesh)
                self.assertFalse(mesh.GetDoubleSidedAttr().Get())
                self.assertEqual(mesh.GetOrientationAttr().Get(), UsdGeom.Tokens.rightHanded)
                self.assertEqual(list(mesh.GetFaceVertexIndicesAttr().Get()), indices)
                self.assertEqual(mesh.GetNormalsInterpolation(), interpolation)
                transform = UsdGeom.XformCache().GetLocalToWorldTransform(mesh.GetPrim())
                self.assertEqual(1 if transform.GetDeterminant() > 0 else -1, parity)

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
                    before = layer_text(layer)
                    document = scene_document()
                    document["nodes"][1]["children"] = [0]
                    write_asset(path, document)
                    with self.assertRaisesRegex(Tf.ErrorException, "cyclic node"):
                        layer.Reload(True)
                    self.assertEqual(layer_text(layer), before)
                    self.assertTrue(stage.GetPrimAtPath("/Asset/geo/Node_0/Node_1"))
                    changed = scene_document()
                    changed["nodes"][0]["children"] = []
                    write_asset(path, changed)
                    self.assertTrue(layer.Reload(True))
                    self.assertFalse(stage.GetPrimAtPath("/Asset/geo/Node_0/Node_1"))
                    self.assertTrue(stage.GetPrimAtPath("/Asset/geo/Node_0"))


if __name__ == "__main__":
    unittest.main()
