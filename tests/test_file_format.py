# SPDX-License-Identifier: Apache-2.0
import base64
import faulthandler
import itertools
import json
import math
import pathlib
import re
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
import zlib

from pxr import Ar, Gf, Sdf, Sdr, Tf, Usd, UsdGeom, UsdLux, UsdShade

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


QUANTIZED_BOUNDS = {5120: (-128, -127, 127), 5121: (0, 1, 255), 5122: (-32768, -32767, 32767), 5123: (0, 1, 65535)}
FLOAT_POSITIONS = (5126, False, ((0, 0, 0), (1, 0, 0), (0, 1, 0)))


def quantized_document(extension, attributes):
    """A non-indexed triangle whose attributes are name: (componentType, normalized, rows),
    each in a bufferView of its own, padded to the stride that alignment needs."""
    binary = b""
    views, accessors = [], []
    for component_type, normalized, rows in attributes.values():
        code = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5126: "f"}[component_type]
        width = len(rows[0])
        size = struct.calcsize("<" + code)
        stride = (size * width + 3) // 4 * 4
        data = b"".join(struct.pack(f"<{width}{code}", *row).ljust(stride, b"\0") for row in rows)
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(data), "byteStride": stride})
        accessors.append({"bufferView": len(views) - 1, "componentType": component_type, "count": len(rows),
                          "type": f"VEC{width}", "normalized": normalized})
        binary += data
    buffer = {"byteLength": len(binary)}
    if extension == "gltf":
        buffer["uri"] = "data:application/octet-stream;base64," + base64.b64encode(binary).decode()
    document = {
        "asset": {"version": "2.0"},
        "extensionsUsed": ["KHR_mesh_quantization"],
        "extensionsRequired": ["KHR_mesh_quantization"],
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {name: i for i, name in enumerate(attributes)}}]}],
        "buffers": [buffer],
        "bufferViews": views,
        "accessors": accessors,
    }
    return document, binary if extension == "glb" else None


def decoded_component(component_type, normalized, value):
    if not normalized:
        return float(value)
    scale = {5120: 127.0, 5121: 255.0, 5122: 32767.0, 5123: 65535.0}[component_type]
    return max(value / scale, -1.0)


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


def webp_fixture(name):
    return (pathlib.Path(__file__).parent / "fixtures" / "textures" / (name + ".webp")).read_bytes()


def webp_document(extension, image, data):
    """A textured_document whose texture requires EXT_texture_webp."""
    document, binary = textured_document(extension, image, data)
    if image is None:
        document["images"][0]["mimeType"] = "image/webp"
    document["textures"] = [{"extensions": {"EXT_texture_webp": {"source": 0}}}]
    document["extensionsUsed"] = ["EXT_texture_webp"]
    document["extensionsRequired"] = ["EXT_texture_webp"]
    return document, binary


def riff(chunks):
    """A WebP file of the given (tag, payload) chunks, each padded to an even size."""
    body = b"WEBP"
    for tag, payload in chunks:
        body += tag + struct.pack("<I", len(payload)) + payload + b"\0" * (len(payload) % 2)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def webp_without_tokens():
    """lossy.webp with only its frame header and first partition, which the header still describes
    consistently. The token partition is missing, so only decoding finds it."""
    data = webp_fixture("lossy")
    assert data[12:16] == b"VP8 "
    payload = data[20:20 + struct.unpack_from("<I", data, 16)[0]]
    first_partition = (payload[0] | payload[1] << 8 | payload[2] << 16) >> 5
    assert 10 + first_partition < len(payload)
    return riff([(b"VP8 ", payload[:10 + first_partition])])


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


HALF_TURN_SINE = math.sqrt(0.5)


def camera_document(extension):
    """Cameras on a mesh's node, under scaled nodes and on a matrix node, with camera 0 used twice."""
    document, binary = mesh_document(extension)
    document["scenes"][0]["nodes"] = [0, 1, 3]
    document["nodes"] = [
        {"mesh": 0, "camera": 0, "translation": [2, 0, 0]},
        {"camera": 1, "translation": [0, 1, 5], "rotation": [0, HALF_TURN_SINE, 0, HALF_TURN_SINE],
         "scale": [2, 2, 2], "children": [2]},
        {"camera": 2, "translation": [1, 0, 0], "scale": [1, 3, 0.5]},
        {"camera": 0, "matrix": [1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 10, 1]},
    ]
    document["cameras"] = [
        {"name": "Main camera", "type": "perspective", "perspective": {
            "aspectRatio": 1.5, "yfov": 0.660593, "zfar": 100, "znear": 0.01,
        }},
        {"type": "perspective", "perspective": {"yfov": 1.2, "znear": 0.5}},
        {"type": "orthographic", "orthographic": {"xmag": 2, "ymag": 1.5, "zfar": 50, "znear": 0}},
    ]
    return document, binary


def light_extension(index):
    return {"KHR_lights_punctual": {"light": index}}


def light_document(extension):
    """Lights under rotated, scaled, sheared and reflected nodes, with light 1 used twice."""
    document, binary = mesh_document(extension)
    document["extensionsUsed"] = ["KHR_lights_punctual"]
    document["scenes"][0]["nodes"] = [0, 1, 3]
    document["nodes"] = [
        {"mesh": 0, "extensions": light_extension(0), "translation": [2, 0, 0],
         "rotation": [-HALF_TURN_SINE, 0, 0, HALF_TURN_SINE]},
        {"extensions": light_extension(1), "translation": [0, 1, 5], "scale": [2, 1, 1], "children": [2]},
        # A rotation inside an unequal scale, which shears the node.
        {"extensions": light_extension(2), "translation": [1, 0, 0],
         "rotation": [0, 0.3826834323650898, 0, 0.9238795325112867]},
        {"extensions": light_extension(1), "matrix": [-1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 10, 1],
         "children": [4]},
        {"extensions": light_extension(3)},
    ]
    document["extensions"] = {"KHR_lights_punctual": {"lights": [
        {"name": "Sun", "type": "directional", "intensity": 2, "color": [1, 0.5, 0.25]},
        {"type": "point", "intensity": 50},
        {"type": "spot", "intensity": 30, "color": [0, 1, 0],
         "spot": {"innerConeAngle": 0.2, "outerConeAngle": 0.6}},
        {"type": "spot", "spot": {}},
    ]}}
    return document, binary


def gltf_projection(camera):
    """Returns a camera's projection from the glTF specification, in USD's row-vector convention.

    An infinite projection uses the far plane the importer authors for it.
    """
    if camera["type"] == "perspective":
        values = camera["perspective"]
        aspect = values.get("aspectRatio", 1.0)
        near, far = values["znear"], values.get("zfar", 1000000.0)
        tangent = math.tan(values["yfov"] / 2)
        columns = Gf.Matrix4d(
            1 / (aspect * tangent), 0, 0, 0,
            0, 1 / tangent, 0, 0,
            0, 0, (far + near) / (near - far), 2 * far * near / (near - far),
            0, 0, -1, 0)
    else:
        values = camera["orthographic"]
        near, far = values["znear"], values["zfar"]
        columns = Gf.Matrix4d(
            1 / values["xmag"], 0, 0, 0,
            0, 1 / values["ymag"], 0, 0,
            0, 0, 2 / (near - far), (far + near) / (near - far),
            0, 0, 0, 1)
    return columns.GetTranspose()


def rigid(axis, degrees, position):
    """Returns a rotation followed by a translation."""
    return Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(*axis), degrees)).SetTranslateOnly(Gf.Vec3d(*position))


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
            "normalized integer": "requires KHR_mesh_quantization",
            "component": "requires KHR_mesh_quantization",
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
            "component type": "requires KHR_mesh_quantization",
            "alignment": "alignment",
            "normalized": "cannot parse",
            "sparse": "sparse",
            "index count": "triangle index count",
            "normal count": "normal and position counts",
            "stride": "bufferView",
            "material": "texture coordinate set the primitive does not have",
            "attribute": "unsupported or repeated vertex attribute",
            "mode": "primitive mode LINES is not supported",
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
                        ("corrupt image", {"index": 1}, "embedded image is not PNG, JPEG, KTX2 or WebP"),
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

    def test_texture_transform_networks(self):
        slots = (("baseColor", "baseColorTexture", "baseColorImage"),
                 ("metallicRoughness", "metallicRoughnessTexture", "metallicRoughnessImage"),
                 ("normal", "normalTexture", "normalImage"),
                 ("occlusion", "occlusionTexture", "occlusionImage"),
                 ("emissive", "emissiveTexture", "emissiveImage"))
        # (u, v) to (u', v'), computed by hand from the extension's matrix, with V flipped by the primvar.
        transforms = (
            ("baseColor", {"offset": [0.25, 0.125]}, (1, 1), 0, (0.25, -0.125)),
            ("metallicRoughness", {"scale": [2, 3]}, (2, 3), 0, (0, -2)),
            ("normal", {"rotation": math.pi / 2}, (1, 1), 90, (1, 1)),
            ("occlusion", {"rotation": -math.pi / 2}, (1, 1), -90, (-1, 1)),
            ("emissive", {"offset": [0.5, 0.25], "rotation": math.pi, "scale": [2, 4]}, (2, 4), 180, (0.5, 4.75)),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                for required in (False, True):
                    with self.subTest(extension=extension, required=required):
                        path = root / f"transforms{int(required)}.{extension}"
                        document, binary = textured_document(extension, None, PNG)
                        document["extensionsUsed"] = ["KHR_texture_transform"]
                        if required:
                            document["extensionsRequired"] = ["KHR_texture_transform"]
                        infos = {slot: {"index": 0, "extensions": {"KHR_texture_transform": transform}}
                                 for slot, transform, _, _, _ in transforms}
                        document["materials"] = [{
                            "pbrMetallicRoughness": {"baseColorTexture": infos["baseColor"],
                                                     "metallicRoughnessTexture": infos["metallicRoughness"]},
                            "normalTexture": infos["normal"],
                            "occlusionTexture": infos["occlusion"],
                            "emissiveTexture": infos["emissive"],
                        }]
                        write_asset(path, document, binary)
                        stage = Usd.Stage.Open(str(path))
                        base = "/Asset/mtl/Material_0"
                        for slot, _, scale, rotation, translation in transforms:
                            preview_name, mtlx_name = next((p, m) for s, p, m in slots if s == slot)
                            texture = UsdShade.Shader.Get(stage, f"{base}/preview/{preview_name}")
                            node = UsdShade.Shader(texture.GetInput("st").GetConnectedSource()[0].GetPrim())
                            self.assertEqual(node.GetShaderId(), "UsdTransform2d")
                            self.assertEqual(node.GetPath().name, preview_name + "Transform")
                            self.assertEqual(node.GetInput("scale").Get(), Gf.Vec2f(*scale))
                            self.assertAlmostEqual(node.GetInput("rotation").Get(), rotation, places=4)
                            for actual, expected in zip(node.GetInput("translation").Get(), translation):
                                self.assertAlmostEqual(actual, expected, places=5)
                            reader = UsdShade.Shader(node.GetInput("in").GetConnectedSource()[0].GetPrim())
                            self.assertEqual(reader.GetInput("varname").Get(), "st")

                            image = UsdShade.Shader.Get(stage, f"{base}/mtlx/{mtlx_name}")
                            add = UsdShade.Shader(image.GetInput("texcoord").GetConnectedSource()[0].GetPrim())
                            self.assertEqual(add.GetShaderId(), "ND_add_vector2")
                            for actual, expected in zip(add.GetInput("in2").Get(), translation):
                                self.assertAlmostEqual(actual, expected, places=5)
                            rotate = UsdShade.Shader(add.GetInput("in1").GetConnectedSource()[0].GetPrim())
                            self.assertEqual(rotate.GetShaderId(), "ND_rotate2d_vector2")
                            self.assertAlmostEqual(rotate.GetInput("amount").Get(), -rotation, places=4)
                            multiply = UsdShade.Shader(rotate.GetInput("in").GetConnectedSource()[0].GetPrim())
                            self.assertEqual(multiply.GetShaderId(), "ND_multiply_vector2")
                            self.assertEqual(multiply.GetInput("in2").Get(), Gf.Vec2f(*scale))
                            reader = UsdShade.Shader(multiply.GetInput("in1").GetConnectedSource()[0].GetPrim())
                            self.assertEqual(reader.GetInput("geomprop").Get(), "st")

    def test_texture_transform_uv_set_and_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = root / ("override." + extension)
                    document, binary = textured_document(extension, None, PNG)
                    document["extensionsUsed"] = ["KHR_texture_transform"]
                    transform = {"KHR_texture_transform": {"texCoord": 2}}
                    document["materials"] = [{
                        "pbrMetallicRoughness": {"baseColorTexture": {"index": 0, "texCoord": 1, "extensions": transform}},
                        # The same image under another transform, with its own set.
                        "emissiveTexture": {"index": 0, "texCoord": 2, "extensions": {
                            "KHR_texture_transform": {"offset": [0.5, 0], "texCoord": 1}}},
                    }]
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    base = "/Asset/mtl/Material_0"
                    # A transform of defaults authors no node, and reads the overriding set directly.
                    texture = UsdShade.Shader.Get(stage, f"{base}/preview/baseColorTexture")
                    reader = UsdShade.Shader(texture.GetInput("st").GetConnectedSource()[0].GetPrim())
                    self.assertEqual(reader.GetInput("varname").Get(), "st2")
                    self.assertFalse(stage.GetPrimAtPath(f"{base}/preview/baseColorTextureTransform"))
                    image = UsdShade.Shader.Get(stage, f"{base}/mtlx/baseColorImage")
                    reader = UsdShade.Shader(image.GetInput("texcoord").GetConnectedSource()[0].GetPrim())
                    self.assertEqual(reader.GetInput("geomprop").Get(), "st2")
                    self.assertFalse(stage.GetPrimAtPath(f"{base}/mtlx/baseColorImageScale"))
                    node = UsdShade.Shader.Get(stage, f"{base}/preview/emissiveTextureTransform")
                    reader = UsdShade.Shader(node.GetInput("in").GetConnectedSource()[0].GetPrim())
                    self.assertEqual(reader.GetInput("varname").Get(), "st1")
                    self.assertEqual(UsdShade.Shader.Get(stage, f"{base}/mtlx/emissiveImageScale")
                                     .GetInput("in1").GetConnectedSource()[0].GetPath().name, "st1Reader")

    def test_texture_transform_diagnostics(self):
        def run(path):
            return subprocess.run(
                [sys.executable, "-c", "from pxr import Usd; import sys; assert Usd.Stage.Open(sys.argv[1])", str(path)],
                capture_output=True, text=True, check=False,
            )

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                for name, transform, warns in (
                    ("offset", {"offset": [0.5, 0]}, False),
                    ("stretch", {"scale": [2, 0.5]}, False),
                    ("rotation", {"rotation": 1}, True),
                    ("mirror", {"scale": [-1, 1]}, True),
                ):
                    with self.subTest(extension=extension, name=name):
                        path = root / f"{name}.{extension}"
                        document, binary = textured_document(extension, None, PNG)
                        document["extensionsUsed"] = ["KHR_texture_transform"]
                        document["materials"] = [{"normalTexture": {
                            "index": 0, "extensions": {"KHR_texture_transform": transform}}}]
                        write_asset(path, document, binary)
                        result = run(path)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertNotIn("is not supported", result.stderr)
                        self.assertEqual("tangent frame" in result.stderr, warns, result.stderr)

                with self.subTest(extension=extension, name="base color does not warn"):
                    path = root / f"base.{extension}"
                    document, binary = textured_document(extension, None, PNG)
                    document["extensionsUsed"] = ["KHR_texture_transform"]
                    document["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"]["extensions"] = {
                        "KHR_texture_transform": {"rotation": 1}}
                    write_asset(path, document, binary)
                    result = run(path)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn("tangent frame", result.stderr)

    def test_texture_transform_failures_preserve_layer(self):
        cases = (
            ("rotation type", {"rotation": "1"}, "KHR_texture_transform has a field of the wrong type"),
            ("offset type", {"offset": "1"}, "KHR_texture_transform has a field of the wrong type"),
            ("offset length", {"offset": [1, 2, 3]}, "KHR_texture_transform has a field of the wrong type"),
            ("offset short", {"offset": [1]}, "KHR_texture_transform has a field of the wrong type|cannot parse"),
            ("offset element", {"offset": [1, "2"]}, "cannot parse"),
            ("scale type", {"scale": 2}, "KHR_texture_transform has a field of the wrong type"),
            ("texCoord type", {"texCoord": 0.5}, "KHR_texture_transform has a field of the wrong type"),
            ("texCoord negative", {"texCoord": -1}, "KHR_texture_transform has a field of the wrong type"),
            ("rotation beyond float", {"rotation": 1e39}, "cannot parse"),
            ("offset beyond float", {"offset": [1e39, 0]}, "cannot parse"),
            ("scale beyond float", {"scale": [1, 1e39]}, "cannot parse"),
            ("translation overflows", {"offset": [0, 3e38], "scale": [1, 3e38]}, "KHR_texture_transform is out of range"),
            ("override has no set", {"texCoord": 7}, "texture coordinate set the primitive does not have"),
        )
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                path = pathlib.Path(directory) / ("transform." + extension)
                document, binary = textured_document(extension, None, PNG)
                write_asset(path, document, binary)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer_text(layer)
                for name, transform, message in cases:
                    with self.subTest(extension=extension, name=name):
                        invalid, invalid_binary = textured_document(extension, None, PNG)
                        invalid["extensionsUsed"] = ["KHR_texture_transform"]
                        info = invalid["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"]
                        info["extensions"] = {"KHR_texture_transform": transform}
                        write_asset(path, invalid, invalid_binary)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(force=True)
                        self.assertEqual(layer_text(layer), before)

    def test_texture_transforms_view_fixture(self):
        fixtures = pathlib.Path(__file__).parent / "fixtures"
        stage = Usd.Stage.Open(str(fixtures / "texture_transforms.gltf"))
        reference = Usd.Stage.Open(str(fixtures / "texture_transforms_reference.gltf"))
        materials = stage.GetPrimAtPath("/Asset/mtl").GetChildren()
        self.assertEqual(len(materials), 22)
        self.assertEqual(len(reference.GetPrimAtPath("/Asset/mtl").GetChildren()), 22)
        for prim in reference.Traverse():
            self.assertNotEqual(UsdShade.Shader(prim).GetShaderId() if prim.IsA(UsdShade.Shader) else "", "UsdTransform2d")
        self.assertTrue(any(UsdShade.Shader(prim).GetShaderId() == "UsdTransform2d"
                            for prim in stage.Traverse() if prim.IsA(UsdShade.Shader)))

    def test_quantized_attribute_formats(self):
        def rows(component_type, width):
            a, b, c = QUANTIZED_BOUNDS[component_type]
            return tuple((row * 2)[:width] for row in ((a, b, c), (b, c, a), (c, a, b)))

        formats = {
            "POSITION": [(c, n) for c in QUANTIZED_BOUNDS for n in (False, True)],
            "NORMAL": [(5120, True), (5122, True)],
            "TANGENT": [(5120, True), (5122, True)],
            "TEXCOORD_0": [(5120, False), (5120, True), (5121, False), (5122, False), (5122, True), (5123, False)],
        }
        widths = {"POSITION": 3, "NORMAL": 3, "TANGENT": 4, "TEXCOORD_0": 2}
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                for name, cases in formats.items():
                    for component_type, normalized in cases:
                        with self.subTest(extension=extension, name=name, component_type=component_type,
                                          normalized=normalized):
                            path = pathlib.Path(directory) / f"{name}_{component_type}_{normalized}.{extension}"
                            raw = rows(component_type, widths[name])
                            attributes = {"POSITION": FLOAT_POSITIONS}
                            attributes[name] = (component_type, normalized, raw)
                            write_asset(path, *quantized_document(extension, attributes))
                            stage = Usd.Stage.Open(str(path))
                            prim = stage.GetPrimAtPath("/Asset/geo/Node_0/Mesh_0/Primitive_0")
                            mesh = UsdGeom.Mesh(prim)
                            api = UsdGeom.PrimvarsAPI(prim)
                            values = {
                                "POSITION": lambda: mesh.GetPointsAttr().Get(),
                                "NORMAL": lambda: mesh.GetNormalsAttr().Get(),
                                "TANGENT": lambda: api.GetPrimvar("gltf_tangent").Get(),
                                "TEXCOORD_0": lambda: api.GetPrimvar("st").Get(),
                            }[name]()
                            self.assertEqual(len(values), 3)
                            for value, row in zip(values, raw):
                                expected = [decoded_component(component_type, normalized, c) for c in row]
                                if name == "TEXCOORD_0":
                                    expected[1] = 1 - expected[1]
                                for got, wanted in zip(value, expected):
                                    self.assertAlmostEqual(got, wanted, delta=1e-6 * max(1, abs(wanted)))
                            self.assertTrue(stage.GetRootLayer().Reload(True))

    def test_quantized_values_keep_their_range_and_precision(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = pathlib.Path(directory) / ("precision." + extension)
                    # Every unsigned short is exact in a float, and differences of these would wrap
                    # around if they were computed before converting.
                    positions = ((0, 0, 65535), (65535, 0, 65535), (0, 65534, 65535))
                    write_asset(path, *quantized_document(extension, {
                        "POSITION": (5123, False, positions),
                        "TANGENT": (5120, True, ((127, 0, 0, 127), (0, 127, 0, -127), (0, 0, 127, -128))),
                    }))
                    stage = Usd.Stage.Open(str(path))
                    prim = stage.GetPrimAtPath("/Asset/geo/Node_0/Mesh_0/Primitive_0")
                    mesh = UsdGeom.Mesh(prim)
                    self.assertEqual([tuple(point) for point in mesh.GetPointsAttr().Get()], list(positions))
                    self.assertEqual(mesh.GetNormalsInterpolation(), UsdGeom.Tokens.uniform)
                    self.assertEqual(list(mesh.GetNormalsAttr().Get()), [Gf.Vec3f(0, 0, 1)])
                    self.assertEqual([Gf.Vec3f(*corner) for corner in mesh.GetExtentAttr().Get()],
                                     [Gf.Vec3f(0, 0, 65535), Gf.Vec3f(65535, 65534, 65535)])
                    # Both of the lowest values mean -1, so W stays a sign.
                    tangents = UsdGeom.PrimvarsAPI(prim).GetPrimvar("gltf_tangent").Get()
                    self.assertEqual([tangent[3] for tangent in tangents], [1, -1, -1])

    def test_quantized_attributes_interleaved_with_node_transform(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = pathlib.Path(directory) / ("interleaved." + extension)
                    binary = b"".join(struct.pack("<3Hxx3bx2H", *vertex) for vertex in (
                        (0, 0, 0, 0, 0, 127, 0, 65535),
                        (65535, 0, 0, 0, 0, 127, 65535, 65535),
                        (0, 65535, 0, 0, 0, -127, 0, 0),
                    ))
                    document, _ = quantized_document(extension, {"POSITION": FLOAT_POSITIONS})
                    document["buffers"][0]["byteLength"] = len(binary)
                    if extension == "gltf":
                        document["buffers"][0]["uri"] = "data:application/octet-stream;base64," + (
                            base64.b64encode(binary).decode())
                    document["bufferViews"] = [{"buffer": 0, "byteLength": len(binary), "byteStride": 16}]
                    document["accessors"] = [
                        {"bufferView": 0, "componentType": 5123, "count": 3, "type": "VEC3"},
                        {"bufferView": 0, "byteOffset": 8, "componentType": 5120, "normalized": True, "count": 3,
                         "type": "VEC3"},
                        {"bufferView": 0, "byteOffset": 12, "componentType": 5123, "count": 3, "type": "VEC2"},
                    ]
                    document["meshes"][0]["primitives"][0]["attributes"] = {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2}
                    # A dequantization transform, as the extension's specification suggests.
                    document["nodes"][0].update({"scale": [2 / 65535] * 3, "translation": [-1, -1, 0]})
                    write_asset(path, document, binary if extension == "glb" else None)
                    stage = Usd.Stage.Open(str(path))
                    prim = stage.GetPrimAtPath("/Asset/geo/Node_0/Mesh_0/Primitive_0")
                    mesh = UsdGeom.Mesh(prim)
                    self.assertEqual(list(mesh.GetNormalsAttr().Get()),
                                     [Gf.Vec3f(0, 0, 1), Gf.Vec3f(0, 0, 1), Gf.Vec3f(0, 0, -1)])
                    self.assertEqual(list(UsdGeom.PrimvarsAPI(prim).GetPrimvar("st").Get()),
                                     [Gf.Vec2f(0, -65534), Gf.Vec2f(65535, -65534), Gf.Vec2f(0, 1)])
                    world = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
                    for point, expected in zip(mesh.GetPointsAttr().Get(), ((-1, -1, 0), (1, -1, 0), (-1, 1, 0))):
                        self.assertTrue(Gf.IsClose(world.Transform(Gf.Vec3d(point)), Gf.Vec3d(*expected), 1e-9))

    def test_quantization_declared_without_quantized_data(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                for required in (False, True):
                    with self.subTest(extension=extension, required=required):
                        path = pathlib.Path(directory) / f"{required}.{extension}"
                        document, binary = uv_document(extension)
                        document["extensionsUsed"] = ["KHR_mesh_quantization"]
                        if required:
                            document["extensionsRequired"] = ["KHR_mesh_quantization"]
                        write_asset(path, document, binary if extension == "glb" else None)
                        result = subprocess.run(
                            [sys.executable, "-c",
                             "from pxr import Usd; import sys; assert Usd.Stage.Open(sys.argv[1])", str(path)],
                            capture_output=True, text=True, check=False,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertNotIn("KHR_mesh_quantization", result.stderr)

    def test_quantized_failures_preserve_layer(self):
        def base(extension):
            return quantized_document(extension, {
                "POSITION": (5123, False, ((0, 0, 0), (9, 0, 0), (0, 9, 0))),
                "NORMAL": (5120, True, ((0, 0, 127),) * 3),
                "TANGENT": (5122, True, ((32767, 0, 0, 32767),) * 3),
                "TEXCOORD_0": (5123, False, ((0, 0), (9, 0), (0, 9))),
            })

        def edit(field, value, target="accessors", index=0):
            def apply(document):
                document[target][index][field] = value
            return apply

        def undeclared(document):
            del document["extensionsUsed"], document["extensionsRequired"]

        def optional(document):
            del document["extensionsRequired"]

        def without_stride(index):
            def apply(document):
                del document["bufferViews"][index]["byteStride"]
            return apply

        cases = {
            "undeclared": (undeclared, "requires KHR_mesh_quantization"),
            "optional": (optional, "requires KHR_mesh_quantization"),
            "position unsigned int": (edit("componentType", 5125), "invalid type"),
            "position type": (edit("type", "VEC2"), "invalid type"),
            "normal not normalized": (edit("normalized", False, index=1), "invalid type"),
            "normal unsigned": (edit("componentType", 5121, index=1), "invalid type"),
            "tangent not normalized": (edit("normalized", False, index=2), "invalid type"),
            "tangent unsigned": (edit("componentType", 5123, index=2), "invalid type"),
            "texture coordinate unsigned int": (edit("componentType", 5125, index=3), "invalid type"),
            "count": (edit("count", 2, index=1), "normal and position counts differ"),
            "short without padding": (without_stride(0), "invalid stride"),
            "byte without padding": (without_stride(1), "invalid stride"),
            "stride not a multiple of 4": (edit("byteStride", 6, "bufferViews"), "invalid stride"),
            "offset of a component": (edit("byteOffset", 1), "alignment"),
            "offset not a multiple of 4": (edit("byteOffset", 2), "alignment"),
            # The last element needs no padding, so it needs 22 of the 24 bytes.
            "view too short": (edit("byteLength", 21, "bufferViews"), "bufferView"),
            "sparse": (edit("sparse", {"count": 1, "indices": {"bufferView": 1, "componentType": 5121},
                                       "values": {"bufferView": 1}}), "sparse"),
        }
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                path = pathlib.Path(directory) / ("quantized." + extension)
                write_asset(path, *base(extension))
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer_text(layer)
                for name, (mutate, message) in cases.items():
                    with self.subTest(extension=extension, name=name):
                        document, binary = base(extension)
                        mutate(document)
                        write_asset(path, document, binary)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)
                with self.subTest(extension=extension, name="color is not extended"):
                    write_asset(path, *quantized_document(extension, {
                        "POSITION": FLOAT_POSITIONS, "COLOR_0": (5120, True, ((1, 1, 1),) * 3)}))
                    with self.assertRaisesRegex(Tf.ErrorException, "unsupported component type"):
                        layer.Reload(True)
                    self.assertEqual(layer_text(layer), before)

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

    def test_webp_images_are_served_as_png(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for name, alpha in (("lossless", False), ("lossless_alpha", True), ("lossy", False),
                                ("lossy_alpha", True)):
                data = webp_fixture(name)
                (root / (name + ".webp")).write_bytes(data)
                encoded = base64.b64encode(data).decode()
                for extension in ("gltf", "glb"):
                    for source, image in (
                        ("bufferView", None),
                        ("data URI", {"uri": "data:image/webp;base64," + encoded}),
                        ("file", {"uri": name + ".webp", "mimeType": "image/webp"}),
                    ):
                        with self.subTest(name=name, extension=extension, source=source):
                            path = root / f"{name}_{source[0]}.{extension}"
                            document, binary = webp_document(extension, image, data)
                            write_asset(path, document, binary)
                            stage = Usd.Stage.Open(str(path))
                            asset = base_color_file(stage)
                            self.assertEqual(asset.path, f"./{path.name}[images/0.png]")
                            self.assert_quadrants(asset_bytes(asset.resolvedPath), alpha)

    def test_webp_is_used_over_its_fallback_and_does_not_warn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = root / ("asset." + extension)
                    document, binary = webp_document(extension, None, webp_fixture("lossy"))
                    # Optional, with a plain source for clients without the extension.
                    del document["extensionsRequired"]
                    document["images"].append({"uri": "data:image/png;base64,"
                                               + base64.b64encode(PNG).decode()})
                    document["textures"][0]["source"] = 1
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    asset = base_color_file(stage)
                    self.assertEqual(asset.path, f"./{path.name}[images/0.png]")
                    self.assert_quadrants(asset_bytes(asset.resolvedPath), False)
                    result = subprocess.run(
                        [sys.executable, "-c",
                         "import sys\nfrom pxr import Usd\nUsd.Stage.Open(sys.argv[1])",
                         str(path)],
                        capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn("EXT_texture_webp", result.stderr)

    def test_ktx2_is_preferred_over_webp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = root / ("asset." + extension)
                    document, binary = ktx2_document(extension, None, ktx2_fixture("etc1s"))
                    # An animation is not a valid WebP image here, so using it would fail.
                    document["images"].append({"uri": "data:image/webp;base64,"
                                               + base64.b64encode(webp_fixture("animated")).decode()})
                    document["textures"][0]["extensions"]["EXT_texture_webp"] = {"source": 1}
                    document["extensionsUsed"].append("EXT_texture_webp")
                    write_asset(path, document, binary)
                    asset = base_color_file(Usd.Stage.Open(str(path)))
                    self.assertEqual(asset.path, f"./{path.name}[images/0.png]")
                    self.assert_quadrants(asset_bytes(asset.resolvedPath), False)

    def test_webp_file_resolves_after_moving_and_on_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "image.webp").write_bytes(webp_fixture("lossy"))
            document, binary = webp_document("gltf", {"uri": "image.webp"}, b"")
            write_asset(source / "asset.gltf", document, binary)
            moved = root / "moved"
            source.rename(moved)
            stage = Usd.Stage.Open(str(moved / "asset.gltf"))
            asset = base_color_file(stage)
            self.assertEqual(asset.path, "./asset.gltf[images/0.png]")
            self.assert_quadrants(asset_bytes(asset.resolvedPath), False)

            # The same request is answered from the file's current content.
            (moved / "image.webp").write_bytes(webp_fixture("lossless_alpha"))
            self.assert_quadrants(asset_bytes(asset.resolvedPath), True)

    def test_webp_failures_preserve_layer(self):
        valid = webp_fixture("lossy_alpha")
        chunk_too_long = bytearray(valid)
        struct.pack_into("<I", chunk_too_long, 16, len(valid))
        bad_signature = bytearray(webp_fixture("lossy"))
        bad_signature[23:26] = b"\0\0\0"
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "image.webp").write_bytes(webp_fixture("animated"))
            for extension in ("gltf", "glb"):
                path = root / ("asset." + extension)
                document, binary = webp_document(extension, None, valid)
                write_asset(path, document, binary)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer_text(layer)
                for case, data, message in (
                    ("header only", b"RIFF\4\0\0\0WEBP", "WebP image is damaged"),
                    ("truncated", valid[:len(valid) // 2], "WebP image is damaged"),
                    ("chunk too long", bytes(chunk_too_long), "WebP image is damaged"),
                    ("no image chunk", riff([(b"EXIF", b"\0" * 6)]), "WebP image is damaged"),
                    ("bitstream signature", bytes(bad_signature), "WebP image is damaged"),
                    ("animated", webp_fixture("animated"), "animated WebP image is not supported"),
                ):
                    with self.subTest(extension=extension, case=case):
                        invalid, invalid_binary = webp_document(extension, None, data)
                        write_asset(path, invalid, invalid_binary)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)
                for case, change, message in (
                    ("plain source", lambda d: d["textures"].__setitem__(0, {"source": 0}),
                     "WebP image requires EXT_texture_webp"),
                    ("not WebP", lambda d: d["images"].__setitem__(0, {
                        "uri": "data:image/png;base64," + base64.b64encode(PNG).decode()}),
                     "EXT_texture_webp image is not WebP"),
                    ("animated file", lambda d: d["images"].__setitem__(0, {"uri": "image.webp"}),
                     "animated WebP image is not supported"),
                ):
                    with self.subTest(extension=extension, case=case):
                        invalid, invalid_binary = webp_document(extension, None, valid)
                        change(invalid)
                        write_asset(path, invalid, invalid_binary)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)

    def test_damaged_webp_body_fails_when_read(self):
        # The headers are valid, so import succeeds and the image's reader reports it.
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "asset.glb"
            document, binary = webp_document("glb", None, webp_without_tokens())
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
                        "uri": "data:image/png;base64,AAAAAAAAAAAA"}), "embedded image is not PNG, JPEG, KTX2 or WebP"),
                    ("webp data", lambda d: d["images"].__setitem__(0, {
                        "uri": "data:image/webp;base64,UklGRg=="}), "embedded image is not PNG, JPEG, KTX2 or WebP"),
                    ("missing file", lambda d: d["images"].__setitem__(0, {"uri": "absent.png"}),
                     "cannot resolve external image"),
                    ("parent path", lambda d: d["images"].__setitem__(0, {"uri": "../outside.png"}),
                     "unsafe or unsupported external image URI"),
                    ("absolute path", lambda d: d["images"].__setitem__(0, {"uri": "/outside.png"}),
                     "unsafe or unsupported external image URI"),
                    ("remote", lambda d: d["images"].__setitem__(0, {"uri": "https://example.com/a.png"}),
                     "unsafe or unsupported external image URI"),
                    ("not an image", lambda d: d["images"].__setitem__(0, {"uri": "not-image.png"}),
                     "external image is not PNG, JPEG, KTX2 or WebP"),
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

    def test_unlit_materials(self):
        def connected(shader, name):
            return UsdShade.Shader(shader.GetInput(name).GetConnectedSource()[0].GetPrim())

        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                for mode, required in (("OPAQUE", False), ("MASK", True), ("BLEND", False)):
                    with self.subTest(extension=extension, mode=mode, required=required):
                        path = pathlib.Path(directory) / f"unlit_{mode}.{extension}"
                        document, binary = mesh_document(extension)
                        document["extensionsUsed"] = ["KHR_materials_unlit"]
                        if required:
                            document["extensionsRequired"] = ["KHR_materials_unlit"]
                        document["meshes"][0]["primitives"][0]["material"] = 0
                        document["meshes"][0]["primitives"][1]["material"] = 1
                        # The fallback properties, which glTF recommends for clients without the extension.
                        lit = {
                            "pbrMetallicRoughness": {
                                "baseColorFactor": [0.2, 0.4, 0.6, 0.25],
                                "metallicFactor": 0.3,
                                "roughnessFactor": 0.7,
                            },
                            "emissiveFactor": [0.1, 0.2, 0.3],
                            "alphaMode": mode,
                            "alphaCutoff": 0.375,
                            "doubleSided": True,
                        }
                        document["materials"] = [{**lit, "extensions": {"KHR_materials_unlit": {}}}, lit]
                        write_asset(path, document, binary)
                        result = subprocess.run(
                            [sys.executable, "-c",
                             "from pxr import Usd; import sys; assert Usd.Stage.Open(sys.argv[1])", str(path)],
                            capture_output=True, text=True, check=False,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertNotIn("KHR_materials_unlit", result.stderr)

                        stage = Usd.Stage.Open(str(path))
                        material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                        preview = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/preview/surface")
                        mtlx = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/mtlx/surface")
                        self.assert_material_nodegraphs(material, preview, mtlx)
                        self.assertEqual(preview.GetIdAttr().Get(), "UsdPreviewSurface")
                        self.assertEqual(mtlx.GetIdAttr().Get(), "ND_gltf_pbr_surfaceshader")
                        base = Gf.Vec3f(0.2, 0.4, 0.6)
                        # The base color is emitted, and nothing is reflected.
                        self.assertEqual(preview.GetInput("diffuseColor").Get(), Gf.Vec3f(0))
                        self.assertEqual(preview.GetInput("metallic").Get(), 1)
                        self.assertEqual(preview.GetInput("roughness").Get(), 1)
                        self.assertTrue(Gf.IsClose(preview.GetInput("emissiveColor").Get(), base, 1e-6))
                        self.assertFalse(preview.GetInput("useSpecularWorkflow"))
                        self.assertEqual(mtlx.GetInput("base_color").Get(), Gf.Vec3f(0))
                        self.assertEqual(mtlx.GetInput("metallic").Get(), 0)
                        self.assertEqual(mtlx.GetInput("specular").Get(), 0)
                        self.assertTrue(Gf.IsClose(mtlx.GetInput("emissive").Get(), base, 1e-6))
                        self.assertFalse(mtlx.GetInput("emissive_strength"))
                        # Alpha coverage still applies.
                        self.assertEqual(mtlx.GetInput("alpha_mode").Get(), {"OPAQUE": 0, "MASK": 1, "BLEND": 2}[mode])
                        if mode == "OPAQUE":
                            self.assertFalse(preview.GetInput("opacity"))
                            self.assertFalse(mtlx.GetInput("alpha"))
                        else:
                            self.assertAlmostEqual(preview.GetInput("opacity").Get(), 0.25, places=6)
                            self.assertAlmostEqual(mtlx.GetInput("alpha").Get(), 0.25, places=6)
                        if mode == "MASK":
                            self.assertAlmostEqual(preview.GetInput("opacityThreshold").Get(), 0.375, places=6)
                            self.assertAlmostEqual(mtlx.GetInput("alpha_cutoff").Get(), 0.375, places=6)
                        mesh = UsdGeom.Mesh.Get(stage, "/Asset/geo/Node_0/Mesh_0/Primitive_0")
                        bound, _ = UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()
                        self.assertEqual(bound.GetPath(), material.GetPath())
                        self.assertTrue(mesh.GetDoubleSidedAttr().Get())

                        # The same properties without the extension stay lit.
                        lit_preview = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_1/preview/surface")
                        lit_mtlx = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_1/mtlx/surface")
                        self.assertTrue(Gf.IsClose(lit_preview.GetInput("diffuseColor").Get(), base, 1e-6))
                        self.assertTrue(Gf.IsClose(lit_preview.GetInput("emissiveColor").Get(),
                                                   Gf.Vec3f(0.1, 0.2, 0.3), 1e-6))
                        self.assertFalse(lit_mtlx.GetInput("specular"))
                        self.assertTrue(Gf.IsClose(lit_mtlx.GetInput("base_color").Get(), base, 1e-6))

    def test_unlit_textures_and_vertex_colors(self):
        def source_of(shader, name):
            connection = shader.GetInput(name).GetConnectedSource()
            return UsdShade.Shader(connection[0].GetPrim()) if connection else None

        unlit = {"KHR_materials_unlit": {}}
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension, case="texture"):
                    path = root / ("texture." + extension)
                    document, binary = textured_document(extension, None, PNG)
                    document["extensionsUsed"] = ["KHR_materials_unlit"]
                    # Fallback textures are never read, so a damaged one does not matter.
                    document["images"].append({"uri": "data:image/png;base64,AA=="})
                    document["textures"].append({"source": 1})
                    fallback = {"index": 1}
                    material = document["materials"][0]
                    material["pbrMetallicRoughness"].update(baseColorFactor=[0.5, 0.25, 1, 0.5],
                                                            metallicRoughnessTexture=fallback)
                    material.update(alphaMode="BLEND", normalTexture=fallback, occlusionTexture=fallback,
                                    emissiveTexture=fallback, emissiveFactor=[1, 1, 1], extensions=unlit)
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    usd_material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")

                    preview, _, _ = usd_material.ComputeSurfaceSource()
                    texture = source_of(preview, "emissiveColor")
                    self.assertEqual(texture.GetPath().name, "baseColorTexture")
                    self.assertEqual(texture.GetInput("sourceColorSpace").Get(), "sRGB")
                    self.assertEqual(texture.GetInput("scale").Get(), Gf.Vec4f(0.5, 0.25, 1, 0.5))
                    self.assertEqual(source_of(preview, "opacity").GetPath(), texture.GetPath())
                    self.assertIsNone(source_of(preview, "diffuseColor"))
                    self.assertEqual(preview.GetInput("diffuseColor").Get(), Gf.Vec3f(0))
                    for name in ("metallic", "roughness"):
                        self.assertIsNone(source_of(preview, name))
                    for name in ("normal", "occlusion"):
                        self.assertFalse(preview.GetInput(name))

                    mtlx, _, _ = usd_material.ComputeSurfaceSource("mtlx")
                    factor = source_of(mtlx, "emissive")
                    self.assertEqual(factor.GetPath().name, "baseColorFactor")
                    self.assertEqual(factor.GetInput("in2").Get(), Gf.Vec3f(0.5, 0.25, 1))
                    self.assertEqual(source_of(factor, "in1").GetPath().name, "baseColorRgb")
                    alpha = source_of(mtlx, "alpha")
                    self.assertEqual(alpha.GetPath().name, "alphaFactor")
                    self.assertEqual(source_of(alpha, "in1").GetPath().name, "baseColorA")
                    self.assertIsNone(source_of(mtlx, "base_color"))
                    self.assertEqual(mtlx.GetInput("base_color").Get(), Gf.Vec3f(0))
                    for name in ("metallic", "roughness"):
                        self.assertIsNone(source_of(mtlx, name))
                    for name in ("normal", "occlusion"):
                        self.assertFalse(mtlx.GetInput(name))

                    names = {prim.GetName() for prim in Usd.PrimRange(usd_material.GetPrim())}
                    for slot in ("metallicRoughness", "normal", "occlusion", "emissive"):
                        self.assertFalse({name for name in names if name.startswith(slot)}, slot)

                with self.subTest(extension=extension, case="vertex colors"):
                    path = root / ("colors." + extension)
                    document, binary = color_document(extension, 5126, 4)
                    document["extensionsUsed"] = ["KHR_materials_unlit"]
                    primitives = document["meshes"][0]["primitives"]
                    primitives[0]["material"] = 0
                    primitives[1]["material"] = 0
                    del primitives[1]["attributes"]["COLOR_0"]
                    document["materials"] = [{
                        "alphaMode": "BLEND",
                        "pbrMetallicRoughness": {"baseColorFactor": [0.5, 0.25, 1, 0.5]},
                        "extensions": unlit,
                    }]
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    usd_material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0_VertexColor")
                    preview, _, _ = usd_material.ComputeSurfaceSource()
                    reader = source_of(preview, "emissiveColor")
                    self.assertEqual(reader.GetIdAttr().Get(), "UsdPrimvarReader_float3")
                    self.assertEqual(reader.GetInput("varname").Get(), "gltf_previewBaseColor")
                    self.assertEqual(source_of(preview, "opacity").GetInput("varname").Get(), "gltf_previewAlpha")
                    self.assertEqual(preview.GetInput("diffuseColor").Get(), Gf.Vec3f(0))
                    mesh = stage.GetPrimAtPath("/Asset/geo/Node_0/Mesh_0/Primitive_0")
                    products = UsdGeom.PrimvarsAPI(mesh).GetPrimvar("gltf_previewBaseColor").Get()
                    self.assertTrue(Gf.IsClose(products[1], Gf.Vec3f(0.5, 0, 1), 1e-6))

                    mtlx, _, _ = usd_material.ComputeSurfaceSource("mtlx")
                    factor = source_of(mtlx, "emissive")
                    self.assertEqual(factor.GetPath().name, "baseColorFactor")
                    self.assertEqual(source_of(factor, "in1").GetInput("geomprop").Get(), "displayColor")
                    self.assertEqual(mtlx.GetInput("base_color").Get(), Gf.Vec3f(0))

                    plain = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                    plain_preview, _, _ = plain.ComputeSurfaceSource()
                    self.assertTrue(Gf.IsClose(plain_preview.GetInput("emissiveColor").Get(),
                                               Gf.Vec3f(0.5, 0.25, 1), 1e-6))
                    self.assertIsNone(source_of(plain_preview, "emissiveColor"))

    def test_unlit_failures_preserve_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                path = pathlib.Path(directory) / ("unlit." + extension)
                document, binary = mesh_document(extension)
                document["extensionsUsed"] = ["KHR_materials_unlit"]
                document["materials"] = [{"extensions": {"KHR_materials_unlit": {}}}]
                document["meshes"][0]["primitives"][0]["material"] = 0
                write_asset(path, document, binary)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer_text(layer)
                self.assertEqual(UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/preview/surface")
                                 .GetInput("emissiveColor").Get(), Gf.Vec3f(1))
                for case in ("not an object", "base color", "fallback factor", "fallback UV set"):
                    with self.subTest(extension=extension, case=case):
                        invalid, invalid_binary = mesh_document(extension)
                        invalid["extensionsUsed"] = ["KHR_materials_unlit"]
                        invalid["meshes"][0]["primitives"][0]["material"] = 0
                        material = {"extensions": {"KHR_materials_unlit": {}}}
                        invalid["materials"] = [material]
                        if case == "not an object":
                            material["extensions"]["KHR_materials_unlit"] = True
                            message = "KHR_materials_unlit is not an object"
                        elif case == "base color":
                            material["pbrMetallicRoughness"] = {"baseColorFactor": [1, 1, 1.5, 1]}
                            message = "material base color factor is non-finite or out of range"
                        elif case == "fallback factor":
                            # Ignoring a property does not make an invalid value valid.
                            material["pbrMetallicRoughness"] = {"metallicFactor": 2}
                            message = "material metallic or roughness factor is non-finite or out of range"
                        else:
                            invalid["images"] = [{"uri": "data:image/png;base64,AA=="}]
                            invalid["textures"] = [{"source": 0}]
                            material["normalTexture"] = {"index": 0}
                            message = "texture coordinate set the primitive does not have"
                        write_asset(path, invalid, invalid_binary)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)

    def test_ior_materials(self):
        def open_with_stderr(path):
            return subprocess.run(
                [sys.executable, "-c",
                 "from pxr import Usd; import sys; assert Usd.Stage.Open(sys.argv[1])", str(path)],
                capture_output=True, text=True, check=False,
            )

        def ior_input(shader):
            value = shader.GetInput("ior")
            return value.Get() if value else None

        # The value authored in both paths, or None where the shaders' default of 1.5 applies.
        cases = (
            ("water", {"ior": 1.33}, False, 1.33),
            ("one", {"ior": 1}, False, 1),
            ("large", {"ior": 4}, False, 4),
            ("infinite", {"ior": 0}, False, 1e6),
            ("explicit default", {"ior": 1.5}, False, None),
            ("default", {}, False, None),
            ("unlit", {"ior": 2}, True, None),
        )
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                for (case, ior, unlit, expected), required in itertools.product(cases, (False, True)):
                    with self.subTest(extension=extension, case=case, required=required):
                        name = f"{case.replace(' ', '_')}_{'required' if required else 'optional'}"
                        path = pathlib.Path(directory) / f"ior_{name}.{extension}"
                        document, binary = mesh_document(extension)
                        used = ["KHR_materials_ior"] + (["KHR_materials_unlit"] if unlit else [])
                        document["extensionsUsed"] = used
                        if required:
                            document["extensionsRequired"] = used
                        document["meshes"][0]["primitives"][0]["material"] = 0
                        document["meshes"][0]["primitives"][1]["material"] = 1
                        lit = {"pbrMetallicRoughness": {"baseColorFactor": [0.2, 0.4, 0.6, 1],
                                                        "metallicFactor": 0.25, "roughnessFactor": 0.5}}
                        extensions = {"KHR_materials_ior": ior}
                        if unlit:
                            extensions["KHR_materials_unlit"] = {}
                        document["materials"] = [{**lit, "extensions": extensions}, lit]
                        write_asset(path, document, binary)
                        result = open_with_stderr(path)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertNotIn("'KHR_materials_ior' is not supported", result.stderr)
                        self.assertEqual("KHR_materials_ior is ignored, because the material is unlit"
                                         in result.stderr, unlit, result.stderr)

                        stage = Usd.Stage.Open(str(path))
                        material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                        preview = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/preview/surface")
                        mtlx = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/mtlx/surface")
                        self.assert_material_nodegraphs(material, preview, mtlx)
                        if expected is None:
                            self.assertIsNone(ior_input(preview))
                            self.assertIsNone(ior_input(mtlx))
                        else:
                            self.assertAlmostEqual(ior_input(preview), expected, places=5)
                            self.assertAlmostEqual(ior_input(mtlx), expected, places=5)
                            # gltf_pbr declares its ior uniform.
                            self.assertEqual(mtlx.GetInput("ior").GetAttr().GetVariability(),
                                             Sdf.VariabilityUniform)
                            self.assertFalse(preview.GetInput("ior").GetAttr().GetVariability()
                                             == Sdf.VariabilityUniform)
                        # The ior changes only the Fresnel term; every other input is as without it.
                        self.assertTrue(Gf.IsClose(preview.GetInput("diffuseColor").Get(),
                                                   Gf.Vec3f(0) if unlit else Gf.Vec3f(0.2, 0.4, 0.6), 1e-6))
                        self.assertFalse(preview.GetInput("useSpecularWorkflow"))
                        if not unlit:
                            self.assertFalse(mtlx.GetInput("specular"))
                            self.assertAlmostEqual(preview.GetInput("metallic").Get(), 0.25, places=6)
                            self.assertAlmostEqual(mtlx.GetInput("roughness").Get(), 0.5, places=6)
                        # A material without the extension authors no ior.
                        self.assertIsNone(ior_input(UsdShade.Shader.Get(stage, "/Asset/mtl/Material_1/preview/surface")))
                        self.assertIsNone(ior_input(UsdShade.Shader.Get(stage, "/Asset/mtl/Material_1/mtlx/surface")))

    def test_ior_failures_preserve_layer(self):
        cases = (
            ("not an object", 1.4, "KHR_materials_ior is not an object"),
            ("wrong type", {"ior": "1.4"}, "KHR_materials_ior has a field of the wrong type"),
            ("overflow", {"ior": 1e39}, "KHR_materials_ior is out of range"),
            ("below one", {"ior": 0.5}, "material ior is neither 0 nor at least 1"),
            ("negative", {"ior": -1.5}, "material ior is neither 0 nor at least 1"),
            ("unlit below one", {"ior": 0.5}, "material ior is neither 0 nor at least 1"),
        )
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                path = pathlib.Path(directory) / ("ior." + extension)
                document, binary = mesh_document(extension)
                document["extensionsUsed"] = ["KHR_materials_ior"]
                document["materials"] = [{"extensions": {"KHR_materials_ior": {"ior": 1.25}}}]
                document["meshes"][0]["primitives"][0]["material"] = 0
                write_asset(path, document, binary)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer_text(layer)
                preview = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/preview/surface")
                self.assertAlmostEqual(preview.GetInput("ior").Get(), 1.25, places=6)
                for case, value, message in cases:
                    with self.subTest(extension=extension, case=case):
                        invalid, invalid_binary = mesh_document(extension)
                        invalid["extensionsUsed"] = ["KHR_materials_ior"]
                        invalid["meshes"][0]["primitives"][0]["material"] = 0
                        extensions = {"KHR_materials_ior": value}
                        if case.startswith("unlit"):
                            # Ignoring a property does not make an invalid value valid.
                            invalid["extensionsUsed"].append("KHR_materials_unlit")
                            extensions["KHR_materials_unlit"] = {}
                        invalid["materials"] = [{"extensions": extensions}]
                        write_asset(path, invalid, invalid_binary)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)

    def test_clearcoat_materials(self):
        def open_with_stderr(path):
            return subprocess.run(
                [sys.executable, "-c",
                 "from pxr import Usd; import sys; assert Usd.Stage.Open(sys.argv[1])", str(path)],
                capture_output=True, text=True, check=False,
            )

        def value_of(shader, name):
            value = shader.GetInput(name)
            return value.Get() if value else None

        ignored = "KHR_materials_clearcoat is ignored, because the material is unlit"
        fresnel = "UsdPreviewSurface computes the clearcoat's Fresnel term from the material's ior"
        # The clearcoat and its roughness authored in both paths, or None where the layer is disabled.
        cases = (
            ("factors", {"clearcoatFactor": 0.5, "clearcoatRoughnessFactor": 0.25}, {}, (0.5, 0.25)),
            # Preview's clearcoat roughness defaults to 0.01, so glTF's 0 is authored.
            ("smooth", {"clearcoatFactor": 1}, {}, (1, 0)),
            ("disabled", {"clearcoatRoughnessFactor": 0.5}, {}, None),
            ("empty", {}, {}, None),
            ("unlit", {"clearcoatFactor": 1}, {"KHR_materials_unlit": {}}, None),
            ("with ior", {"clearcoatFactor": 1}, {"KHR_materials_ior": {"ior": 2}}, (1, 0)),
        )
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                for (case, clearcoat, others, expected), required in itertools.product(cases, (False, True)):
                    with self.subTest(extension=extension, case=case, required=required):
                        name = f"{case.replace(' ', '_')}_{'required' if required else 'optional'}"
                        path = pathlib.Path(directory) / f"clearcoat_{name}.{extension}"
                        document, binary = mesh_document(extension)
                        used = ["KHR_materials_clearcoat", *others]
                        document["extensionsUsed"] = used
                        if required:
                            document["extensionsRequired"] = used
                        document["meshes"][0]["primitives"][0]["material"] = 0
                        document["meshes"][0]["primitives"][1]["material"] = 1
                        lit = {"pbrMetallicRoughness": {"baseColorFactor": [0.2, 0.4, 0.6, 1],
                                                        "metallicFactor": 0.25, "roughnessFactor": 0.5}}
                        extensions = {"KHR_materials_clearcoat": clearcoat, **others}
                        document["materials"] = [{**lit, "extensions": extensions}, lit]
                        write_asset(path, document, binary)
                        result = open_with_stderr(path)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertNotIn("'KHR_materials_clearcoat' is not supported", result.stderr)
                        self.assertEqual(ignored in result.stderr, case == "unlit", result.stderr)
                        self.assertEqual(fresnel in result.stderr, case == "with ior", result.stderr)

                        stage = Usd.Stage.Open(str(path))
                        material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                        preview = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/preview/surface")
                        mtlx = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/mtlx/surface")
                        self.assert_material_nodegraphs(material, preview, mtlx)
                        if expected is None:
                            for shader, names in ((preview, ("clearcoat", "clearcoatRoughness")),
                                                  (mtlx, ("clearcoat", "clearcoat_roughness", "clearcoat_normal"))):
                                for input_name in names:
                                    self.assertFalse(shader.GetInput(input_name), input_name)
                        else:
                            amount, roughness = expected
                            self.assertAlmostEqual(value_of(preview, "clearcoat"), amount, places=6)
                            self.assertAlmostEqual(value_of(preview, "clearcoatRoughness"), roughness, places=6)
                            self.assertAlmostEqual(value_of(mtlx, "clearcoat"), amount, places=6)
                            self.assertAlmostEqual(value_of(mtlx, "clearcoat_roughness"), roughness, places=6)
                            # Without a clearcoat normal texture, gltf_pbr uses the geometric normal.
                            self.assertFalse(mtlx.GetInput("clearcoat_normal"))
                        # The clearcoat changes nothing of the base layer.
                        if case != "unlit":
                            self.assertTrue(Gf.IsClose(preview.GetInput("diffuseColor").Get(),
                                                       Gf.Vec3f(0.2, 0.4, 0.6), 1e-6))
                            self.assertAlmostEqual(preview.GetInput("metallic").Get(), 0.25, places=6)
                            self.assertAlmostEqual(preview.GetInput("roughness").Get(), 0.5, places=6)
                            self.assertAlmostEqual(mtlx.GetInput("roughness").Get(), 0.5, places=6)
                            self.assertFalse(mtlx.GetInput("specular"))
                        # A material without the extension authors no clearcoat.
                        plain = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_1/preview/surface")
                        plain_mtlx = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_1/mtlx/surface")
                        self.assertFalse(plain.GetInput("clearcoat"))
                        self.assertFalse(plain_mtlx.GetInput("clearcoat"))

    def test_clearcoat_textures(self):
        def open_with_stderr(path):
            return subprocess.run(
                [sys.executable, "-c",
                 "from pxr import Usd; import sys; assert Usd.Stage.Open(sys.argv[1])", str(path)],
                capture_output=True, text=True, check=False,
            )

        def source_of(shader, name):
            connection = shader.GetInput(name).GetConnectedSource() if shader.GetInput(name) else None
            return (UsdShade.Shader(connection[0].GetPrim()), connection[1]) if connection else (None, None)

        normal_warning = "UsdPreviewSurface has no clearcoat normal; its network ignores clearcoatNormalTexture"
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension, case="textures"):
                    path = root / ("clearcoat." + extension)
                    document, binary = textured_document(extension, None, PNG)
                    document["extensionsUsed"] = ["KHR_materials_clearcoat"]
                    document["samplers"] = [{"wrapS": 33648, "wrapT": 33071}]
                    document["textures"].append({"source": 0, "sampler": 0})
                    material = document["materials"][0]
                    material["normalTexture"] = {"index": 0}
                    material["extensions"] = {"KHR_materials_clearcoat": {
                        "clearcoatFactor": 0.5, "clearcoatTexture": {"index": 1, "texCoord": 1},
                        "clearcoatRoughnessFactor": 0.25, "clearcoatRoughnessTexture": {"index": 0, "texCoord": 2},
                        "clearcoatNormalTexture": {"index": 0, "scale": 0.75},
                    }}
                    write_asset(path, document, binary)
                    result = open_with_stderr(path)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(normal_warning, result.stderr)
                    stage = Usd.Stage.Open(str(path))
                    usd_material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                    expected = f"./{path.name}[images/0.png]"

                    preview, _, _ = usd_material.ComputeSurfaceSource()
                    amount, amount_output = source_of(preview, "clearcoat")
                    self.assertEqual((amount.GetPath().name, amount_output), ("clearcoatTexture", "r"))
                    self.assertEqual(amount.GetInput("sourceColorSpace").Get(), "raw")
                    self.assertEqual(amount.GetInput("scale").Get(), Gf.Vec4f(0.5, 1, 1, 1))
                    self.assertFalse(amount.GetInput("bias"))
                    self.assertEqual(amount.GetInput("wrapS").Get(), "mirror")
                    self.assertEqual(amount.GetInput("wrapT").Get(), "clamp")
                    self.assertEqual(source_of(amount, "st")[0].GetInput("varname").Get(), "st1")
                    rough, rough_output = source_of(preview, "clearcoatRoughness")
                    self.assertEqual((rough.GetPath().name, rough_output), ("clearcoatRoughnessTexture", "g"))
                    self.assertEqual(rough.GetInput("sourceColorSpace").Get(), "raw")
                    self.assertEqual(rough.GetInput("scale").Get(), Gf.Vec4f(1, 0.25, 1, 1))
                    self.assertEqual(rough.GetInput("wrapS").Get(), "repeat")
                    self.assertEqual(source_of(rough, "st")[0].GetInput("varname").Get(), "st2")
                    # Preview's clearcoat reads the base normal, which keeps its own texture.
                    self.assertEqual(source_of(preview, "normal")[0].GetPath().name, "normalTexture")
                    preview_graph = usd_material.GetPrim().GetChild("preview")
                    self.assertFalse([prim for prim in Usd.PrimRange(preview_graph)
                                      if prim.GetName().startswith("clearcoatNormal")])

                    mtlx, _, _ = usd_material.ComputeSurfaceSource("mtlx")
                    factor, _ = source_of(mtlx, "clearcoat")
                    self.assertEqual(factor.GetPath().name, "clearcoatFactor")
                    self.assertEqual(factor.GetIdAttr().Get(), "ND_multiply_float")
                    self.assertAlmostEqual(factor.GetInput("in2").Get(), 0.5)
                    channel, _ = source_of(factor, "in1")
                    self.assertEqual(channel.GetPath().name, "clearcoatChannel")
                    self.assertEqual(channel.GetInput("index").Get(), 0)
                    image, _ = source_of(channel, "in")
                    self.assertEqual(image.GetPath().name, "clearcoatImage")
                    self.assertEqual(image.GetIdAttr().Get(), "ND_image_vector4")
                    self.assertFalse(image.GetInput("file").GetAttr().HasColorSpace())
                    self.assertEqual(image.GetInput("uaddressmode").Get(), "mirror")
                    self.assertEqual(image.GetInput("vaddressmode").Get(), "clamp")
                    self.assertEqual(source_of(image, "texcoord")[0].GetInput("geomprop").Get(), "st1")
                    rough_factor, _ = source_of(mtlx, "clearcoat_roughness")
                    self.assertEqual(rough_factor.GetPath().name, "clearcoatRoughnessFactor")
                    self.assertAlmostEqual(rough_factor.GetInput("in2").Get(), 0.25)
                    rough_channel, _ = source_of(rough_factor, "in1")
                    self.assertEqual(rough_channel.GetInput("index").Get(), 1)
                    rough_image, _ = source_of(rough_channel, "in")
                    self.assertEqual(rough_image.GetPath().name, "clearcoatRoughnessImage")
                    self.assertEqual(source_of(rough_image, "texcoord")[0].GetInput("geomprop").Get(), "st2")
                    coat_map, _ = source_of(mtlx, "clearcoat_normal")
                    self.assertEqual(coat_map.GetPath().name, "clearcoatNormalMap")
                    self.assertEqual(coat_map.GetIdAttr().Get(), "ND_normalmap_float")
                    self.assertAlmostEqual(coat_map.GetInput("scale").Get(), 0.75)
                    self.assertEqual(mtlx.GetInput("clearcoat_normal").GetTypeName(), Sdf.ValueTypeNames.Vector3f)
                    coat_xyz, _ = source_of(coat_map, "in")
                    self.assertEqual(coat_xyz.GetIdAttr().Get(), "ND_convert_vector4_vector3")
                    coat_image, _ = source_of(coat_xyz, "in")
                    self.assertEqual(coat_image.GetPath().name, "clearcoatNormalImage")
                    self.assertFalse(coat_image.GetInput("file").GetAttr().HasColorSpace())
                    # The base normal map is separate, and unchanged.
                    base_map, _ = source_of(mtlx, "normal")
                    self.assertEqual(base_map.GetPath().name, "normalMap")
                    self.assertFalse(base_map.GetInput("scale"))

                    for shader in (amount, rough, image, rough_image, coat_image):
                        asset = shader.GetInput("file").Get()
                        self.assertEqual(asset.path, expected)
                        self.assertEqual(asset_bytes(asset.resolvedPath), PNG)

                with self.subTest(extension=extension, case="factor of one"):
                    path = root / ("clearcoat_one." + extension)
                    document, binary = textured_document(extension, None, PNG)
                    document["extensionsUsed"] = ["KHR_materials_clearcoat"]
                    document["materials"][0]["extensions"] = {"KHR_materials_clearcoat": {
                        "clearcoatFactor": 1, "clearcoatTexture": {"index": 0},
                        "clearcoatRoughnessTexture": {"index": 0},
                    }}
                    write_asset(path, document, binary)
                    result = open_with_stderr(path)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn(normal_warning, result.stderr)
                    stage = Usd.Stage.Open(str(path))
                    usd_material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                    mtlx, _, _ = usd_material.ComputeSurfaceSource("mtlx")
                    # A factor of 1 needs no multiply; the default roughness factor of 0 does.
                    self.assertEqual(source_of(mtlx, "clearcoat")[0].GetPath().name, "clearcoatChannel")
                    rough_factor, _ = source_of(mtlx, "clearcoat_roughness")
                    self.assertEqual(rough_factor.GetPath().name, "clearcoatRoughnessFactor")
                    self.assertEqual(rough_factor.GetInput("in2").Get(), 0)
                    self.assertFalse(mtlx.GetInput("clearcoat_normal"))
                    preview, _, _ = usd_material.ComputeSurfaceSource()
                    self.assertEqual(source_of(preview, "clearcoat")[0].GetInput("scale").Get(), Gf.Vec4f(1))
                    self.assertEqual(source_of(preview, "clearcoatRoughness")[0].GetInput("scale").Get(),
                                     Gf.Vec4f(1, 0, 1, 1))

                for case, extensions in (
                    ("disabled", {"KHR_materials_clearcoat": {"clearcoatRoughnessFactor": 0.5}}),
                    ("unlit", {"KHR_materials_clearcoat": {"clearcoatFactor": 1}, "KHR_materials_unlit": {}}),
                ):
                    with self.subTest(extension=extension, case=case):
                        path = root / (f"clearcoat_{case}." + extension)
                        document, binary = textured_document(extension, None, PNG)
                        document["extensionsUsed"] = list(extensions)
                        # A disabled clearcoat's textures are never read, so a damaged one does not matter.
                        document["images"].append({"uri": "data:image/png;base64,AA=="})
                        document["textures"].append({"source": 1})
                        damaged = {"index": 1}
                        extensions["KHR_materials_clearcoat"].update(
                            clearcoatTexture=damaged, clearcoatRoughnessTexture=damaged,
                            clearcoatNormalTexture=damaged)
                        document["materials"][0]["extensions"] = extensions
                        write_asset(path, document, binary)
                        result = open_with_stderr(path)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertNotIn(normal_warning, result.stderr)
                        stage = Usd.Stage.Open(str(path))
                        usd_material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                        names = {prim.GetName() for prim in Usd.PrimRange(usd_material.GetPrim())}
                        self.assertFalse({name for name in names if name.startswith("clearcoat")})
                        self.assertFalse(usd_material.ComputeSurfaceSource()[0].GetInput("clearcoat"))
                        self.assertFalse(usd_material.ComputeSurfaceSource("mtlx")[0].GetInput("clearcoat"))

    def test_clearcoat_failures_preserve_layer(self):
        texture_range = "material texture uses a texture coordinate set the primitive does not have"
        factor_range = "material clearcoat factor or roughness is non-finite or out of range"
        cases = (
            ("not an object", 0.5, "KHR_materials_clearcoat is not an object"),
            ("factor of the wrong type", {"clearcoatFactor": "0.5"},
             "KHR_materials_clearcoat has a field of the wrong type"),
            ("roughness of the wrong type", {"clearcoatFactor": 1, "clearcoatRoughnessFactor": [0.5]},
             "KHR_materials_clearcoat has a field of the wrong type"),
            ("texture not an object", {"clearcoatFactor": 1, "clearcoatTexture": 0},
             "KHR_materials_clearcoat has a field of the wrong type"),
            ("texture without an index", {"clearcoatFactor": 1, "clearcoatRoughnessTexture": {}},
             "KHR_materials_clearcoat has a field of the wrong type"),
            ("normal scale of the wrong type",
             {"clearcoatFactor": 1, "clearcoatNormalTexture": {"index": 0, "scale": "1"}},
             "KHR_materials_clearcoat has a field of the wrong type"),
            ("overflow", {"clearcoatFactor": 1e39}, "KHR_materials_clearcoat is out of range"),
            ("normal scale overflow", {"clearcoatFactor": 1, "clearcoatNormalTexture": {"index": 0, "scale": -1e39}},
             "KHR_materials_clearcoat is out of range"),
            ("factor above one", {"clearcoatFactor": 1.5}, factor_range),
            ("negative roughness", {"clearcoatFactor": 1, "clearcoatRoughnessFactor": -0.5}, factor_range),
            ("missing UV set", {"clearcoatFactor": 1, "clearcoatTexture": {"index": 0, "texCoord": 1}},
             texture_range),
            ("texture out of range", {"clearcoatFactor": 1, "clearcoatTexture": {"index": 5}},
             "texture index is out of range|cannot parse"),
            # Ignoring a layer does not make an invalid value or a missing UV set valid.
            ("disabled missing UV set", {"clearcoatNormalTexture": {"index": 0, "texCoord": 1}}, texture_range),
            ("unlit above one", {"clearcoatFactor": 2}, factor_range),
        )
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                path = pathlib.Path(directory) / ("clearcoat." + extension)
                document, binary = textured_document(extension, None, PNG)
                document["extensionsUsed"] = ["KHR_materials_clearcoat"]
                document["materials"][0]["extensions"] = {"KHR_materials_clearcoat": {"clearcoatFactor": 0.5}}
                # The second primitive has one UV set only.
                document["materials"].append({})
                document["meshes"][0]["primitives"][1]["material"] = 1
                write_asset(path, document, binary)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer_text(layer)
                preview = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/preview/surface")
                self.assertAlmostEqual(preview.GetInput("clearcoat").Get(), 0.5, places=6)
                for case, value, message in cases:
                    with self.subTest(extension=extension, case=case):
                        invalid, invalid_binary = textured_document(extension, None, PNG)
                        invalid["extensionsUsed"] = ["KHR_materials_clearcoat"]
                        invalid["meshes"][0]["primitives"][1]["material"] = 1
                        extensions = {"KHR_materials_clearcoat": value}
                        if case.startswith("unlit"):
                            invalid["extensionsUsed"].append("KHR_materials_unlit")
                            extensions["KHR_materials_unlit"] = {}
                        invalid["materials"].append({"extensions": extensions})
                        write_asset(path, invalid, invalid_binary)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)

    def test_transmission_materials(self):
        def open_with_stderr(path):
            return subprocess.run(
                [sys.executable, "-c",
                 "from pxr import Usd; import sys; assert Usd.Stage.Open(sys.argv[1])", str(path)],
                capture_output=True, text=True, check=False,
            )

        ignored = "KHR_materials_transmission is ignored, because the material is unlit"
        preview_warning = "UsdPreviewSurface has no transmission; its network ignores KHR_materials_transmission"
        # The transmission authored in MaterialX, or None where there is none.
        cases = (
            ("half", {"transmissionFactor": 0.5}, {}, 0.5),
            ("full", {"transmissionFactor": 1}, {}, 1),
            ("zero", {"transmissionFactor": 0}, {}, None),
            ("empty", {}, {}, None),
            ("unlit", {"transmissionFactor": 1}, {"KHR_materials_unlit": {}}, None),
            ("with ior", {"transmissionFactor": 1}, {"KHR_materials_ior": {"ior": 1.25}}, 1),
        )
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                for (case, transmission, others, expected), required in itertools.product(cases, (False, True)):
                    with self.subTest(extension=extension, case=case, required=required):
                        name = f"{case.replace(' ', '_')}_{'required' if required else 'optional'}"
                        path = pathlib.Path(directory) / f"transmission_{name}.{extension}"
                        document, binary = mesh_document(extension)
                        used = ["KHR_materials_transmission", *others]
                        document["extensionsUsed"] = used
                        if required:
                            document["extensionsRequired"] = used
                        document["meshes"][0]["primitives"][0]["material"] = 0
                        document["meshes"][0]["primitives"][1]["material"] = 1
                        lit = {"pbrMetallicRoughness": {"baseColorFactor": [0.2, 0.4, 0.6, 1],
                                                        "metallicFactor": 0.25, "roughnessFactor": 0.5}}
                        extensions = {"KHR_materials_transmission": transmission, **others}
                        document["materials"] = [{**lit, "extensions": extensions}, lit]
                        write_asset(path, document, binary)
                        result = open_with_stderr(path)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertNotIn("'KHR_materials_transmission' is not supported", result.stderr)
                        self.assertEqual(ignored in result.stderr, case == "unlit", result.stderr)
                        self.assertEqual(preview_warning in result.stderr, expected is not None, result.stderr)

                        stage = Usd.Stage.Open(str(path))
                        material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                        preview = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/preview/surface")
                        mtlx = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/mtlx/surface")
                        self.assert_material_nodegraphs(material, preview, mtlx)
                        if expected is None:
                            self.assertFalse(mtlx.GetInput("transmission"))
                        else:
                            self.assertAlmostEqual(mtlx.GetInput("transmission").Get(), expected, places=6)
                        # Without KHR_materials_volume the material is thin, gltf_pbr's default.
                        for input_name in ("thickness", "attenuation_distance", "attenuation_color"):
                            self.assertFalse(mtlx.GetInput(input_name), input_name)
                        # UsdPreviewSurface has no transmission, and its opacity remains glTF's alpha.
                        self.assertFalse(preview.GetInput("transmission"))
                        self.assertFalse(preview.GetInput("opacity"))
                        self.assertEqual(mtlx.GetInput("alpha_mode").Get(), 0)
                        self.assertFalse(mtlx.GetInput("alpha"))
                        # The transmission changes nothing else.
                        if case != "unlit":
                            self.assertTrue(Gf.IsClose(preview.GetInput("diffuseColor").Get(),
                                                       Gf.Vec3f(0.2, 0.4, 0.6), 1e-6))
                            self.assertAlmostEqual(preview.GetInput("metallic").Get(), 0.25, places=6)
                            self.assertAlmostEqual(preview.GetInput("roughness").Get(), 0.5, places=6)
                            self.assertTrue(Gf.IsClose(mtlx.GetInput("base_color").Get(),
                                                       Gf.Vec3f(0.2, 0.4, 0.6), 1e-6))
                            self.assertAlmostEqual(mtlx.GetInput("metallic").Get(), 0.25, places=6)
                            self.assertFalse(mtlx.GetInput("specular"))
                        if case == "with ior":
                            self.assertAlmostEqual(mtlx.GetInput("ior").Get(), 1.25, places=6)
                        # A material without the extension authors no transmission.
                        plain_mtlx = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_1/mtlx/surface")
                        self.assertFalse(plain_mtlx.GetInput("transmission"))

    def test_transmission_textures(self):
        def open_with_stderr(path):
            return subprocess.run(
                [sys.executable, "-c",
                 "from pxr import Usd; import sys; assert Usd.Stage.Open(sys.argv[1])", str(path)],
                capture_output=True, text=True, check=False,
            )

        def source_of(shader, name):
            connection = shader.GetInput(name).GetConnectedSource() if shader.GetInput(name) else None
            return (UsdShade.Shader(connection[0].GetPrim()), connection[1]) if connection else (None, None)

        def transmission_prims(material, graph):
            return [prim for prim in Usd.PrimRange(material.GetPrim().GetChild(graph))
                    if prim.GetName().startswith("transmission")]

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension, case="texture"):
                    path = root / ("transmission." + extension)
                    document, binary = textured_document(extension, None, PNG)
                    document["extensionsUsed"] = ["KHR_materials_transmission", "KHR_texture_transform"]
                    document["samplers"] = [{"wrapS": 33648, "wrapT": 33071}]
                    document["textures"].append({"source": 0, "sampler": 0})
                    document["materials"][0]["extensions"] = {"KHR_materials_transmission": {
                        "transmissionFactor": 0.5,
                        "transmissionTexture": {"index": 1, "texCoord": 1, "extensions": {
                            "KHR_texture_transform": {"offset": [0.25, 0]}}},
                    }}
                    write_asset(path, document, binary)
                    result = open_with_stderr(path)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    stage = Usd.Stage.Open(str(path))
                    usd_material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")

                    mtlx, _, _ = usd_material.ComputeSurfaceSource("mtlx")
                    factor, _ = source_of(mtlx, "transmission")
                    self.assertEqual(factor.GetPath().name, "transmissionFactor")
                    self.assertEqual(factor.GetIdAttr().Get(), "ND_multiply_float")
                    self.assertAlmostEqual(factor.GetInput("in2").Get(), 0.5)
                    channel, _ = source_of(factor, "in1")
                    self.assertEqual(channel.GetPath().name, "transmissionChannel")
                    self.assertEqual(channel.GetInput("index").Get(), 0)
                    image, _ = source_of(channel, "in")
                    self.assertEqual(image.GetPath().name, "transmissionImage")
                    self.assertEqual(image.GetIdAttr().Get(), "ND_image_vector4")
                    self.assertFalse(image.GetInput("file").GetAttr().HasColorSpace())
                    self.assertEqual(image.GetInput("uaddressmode").Get(), "mirror")
                    self.assertEqual(image.GetInput("vaddressmode").Get(), "clamp")
                    # The transform sits between the reader of TEXCOORD_1 and the image.
                    translate, _ = source_of(image, "texcoord")
                    self.assertEqual(translate.GetIdAttr().Get(), "ND_add_vector2")
                    reader = translate
                    while reader and reader.GetIdAttr().Get() != "ND_geompropvalue_vector2":
                        reader = source_of(reader, "in1")[0] or source_of(reader, "in")[0]
                    self.assertEqual(reader.GetInput("geomprop").Get(), "st1")
                    asset = image.GetInput("file").Get()
                    self.assertEqual(asset.path, f"./{path.name}[images/0.png]")
                    self.assertEqual(asset_bytes(asset.resolvedPath), PNG)
                    # UsdPreviewSurface has no transmission to connect it to.
                    self.assertFalse(transmission_prims(usd_material, "preview"))
                    preview, _, _ = usd_material.ComputeSurfaceSource()
                    self.assertFalse(preview.GetInput("transmission"))
                    self.assertFalse(preview.GetInput("opacity"))

                with self.subTest(extension=extension, case="factor of one"):
                    path = root / ("transmission_one." + extension)
                    document, binary = textured_document(extension, None, PNG)
                    document["extensionsUsed"] = ["KHR_materials_transmission"]
                    document["materials"][0]["extensions"] = {"KHR_materials_transmission": {
                        "transmissionFactor": 1, "transmissionTexture": {"index": 0}}}
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    usd_material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                    mtlx, _, _ = usd_material.ComputeSurfaceSource("mtlx")
                    # A factor of 1 needs no multiply.
                    channel, _ = source_of(mtlx, "transmission")
                    self.assertEqual(channel.GetPath().name, "transmissionChannel")
                    self.assertEqual(source_of(source_of(channel, "in")[0], "texcoord")[0].GetInput("geomprop").Get(),
                                     "st")

                for case, extensions in (
                    ("default factor", {"KHR_materials_transmission": {}}),
                    ("unlit", {"KHR_materials_transmission": {"transmissionFactor": 1}, "KHR_materials_unlit": {}}),
                ):
                    with self.subTest(extension=extension, case=case):
                        path = root / (f"transmission_{case.replace(' ', '_')}." + extension)
                        document, binary = textured_document(extension, None, PNG)
                        document["extensionsUsed"] = list(extensions)
                        # A texture without transmission is never read, so a damaged one does not matter.
                        document["images"].append({"uri": "data:image/png;base64,AA=="})
                        document["textures"].append({"source": 1})
                        extensions["KHR_materials_transmission"]["transmissionTexture"] = {"index": 1}
                        document["materials"][0]["extensions"] = extensions
                        write_asset(path, document, binary)
                        result = open_with_stderr(path)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertNotIn("UsdPreviewSurface has no transmission", result.stderr)
                        stage = Usd.Stage.Open(str(path))
                        usd_material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                        self.assertFalse(transmission_prims(usd_material, "mtlx"))
                        self.assertFalse(usd_material.ComputeSurfaceSource("mtlx")[0].GetInput("transmission"))

    def test_transmission_failures_preserve_layer(self):
        texture_range = "material texture uses a texture coordinate set the primitive does not have"
        factor_range = "material transmission factor is non-finite or out of range"
        cases = (
            ("not an object", [1], "KHR_materials_transmission is not an object"),
            ("factor of the wrong type", {"transmissionFactor": "1"},
             "KHR_materials_transmission has a field of the wrong type"),
            ("texture not an object", {"transmissionFactor": 1, "transmissionTexture": 0},
             "KHR_materials_transmission has a field of the wrong type"),
            ("texture without an index", {"transmissionFactor": 1, "transmissionTexture": {"texCoord": 0}},
             "KHR_materials_transmission has a field of the wrong type"),
            ("texCoord of the wrong type", {"transmissionFactor": 1, "transmissionTexture": {"index": 0, "texCoord": -1}},
             "KHR_materials_transmission has a field of the wrong type"),
            ("overflow", {"transmissionFactor": 1e39}, "KHR_materials_transmission is out of range"),
            ("factor above one", {"transmissionFactor": 1.5}, factor_range),
            ("negative factor", {"transmissionFactor": -0.5}, factor_range),
            ("missing UV set", {"transmissionFactor": 1, "transmissionTexture": {"index": 0, "texCoord": 1}},
             texture_range),
            ("texture out of range", {"transmissionFactor": 1, "transmissionTexture": {"index": 5}},
             "texture index is out of range|cannot parse"),
            # Ignoring the extension does not make an invalid value or a missing UV set valid.
            ("zero missing UV set", {"transmissionTexture": {"index": 0, "texCoord": 1}}, texture_range),
            ("unlit above one", {"transmissionFactor": 2}, factor_range),
        )
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                path = pathlib.Path(directory) / ("transmission." + extension)
                document, binary = textured_document(extension, None, PNG)
                document["extensionsUsed"] = ["KHR_materials_transmission"]
                document["materials"][0]["extensions"] = {"KHR_materials_transmission": {"transmissionFactor": 0.5}}
                # The second primitive has one UV set only.
                document["materials"].append({})
                document["meshes"][0]["primitives"][1]["material"] = 1
                write_asset(path, document, binary)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer_text(layer)
                mtlx = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/mtlx/surface")
                self.assertAlmostEqual(mtlx.GetInput("transmission").Get(), 0.5, places=6)
                for case, value, message in cases:
                    with self.subTest(extension=extension, case=case):
                        invalid, invalid_binary = textured_document(extension, None, PNG)
                        invalid["extensionsUsed"] = ["KHR_materials_transmission"]
                        invalid["meshes"][0]["primitives"][1]["material"] = 1
                        extensions = {"KHR_materials_transmission": value}
                        if case.startswith("unlit"):
                            invalid["extensionsUsed"].append("KHR_materials_unlit")
                            extensions["KHR_materials_unlit"] = {}
                        invalid["materials"].append({"extensions": extensions})
                        write_asset(path, invalid, invalid_binary)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)

    def test_volume_materials(self):
        def open_with_stderr(path):
            return subprocess.run(
                [sys.executable, "-c",
                 "from pxr import Usd; import sys; assert Usd.Stage.Open(sys.argv[1])", str(path)],
                capture_output=True, text=True, check=False,
            )

        ignored = "KHR_materials_volume is ignored, because the material is unlit"
        preview_warning = "UsdPreviewSurface has no volume; its network ignores KHR_materials_volume"
        transmits = {"KHR_materials_transmission": {"transmissionFactor": 1}}
        attenuated = {"thicknessFactor": 2, "attenuationDistance": 0.5, "attenuationColor": [0.9, 0.5, 0.25]}
        # The thickness and attenuation authored in MaterialX, or None where there are none.
        cases = (
            ("thick", {"thicknessFactor": 0.25}, transmits, 0.25, None),
            ("attenuated", attenuated, transmits, 2, (0.5, (0.9, 0.5, 0.25))),
            ("white", {"thicknessFactor": 1, "attenuationDistance": 3}, transmits, 1, (3, (1, 1, 1))),
            ("thin", {"attenuationDistance": 0.5, "attenuationColor": [0.5, 0.5, 0.5]}, transmits, None, None),
            ("empty", {}, transmits, None, None),
            ("no transmission", attenuated, {}, None, None),
            ("zero transmission", attenuated, {"KHR_materials_transmission": {"transmissionFactor": 0}}, None, None),
            ("unlit", attenuated, {**transmits, "KHR_materials_unlit": {}}, None, None),
        )
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                for (case, volume, others, thickness, attenuation), required in itertools.product(
                        cases, (False, True)):
                    with self.subTest(extension=extension, case=case, required=required):
                        name = f"{case.replace(' ', '_')}_{'required' if required else 'optional'}"
                        path = pathlib.Path(directory) / f"volume_{name}.{extension}"
                        document, binary = mesh_document(extension)
                        used = ["KHR_materials_volume", *others]
                        document["extensionsUsed"] = used
                        if required:
                            document["extensionsRequired"] = used
                        document["meshes"][0]["primitives"][0]["material"] = 0
                        document["meshes"][0]["primitives"][1]["material"] = 1
                        lit = {"pbrMetallicRoughness": {"baseColorFactor": [0.2, 0.4, 0.6, 1],
                                                        "metallicFactor": 0.25, "roughnessFactor": 0.5}}
                        extensions = {"KHR_materials_volume": volume, **others}
                        document["materials"] = [{**lit, "extensions": extensions},
                                                 {**lit, "extensions": dict(others)}]
                        write_asset(path, document, binary)
                        result = open_with_stderr(path)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertNotIn("'KHR_materials_volume' is not supported", result.stderr)
                        self.assertEqual(ignored in result.stderr, case == "unlit", result.stderr)
                        self.assertEqual(preview_warning in result.stderr, thickness is not None, result.stderr)

                        stage = Usd.Stage.Open(str(path))
                        material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                        preview = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/preview/surface")
                        mtlx = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/mtlx/surface")
                        self.assert_material_nodegraphs(material, preview, mtlx)
                        if thickness is None:
                            self.assertFalse(mtlx.GetInput("thickness"))
                        else:
                            self.assertAlmostEqual(mtlx.GetInput("thickness").Get(), thickness, places=6)
                        if attenuation is None:
                            self.assertFalse(mtlx.GetInput("attenuation_distance"))
                            self.assertFalse(mtlx.GetInput("attenuation_color"))
                        else:
                            distance, color = attenuation
                            for input_name in ("attenuation_distance", "attenuation_color"):
                                self.assertEqual(mtlx.GetInput(input_name).GetAttr().GetVariability(),
                                                 Sdf.VariabilityUniform, input_name)
                            self.assertAlmostEqual(mtlx.GetInput("attenuation_distance").Get(), distance, places=6)
                            self.assertTrue(Gf.IsClose(mtlx.GetInput("attenuation_color").Get(), Gf.Vec3f(*color),
                                                       1e-6))
                        # UsdPreviewSurface has no volume.
                        for input_name in ("thickness", "attenuation_distance", "attenuation_color", "opacity"):
                            self.assertFalse(preview.GetInput(input_name), input_name)
                        # The volume changes nothing else.
                        self.assertEqual(bool(mtlx.GetInput("transmission")),
                                         case not in ("no transmission", "zero transmission", "unlit"))
                        if case != "unlit":
                            self.assertTrue(Gf.IsClose(mtlx.GetInput("base_color").Get(),
                                                       Gf.Vec3f(0.2, 0.4, 0.6), 1e-6))
                            self.assertAlmostEqual(mtlx.GetInput("roughness").Get(), 0.5, places=6)
                        self.assertEqual(mtlx.GetInput("alpha_mode").Get(), 0)
                        # A material without the extension authors no volume.
                        plain_mtlx = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_1/mtlx/surface")
                        for input_name in ("thickness", "attenuation_distance", "attenuation_color"):
                            self.assertFalse(plain_mtlx.GetInput(input_name), input_name)

    def test_volume_textures(self):
        def open_with_stderr(path):
            return subprocess.run(
                [sys.executable, "-c",
                 "from pxr import Usd; import sys; assert Usd.Stage.Open(sys.argv[1])", str(path)],
                capture_output=True, text=True, check=False,
            )

        def source_of(shader, name):
            connection = shader.GetInput(name).GetConnectedSource() if shader.GetInput(name) else None
            return (UsdShade.Shader(connection[0].GetPrim()), connection[1]) if connection else (None, None)

        def thickness_prims(material, graph):
            return [prim for prim in Usd.PrimRange(material.GetPrim().GetChild(graph))
                    if prim.GetName().startswith("thickness")]

        transmission = {"transmissionFactor": 1}
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension, case="texture"):
                    path = root / ("volume." + extension)
                    document, binary = textured_document(extension, None, PNG)
                    document["extensionsUsed"] = ["KHR_materials_transmission", "KHR_materials_volume",
                                                  "KHR_texture_transform"]
                    document["samplers"] = [{"wrapS": 33648, "wrapT": 33071}]
                    document["textures"].append({"source": 0, "sampler": 0})
                    document["materials"][0]["extensions"] = {
                        "KHR_materials_transmission": transmission,
                        "KHR_materials_volume": {
                            "thicknessFactor": 0.5,
                            "thicknessTexture": {"index": 1, "texCoord": 1, "extensions": {
                                "KHR_texture_transform": {"offset": [0.25, 0]}}},
                        },
                    }
                    write_asset(path, document, binary)
                    result = open_with_stderr(path)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    stage = Usd.Stage.Open(str(path))
                    usd_material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")

                    mtlx, _, _ = usd_material.ComputeSurfaceSource("mtlx")
                    factor, _ = source_of(mtlx, "thickness")
                    self.assertEqual(factor.GetPath().name, "thicknessFactor")
                    self.assertEqual(factor.GetIdAttr().Get(), "ND_multiply_float")
                    self.assertAlmostEqual(factor.GetInput("in2").Get(), 0.5)
                    channel, _ = source_of(factor, "in1")
                    self.assertEqual(channel.GetPath().name, "thicknessChannel")
                    # glTF's thickness is the texture's G channel.
                    self.assertEqual(channel.GetInput("index").Get(), 1)
                    image, _ = source_of(channel, "in")
                    self.assertEqual(image.GetPath().name, "thicknessImage")
                    self.assertEqual(image.GetIdAttr().Get(), "ND_image_vector4")
                    self.assertFalse(image.GetInput("file").GetAttr().HasColorSpace())
                    self.assertEqual(image.GetInput("uaddressmode").Get(), "mirror")
                    self.assertEqual(image.GetInput("vaddressmode").Get(), "clamp")
                    # The transform sits between the reader of TEXCOORD_1 and the image.
                    translate, _ = source_of(image, "texcoord")
                    self.assertEqual(translate.GetIdAttr().Get(), "ND_add_vector2")
                    reader = translate
                    while reader and reader.GetIdAttr().Get() != "ND_geompropvalue_vector2":
                        reader = source_of(reader, "in1")[0] or source_of(reader, "in")[0]
                    self.assertEqual(reader.GetInput("geomprop").Get(), "st1")
                    asset = image.GetInput("file").Get()
                    self.assertEqual(asset.path, f"./{path.name}[images/0.png]")
                    self.assertEqual(asset_bytes(asset.resolvedPath), PNG)
                    # UsdPreviewSurface has no volume to connect it to.
                    self.assertFalse(thickness_prims(usd_material, "preview"))
                    preview, _, _ = usd_material.ComputeSurfaceSource()
                    self.assertFalse(preview.GetInput("thickness"))

                with self.subTest(extension=extension, case="factor of one"):
                    path = root / ("volume_one." + extension)
                    document, binary = textured_document(extension, None, PNG)
                    document["extensionsUsed"] = ["KHR_materials_transmission", "KHR_materials_volume"]
                    document["materials"][0]["extensions"] = {
                        "KHR_materials_transmission": transmission,
                        "KHR_materials_volume": {"thicknessFactor": 1, "thicknessTexture": {"index": 0}},
                    }
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    usd_material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                    mtlx, _, _ = usd_material.ComputeSurfaceSource("mtlx")
                    # A factor of 1 needs no multiply.
                    channel, _ = source_of(mtlx, "thickness")
                    self.assertEqual(channel.GetPath().name, "thicknessChannel")
                    self.assertEqual(source_of(source_of(channel, "in")[0], "texcoord")[0].GetInput("geomprop").Get(),
                                     "st")

                for case, extensions in (
                    ("default factor", {"KHR_materials_transmission": transmission, "KHR_materials_volume": {}}),
                    ("no transmission", {"KHR_materials_volume": {"thicknessFactor": 1}}),
                    ("unlit", {"KHR_materials_transmission": transmission,
                               "KHR_materials_volume": {"thicknessFactor": 1}, "KHR_materials_unlit": {}}),
                ):
                    with self.subTest(extension=extension, case=case):
                        path = root / (f"volume_{case.replace(' ', '_')}." + extension)
                        document, binary = textured_document(extension, None, PNG)
                        document["extensionsUsed"] = list(extensions)
                        # A texture without a volume is never read, so a damaged one does not matter.
                        document["images"].append({"uri": "data:image/png;base64,AA=="})
                        document["textures"].append({"source": 1})
                        extensions = json.loads(json.dumps(extensions))
                        extensions["KHR_materials_volume"]["thicknessTexture"] = {"index": 1}
                        document["materials"][0]["extensions"] = extensions
                        write_asset(path, document, binary)
                        result = open_with_stderr(path)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertNotIn("UsdPreviewSurface has no volume", result.stderr)
                        stage = Usd.Stage.Open(str(path))
                        usd_material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                        self.assertFalse(thickness_prims(usd_material, "mtlx"))
                        self.assertFalse(usd_material.ComputeSurfaceSource("mtlx")[0].GetInput("thickness"))

    def test_volume_failures_preserve_layer(self):
        texture_range = "material texture uses a texture coordinate set the primitive does not have"
        wrong_type = "KHR_materials_volume has a field of the wrong type"
        out_of_range = "KHR_materials_volume is out of range"
        thickness_range = "material thickness factor is non-finite or negative"
        distance_range = "material attenuation distance is not positive"
        color_range = "material attenuation color is non-finite or out of range"
        cases = (
            ("not an object", [1], "KHR_materials_volume is not an object"),
            ("factor of the wrong type", {"thicknessFactor": "1"}, wrong_type),
            ("distance of the wrong type", {"thicknessFactor": 1, "attenuationDistance": [1]}, wrong_type),
            ("color not an array", {"thicknessFactor": 1, "attenuationColor": 1}, wrong_type),
            # fastgltf accepts a short color, keeping 1 for the rest.
            ("short color", {"thicknessFactor": 1, "attenuationColor": [0.5, 0.5]}, wrong_type),
            ("long color", {"thicknessFactor": 1, "attenuationColor": [0.5, 0.5, 0.5, 0.5]}, wrong_type),
            ("color of the wrong type", {"thicknessFactor": 1, "attenuationColor": [0.5, "0.5", 0.5]}, wrong_type),
            ("texture not an object", {"thicknessFactor": 1, "thicknessTexture": 0}, wrong_type),
            ("texture without an index", {"thicknessFactor": 1, "thicknessTexture": {"texCoord": 0}}, wrong_type),
            ("factor overflow", {"thicknessFactor": 1e39}, out_of_range),
            ("distance overflow", {"thicknessFactor": 1, "attenuationDistance": 1e39}, out_of_range),
            ("color overflow", {"thicknessFactor": 1, "attenuationColor": [1e39, 0, 0]}, out_of_range),
            ("negative factor", {"thicknessFactor": -0.5}, thickness_range),
            ("zero distance", {"thicknessFactor": 1, "attenuationDistance": 0}, distance_range),
            ("negative distance", {"thicknessFactor": 1, "attenuationDistance": -1}, distance_range),
            ("color above one", {"thicknessFactor": 1, "attenuationColor": [1, 1.5, 1]}, color_range),
            ("negative color", {"thicknessFactor": 1, "attenuationColor": [1, 1, -0.5]}, color_range),
            ("missing UV set", {"thicknessFactor": 1, "thicknessTexture": {"index": 0, "texCoord": 1}},
             texture_range),
            ("texture out of range", {"thicknessFactor": 1, "thicknessTexture": {"index": 5}},
             "texture index is out of range|cannot parse"),
            # Ignoring the extension does not make an invalid value or a missing UV set valid.
            ("thin missing UV set", {"thicknessTexture": {"index": 0, "texCoord": 1}}, texture_range),
            ("thin zero distance", {"attenuationDistance": 0}, distance_range),
            ("unlit negative factor", {"thicknessFactor": -1}, thickness_range),
        )
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                path = pathlib.Path(directory) / ("volume." + extension)
                document, binary = textured_document(extension, None, PNG)
                document["extensionsUsed"] = ["KHR_materials_transmission", "KHR_materials_volume"]
                document["materials"][0]["extensions"] = {
                    "KHR_materials_transmission": {"transmissionFactor": 1},
                    "KHR_materials_volume": {"thicknessFactor": 0.5},
                }
                # The second primitive has one UV set only.
                document["materials"].append({})
                document["meshes"][0]["primitives"][1]["material"] = 1
                write_asset(path, document, binary)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer_text(layer)
                mtlx = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/mtlx/surface")
                self.assertAlmostEqual(mtlx.GetInput("thickness").Get(), 0.5, places=6)
                for case, value, message in cases:
                    with self.subTest(extension=extension, case=case):
                        invalid, invalid_binary = textured_document(extension, None, PNG)
                        invalid["extensionsUsed"] = ["KHR_materials_transmission", "KHR_materials_volume"]
                        invalid["meshes"][0]["primitives"][1]["material"] = 1
                        extensions = {"KHR_materials_transmission": {"transmissionFactor": 1},
                                      "KHR_materials_volume": value}
                        if case.startswith("unlit"):
                            invalid["extensionsUsed"].append("KHR_materials_unlit")
                            extensions["KHR_materials_unlit"] = {}
                        invalid["materials"].append({"extensions": extensions})
                        write_asset(path, invalid, invalid_binary)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)

    def test_specular_materials(self):
        def open_with_stderr(path):
            return subprocess.run(
                [sys.executable, "-c",
                 "from pxr import Usd; import sys; assert Usd.Stage.Open(sys.argv[1])", str(path)],
                capture_output=True, text=True, check=False,
            )

        ignored = "KHR_materials_specular is ignored, because the material is unlit"
        preview_warning = ("UsdPreviewSurface's metallic workflow has no specular weight or color; its network "
                           "ignores KHR_materials_specular")
        # The specular weight and color authored in MaterialX, or None where they keep their defaults of 1.
        cases = (
            ("weight", {"specularFactor": 0.5}, {}, 0.5, None),
            ("zero weight", {"specularFactor": 0}, {}, 0, None),
            # glTF bounds the color below only.
            ("color", {"specularColorFactor": [0.5, 1, 2]}, {}, None, (0.5, 1, 2)),
            ("both", {"specularFactor": 0.25, "specularColorFactor": [1, 0, 0]}, {}, 0.25, (1, 0, 0)),
            ("defaults", {"specularFactor": 1, "specularColorFactor": [1, 1, 1]}, {}, None, None),
            ("empty", {}, {}, None, None),
            # gltf_pbr ignores the specular inputs of a metal, as glTF does, so they are authored all the same.
            ("metal", {"specularFactor": 0.5}, {"metallicFactor": 1}, 0.5, None),
        )
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                for (case, specular, pbr, weight, color), required in itertools.product(cases, (False, True)):
                    with self.subTest(extension=extension, case=case, required=required):
                        name = f"{case.replace(' ', '_')}_{'required' if required else 'optional'}"
                        path = pathlib.Path(directory) / f"specular_{name}.{extension}"
                        document, binary = mesh_document(extension)
                        document["extensionsUsed"] = ["KHR_materials_specular"]
                        if required:
                            document["extensionsRequired"] = ["KHR_materials_specular"]
                        document["meshes"][0]["primitives"][0]["material"] = 0
                        document["meshes"][0]["primitives"][1]["material"] = 1
                        lit = {"pbrMetallicRoughness": {"baseColorFactor": [0.2, 0.4, 0.6, 1],
                                                        "metallicFactor": 0, "roughnessFactor": 0.5, **pbr}}
                        document["materials"] = [{**lit, "extensions": {"KHR_materials_specular": specular}},
                                                 lit]
                        write_asset(path, document, binary)
                        result = open_with_stderr(path)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertNotIn("'KHR_materials_specular' is not supported", result.stderr)
                        self.assertNotIn(ignored, result.stderr)
                        self.assertEqual(preview_warning in result.stderr, weight is not None or color is not None,
                                         result.stderr)

                        stage = Usd.Stage.Open(str(path))
                        material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                        preview = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/preview/surface")
                        mtlx = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/mtlx/surface")
                        self.assert_material_nodegraphs(material, preview, mtlx)
                        if weight is None:
                            self.assertFalse(mtlx.GetInput("specular"))
                        else:
                            self.assertAlmostEqual(mtlx.GetInput("specular").Get(), weight, places=6)
                        if color is None:
                            self.assertFalse(mtlx.GetInput("specular_color"))
                        else:
                            self.assertTrue(Gf.IsClose(mtlx.GetInput("specular_color").Get(), Gf.Vec3f(*color), 1e-6))
                        # UsdPreviewSurface keeps the metallic workflow, which has neither.
                        for input_name in ("useSpecularWorkflow", "specularColor"):
                            self.assertFalse(preview.GetInput(input_name), input_name)
                        # The specular changes nothing else.
                        self.assertFalse(mtlx.GetInput("ior"))
                        self.assertTrue(Gf.IsClose(mtlx.GetInput("base_color").Get(), Gf.Vec3f(0.2, 0.4, 0.6), 1e-6))
                        self.assertAlmostEqual(mtlx.GetInput("metallic").Get(), 1 if case == "metal" else 0, places=6)
                        self.assertAlmostEqual(mtlx.GetInput("roughness").Get(), 0.5, places=6)
                        # A material without the extension authors no specular.
                        plain_mtlx = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_1/mtlx/surface")
                        for input_name in ("specular", "specular_color"):
                            self.assertFalse(plain_mtlx.GetInput(input_name), input_name)

                with self.subTest(extension=extension, case="unlit"):
                    path = pathlib.Path(directory) / f"specular_unlit.{extension}"
                    document, binary = mesh_document(extension)
                    document["extensionsUsed"] = ["KHR_materials_specular", "KHR_materials_unlit"]
                    document["meshes"][0]["primitives"][0]["material"] = 0
                    document["materials"] = [{"extensions": {
                        "KHR_materials_specular": {"specularFactor": 0.5, "specularColorFactor": [1, 0, 0]},
                        "KHR_materials_unlit": {}}}]
                    write_asset(path, document, binary)
                    result = open_with_stderr(path)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(ignored, result.stderr)
                    self.assertNotIn(preview_warning, result.stderr)
                    stage = Usd.Stage.Open(str(path))
                    mtlx = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/mtlx/surface")
                    # An unlit material reflects nothing.
                    self.assertEqual(mtlx.GetInput("specular").Get(), 0)
                    self.assertFalse(mtlx.GetInput("specular_color"))

    def test_specular_textures(self):
        def open_with_stderr(path):
            return subprocess.run(
                [sys.executable, "-c",
                 "from pxr import Usd; import sys; assert Usd.Stage.Open(sys.argv[1])", str(path)],
                capture_output=True, text=True, check=False,
            )

        def source_of(shader, name):
            connection = shader.GetInput(name).GetConnectedSource() if shader.GetInput(name) else None
            return (UsdShade.Shader(connection[0].GetPrim()), connection[1]) if connection else (None, None)

        def specular_prims(material, graph):
            return [prim for prim in Usd.PrimRange(material.GetPrim().GetChild(graph))
                    if prim.GetName().startswith("specular")]

        def reader_of(shader):
            while shader and shader.GetIdAttr().Get() != "ND_geompropvalue_vector2":
                shader = source_of(shader, "in1")[0] or source_of(shader, "in")[0]
            return shader

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension, case="textures"):
                    path = root / ("specular." + extension)
                    document, binary = textured_document(extension, None, PNG)
                    document["extensionsUsed"] = ["KHR_materials_specular", "KHR_texture_transform"]
                    document["samplers"] = [{"wrapS": 33648, "wrapT": 33071}]
                    document["textures"].append({"source": 0, "sampler": 0})
                    document["materials"][0]["extensions"] = {"KHR_materials_specular": {
                        "specularFactor": 0.5,
                        "specularTexture": {"index": 1, "texCoord": 1, "extensions": {
                            "KHR_texture_transform": {"offset": [0.25, 0]}}},
                        "specularColorFactor": [0.5, 0.25, 2],
                        "specularColorTexture": {"index": 0},
                    }}
                    write_asset(path, document, binary)
                    result = open_with_stderr(path)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    stage = Usd.Stage.Open(str(path))
                    usd_material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                    mtlx, _, _ = usd_material.ComputeSurfaceSource("mtlx")

                    factor, _ = source_of(mtlx, "specular")
                    self.assertEqual(factor.GetPath().name, "specularFactor")
                    self.assertEqual(factor.GetIdAttr().Get(), "ND_multiply_float")
                    self.assertAlmostEqual(factor.GetInput("in2").Get(), 0.5)
                    channel, _ = source_of(factor, "in1")
                    self.assertEqual(channel.GetPath().name, "specularChannel")
                    # glTF's specular weight is the texture's A channel, as non-color data.
                    self.assertEqual(channel.GetInput("index").Get(), 3)
                    image, _ = source_of(channel, "in")
                    self.assertEqual(image.GetPath().name, "specularImage")
                    self.assertEqual(image.GetIdAttr().Get(), "ND_image_vector4")
                    self.assertFalse(image.GetInput("file").GetAttr().HasColorSpace())
                    self.assertEqual(image.GetInput("uaddressmode").Get(), "mirror")
                    self.assertEqual(image.GetInput("vaddressmode").Get(), "clamp")
                    # The transform sits between the reader of TEXCOORD_1 and the image.
                    translate, _ = source_of(image, "texcoord")
                    self.assertEqual(translate.GetIdAttr().Get(), "ND_add_vector2")
                    self.assertEqual(reader_of(translate).GetInput("geomprop").Get(), "st1")
                    asset = image.GetInput("file").Get()
                    self.assertEqual(asset.path, f"./{path.name}[images/0.png]")
                    self.assertEqual(asset_bytes(asset.resolvedPath), PNG)

                    factor, _ = source_of(mtlx, "specular_color")
                    self.assertEqual(factor.GetPath().name, "specularColorFactor")
                    self.assertEqual(factor.GetIdAttr().Get(), "ND_multiply_color3")
                    self.assertTrue(Gf.IsClose(factor.GetInput("in2").Get(), Gf.Vec3f(0.5, 0.25, 2), 1e-6))
                    rgb, _ = source_of(factor, "in1")
                    self.assertEqual(rgb.GetPath().name, "specularColorRgb")
                    self.assertEqual(rgb.GetIdAttr().Get(), "ND_convert_color4_color3")
                    image, _ = source_of(rgb, "in")
                    self.assertEqual(image.GetPath().name, "specularColorImage")
                    # glTF's specular color texture is sRGB-encoded.
                    self.assertEqual(image.GetIdAttr().Get(), "ND_image_color4")
                    self.assertEqual(image.GetInput("file").GetAttr().GetColorSpace(), "srgb_texture")
                    self.assertEqual(image.GetInput("uaddressmode").Get(), "periodic")
                    self.assertEqual(reader_of(source_of(image, "texcoord")[0]).GetInput("geomprop").Get(), "st")
                    # UsdPreviewSurface has no specular weight or color to connect them to.
                    self.assertFalse(specular_prims(usd_material, "preview"))
                    preview, _, _ = usd_material.ComputeSurfaceSource()
                    self.assertFalse(preview.GetInput("specularColor"))

                with self.subTest(extension=extension, case="factors of one"):
                    path = root / ("specular_one." + extension)
                    document, binary = textured_document(extension, None, PNG)
                    document["extensionsUsed"] = ["KHR_materials_specular"]
                    document["materials"][0]["extensions"] = {"KHR_materials_specular": {
                        "specularTexture": {"index": 0}, "specularColorTexture": {"index": 0}}}
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    usd_material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                    mtlx, _, _ = usd_material.ComputeSurfaceSource("mtlx")
                    # A factor of 1 needs no multiply.
                    self.assertEqual(source_of(mtlx, "specular")[0].GetPath().name, "specularChannel")
                    self.assertEqual(source_of(mtlx, "specular_color")[0].GetPath().name, "specularColorRgb")

                # A texture whose product is fixed is never read, so a damaged one does not matter.
                for case, specular, weight, color in (
                    ("zero weight", {"specularFactor": 0, "specularColorFactor": [1, 0, 0],
                                     "specularTexture": {"index": 1}, "specularColorTexture": {"index": 1}},
                     0, (1, 0, 0)),
                    ("black color", {"specularFactor": 0.5, "specularColorFactor": [0, 0, 0],
                                     "specularColorTexture": {"index": 1}}, 0.5, (0, 0, 0)),
                    ("unlit", {"specularTexture": {"index": 1}, "specularColorTexture": {"index": 1}}, 0, None),
                ):
                    with self.subTest(extension=extension, case=case):
                        path = root / (f"specular_{case.replace(' ', '_')}." + extension)
                        document, binary = textured_document(extension, None, PNG)
                        document["extensionsUsed"] = ["KHR_materials_specular"]
                        document["images"].append({"uri": "data:image/png;base64,AA=="})
                        document["textures"].append({"source": 1})
                        extensions = {"KHR_materials_specular": specular}
                        if case == "unlit":
                            document["extensionsUsed"].append("KHR_materials_unlit")
                            extensions["KHR_materials_unlit"] = {}
                        document["materials"][0]["extensions"] = extensions
                        write_asset(path, document, binary)
                        result = open_with_stderr(path)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        stage = Usd.Stage.Open(str(path))
                        usd_material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                        self.assertFalse(specular_prims(usd_material, "mtlx"))
                        mtlx = usd_material.ComputeSurfaceSource("mtlx")[0]
                        self.assertEqual(mtlx.GetInput("specular").Get(), weight)
                        if color is None:
                            self.assertFalse(mtlx.GetInput("specular_color"))
                        else:
                            self.assertEqual(mtlx.GetInput("specular_color").Get(), Gf.Vec3f(*color))

    def test_specular_failures_preserve_layer(self):
        texture_range = "material texture uses a texture coordinate set the primitive does not have"
        wrong_type = "KHR_materials_specular has a field of the wrong type"
        out_of_range = "KHR_materials_specular is out of range"
        factor_range = "material specular factor is non-finite or out of range"
        color_range = "material specular color factor is non-finite or negative"
        cases = (
            ("not an object", [1], "KHR_materials_specular is not an object"),
            ("factor of the wrong type", {"specularFactor": "1"}, wrong_type),
            ("color not an array", {"specularColorFactor": 1}, wrong_type),
            # fastgltf accepts a short color, keeping 1 for the rest.
            ("short color", {"specularColorFactor": [0.5, 0.5]}, wrong_type),
            ("long color", {"specularColorFactor": [0.5, 0.5, 0.5, 0.5]}, wrong_type),
            ("color of the wrong type", {"specularColorFactor": [0.5, "0.5", 0.5]}, wrong_type),
            ("texture not an object", {"specularTexture": 0}, wrong_type),
            ("color texture without an index", {"specularColorTexture": {"texCoord": 0}}, wrong_type),
            ("factor overflow", {"specularFactor": 1e39}, out_of_range),
            ("color overflow", {"specularColorFactor": [1e39, 0, 0]}, out_of_range),
            ("factor above one", {"specularFactor": 1.5}, factor_range),
            ("negative factor", {"specularFactor": -0.5}, factor_range),
            ("negative color", {"specularColorFactor": [1, 1, -0.5]}, color_range),
            ("missing UV set", {"specularTexture": {"index": 0, "texCoord": 1}}, texture_range),
            ("missing color UV set", {"specularColorTexture": {"index": 0, "texCoord": 1}}, texture_range),
            ("texture out of range", {"specularTexture": {"index": 5}}, "texture index is out of range|cannot parse"),
            # Ignoring the texture or the extension does not make an invalid value or a missing UV set valid.
            ("zero weight missing UV set", {"specularFactor": 0, "specularTexture": {"index": 0, "texCoord": 1}},
             texture_range),
            ("unlit negative color", {"specularColorFactor": [-1, 1, 1]}, color_range),
        )
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                path = pathlib.Path(directory) / ("specular." + extension)
                document, binary = textured_document(extension, None, PNG)
                document["extensionsUsed"] = ["KHR_materials_specular"]
                document["materials"][0]["extensions"] = {"KHR_materials_specular": {"specularFactor": 0.5}}
                # The second primitive has one UV set only.
                document["materials"].append({})
                document["meshes"][0]["primitives"][1]["material"] = 1
                write_asset(path, document, binary)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer_text(layer)
                mtlx = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/mtlx/surface")
                self.assertAlmostEqual(mtlx.GetInput("specular").Get(), 0.5, places=6)
                for case, value, message in cases:
                    with self.subTest(extension=extension, case=case):
                        invalid, invalid_binary = textured_document(extension, None, PNG)
                        invalid["extensionsUsed"] = ["KHR_materials_specular"]
                        invalid["meshes"][0]["primitives"][1]["material"] = 1
                        extensions = {"KHR_materials_specular": value}
                        if case.startswith("unlit"):
                            invalid["extensionsUsed"].append("KHR_materials_unlit")
                            extensions["KHR_materials_unlit"] = {}
                        invalid["materials"].append({"extensions": extensions})
                        write_asset(path, invalid, invalid_binary)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)

    def test_sheen_materials(self):
        def open_with_stderr(path):
            return subprocess.run(
                [sys.executable, "-c",
                 "from pxr import Usd; import sys; assert Usd.Stage.Open(sys.argv[1])", str(path)],
                capture_output=True, text=True, check=False,
            )

        ignored = "KHR_materials_sheen is ignored, because the material is unlit"
        preview_warning = "UsdPreviewSurface has no sheen; its network ignores KHR_materials_sheen"
        # The sheen color and roughness authored in MaterialX, or None where they are not.
        cases = (
            ("color", {"sheenColorFactor": [0.5, 0.25, 1]}, {}, (0.5, 0.25, 1), None),
            ("both", {"sheenColorFactor": [1, 1, 1], "sheenRoughnessFactor": 0.5}, {}, (1, 1, 1), 0.5),
            # A black color disables the layer, whatever its roughness.
            ("black", {"sheenColorFactor": [0, 0, 0], "sheenRoughnessFactor": 0.5}, {}, None, None),
            ("roughness only", {"sheenRoughnessFactor": 0.5}, {}, None, None),
            ("empty", {}, {}, None, None),
            # glTF layers the sheen over a metal too.
            ("metal", {"sheenColorFactor": [1, 0, 0]}, {"metallicFactor": 1}, (1, 0, 0), None),
        )
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                for (case, sheen, pbr, color, roughness), required in itertools.product(cases, (False, True)):
                    with self.subTest(extension=extension, case=case, required=required):
                        name = f"{case.replace(' ', '_')}_{'required' if required else 'optional'}"
                        path = pathlib.Path(directory) / f"sheen_{name}.{extension}"
                        document, binary = mesh_document(extension)
                        document["extensionsUsed"] = ["KHR_materials_sheen"]
                        if required:
                            document["extensionsRequired"] = ["KHR_materials_sheen"]
                        document["meshes"][0]["primitives"][0]["material"] = 0
                        document["meshes"][0]["primitives"][1]["material"] = 1
                        lit = {"pbrMetallicRoughness": {"baseColorFactor": [0.2, 0.4, 0.6, 1],
                                                        "metallicFactor": 0, "roughnessFactor": 0.5, **pbr}}
                        document["materials"] = [{**lit, "extensions": {"KHR_materials_sheen": sheen}}, lit]
                        write_asset(path, document, binary)
                        result = open_with_stderr(path)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertNotIn("'KHR_materials_sheen' is not supported", result.stderr)
                        self.assertNotIn(ignored, result.stderr)
                        self.assertEqual(preview_warning in result.stderr, color is not None, result.stderr)

                        stage = Usd.Stage.Open(str(path))
                        material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                        preview = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/preview/surface")
                        mtlx = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/mtlx/surface")
                        self.assert_material_nodegraphs(material, preview, mtlx)
                        if color is None:
                            self.assertFalse(mtlx.GetInput("sheen_color"))
                        else:
                            self.assertTrue(Gf.IsClose(mtlx.GetInput("sheen_color").Get(), Gf.Vec3f(*color), 1e-6))
                        if roughness is None:
                            self.assertFalse(mtlx.GetInput("sheen_roughness"))
                        else:
                            self.assertAlmostEqual(mtlx.GetInput("sheen_roughness").Get(), roughness, places=6)
                        # UsdPreviewSurface has no sheen.
                        self.assertFalse([i for i in preview.GetInputs() if "sheen" in i.GetBaseName().lower()])
                        # The sheen changes nothing else.
                        for input_name in ("ior", "specular", "specular_color"):
                            self.assertFalse(mtlx.GetInput(input_name), input_name)
                        self.assertTrue(Gf.IsClose(mtlx.GetInput("base_color").Get(), Gf.Vec3f(0.2, 0.4, 0.6), 1e-6))
                        self.assertAlmostEqual(mtlx.GetInput("metallic").Get(), 1 if case == "metal" else 0, places=6)
                        self.assertAlmostEqual(mtlx.GetInput("roughness").Get(), 0.5, places=6)
                        # A material without the extension authors no sheen.
                        plain_mtlx = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_1/mtlx/surface")
                        for input_name in ("sheen_color", "sheen_roughness"):
                            self.assertFalse(plain_mtlx.GetInput(input_name), input_name)

                with self.subTest(extension=extension, case="unlit"):
                    path = pathlib.Path(directory) / f"sheen_unlit.{extension}"
                    document, binary = mesh_document(extension)
                    document["extensionsUsed"] = ["KHR_materials_sheen", "KHR_materials_unlit"]
                    document["meshes"][0]["primitives"][0]["material"] = 0
                    document["materials"] = [{"extensions": {
                        "KHR_materials_sheen": {"sheenColorFactor": [1, 1, 1], "sheenRoughnessFactor": 0.5},
                        "KHR_materials_unlit": {}}}]
                    write_asset(path, document, binary)
                    result = open_with_stderr(path)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(ignored, result.stderr)
                    self.assertNotIn(preview_warning, result.stderr)
                    stage = Usd.Stage.Open(str(path))
                    mtlx = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/mtlx/surface")
                    for input_name in ("sheen_color", "sheen_roughness"):
                        self.assertFalse(mtlx.GetInput(input_name), input_name)

    def test_sheen_textures(self):
        def open_with_stderr(path):
            return subprocess.run(
                [sys.executable, "-c",
                 "from pxr import Usd; import sys; assert Usd.Stage.Open(sys.argv[1])", str(path)],
                capture_output=True, text=True, check=False,
            )

        def source_of(shader, name):
            connection = shader.GetInput(name).GetConnectedSource() if shader.GetInput(name) else None
            return (UsdShade.Shader(connection[0].GetPrim()), connection[1]) if connection else (None, None)

        def sheen_prims(material, graph):
            return [prim for prim in Usd.PrimRange(material.GetPrim().GetChild(graph))
                    if prim.GetName().startswith("sheen")]

        def reader_of(shader):
            while shader and shader.GetIdAttr().Get() != "ND_geompropvalue_vector2":
                shader = source_of(shader, "in1")[0] or source_of(shader, "in")[0]
            return shader

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension, case="textures"):
                    path = root / ("sheen." + extension)
                    document, binary = textured_document(extension, None, PNG)
                    document["extensionsUsed"] = ["KHR_materials_sheen", "KHR_texture_transform"]
                    document["samplers"] = [{"wrapS": 33648, "wrapT": 33071}]
                    document["textures"].append({"source": 0, "sampler": 0})
                    document["materials"][0]["extensions"] = {"KHR_materials_sheen": {
                        "sheenColorFactor": [0.5, 0.25, 1],
                        "sheenColorTexture": {"index": 0},
                        "sheenRoughnessFactor": 0.5,
                        "sheenRoughnessTexture": {"index": 1, "texCoord": 1, "extensions": {
                            "KHR_texture_transform": {"offset": [0.25, 0]}}},
                    }}
                    write_asset(path, document, binary)
                    result = open_with_stderr(path)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    stage = Usd.Stage.Open(str(path))
                    usd_material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                    mtlx, _, _ = usd_material.ComputeSurfaceSource("mtlx")

                    factor, _ = source_of(mtlx, "sheen_color")
                    self.assertEqual(factor.GetPath().name, "sheenColorFactor")
                    self.assertEqual(factor.GetIdAttr().Get(), "ND_multiply_color3")
                    self.assertTrue(Gf.IsClose(factor.GetInput("in2").Get(), Gf.Vec3f(0.5, 0.25, 1), 1e-6))
                    rgb, _ = source_of(factor, "in1")
                    self.assertEqual(rgb.GetPath().name, "sheenColorRgb")
                    self.assertEqual(rgb.GetIdAttr().Get(), "ND_convert_color4_color3")
                    image, _ = source_of(rgb, "in")
                    self.assertEqual(image.GetPath().name, "sheenColorImage")
                    # glTF's sheen color texture is sRGB-encoded.
                    self.assertEqual(image.GetIdAttr().Get(), "ND_image_color4")
                    self.assertEqual(image.GetInput("file").GetAttr().GetColorSpace(), "srgb_texture")
                    self.assertEqual(image.GetInput("uaddressmode").Get(), "periodic")
                    self.assertEqual(reader_of(source_of(image, "texcoord")[0]).GetInput("geomprop").Get(), "st")

                    factor, _ = source_of(mtlx, "sheen_roughness")
                    self.assertEqual(factor.GetPath().name, "sheenRoughnessFactor")
                    self.assertEqual(factor.GetIdAttr().Get(), "ND_multiply_float")
                    self.assertAlmostEqual(factor.GetInput("in2").Get(), 0.5)
                    channel, _ = source_of(factor, "in1")
                    self.assertEqual(channel.GetPath().name, "sheenRoughnessChannel")
                    # glTF's sheen roughness is the texture's A channel, as non-color data.
                    self.assertEqual(channel.GetInput("index").Get(), 3)
                    image, _ = source_of(channel, "in")
                    self.assertEqual(image.GetPath().name, "sheenRoughnessImage")
                    self.assertEqual(image.GetIdAttr().Get(), "ND_image_vector4")
                    self.assertFalse(image.GetInput("file").GetAttr().HasColorSpace())
                    self.assertEqual(image.GetInput("uaddressmode").Get(), "mirror")
                    self.assertEqual(image.GetInput("vaddressmode").Get(), "clamp")
                    # The transform sits between the reader of TEXCOORD_1 and the image.
                    translate, _ = source_of(image, "texcoord")
                    self.assertEqual(translate.GetIdAttr().Get(), "ND_add_vector2")
                    self.assertEqual(reader_of(translate).GetInput("geomprop").Get(), "st1")
                    asset = image.GetInput("file").Get()
                    self.assertEqual(asset.path, f"./{path.name}[images/0.png]")
                    self.assertEqual(asset_bytes(asset.resolvedPath), PNG)
                    # UsdPreviewSurface has no sheen to connect them to.
                    self.assertFalse(sheen_prims(usd_material, "preview"))

                with self.subTest(extension=extension, case="factors of one"):
                    path = root / ("sheen_one." + extension)
                    document, binary = textured_document(extension, None, PNG)
                    document["extensionsUsed"] = ["KHR_materials_sheen"]
                    document["materials"][0]["extensions"] = {"KHR_materials_sheen": {
                        "sheenColorFactor": [1, 1, 1], "sheenColorTexture": {"index": 0},
                        "sheenRoughnessFactor": 1, "sheenRoughnessTexture": {"index": 0}}}
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    usd_material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                    mtlx, _, _ = usd_material.ComputeSurfaceSource("mtlx")
                    # A factor of 1 needs no multiply.
                    self.assertEqual(source_of(mtlx, "sheen_color")[0].GetPath().name, "sheenColorRgb")
                    self.assertEqual(source_of(mtlx, "sheen_roughness")[0].GetPath().name, "sheenRoughnessChannel")

                # A texture whose product is fixed is never read, so a damaged one does not matter.
                for case, sheen, color in (
                    ("black", {"sheenColorFactor": [0, 0, 0], "sheenRoughnessFactor": 1,
                               "sheenColorTexture": {"index": 1}, "sheenRoughnessTexture": {"index": 1}}, None),
                    ("default color", {"sheenRoughnessFactor": 1, "sheenColorTexture": {"index": 1},
                                       "sheenRoughnessTexture": {"index": 1}}, None),
                    ("zero roughness", {"sheenColorFactor": [1, 1, 1], "sheenRoughnessTexture": {"index": 1}},
                     (1, 1, 1)),
                    ("unlit", {"sheenColorFactor": [1, 1, 1], "sheenRoughnessFactor": 1,
                               "sheenColorTexture": {"index": 1}, "sheenRoughnessTexture": {"index": 1}}, None),
                ):
                    with self.subTest(extension=extension, case=case):
                        path = root / (f"sheen_{case.replace(' ', '_')}." + extension)
                        document, binary = textured_document(extension, None, PNG)
                        document["extensionsUsed"] = ["KHR_materials_sheen"]
                        document["images"].append({"uri": "data:image/png;base64,AA=="})
                        document["textures"].append({"source": 1})
                        extensions = {"KHR_materials_sheen": sheen}
                        if case == "unlit":
                            document["extensionsUsed"].append("KHR_materials_unlit")
                            extensions["KHR_materials_unlit"] = {}
                        document["materials"][0]["extensions"] = extensions
                        write_asset(path, document, binary)
                        result = open_with_stderr(path)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        stage = Usd.Stage.Open(str(path))
                        usd_material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                        self.assertFalse(sheen_prims(usd_material, "mtlx"))
                        mtlx = usd_material.ComputeSurfaceSource("mtlx")[0]
                        if color is None:
                            self.assertFalse(mtlx.GetInput("sheen_color"))
                        else:
                            self.assertEqual(mtlx.GetInput("sheen_color").Get(), Gf.Vec3f(*color))
                        self.assertFalse(mtlx.GetInput("sheen_roughness"))

    def test_sheen_failures_preserve_layer(self):
        texture_range = "material texture uses a texture coordinate set the primitive does not have"
        wrong_type = "KHR_materials_sheen has a field of the wrong type"
        out_of_range = "KHR_materials_sheen is out of range"
        color_range = "material sheen color factor is non-finite or out of range"
        roughness_range = "material sheen roughness factor is non-finite or out of range"
        white = {"sheenColorFactor": [1, 1, 1]}
        cases = (
            ("not an object", [1], "KHR_materials_sheen is not an object"),
            ("roughness of the wrong type", {"sheenRoughnessFactor": "1"}, wrong_type),
            ("color not an array", {"sheenColorFactor": 1}, wrong_type),
            # fastgltf accepts a short color, keeping 0 for the rest.
            ("short color", {"sheenColorFactor": [0.5, 0.5]}, wrong_type),
            ("long color", {"sheenColorFactor": [0.5, 0.5, 0.5, 0.5]}, wrong_type),
            ("color of the wrong type", {"sheenColorFactor": [0.5, "0.5", 0.5]}, wrong_type),
            ("texture not an object", {"sheenColorTexture": 0}, wrong_type),
            ("roughness texture without an index", {"sheenRoughnessTexture": {"texCoord": 0}}, wrong_type),
            ("roughness overflow", {"sheenRoughnessFactor": 1e39}, out_of_range),
            ("color overflow", {"sheenColorFactor": [1e39, 0, 0]}, out_of_range),
            ("color above one", {"sheenColorFactor": [1.5, 1, 1]}, color_range),
            ("negative color", {"sheenColorFactor": [1, 1, -0.5]}, color_range),
            ("roughness above one", {**white, "sheenRoughnessFactor": 1.5}, roughness_range),
            ("negative roughness", {**white, "sheenRoughnessFactor": -0.5}, roughness_range),
            ("missing color UV set", {**white, "sheenColorTexture": {"index": 0, "texCoord": 1}}, texture_range),
            ("missing roughness UV set", {**white, "sheenRoughnessFactor": 1,
                                          "sheenRoughnessTexture": {"index": 0, "texCoord": 1}}, texture_range),
            ("texture out of range", {**white, "sheenColorTexture": {"index": 5}},
             "texture index is out of range|cannot parse"),
            # Ignoring the texture or the extension does not make an invalid value or a missing UV set valid.
            ("black missing UV set", {"sheenRoughnessTexture": {"index": 0, "texCoord": 1}}, texture_range),
            ("black roughness above one", {"sheenRoughnessFactor": 1.5}, roughness_range),
            ("unlit color above one", {"sheenColorFactor": [2, 1, 1]}, color_range),
        )
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                path = pathlib.Path(directory) / ("sheen." + extension)
                document, binary = textured_document(extension, None, PNG)
                document["extensionsUsed"] = ["KHR_materials_sheen"]
                document["materials"][0]["extensions"] = {"KHR_materials_sheen": white}
                # The second primitive has one UV set only.
                document["materials"].append({})
                document["meshes"][0]["primitives"][1]["material"] = 1
                write_asset(path, document, binary)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer_text(layer)
                mtlx = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/mtlx/surface")
                self.assertEqual(mtlx.GetInput("sheen_color").Get(), Gf.Vec3f(1, 1, 1))
                for case, value, message in cases:
                    with self.subTest(extension=extension, case=case):
                        invalid, invalid_binary = textured_document(extension, None, PNG)
                        invalid["extensionsUsed"] = ["KHR_materials_sheen"]
                        invalid["meshes"][0]["primitives"][1]["material"] = 1
                        extensions = {"KHR_materials_sheen": value}
                        if case.startswith("unlit"):
                            invalid["extensionsUsed"].append("KHR_materials_unlit")
                            extensions["KHR_materials_unlit"] = {}
                        invalid["materials"].append({"extensions": extensions})
                        write_asset(path, invalid, invalid_binary)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)

    def test_anisotropy_materials(self):
        name = "KHR_materials_anisotropy"
        cases = (("default", {}, False), ("disabled", {"anisotropyRotation": 0.5}, False),
                 ("full", {"anisotropyStrength": 1}, True),
                 ("negative rotation", {"anisotropyStrength": 0.5, "anisotropyRotation": -0.7}, True),
                 ("large rotation", {"anisotropyStrength": 1, "anisotropyRotation": 1e30}, True),
                 ("metal", {"anisotropyStrength": 1}, True),
                 ("unlit", {"anisotropyStrength": 1}, False))
        with tempfile.TemporaryDirectory() as directory:
            for extension, required, (case, values, active) in itertools.product(
                    ("gltf", "glb"), (False, True), cases):
                with self.subTest(extension=extension, required=required, case=case):
                    document, binary = mesh_document(extension)
                    document["extensionsUsed"] = [name]
                    if required:
                        document["extensionsRequired"] = [name]
                    extensions = {name: values}
                    if case == "unlit":
                        document["extensionsUsed"].append("KHR_materials_unlit")
                        extensions["KHR_materials_unlit"] = {}
                    plain = {"pbrMetallicRoughness": {"baseColorFactor": [0.2, 0.4, 0.6, 1],
                                                    "metallicFactor": int(case == "metal"), "roughnessFactor": 0.5}}
                    document["materials"] = [{**plain, "extensions": extensions}, plain]
                    for i, primitive in enumerate(document["meshes"][0]["primitives"]):
                        primitive["material"] = i
                    path = pathlib.Path(directory) / f"anisotropy_{case}_{required}.{extension}"
                    write_asset(path, document, binary)
                    result = subprocess.run([sys.executable, "-c",
                                             "from pxr import Usd; import sys; assert Usd.Stage.Open(sys.argv[1])",
                                             str(path)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn(f"'{name}' is not supported", result.stderr)
                    self.assertEqual("UsdPreviewSurface has no anisotropy" in result.stderr, active)
                    self.assertEqual("renderer-derived tangent frame" in result.stderr, active)
                    self.assertEqual("is ignored, because the material is unlit" in result.stderr, case == "unlit")
                    stage = Usd.Stage.Open(str(path))
                    material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                    preview, mtlx = material.ComputeSurfaceSource()[0], material.ComputeSurfaceSource("mtlx")[0]
                    self.assert_material_nodegraphs(material, preview, mtlx)
                    rotation = struct.unpack("<f", struct.pack("<f", values.get("anisotropyRotation", 0)))[0]
                    expected = {"anisotropy_strength": values.get("anisotropyStrength", 0),
                                "anisotropy_rotation": math.remainder(rotation, 2 * math.pi)}
                    for input_name, value in expected.items():
                        self.assertFalse(preview.GetInput(input_name))
                        if active:
                            self.assertAlmostEqual(mtlx.GetInput(input_name).Get(), value, places=6)
                        else:
                            self.assertFalse(mtlx.GetInput(input_name))
                        self.assertFalse(UsdShade.Shader.Get(stage, "/Asset/mtl/Material_1/mtlx/surface").GetInput(input_name))
                    self.assertFalse(mtlx.GetInput("tangent"))  # Explicit, warned renderer-frame approximation.

    def test_anisotropy_textures(self):
        def source(shader, name):
            return UsdShade.Shader(shader.GetInput(name).GetConnectedSource()[0].GetPrim())

        name = "KHR_materials_anisotropy"
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "anisotropy.png").write_bytes(PNG)
            for case_index, (extension, uri, factor, rotation) in enumerate(itertools.product(
                    ("gltf", "glb"), (None, "anisotropy.png", "data:image/png;base64," + base64.b64encode(PNG).decode()),
                    (0.5, 1), (0, -0.7))):
                with self.subTest(extension=extension, uri=uri, factor=factor, rotation=rotation):
                    document, binary = textured_document(extension, None if uri is None else {"uri": uri}, PNG)
                    document["extensionsUsed"] = [name, "KHR_texture_transform"]
                    document["extensionsRequired"] = [name]
                    document["samplers"] = [{"wrapS": 33648, "wrapT": 33071}]
                    document["textures"][0]["sampler"] = 0
                    document["materials"][0]["extensions"] = {name: {
                        "anisotropyStrength": factor, "anisotropyRotation": rotation,
                        "anisotropyTexture": {"index": 0, "extensions": {
                            "KHR_texture_transform": {"texCoord": 1, "offset": [0.25, 0.5], "rotation": 0.3}}}}}
                    path = root / f"anisotropy_{case_index}.{extension}"
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                    surface = material.ComputeSurfaceSource("mtlx")[0]
                    weight = source(surface, "anisotropy_strength")
                    if factor != 1:
                        self.assertEqual(weight.GetIdAttr().Get(), "ND_multiply_float")
                        self.assertEqual(weight.GetInput("in2").Get(), factor)
                        weight = source(weight, "in1")
                    self.assertEqual(weight.GetInput("index").Get(), 2)
                    image = source(weight, "in")
                    self.assertEqual(image.GetIdAttr().Get(), "ND_image_vector4")
                    self.assertFalse(image.GetInput("file").GetAttr().HasColorSpace())
                    self.assertEqual(asset_bytes(image.GetInput("file").Get().resolvedPath), PNG)
                    self.assertEqual(image.GetInput("uaddressmode").Get(), "mirror")
                    self.assertEqual(image.GetInput("vaddressmode").Get(), "clamp")
                    reader = source(image, "texcoord")
                    while reader.GetIdAttr().Get() != "ND_geompropvalue_vector2":
                        reader = source(reader, "in1" if reader.GetInput("in1") else "in")
                    self.assertEqual(reader.GetInput("geomprop").Get(), "st1")
                    selected = source(surface, "anisotropy_rotation")
                    if rotation:
                        self.assertEqual(selected.GetIdAttr().Get(), "ND_add_float")
                        self.assertAlmostEqual(selected.GetInput("in2").Get(), rotation, places=6)
                        selected = source(selected, "in1")
                    self.assertEqual(selected.GetIdAttr().Get(), "ND_ifgreater_float")
                    self.assertEqual(selected.GetInput("in2").Get(), 0)
                    angle = source(selected, "in1")
                    self.assertEqual(angle.GetIdAttr().Get(), "ND_atan2_float")
                    for input_name, index in (("inx", 0), ("iny", 1)):
                        channel = source(angle, input_name)
                        self.assertEqual(channel.GetInput("index").Get(), index)
                        remap = source(channel, "in")
                        self.assertEqual(remap.GetIdAttr().Get(), "ND_subtract_vector4")
                        self.assertEqual(remap.GetInput("in2").Get(), Gf.Vec4f(1))
                        scale = source(remap, "in1")
                        self.assertEqual(scale.GetInput("in2").Get(), Gf.Vec4f(2))
                        self.assertEqual(source(scale, "in1").GetPath(), image.GetPath())
                    for prim in Usd.PrimRange(material.GetPrim().GetChild("mtlx")):
                        if prim.IsA(UsdShade.Shader):
                            shader = UsdShade.Shader(prim)
                            node = Sdr.Registry().GetShaderNodeByIdentifier(shader.GetIdAttr().Get())
                            self.assertTrue(node, shader.GetIdAttr().Get())
                            for port in shader.GetInputs():
                                self.assertIsNotNone(node.GetShaderInput(port.GetBaseName()), str(port))
                    self.assertFalse([p for p in Usd.PrimRange(material.GetPrim().GetChild("preview"))
                                      if p.GetName().startswith("anisotropy")])
            for extension, unlit in itertools.product(("gltf", "glb"), (False, True)):
                document, binary = textured_document(extension, None, PNG)
                document["images"].append({"uri": "data:image/png;base64,AA=="})
                document["textures"].append({"source": 1})
                document["extensionsUsed"] = [name]
                document["materials"][0]["extensions"] = {name: {
                    "anisotropyStrength": int(unlit), "anisotropyTexture": {"index": 1}}}
                if unlit:
                    document["extensionsUsed"].append("KHR_materials_unlit")
                    document["materials"][0]["extensions"]["KHR_materials_unlit"] = {}
                path = root / f"unused_{unlit}.{extension}"
                write_asset(path, document, binary)
                stage = Usd.Stage.Open(str(path))
                self.assertFalse(UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/mtlx/surface").GetInput("anisotropy_strength"))

    def test_anisotropy_failures_preserve_layer(self):
        name = "KHR_materials_anisotropy"
        cases = [("object", [], name + " is not an object")]
        for field in ("anisotropyStrength", "anisotropyRotation"):
            cases.extend(((field + " type", {field: "1"}, name + " has a field of the wrong type"),
                          (field + " overflow", {field: 1e39}, name + " is out of range")))
        cases.extend((
            ("negative", {"anisotropyStrength": -0.1}, "material anisotropy strength is non-finite or out of range"),
            ("large", {"anisotropyStrength": 1.1}, "material anisotropy strength is non-finite or out of range"),
            ("unlit invalid", {"anisotropyStrength": -1}, "material anisotropy strength is non-finite or out of range"),
            ("texture type", {"anisotropyTexture": 0}, name + " has a field of the wrong type"),
            ("texture index", {"anisotropyTexture": {}}, name + " has a field of the wrong type"),
            ("UV", {"anisotropyTexture": {"index": 0, "texCoord": 1}}, "material texture uses a texture coordinate set"),
            ("index range", {"anisotropyStrength": 1, "anisotropyTexture": {"index": 5}}, "texture index is out of range|cannot parse"),
            ("damaged", {"anisotropyStrength": 1, "anisotropyTexture": {"index": 1}}, "image|PNG|png")))
        with tempfile.TemporaryDirectory() as directory:
            for extension, required in itertools.product(("gltf", "glb"), (False, True)):
                path = pathlib.Path(directory) / f"anisotropy_{required}.{extension}"
                document, binary = textured_document(extension, None, PNG)
                write_asset(path, document, binary)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer_text(layer)
                for case, values, message in cases:
                    with self.subTest(extension=extension, required=required, case=case):
                        invalid, invalid_binary = textured_document(extension, None, PNG)
                        invalid["extensionsUsed"] = [name]
                        if required:
                            invalid["extensionsRequired"] = [name]
                        invalid["images"].append({"uri": "data:image/png;base64,AA=="})
                        invalid["textures"].append({"source": 1})
                        invalid["meshes"][0]["primitives"][1]["material"] = 1
                        extensions = {name: values}
                        if case.startswith("unlit"):
                            invalid["extensionsUsed"].append("KHR_materials_unlit")
                            extensions["KHR_materials_unlit"] = {}
                        invalid["materials"].append({"extensions": extensions})
                        write_asset(path, invalid, invalid_binary)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)

    def test_iridescence_materials(self):
        extension_name = "KHR_materials_iridescence"
        warning = "UsdPreviewSurface has no iridescence; its network ignores " + extension_name
        cases = (
            ("defaults", {}, False),
            ("disabled", {"iridescenceFactor": 0, "iridescenceIor": 2}, False),
            ("enabled", {"iridescenceFactor": 1}, True),
            ("half", {"iridescenceFactor": 0.5, "iridescenceIor": 2,
                      "iridescenceThicknessMinimum": 800, "iridescenceThicknessMaximum": 200}, True),
            ("zero thickness", {"iridescenceFactor": 1, "iridescenceIor": 1,
                                "iridescenceThicknessMaximum": 0}, True),
            ("metal", {"iridescenceFactor": 1}, True),
            ("unlit", {"iridescenceFactor": 1}, False),
        )
        with tempfile.TemporaryDirectory() as directory:
            for extension, (case, film, enabled), required in itertools.product(
                    ("gltf", "glb"), cases, (False, True)):
                with self.subTest(extension=extension, case=case, required=required):
                    path = pathlib.Path(directory) / f"film_{case}_{required}.{extension}"
                    document, binary = mesh_document(extension)
                    document["extensionsUsed"] = [extension_name]
                    if required:
                        document["extensionsRequired"] = [extension_name]
                    extensions = {extension_name: film}
                    if case == "unlit":
                        extensions["KHR_materials_unlit"] = {}
                        document["extensionsUsed"].append("KHR_materials_unlit")
                    plain = {"pbrMetallicRoughness": {"baseColorFactor": [0.2, 0.4, 0.6, 1],
                                                    "metallicFactor": int(case == "metal"), "roughnessFactor": 0.5}}
                    document["materials"] = [{**plain, "extensions": extensions}, plain]
                    for i, primitive in enumerate(document["meshes"][0]["primitives"]):
                        primitive["material"] = i
                    write_asset(path, document, binary)
                    result = subprocess.run([sys.executable, "-c",
                                             "from pxr import Usd; import sys; assert Usd.Stage.Open(sys.argv[1])",
                                             str(path)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn(f"'{extension_name}' is not supported", result.stderr)
                    self.assertEqual(warning in result.stderr, enabled, result.stderr)
                    self.assertEqual("is ignored, because the material is unlit" in result.stderr,
                                     case == "unlit", result.stderr)
                    stage = Usd.Stage.Open(str(path))
                    material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                    preview = material.ComputeSurfaceSource()[0]
                    mtlx = material.ComputeSurfaceSource("mtlx")[0]
                    self.assert_material_nodegraphs(material, preview, mtlx)
                    expected = {"iridescence": film.get("iridescenceFactor", 0),
                                "iridescence_ior": film.get("iridescenceIor", 1.3),
                                "iridescence_thickness": film.get("iridescenceThicknessMaximum", 400)}
                    for name, value in expected.items():
                        self.assertFalse(preview.GetInput(name))
                        if enabled:
                            self.assertAlmostEqual(mtlx.GetInput(name).Get(), value, places=6)
                        else:
                            self.assertFalse(mtlx.GetInput(name))
                        plain_shader = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_1/mtlx/surface")
                        self.assertFalse(plain_shader.GetInput(name))
                    if enabled:
                        self.assertEqual(mtlx.GetInput("iridescence_ior").GetAttr().GetVariability(),
                                         Sdf.VariabilityUniform)
                        self.assertFalse(mtlx.GetInput("ior"))
                        self.assertFalse(mtlx.GetInput("specular"))
                        self.assertTrue(Gf.IsClose(mtlx.GetInput("base_color").Get(), Gf.Vec3f(0.2, 0.4, 0.6), 1e-6))

    def test_iridescence_textures(self):
        def source(shader, name):
            connected = shader.GetInput(name).GetConnectedSource()
            return UsdShade.Shader(connected[0].GetPrim()) if connected else None

        # Exercise every supported image source, including GLB bufferViews, both range directions,
        # the factor multiply and the direct channel at 1.
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "film.png").write_bytes(PNG)
            for extension, uri, factor, endpoints in itertools.product(
                    ("gltf", "glb"), (None, "film.png", "data:image/png;base64," + base64.b64encode(PNG).decode()),
                    (0.5, 1), ((100, 400), (800, 200))):
                with self.subTest(extension=extension, uri=uri, factor=factor, endpoints=endpoints):
                    path = root / f"film_{extension}_{factor}_{endpoints[0]}_{len(uri or '')}.{extension}"
                    document, binary = textured_document(extension, {"uri": uri} if uri else None, PNG)
                    document["extensionsUsed"] = ["KHR_materials_iridescence", "KHR_texture_transform"]
                    document["extensionsRequired"] = ["KHR_materials_iridescence"]
                    document["samplers"] = [{"wrapS": 33648, "wrapT": 33071}]
                    document["textures"].append({"source": 0, "sampler": 0})
                    document["materials"][0]["extensions"] = {"KHR_materials_iridescence": {
                        "iridescenceFactor": factor, "iridescenceTexture": {"index": 0},
                        "iridescenceThicknessMinimum": endpoints[0], "iridescenceThicknessMaximum": endpoints[1],
                        "iridescenceThicknessTexture": {"index": 1, "extensions": {
                            "KHR_texture_transform": {"offset": [0.25, 0], "texCoord": 1}}}}}
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    material = UsdShade.Material.Get(stage, "/Asset/mtl/Material_0")
                    mtlx = material.ComputeSurfaceSource("mtlx")[0]
                    weight = source(mtlx, "iridescence")
                    if factor != 1:
                        self.assertEqual(weight.GetIdAttr().Get(), "ND_multiply_float")
                        self.assertEqual(weight.GetInput("in2").Get(), factor)
                        weight = source(weight, "in1")
                    self.assertEqual(weight.GetInput("index").Get(), 0)
                    weight_image = source(weight, "in")
                    mix = source(mtlx, "iridescence_thickness")
                    self.assertEqual(mix.GetIdAttr().Get(), "ND_mix_float")
                    self.assertEqual(mix.GetInput("bg").Get(), endpoints[0])
                    self.assertEqual(mix.GetInput("fg").Get(), endpoints[1])
                    channel = source(mix, "mix")
                    self.assertEqual(channel.GetInput("index").Get(), 1)
                    image = source(channel, "in")
                    for reader in (image, weight_image):
                        self.assertEqual(reader.GetIdAttr().Get(), "ND_image_vector4")
                        self.assertFalse(reader.GetInput("file").GetAttr().HasColorSpace())
                        self.assertEqual(asset_bytes(reader.GetInput("file").Get().resolvedPath), PNG)
                    self.assertEqual(image.GetInput("uaddressmode").Get(), "mirror")
                    self.assertEqual(image.GetInput("vaddressmode").Get(), "clamp")
                    translate = source(image, "texcoord")
                    self.assertEqual(translate.GetIdAttr().Get(), "ND_add_vector2")
                    reader = translate
                    while reader.GetIdAttr().Get() != "ND_geompropvalue_vector2":
                        reader = source(reader, "in1" if reader.GetInput("in1") else "in")
                    self.assertEqual(reader.GetInput("geomprop").Get(), "st1")
                    self.assertFalse([p for p in Usd.PrimRange(material.GetPrim().GetChild("preview"))
                                      if p.GetName().startswith("iridescence")])

            # Do not resolve damaged images when the film is disabled or its thickness is fixed.
            for extension, case in itertools.product(("gltf", "glb"), ("zero", "unlit", "fixed")):
                with self.subTest(extension=extension, case=case):
                    document, binary = textured_document(extension, None, PNG)
                    document["extensionsUsed"] = ["KHR_materials_iridescence"]
                    document["images"].append({"uri": "data:image/png;base64,AA=="})
                    document["textures"].append({"source": 1})
                    film = {"iridescenceFactor": 0 if case == "zero" else 1,
                            "iridescenceThicknessTexture": {"index": 1}}
                    if case == "fixed":
                        film.update(iridescenceThicknessMinimum=200, iridescenceThicknessMaximum=200)
                    else:
                        film["iridescenceTexture"] = {"index": 1}
                    document["materials"][0]["extensions"] = {"KHR_materials_iridescence": film}
                    if case == "unlit":
                        document["extensionsUsed"].append("KHR_materials_unlit")
                        document["materials"][0]["extensions"]["KHR_materials_unlit"] = {}
                    path = root / f"unused_{case}.{extension}"
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    shader = UsdShade.Shader.Get(stage, "/Asset/mtl/Material_0/mtlx/surface")
                    if case == "fixed":
                        self.assertEqual(shader.GetInput("iridescence_thickness").Get(), 200)
                        self.assertFalse(shader.GetInput("iridescence_thickness").HasConnectedSource())
                    else:
                        self.assertFalse(shader.GetInput("iridescence"))

    def test_iridescence_failures_preserve_layer(self):
        wrong = "KHR_materials_iridescence has a field of the wrong type"
        overflow = "KHR_materials_iridescence is out of range"
        uv = "material texture uses a texture coordinate set the primitive does not have"
        cases = [("object", [], "KHR_materials_iridescence is not an object")]
        for field in ("iridescenceFactor", "iridescenceIor", "iridescenceThicknessMinimum",
                      "iridescenceThicknessMaximum"):
            cases.extend(((field + " type", {field: "1"}, wrong), (field + " overflow", {field: 1e39}, overflow)))
        cases.extend((
            ("negative factor", {"iridescenceFactor": -0.1}, "material iridescence factor is non-finite or out of range"),
            ("large factor", {"iridescenceFactor": 1.1}, "material iridescence factor is non-finite or out of range"),
            ("ior below one", {"iridescenceIor": 0.9}, "material iridescence ior is non-finite or below 1"),
            ("negative minimum", {"iridescenceThicknessMinimum": -1}, "material iridescence thickness is non-finite or negative"),
            ("negative maximum", {"iridescenceThicknessMaximum": -1}, "material iridescence thickness is non-finite or negative"),
            ("unlit invalid", {"iridescenceIor": 0}, "material iridescence ior is non-finite or below 1"),
        ))
        for slot in ("iridescenceTexture", "iridescenceThicknessTexture"):
            cases.extend(((slot + " type", {slot: 0}, wrong),
                          (slot + " index", {slot: {"texCoord": 0}}, wrong),
                          (slot + " UV", {slot: {"index": 0, "texCoord": 1}}, uv),
                          (slot + " range", {"iridescenceFactor": 1, slot: {"index": 5}},
                           "texture index is out of range|cannot parse"),
                          (slot + " damaged", {"iridescenceFactor": 1, slot: {"index": 1}},
                           "image|PNG|png")))
        with tempfile.TemporaryDirectory() as directory:
            for extension, required in itertools.product(("gltf", "glb"), (False, True)):
                path = pathlib.Path(directory) / f"film_{required}.{extension}"
                document, binary = textured_document(extension, None, PNG)
                write_asset(path, document, binary)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer_text(layer)
                for case, film, message in cases:
                    with self.subTest(extension=extension, required=required, case=case):
                        invalid, invalid_binary = textured_document(extension, None, PNG)
                        invalid["extensionsUsed"] = ["KHR_materials_iridescence"]
                        if required:
                            invalid["extensionsRequired"] = ["KHR_materials_iridescence"]
                        invalid["images"].append({"uri": "data:image/png;base64,AA=="})
                        invalid["textures"].append({"source": 1})
                        # Author the valid first primitive before failing on the second.
                        invalid["meshes"][0]["primitives"][1]["material"] = 1
                        extensions = {"KHR_materials_iridescence": film}
                        if case.startswith("unlit"):
                            invalid["extensionsUsed"].append("KHR_materials_unlit")
                            extensions["KHR_materials_unlit"] = {}
                        invalid["materials"].append({"extensions": extensions})
                        write_asset(path, invalid, invalid_binary)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)

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
            "skin": "node 1: skins are not supported",
            "morph target": "morph targets are not supported",
            "mesh weights": "mesh 0: morph weights are not supported",
            "node weights": "node 1: morph weights are not supported",
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

    def test_cameras(self):
        # Each camera prim, its glTF camera, and the rotation and position of its node without scale.
        expected = {
            "Node_0/Camera_0": (0, rigid((1, 0, 0), 0, (2, 0, 0))),
            "Node_1/Camera_1": (1, rigid((0, 1, 0), 90, (0, 1, 5))),
            "Node_1/Node_2/Camera_2": (2, rigid((0, 1, 0), 90, (0, 1, 3))),
            "Node_3/Camera_0": (0, rigid((1, 0, 0), 90, (0, 0, 10))),
        }
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = pathlib.Path(directory) / ("cameras." + extension)
                    document, binary = camera_document(extension)
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    cache = UsdGeom.XformCache()
                    authored = {str(prim.GetPath()) for prim in stage.Traverse() if prim.IsA(UsdGeom.Camera)}
                    self.assertEqual(authored, {"/Asset/geo/" + key for key in expected})
                    self.assertTrue(stage.GetPrimAtPath("/Asset/geo/Node_0/Mesh_0/Primitive_0"))
                    for key, (index, view) in expected.items():
                        prim = stage.GetPrimAtPath("/Asset/geo/" + key)
                        camera = UsdGeom.Camera(prim)
                        source = document["cameras"][index]
                        self.assertEqual(prim.GetDisplayName(), source.get("name", ""))
                        self.assertEqual(camera.GetProjectionAttr().Get(), source["type"])
                        # Only a camera under a scaled node needs a transform to cancel the scale.
                        self.assertEqual(bool(camera.GetOrderedXformOps()), key.startswith("Node_1"), key)
                        if source["type"] == "perspective":
                            self.assertAlmostEqual(camera.GetHorizontalApertureAttr().Get(),
                                                   Gf.Camera.DEFAULT_HORIZONTAL_APERTURE, places=4)
                        else:
                            self.assertFalse(camera.GetFocalLengthAttr().HasAuthoredValue())
                        self.assertFalse(camera.GetFStopAttr().HasAuthoredValue())
                        # Hydra views through the inverse of the prim's whole transform.
                        world = cache.GetLocalToWorldTransform(prim)
                        actual = world.GetInverse() * camera.GetCamera().frustum.ComputeProjectionMatrix()
                        wanted = view.GetInverse() * gltf_projection(source)
                        self.assertTrue(Gf.IsClose(actual, wanted, 1e-5), f"{key}: {actual} != {wanted}")
                    perspective = UsdGeom.Camera(stage.GetPrimAtPath("/Asset/geo/Node_0/Camera_0")).GetCamera()
                    self.assertAlmostEqual(perspective.aspectRatio, 1.5, places=5)
                    self.assertAlmostEqual(math.radians(perspective.GetFieldOfView(Gf.Camera.FOVVertical)),
                                           0.660593, places=5)
                    infinite = UsdGeom.Camera(stage.GetPrimAtPath("/Asset/geo/Node_1/Camera_1")).GetCamera()
                    self.assertEqual(infinite.aspectRatio, 1.0)
                    self.assertEqual(infinite.clippingRange, Gf.Range1f(0.5, 1000000))

    def test_cameras_reference_into_a_scaled_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "cameras.gltf"
            document, binary = camera_document("gltf")
            write_asset(path, document, binary)
            stage = Usd.Stage.CreateInMemory()
            host = UsdGeom.Xform.Define(stage, "/Host")
            host.AddScaleOp().Set(Gf.Vec3f(100))
            host.GetPrim().GetReferences().AddReference(str(path))
            prim = stage.GetPrimAtPath("/Host/geo/Node_1/Camera_1")
            camera = UsdGeom.Camera(prim)
            # The host's scale acts as a change of units, so distances in the view grow with it.
            world = UsdGeom.XformCache().GetLocalToWorldTransform(prim)
            scale = Gf.Matrix4d().SetScale(100)
            actual = world.GetInverse() * camera.GetCamera().frustum.ComputeProjectionMatrix()
            wanted = scale.GetInverse() * rigid((0, 1, 0), 90, (0, 1, 5)).GetInverse() * gltf_projection(
                document["cameras"][1])
            self.assertTrue(Gf.IsClose(actual, wanted, 1e-5), f"{actual} != {wanted}")

    def test_camera_failures_preserve_layer(self):
        def mutate(document, name):
            perspective = document["cameras"][0]["perspective"]
            infinite = document["cameras"][1]["perspective"]
            orthographic = document["cameras"][2]["orthographic"]
            if name == "camera index":
                document["nodes"][0]["camera"] = 3
            elif name == "yfov of pi":
                perspective["yfov"] = math.pi
            elif name == "yfov of 0":
                perspective["yfov"] = 0
            elif name == "aspectRatio":
                perspective["aspectRatio"] = 0
            elif name == "perspective znear":
                perspective["znear"] = 0
            elif name == "perspective zfar":
                perspective["zfar"] = perspective["znear"]
            elif name == "infinite znear":
                infinite["znear"] = 1000000
            elif name == "xmag":
                orthographic["xmag"] = 0
            elif name == "negative ymag":
                orthographic["ymag"] = -1.5
            elif name == "orthographic znear":
                orthographic["znear"] = -1
            elif name == "orthographic zfar":
                orthographic["zfar"] = 0
            elif name == "beyond a float":
                orthographic["xmag"] = 3e38
            elif name == "missing yfov":
                del perspective["yfov"]
            elif name == "reflected":
                document["nodes"][0]["scale"] = [-1, 1, 1]
            elif name == "degenerate":
                document["nodes"][0]["scale"] = [1, 0, 1]
            elif name == "sheared":
                # A rotation inside an unequal scale.
                document["nodes"][1]["scale"] = [2, 1, 2]
                document["nodes"][2]["rotation"] = [0, 0, 0.3826834323650898, 0.9238795325112867]

        cases = {
            "camera index": "camera index 3 is out of range",
            "yfov of pi": "camera 0: yfov must be greater than 0 and less than pi",
            "yfov of 0": "camera 0: yfov must be greater than 0 and less than pi",
            "aspectRatio": "camera 0: aspectRatio must be greater than 0",
            "perspective znear": "camera 0: znear must be greater than 0",
            "perspective zfar": "camera 0: zfar must be greater than znear",
            "infinite znear": "camera 1: znear must be less than the far plane of an infinite projection",
            "xmag": "camera 2: xmag and ymag must be greater than 0",
            "negative ymag": "camera 2: xmag and ymag must be greater than 0",
            "orthographic znear": "camera 2: znear must not be negative",
            "orthographic zfar": "camera 2: zfar must be greater than znear",
            "beyond a float": "camera 2: its projection is beyond a float",
            "missing yfov": "cannot parse",
            "reflected": "node 0: camera 0 has no view",
            "degenerate": "node 0: camera 0 has no view",
            "sheared": "node 2: camera 2 has no view",
        }
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                path = pathlib.Path(directory) / ("cameras." + extension)
                document, binary = camera_document(extension)
                write_asset(path, document, binary)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer_text(layer)
                for name, message in cases.items():
                    with self.subTest(extension=extension, name=name):
                        invalid, invalid_binary = camera_document(extension)
                        mutate(invalid, name)
                        write_asset(path, invalid, invalid_binary)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)
                write_asset(path, document, binary)
                self.assertTrue(layer.Reload(True))

    def test_cameras_outside_selected_scene_are_not_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    document, binary = camera_document(extension)
                    document["scenes"][0]["nodes"] = [3]
                    document["cameras"][1]["perspective"]["yfov"] = 0
                    path = pathlib.Path(directory) / ("unselected." + extension)
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    authored = [str(prim.GetPath()) for prim in stage.Traverse() if prim.IsA(UsdGeom.Camera)]
                    self.assertEqual(authored, ["/Asset/geo/Node_3/Camera_0"])

    def test_lights(self):
        # Each light prim, its glTF light, its position, and the direction glTF gives it: the node's
        # transform applied to -Z.
        sheared = Gf.Vec3d(-2 * HALF_TURN_SINE, 0, -HALF_TURN_SINE).GetNormalized()
        expected = {
            "Node_0/Light_0": (0, (2, 0, 0), (0, -1, 0)),
            "Node_1/Light_1": (1, (0, 1, 5), (0, 0, -1)),
            "Node_1/Node_2/Light_2": (2, (2, 1, 5), sheared),
            "Node_3/Light_1": (1, (0, 0, 10), (0, 1, 0)),
            "Node_3/Node_4/Light_3": (3, (0, 0, 10), (0, 1, 0)),
        }
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    path = pathlib.Path(directory) / ("lights." + extension)
                    document, binary = light_document(extension)
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    cache = UsdGeom.XformCache()
                    authored = {str(prim.GetPath()) for prim in stage.Traverse() if prim.HasAPI(UsdLux.LightAPI)}
                    self.assertEqual(authored, {"/Asset/geo/" + key for key in expected})
                    self.assertTrue(stage.GetPrimAtPath("/Asset/geo/Node_0/Mesh_0/Primitive_0"))
                    sources = document["extensions"]["KHR_lights_punctual"]["lights"]
                    for key, (index, position, direction) in expected.items():
                        prim = stage.GetPrimAtPath("/Asset/geo/" + key)
                        source = sources[index]
                        light = UsdLux.LightAPI(prim)
                        self.assertEqual(prim.GetDisplayName(), source.get("name", ""))
                        self.assertEqual(light.GetColorAttr().Get(), Gf.Vec3f(*source.get("color", [1, 1, 1])), key)
                        # Only a light under an unscaled node needs no transform.
                        self.assertEqual(bool(UsdGeom.Xformable(prim).GetOrderedXformOps()),
                                         key != "Node_0/Light_0", key)
                        # A renderer applies the whole transform, which must not scale the light.
                        world = cache.GetLocalToWorldTransform(prim)
                        rotation = world.ExtractRotationMatrix()
                        self.assertTrue(Gf.IsClose(rotation * rotation.GetTranspose(), Gf.Matrix3d(1), 1e-6), key)
                        self.assertAlmostEqual(rotation.GetDeterminant(), 1, places=6)
                        self.assertTrue(Gf.IsClose(world.ExtractTranslation(), Gf.Vec3d(*position), 1e-9), key)
                        self.assertTrue(Gf.IsClose(world.TransformDir(Gf.Vec3d(0, 0, -1)), Gf.Vec3d(*direction),
                                                   1e-6), key)
                        intensity = light.GetIntensityAttr().Get()
                        if source["type"] == "directional":
                            # A normalized distant light of angle 0 has its intensity in lux.
                            self.assertTrue(prim.IsA(UsdLux.DistantLight))
                            self.assertEqual(UsdLux.DistantLight(prim).GetAngleAttr().Get(), 0)
                            self.assertTrue(light.GetNormalizeAttr().Get())
                            self.assertAlmostEqual(intensity, source.get("intensity", 1), places=5)
                            continue
                        # An unnormalized sphere's intensity in candela is its luminance times its
                        # projected area.
                        self.assertTrue(prim.IsA(UsdLux.SphereLight))
                        sphere = UsdLux.SphereLight(prim)
                        self.assertTrue(sphere.GetTreatAsPointAttr().Get())
                        self.assertFalse(light.GetNormalizeAttr().Get())
                        radius = sphere.GetRadiusAttr().Get()
                        self.assertAlmostEqual(radius, 0.001, places=9)
                        self.assertAlmostEqual(intensity * math.pi * radius * radius / source.get("intensity", 1), 1,
                                               places=5)
                        self.assertEqual(prim.HasAPI(UsdLux.ShapingAPI), source["type"] == "spot", key)
                        if source["type"] == "spot":
                            shaping = UsdLux.ShapingAPI(prim)
                            outer = math.degrees(source["spot"].get("outerConeAngle", math.pi / 4))
                            inner = math.degrees(source["spot"].get("innerConeAngle", 0))
                            angle = shaping.GetShapingConeAngleAttr().Get()
                            softness = shaping.GetShapingConeSoftnessAttr().Get()
                            # UsdLux starts to soften the cone at (1 - softness) times its angle.
                            self.assertAlmostEqual(angle, outer, places=4)
                            self.assertAlmostEqual((1 - softness) * angle, inner, places=4)

    def test_lights_reference_into_a_scaled_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "lights.gltf"
            document, binary = light_document("gltf")
            write_asset(path, document, binary)
            stage = Usd.Stage.CreateInMemory()
            host = UsdGeom.Xform.Define(stage, "/Host")
            host.AddScaleOp().Set(Gf.Vec3f(100))
            host.GetPrim().GetReferences().AddReference(str(path))
            prim = stage.GetPrimAtPath("/Host/geo/Node_1/Light_1")
            # The host's scale acts as a change of units, so the sphere grows with the scene.
            world = UsdGeom.XformCache().GetLocalToWorldTransform(prim)
            wanted = Gf.Matrix4d().SetTranslate(Gf.Vec3d(0, 1, 5)) * Gf.Matrix4d().SetScale(100)
            self.assertTrue(Gf.IsClose(world, wanted, 1e-9), f"{world} != {wanted}")

    def test_light_range_is_ignored_with_a_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    document, binary = light_document(extension)
                    lights = document["extensions"]["KHR_lights_punctual"]["lights"]
                    lights[0]["range"] = 1
                    lights[1]["range"] = 4
                    path = pathlib.Path(directory) / ("range." + extension)
                    write_asset(path, document, binary)
                    result = subprocess.run(
                        [sys.executable, "-c",
                         "from pxr import Usd, UsdLux; import sys; "
                         "s = Usd.Stage.Open(sys.argv[1]); "
                         "assert s.GetPrimAtPath('/Asset/geo/Node_1/Light_1').HasAPI(UsdLux.LightAPI)",
                         str(path)],
                        capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    warnings = re.findall(r"glTF node \d+: the range of light \d+ is ignored", result.stderr)
                    # Light 1 is on two nodes. A directional light's range has no meaning in glTF.
                    self.assertEqual(sorted(warnings), ["glTF node 1: the range of light 1 is ignored",
                                                        "glTF node 3: the range of light 1 is ignored"])

    def test_light_failures_preserve_layer(self):
        def mutate(document, name):
            lights = document["extensions"]["KHR_lights_punctual"]["lights"]
            spot = lights[2]["spot"]
            if name == "light index":
                document["nodes"][0]["extensions"] = light_extension(4)
            elif name == "color above 1":
                lights[0]["color"] = [1, 1.5, 1]
            elif name == "negative color":
                lights[1]["color"] = [1, -0.5, 1]
            elif name == "negative intensity":
                lights[1]["intensity"] = -1
            elif name == "range of 0":
                lights[1]["range"] = 0
            elif name == "negative inner cone":
                spot["innerConeAngle"] = -0.1
            elif name == "inner cone not below outer":
                spot["innerConeAngle"] = spot["outerConeAngle"]
            elif name == "outer cone beyond pi / 2":
                spot["outerConeAngle"] = 1.6
            elif name == "beyond a float":
                lights[1]["intensity"] = 1e34
            elif name == "intensity of the wrong type":
                lights[1]["intensity"] = "50"
            elif name == "range of the wrong type":
                lights[1]["range"] = [4]
            elif name == "unknown type":
                lights[1]["type"] = "area"
            elif name == "spot without its object":
                del lights[2]["spot"]
            elif name == "degenerate":
                document["nodes"][1]["scale"] = [2, 0, 1]
            elif name == "flattened":
                # No axis is zero, but two coincide.
                document["nodes"][3]["matrix"] = [1, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 10, 1]

        cases = {
            "light index": "light index 4 is out of range",
            "color above 1": "light 0: color components must be from 0 to 1",
            "negative color": "light 1: color components must be from 0 to 1",
            "negative intensity": "light 1: intensity must not be negative",
            "range of 0": "light 1: range must be greater than 0",
            "negative inner cone": "light 2: cone angles must satisfy 0 <= innerConeAngle < outerConeAngle <= pi / 2",
            "inner cone not below outer": "light 2: cone angles must satisfy",
            "outer cone beyond pi / 2": "light 2: cone angles must satisfy",
            "beyond a float": "light 1: its intensity is beyond a float",
            "intensity of the wrong type": "KHR_lights_punctual has a field of the wrong type",
            "range of the wrong type": "KHR_lights_punctual has a field of the wrong type",
            "unknown type": "cannot parse",
            "spot without its object": "cannot parse",
            "degenerate": "node 1: light 1 has no position or direction",
            "flattened": "node 3: light 1 has no position or direction",
        }
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                path = pathlib.Path(directory) / ("lights." + extension)
                document, binary = light_document(extension)
                write_asset(path, document, binary)
                stage = Usd.Stage.Open(str(path))
                layer = stage.GetRootLayer()
                before = layer_text(layer)
                for name, message in cases.items():
                    with self.subTest(extension=extension, name=name):
                        invalid, invalid_binary = light_document(extension)
                        mutate(invalid, name)
                        write_asset(path, invalid, invalid_binary)
                        with self.assertRaisesRegex(Tf.ErrorException, message):
                            layer.Reload(True)
                        self.assertEqual(layer_text(layer), before)
                write_asset(path, document, binary)
                self.assertTrue(layer.Reload(True))

    def test_lights_required_and_outside_selected_scene(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                with self.subTest(extension=extension):
                    document, binary = light_document(extension)
                    document["extensionsRequired"] = ["KHR_lights_punctual"]
                    document["scenes"][0]["nodes"] = [0]
                    # Light 2 is invalid, but no selected node uses it.
                    document["extensions"]["KHR_lights_punctual"]["lights"][2]["spot"]["outerConeAngle"] = 2
                    path = pathlib.Path(directory) / ("unselected." + extension)
                    write_asset(path, document, binary)
                    stage = Usd.Stage.Open(str(path))
                    authored = [str(prim.GetPath()) for prim in stage.Traverse() if prim.HasAPI(UsdLux.LightAPI)]
                    self.assertEqual(authored, ["/Asset/geo/Node_0/Light_0"])

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

    def test_rotations_are_normalized_within_the_tolerance(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("gltf", "glb"):
                for label, component in (("short", 0.707), ("long", 0.7073)):
                    with self.subTest(extension=extension, label=label):
                        path = pathlib.Path(directory) / f"{label}.{extension}"
                        write_asset(path, {
                            "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
                            "nodes": [{"rotation": [0, 0, component, component], "scale": [2, 3, 4]}],
                        })
                        stage = Usd.Stage.Open(str(path))
                        matrix = UsdGeom.Xformable(stage.GetPrimAtPath("/Asset/geo/Node_0")) \
                            .GetLocalTransformation(Usd.TimeCode.Default())
                        # A quarter turn about Z, with each scale exact.
                        expected = Gf.Matrix4d(0, 2, 0, 0, -3, 0, 0, 0, 0, 0, 4, 0, 0, 0, 0, 1)
                        for row in range(4):
                            for column in range(4):
                                self.assertAlmostEqual(matrix[row][column], expected[row][column], places=5)

                with self.subTest(extension=extension, label="outside the tolerance"):
                    path = pathlib.Path(directory) / f"outside.{extension}"
                    write_asset(path, {
                        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
                        "nodes": [{"rotation": [0, 0, 0.72, 0.72]}],
                    })
                    with self.assertRaisesRegex(Tf.ErrorException, "invalid transform"):
                        Usd.Stage.Open(str(path))

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
                                    "extensionsRequired": ["UNKNOWN_extension"]},
                                   "cannot parse .*unsupported required extensions: UNKNOWN_extension"),
            "disabled required extension": ({"asset": {"version": "2.0"}, "scenes": [{}],
                                             "extensionsUsed": ["KHR_draco_mesh_compression"],
                                             "extensionsRequired": ["KHR_draco_mesh_compression"]},
                                            "unsupported required extensions: KHR_draco_mesh_compression"),
            "several required extensions": ({"asset": {"version": "2.0"}, "scenes": [{}],
                                             "extensionsUsed": ["KHR_texture_basisu", "EXT_texture_avif",
                                                                "UNKNOWN_extension"],
                                             "extensionsRequired": ["KHR_texture_basisu", "EXT_texture_avif",
                                                                    "UNKNOWN_extension"]},
                                            "unsupported required extensions: EXT_texture_avif, UNKNOWN_extension"),
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

    def test_concurrent_opens_and_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            good = root / "good.gltf"
            write_asset(good, scene_document())
            bad = root / "bad.gltf"
            write_asset(bad, {"asset": {"version": "2.0"}, "scene": 2, "scenes": [{}]})
            outcomes = []

            def open_repeatedly():
                for _ in range(10):
                    stage = Usd.Stage.Open(str(good))
                    outcomes.append(stage.GetDefaultPrim().GetPath())
                    try:
                        Usd.Stage.Open(str(bad))
                        outcomes.append("opened")
                    except Tf.ErrorException:
                        outcomes.append("failed")

            threads = [threading.Thread(target=open_repeatedly) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(outcomes.count(Sdf.Path("/Asset")), 80)
            self.assertEqual(outcomes.count("failed"), 80)

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
