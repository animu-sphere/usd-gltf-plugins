# SPDX-License-Identifier: Apache-2.0
"""Records what happens to every Khronos sample asset, and compares it with the committed baseline.

Run this in the OpenStrata lookdev environment, with a built workspace and a checkout of
glTF-Sample-Assets at the commit in tests/sample_baseline.json:

    python tests/check_samples.py build/cy2026-windows-x86_64-py313-usd ../glTF-Sample-Assets --render

Each file gets three separate results, each in its own process, so that a crash costs one file:

- opened: whether the stage opens, and otherwise the diagnostic.
- meaning: USD data compared with values this script decodes from the glTF itself.
- rendered: whether Storm draws anything through each shader path. Pixels are not judged.

It exits nonzero if a result differs from the baseline, and --update accepts the new results.
It is not part of CTest, because the assets are not in this repository. The capability matrix
summarizes the results and says what they do and do not show.
"""

import argparse
import base64
import concurrent.futures
import json
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.parse

import numpy

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tests" / "sample_baseline.json"
IMPLEMENTED_EXTENSIONS = {"EXT_texture_webp", "KHR_lights_punctual", "KHR_materials_anisotropy", "KHR_materials_clearcoat", "KHR_materials_ior", "KHR_materials_iridescence",
                          "KHR_materials_sheen", "KHR_materials_specular", "KHR_materials_transmission", "KHR_materials_unlit", "KHR_materials_volume",
                          "KHR_mesh_quantization", "KHR_texture_basisu", "KHR_texture_transform"}
PATHS = ("mtlx", "preview")
CAMERA = "/SampleCheckCamera"
RESULT_MARKER = "SAMPLE_RESULT "
TIMEOUT_SECONDS = 600
RENDER_WIDTH = 256
# Of the pixels of a render, this fraction must be covered for it to count as drawn.
MINIMUM_COVERAGE = 0.001
MAXIMUM_MISMATCHES = 6
TOLERANCE = 1e-5
OPTIONAL_WARNING = re.compile(r"glTF optional extension '(.*)' is not supported")
# The text of a rejection, and the causes that make it a fitting one.
DIAGNOSTIC_CAUSES = {
    "morph weight animation is not supported": ("morph targets",),
    "which has a matrix": ("animated matrix",),
    "morph weights are not supported": ("morph targets",),
    "morph targets are not supported": ("morph targets",),
    "primitive mode": ("points or lines",),
    "sparse accessors are not supported": ("sparse accessor",),
    "without a bufferView are not supported": ("accessor without a bufferView",),
}
REQUIRED_DIAGNOSTIC = re.compile(r"unsupported required extensions: (.*)")

COMPONENTS = {5120: numpy.int8, 5121: numpy.uint8, 5122: numpy.int16, 5123: numpy.uint16, 5125: numpy.uint32,
              5126: numpy.float32}
NORMALIZED_SCALE = {5120: 127.0, 5121: 255.0, 5122: 32767.0, 5123: 65535.0}
TYPES = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
PREVIEW_WRAP = {10497: "repeat", 33648: "mirror", 33071: "clamp"}
MTLX_WRAP = {10497: "periodic", 33648: "mirror", 33071: "clamp"}
IMAGE_SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")
# A glTF texture slot, and the prefix of the nodes that read it in each network.
SLOTS = {"baseColorTexture": "baseColor", "metallicRoughnessTexture": "metallicRoughness",
         "normalTexture": "normal", "occlusionTexture": "occlusion", "emissiveTexture": "emissive",
         "clearcoatTexture": "clearcoat", "clearcoatRoughnessTexture": "clearcoatRoughness",
         "transmissionTexture": "transmission", "thicknessTexture": "thickness", "specularTexture": "specular",
         "specularColorTexture": "specularColor", "sheenColorTexture": "sheenColor",
         "sheenRoughnessTexture": "sheenRoughness", "iridescenceTexture": "iridescence",
         "iridescenceThicknessTexture": "iridescenceThickness", "anisotropyTexture": "anisotropy"}
CLEARCOAT_SLOTS = {"clearcoatTexture", "clearcoatRoughnessTexture"}
TRANSMISSION_SLOTS = {"transmissionTexture"}
VOLUME_SLOTS = {"thicknessTexture"}
SPECULAR_SLOTS = {"specularTexture", "specularColorTexture"}
SHEEN_SLOTS = {"sheenColorTexture", "sheenRoughnessTexture"}
IRIDESCENCE_SLOTS = {"iridescenceTexture", "iridescenceThicknessTexture"}
# Slots that UsdPreviewSurface has no input for.
MTLX_ONLY_SLOTS = {"transmissionTexture", "thicknessTexture", "specularTexture", "specularColorTexture",
                   "sheenColorTexture", "sheenRoughnessTexture", "anisotropyTexture", *IRIDESCENCE_SLOTS}
COLOR_SLOTS = {"baseColorTexture", "emissiveTexture", "specularColorTexture", "sheenColorTexture"}
PBR_SLOTS = {"baseColorTexture", "metallicRoughnessTexture"}


class Asset:
    """A glTF file read without the plugin or fastgltf, so that it can check their results."""

    def __init__(self, path):
        self.path = path
        self.binary = None
        data = path.read_bytes()
        if data[:4] == b"glTF":
            self.json = self.read_glb(data)
        else:
            self.json = json.loads(data.decode("utf-8"))
        self.buffers = {}

    def get(self, key):
        return self.json.get(key, [])

    def matrices(self, index):
        return self.accessor(index).astype(numpy.float64).reshape(-1, 4, 4).transpose(0, 2, 1)

    def read_glb(self, data):
        offset = 12
        document = None
        while offset < len(data):
            length = int.from_bytes(data[offset:offset + 4], "little")
            kind = data[offset + 4:offset + 8]
            chunk = data[offset + 8:offset + 8 + length]
            if kind == b"JSON":
                document = json.loads(chunk.decode("utf-8"))
            elif kind == b"BIN\0":
                self.binary = chunk
            offset += 8 + length
        return document

    def read_uri(self, uri):
        if uri.startswith("data:"):
            return base64.b64decode(uri.partition(",")[2])
        return (self.path.parent / urllib.parse.unquote(uri)).read_bytes()

    def buffer(self, index):
        if index not in self.buffers:
            uri = self.json["buffers"][index].get("uri")
            self.buffers[index] = self.read_uri(uri) if uri else self.binary
        return self.buffers[index]

    def buffer_view(self, index):
        view = self.json["bufferViews"][index]
        offset = view.get("byteOffset", 0)
        return self.buffer(view["buffer"])[offset:offset + view["byteLength"]]

    def accessor(self, index, normalize=True):
        """Returns an accessor's values as (count, components), as float for normalized integers."""
        accessor = self.json["accessors"][index]
        component = accessor["componentType"]
        dtype = numpy.dtype(COMPONENTS[component]).newbyteorder("<")
        width = TYPES[accessor["type"]]
        count = accessor["count"]
        if "bufferView" not in accessor:
            return numpy.zeros((count, width), dtype)
        view = self.json["bufferViews"][accessor["bufferView"]]
        stride = view.get("byteStride") or dtype.itemsize * width
        offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        values = numpy.ndarray((count, width), dtype, self.buffer(view["buffer"]), offset, (stride, dtype.itemsize))
        if normalize and component in NORMALIZED_SCALE and accessor.get("normalized"):
            return numpy.maximum(values.astype(numpy.float64) / NORMALIZED_SCALE[component], -1.0)
        return values

    def image_bytes(self, index):
        """Returns the bytes of an image, or None when they are not stored as PNG or JPEG."""
        image = self.json["images"][index]
        data = self.buffer_view(image["bufferView"]) if "bufferView" in image else self.read_uri(image["uri"])
        return data if data.startswith(IMAGE_SIGNATURES) else None

    def texture_source(self, texture):
        """Returns the image index a texture should show, with KTX2 then WebP preferred as the plugin
        documents."""
        extensions = texture.get("extensions", {})
        chosen = extensions.get("KHR_texture_basisu") or extensions.get("EXT_texture_webp")
        return chosen["source"] if chosen else texture.get("source")


class Check:
    def __init__(self):
        self.mismatches = []

    def expect(self, passed, message):
        if not passed:
            self.mismatches.append(message)
        return passed

    def close(self, actual, expected, message, magnitude=None):
        """Compares within TOLERANCE of the magnitude, which defaults to the expected values. A product's
        rounding scales with its terms, which can be far larger than a result they cancel to."""
        actual = numpy.asarray(actual, numpy.float64)
        expected = numpy.asarray(expected, numpy.float64)
        if actual.size == 0 and expected.size == 0:
            return True
        if actual.shape != expected.shape:
            return self.expect(False, f"{message}: shape {actual.shape}, expected {expected.shape}")
        limit = TOLERANCE * numpy.maximum(1.0, numpy.abs(expected) if magnitude is None else magnitude)
        return self.expect(bool(numpy.all(numpy.abs(actual - expected) <= limit)), f"{message}: values differ")


def node_matrix(node):
    """Returns a node's local matrix in USD's row-vector convention."""
    if "matrix" in node:
        # glTF stores columns, which read as rows are this convention already.
        return numpy.array(node["matrix"], numpy.float64).reshape(4, 4)
    x, y, z, w = node.get("rotation", (0, 0, 0, 1))
    norm = (x * x + y * y + z * z + w * w) ** 0.5 or 1.0
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    rotation = numpy.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w)],
        [2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w)],
        [2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y)]])
    matrix = numpy.identity(4)
    matrix[:3, :3] = rotation * numpy.array(node.get("scale", (1, 1, 1)), numpy.float64).reshape(3, 1)
    matrix[3, :3] = node.get("translation", (0, 0, 0))
    return matrix


def gltf_projection(camera):
    """Returns a camera's projection from the glTF specification, in USD's row-vector convention.

    An undefined aspect ratio is 1, and an infinite projection uses the far plane the plugin authors.
    """
    if camera["type"] == "perspective":
        values = camera["perspective"]
        aspect = values.get("aspectRatio", 1.0)
        near, far = values["znear"], values.get("zfar", 1000000.0)
        tangent = math.tan(values["yfov"] / 2)
        columns = numpy.array([
            [1 / (aspect * tangent), 0, 0, 0],
            [0, 1 / tangent, 0, 0],
            [0, 0, (far + near) / (near - far), 2 * far * near / (near - far)],
            [0, 0, -1, 0]])
    else:
        values = camera["orthographic"]
        near, far = values["znear"], values["zfar"]
        columns = numpy.array([
            [1 / values["xmag"], 0, 0, 0],
            [0, 1 / values["ymag"], 0, 0],
            [0, 0, 2 / (near - far), (far + near) / (near - far)],
            [0, 0, 0, 1]])
    return columns.T


def camera_view(world):
    """Returns the rotation and translation of a node's world matrix, which place a glTF camera."""
    result = numpy.array(world, numpy.float64)
    result[:3, :3] /= numpy.linalg.norm(result[:3, :3], axis=1, keepdims=True)
    return result


def check_light(check, prim, light, world, label):
    """Compares a light with glTF's definition: at the node's origin, along its -Z axis, with intensity
    in candela or lux. UsdLux defines a sphere's intensity as luminance, which times its projected area
    is candela, and a normalized distant light of angle 0 as illuminance."""
    from pxr import Usd, UsdGeom, UsdLux

    kind = light["type"]
    schema = UsdLux.DistantLight if kind == "directional" else UsdLux.SphereLight
    if not check.expect(prim and prim.IsA(schema), f"{label} is not a {schema.__name__}"):
        return
    actual = numpy.array(UsdGeom.XformCache(Usd.TimeCode.Default()).GetLocalToWorldTransform(prim))
    rotation = actual[:3, :3]
    check.close(rotation @ rotation.T, numpy.identity(3), f"{label} is scaled or sheared")
    check.close(numpy.linalg.det(rotation), 1.0, f"{label} is reflected")
    check.close(actual[3, :3], world[3, :3], f"{label} position")
    direction = -world[2, :3] / numpy.linalg.norm(world[2, :3])
    check.close(-actual[2, :3], direction, f"{label} direction")
    api = UsdLux.LightAPI(prim)
    check.close(api.GetColorAttr().Get(), light.get("color", (1, 1, 1)), f"{label} color")
    intensity = api.GetIntensityAttr().Get()
    expected = light.get("intensity", 1.0)
    if kind == "directional":
        check.expect(UsdLux.DistantLight(prim).GetAngleAttr().Get() == 0, f"{label} angle")
        check.expect(api.GetNormalizeAttr().Get(), f"{label} is not normalized")
        check.close(intensity, expected, f"{label} illuminance")
        return
    check.expect(not api.GetNormalizeAttr().Get(), f"{label} is normalized")
    radius = UsdLux.SphereLight(prim).GetRadiusAttr().Get()
    check.close(intensity * math.pi * radius * radius, expected, f"{label} candela")
    if not check.expect(prim.HasAPI(UsdLux.ShapingAPI) == (kind == "spot"), f"{label} shaping"):
        return
    if kind == "spot":
        shaping = UsdLux.ShapingAPI(prim)
        angle = shaping.GetShapingConeAngleAttr().Get()
        softness = shaping.GetShapingConeSoftnessAttr().Get()
        spot = light.get("spot", {})
        check.close(angle, math.degrees(spot.get("outerConeAngle", math.pi / 4)), f"{label} outer cone")
        # UsdLux starts to soften the cone at (1 - softness) times its angle.
        check.close((1 - softness) * angle, math.degrees(spot.get("innerConeAngle", 0)), f"{label} inner cone")


def expected_triangles(asset, primitive, vertices):
    """Returns the triangles a primitive means, in a rotation that does not depend on the author."""
    indices = asset.accessor(primitive["indices"]).reshape(-1).astype(numpy.int64) if "indices" in primitive \
        else numpy.arange(vertices, dtype=numpy.int64)
    mode = primitive.get("mode", 4)
    if mode == 4:
        return indices[:len(indices) // 3 * 3].reshape(-1, 3)
    count = max(len(indices) - 2, 0)
    steps = numpy.arange(count)
    if mode == 5:
        odd = steps % 2
        return numpy.stack([indices[steps], indices[steps + 1 + odd], indices[steps + 2 - odd]], axis=1)
    return numpy.stack([indices[steps + 1], indices[steps + 2], numpy.full(count, indices[0])], axis=1)


def canonical_triangles(triangles):
    """Rotates each triangle to start at its smallest index, which keeps its winding."""
    triangles = numpy.asarray(triangles, numpy.int64)
    if not len(triangles):
        return triangles.reshape(0, 3)
    first = numpy.argmin(triangles, axis=1)
    rows = numpy.arange(len(triangles))
    return numpy.stack([triangles[rows, first], triangles[rows, (first + 1) % 3], triangles[rows, (first + 2) % 3]],
                       axis=1)


def check_primitive(check, asset, prim, primitive, label):
    from pxr import UsdGeom, UsdShade

    mesh = UsdGeom.Mesh(prim)
    attributes = primitive["attributes"]
    positions = asset.accessor(attributes["POSITION"])
    check.close(mesh.GetPointsAttr().Get(), positions, f"{label} points")

    counts = numpy.asarray(mesh.GetFaceVertexCountsAttr().Get())
    check.expect(bool(numpy.all(counts == 3)), f"{label} has a face that is not a triangle")
    authored = numpy.asarray(mesh.GetFaceVertexIndicesAttr().Get(), numpy.int64).reshape(-1, 3)
    triangles = expected_triangles(asset, primitive, len(positions))
    check.expect(numpy.array_equal(canonical_triangles(authored), canonical_triangles(triangles)),
                 f"{label} triangles differ")

    normals = mesh.GetNormalsAttr().Get()
    if "NORMAL" in attributes:
        check.close(normals, asset.accessor(attributes["NORMAL"]), f"{label} normals")
    else:
        check_generated_normals(check, mesh, normals, positions, authored, label)

    primvars = UsdGeom.PrimvarsAPI(prim)
    sets = sorted(int(name[9:]) for name in attributes if name.startswith("TEXCOORD_"))
    for number in sets:
        values = numpy.array(asset.accessor(attributes[f"TEXCOORD_{number}"]), numpy.float64)
        values[:, 1] = 1.0 - values[:, 1]
        name = "st" if number == 0 else f"st{number}"
        check.close(primvars.GetPrimvar(name).Get(), values, f"{label} {name}")
    if "TANGENT" in attributes:
        check.close(primvars.GetPrimvar("gltf_tangent").Get(), asset.accessor(attributes["TANGENT"]),
                    f"{label} tangents")
    if "COLOR_0" in attributes:
        colors = asset.accessor(attributes["COLOR_0"])
        check.close(primvars.GetPrimvar("displayColor").Get(), colors[:, :3], f"{label} displayColor")
        if colors.shape[1] == 4:
            check.close(primvars.GetPrimvar("gltf_colorAlpha").Get(), colors[:, 3], f"{label} gltf_colorAlpha")

    index = primitive.get("material")
    material = asset.json["materials"][index] if index is not None else {}
    suffix = "VertexColor" if "COLOR_0" in attributes else ""
    name = f"Material_{index}{'_' + suffix if suffix else ''}" if index is not None else f"Default{suffix}"
    bound = UsdShade.MaterialBindingAPI(prim).GetDirectBinding().GetMaterialPath()
    expected = f"/Asset/mtl/{name}"
    check.expect(str(bound) == expected, f"{label} is bound to {bound}, expected {expected}")
    check.expect(bool(mesh.GetDoubleSidedAttr().Get()) == bool(material.get("doubleSided")),
                 f"{label} doubleSided differs")
    return expected, material, "COLOR_0" in attributes


def check_generated_normals(check, mesh, normals, positions, triangles, label):
    """A primitive without NORMAL is flat shaded: each face's normal follows from its corners."""
    # As floats first, because subtracting quantized positions would wrap around.
    a, b, c = (numpy.asarray(positions[triangles[:, corner]], numpy.float64) for corner in range(3))
    faces = numpy.cross(b - a, c - a)
    lengths = numpy.linalg.norm(faces, axis=1, keepdims=True)
    # A degenerate triangle has no normal to compare.
    valid = lengths[:, 0] > 1e-12
    faces = faces[valid] / lengths[valid]
    authored = numpy.asarray(normals, numpy.float64)
    if mesh.GetNormalsInterpolation() == "faceVarying" and authored.size:
        check.expect(len(authored) == len(triangles) * 3, f"{label} generated normals have the wrong count")
        authored = authored[::3]
    elif mesh.GetNormalsInterpolation() != "uniform":
        check.expect(False, f"{label} generated normals are {mesh.GetNormalsInterpolation()}")
        return
    if check.expect(len(authored) == len(triangles), f"{label} generated normals have the wrong count"):
        check.expect(bool(numpy.all(numpy.sum(authored[valid] * faces, axis=1) > 0.99)),
                     f"{label} generated normals are not the face normals")


def connected_shader(shader_input):
    from pxr import UsdShade

    sources = shader_input.GetConnectedSources()[0] if shader_input else []
    return UsdShade.Shader(sources[0].source.GetPrim()) if sources else None


def resolved_image(check, shader, label, images):
    """Reads an authored image through the resolver, as a renderer would."""
    from pxr import Ar

    asset = shader.GetInput("file").Get() if shader.GetInput("file") else None
    if not check.expect(asset and asset.resolvedPath, f"{label} does not resolve"):
        return None
    resolved = asset.resolvedPath
    if resolved not in images:
        data = Ar.GetResolver().OpenAsset(Ar.ResolvedPath(resolved))
        images[resolved] = bytes(data.GetBuffer()) if data else None
    return images[resolved]


def transform_of(info):
    return info.get("extensions", {}).get("KHR_texture_transform", {})


def specified_uv(transform, u, v):
    """The matrix of the KHR_texture_transform specification, in glTF's own coordinates."""
    offset, scale = transform.get("offset", (0, 0)), transform.get("scale", (1, 1))
    cosine, sine = math.cos(transform.get("rotation", 0)), math.sin(transform.get("rotation", 0))
    u, v = u * scale[0], v * scale[1]
    return cosine * u + sine * v + offset[0], -sine * u + cosine * v + offset[1]


def preview_st(node, st):
    """What UsdTransform2d computes, as its specification defines it."""
    rotation = math.radians(node.GetInput("rotation").Get())
    scale, translation = node.GetInput("scale").Get(), node.GetInput("translation").Get()
    s, t = st[0] * scale[0], st[1] * scale[1]
    return (math.cos(rotation) * s - math.sin(rotation) * t + translation[0],
            math.sin(rotation) * s + math.cos(rotation) * t + translation[1])


def mtlx_st(nodes, st):
    """What the MaterialX nodes compute. rotate2d's formula is that of its GLSL implementation."""
    scale, rotate, translate = nodes
    factor, amount = scale.GetInput("in2").Get(), math.radians(rotate.GetInput("amount").Get())
    offset = translate.GetInput("in2").Get()
    s, t = st[0] * factor[0], st[1] * factor[1]
    return (math.cos(amount) * s + math.sin(amount) * t + offset[0],
            -math.sin(amount) * s + math.cos(amount) * t + offset[1])


def check_transform(check, transform, compute, label):
    """Compares authored nodes with the specification, at points that are not on any axis of symmetry."""
    for u, v in ((0.0, 0.0), (1.0, 0.0), (0.25, 0.75)):
        expected = specified_uv(transform, u, v)
        check.close(compute((u, 1 - v)), (expected[0], 1 - expected[1]), f"{label} at ({u}, {v})")


def is_identity(transform):
    return (not transform.get("rotation", 0) and list(transform.get("offset", (0, 0))) == [0, 0]
            and list(transform.get("scale", (1, 1))) == [1, 1])


def check_texture(check, asset, stage, material_path, key, info, label, images):
    from pxr import UsdShade

    slot = SLOTS[key]
    preview = UsdShade.Shader(stage.GetPrimAtPath(f"{material_path}/preview/{slot}Texture"))
    mtlx = UsdShade.Shader(stage.GetPrimAtPath(f"{material_path}/mtlx/{slot}Image"))
    mtlx_only = key in MTLX_ONLY_SLOTS
    if mtlx_only:
        check.expect(not preview, f"{label} {slot} texture is authored in Preview, which has no input for it")
        if not check.expect(mtlx, f"{label} {slot} texture is not authored in MaterialX"):
            return None
    elif not check.expect(preview and mtlx, f"{label} {slot} texture is not authored in both networks"):
        return None
    texture = asset.json["textures"][info["index"]]
    mtlx_data = resolved_image(check, mtlx, f"{label} {slot} MaterialX", images)
    if mtlx_only:
        data = mtlx_data
    else:
        data = resolved_image(check, preview, f"{label} {slot}", images)
        check.expect(data == mtlx_data, f"{label} {slot} networks read different images")
    if data is not None:
        check.expect(data.startswith(IMAGE_SIGNATURES), f"{label} {slot} is not served as PNG or JPEG")
        source = asset.texture_source(texture)
        original = asset.image_bytes(source) if source is not None else None
        if original is not None:
            check.expect(data == original, f"{label} {slot} shows different bytes from image {source}")

    transform = transform_of(info)
    number = transform.get("texCoord", info.get("texCoord", 0))
    primvar = "st" if number == 0 else f"st{number}"
    reader = None if mtlx_only else connected_shader(preview.GetInput("st"))
    mtlx_reader = connected_shader(mtlx.GetInput("texcoord"))
    if not is_identity(transform):
        if not mtlx_only and check.expect(reader and reader.GetShaderId() == "UsdTransform2d",
                                          f"{label} {slot} Preview has no transform"):
            check_transform(check, transform, lambda st, node=reader: preview_st(node, st),
                            f"{label} {slot} Preview transform")
            reader = connected_shader(reader.GetInput("in"))
        translate = mtlx_reader
        rotate = connected_shader(translate.GetInput("in1")) if translate and translate.GetInput("in1") else None
        scale = connected_shader(rotate.GetInput("in")) if rotate and rotate.GetInput("in") else None
        if check.expect(scale and scale.GetInput("in2"), f"{label} {slot} MaterialX has no transform"):
            check_transform(check, transform, lambda st: mtlx_st((scale, rotate, translate), st),
                            f"{label} {slot} MaterialX transform")
            mtlx_reader = connected_shader(scale.GetInput("in1"))
    check.expect(mtlx_only or (reader and reader.GetInput("varname") and reader.GetInput("varname").Get() == primvar),
                 f"{label} {slot} Preview UV set differs")
    check.expect(mtlx_reader and mtlx_reader.GetInput("geomprop") and mtlx_reader.GetInput("geomprop").Get() == primvar,
                 f"{label} {slot} MaterialX UV set differs")

    sampler = asset.json["samplers"][texture["sampler"]] if "sampler" in texture else {}
    wrap_s, wrap_t = sampler.get("wrapS", 10497), sampler.get("wrapT", 10497)
    check.expect(mtlx.GetInput("uaddressmode").Get() == MTLX_WRAP[wrap_s], f"{label} {slot} MaterialX wrapS differs")
    check.expect(mtlx.GetInput("vaddressmode").Get() == MTLX_WRAP[wrap_t], f"{label} {slot} MaterialX wrapT differs")
    if mtlx_only:
        if key in COLOR_SLOTS:
            check.expect(mtlx.GetIdAttr().Get() == "ND_image_color4"
                         and mtlx.GetInput("file").GetAttr().GetColorSpace() == "srgb_texture",
                         f"{label} {slot} MaterialX image is not sRGB")
        else:
            check.expect(mtlx.GetIdAttr().Get() == "ND_image_vector4", f"{label} {slot} MaterialX image is not data")
        return mtlx
    check.expect(preview.GetInput("wrapS").Get() == PREVIEW_WRAP[wrap_s], f"{label} {slot} Preview wrapS differs")
    check.expect(preview.GetInput("wrapT").Get() == PREVIEW_WRAP[wrap_t], f"{label} {slot} Preview wrapT differs")

    space = "sRGB" if key in COLOR_SLOTS else "raw"
    check.expect(preview.GetInput("sourceColorSpace").Get() == space, f"{label} {slot} color space is not {space}")
    return preview


def check_material(check, asset, stage, path, material, has_colors, images):
    """Compares a bound material's Preview factors and both networks' textures with the glTF."""
    from pxr import Usd, UsdShade

    label = path.rsplit("/", 1)[1]
    surface = UsdShade.Shader(stage.GetPrimAtPath(f"{path}/preview/surface"))
    pbr_surface = UsdShade.Shader(stage.GetPrimAtPath(f"{path}/mtlx/surface"))
    if not check.expect(surface and pbr_surface, f"{label} does not have both surfaces"):
        return
    usd_material = UsdShade.Material(stage.GetPrimAtPath(path))
    for context, shader in (("mtlx", "gltf_pbr"), ("", "UsdPreviewSurface")):
        source = usd_material.ComputeSurfaceSource(context)[0]
        check.expect(source and shader in (source.GetShaderId() or ""), f"{label} does not resolve to {shader}")

    pbr = material.get("pbrMetallicRoughness", {})
    textures = {}
    clearcoat = material.get("extensions", {}).get("KHR_materials_clearcoat")
    transmission = material.get("extensions", {}).get("KHR_materials_transmission")
    volume = material.get("extensions", {}).get("KHR_materials_volume")
    specular = material.get("extensions", {}).get("KHR_materials_specular")
    sheen = material.get("extensions", {}).get("KHR_materials_sheen")
    iridescence = material.get("extensions", {}).get("KHR_materials_iridescence")
    anisotropy = material.get("extensions", {}).get("KHR_materials_anisotropy")

    def owner(key):
        if key in PBR_SLOTS:
            return pbr
        if key in CLEARCOAT_SLOTS:
            return clearcoat or {}
        if key in TRANSMISSION_SLOTS:
            return transmission or {}
        if key in VOLUME_SLOTS:
            return volume or {}
        if key in SPECULAR_SLOTS:
            return specular or {}
        if key in SHEEN_SLOTS:
            return sheen or {}
        if key in IRIDESCENCE_SLOTS:
            return iridescence or {}
        if key == "anisotropyTexture":
            return anisotropy or {}
        return material

    infos = {key: owner(key).get(key) for key in SLOTS}
    # An unlit material uses only its base color; the rest are fallbacks, which are not authored.
    unlit = "KHR_materials_unlit" in material.get("extensions", {})
    if unlit:
        for key in SLOTS.keys() - {"baseColorTexture"}:
            if infos[key]:
                slot = SLOTS[key]
                check.expect(not stage.GetPrimAtPath(f"{path}/preview/{slot}Texture")
                             and not stage.GetPrimAtPath(f"{path}/mtlx/{slot}Image"),
                             f"{label} is unlit but authors its fallback {slot} texture")
                infos[key] = None
    # A clearcoat of factor 0 is disabled, and an unlit material has none.
    coated = clearcoat is not None and not unlit and clearcoat.get("clearcoatFactor", 0) > 0
    if not coated:
        for key in CLEARCOAT_SLOTS:
            infos[key] = None
    # A transmission of factor 0 is none, and an unlit material has none.
    transmits = transmission is not None and not unlit and transmission.get("transmissionFactor", 0) > 0
    if not transmits:
        for key in TRANSMISSION_SLOTS:
            infos[key] = None
    # A volume of thickness 0 is thin, and a material that transmits nothing has none.
    thick = volume is not None and transmits and volume.get("thicknessFactor", 0) > 0
    if not thick:
        for key in VOLUME_SLOTS:
            infos[key] = None
    # A specular weight of 0 fixes the weight, and a black color factor the color, so their textures are
    # not read, and an unlit material has neither.
    reflects = specular is not None and not unlit and specular.get("specularFactor", 1) > 0
    if not reflects:
        infos["specularTexture"] = None
    if not reflects or not any(specular.get("specularColorFactor", (1, 1, 1))):
        infos["specularColorTexture"] = None
    # A black sheen color disables the layer, and a roughness factor of 0 fixes the roughness, so their
    # textures are not read, and an unlit material has none.
    sheened = sheen is not None and not unlit and any(sheen.get("sheenColorFactor", (0, 0, 0)))
    if not sheened:
        infos["sheenColorTexture"] = None
    if not sheened or not sheen.get("sheenRoughnessFactor", 0):
        infos["sheenRoughnessTexture"] = None
    film = iridescence is not None and not unlit and iridescence.get("iridescenceFactor", 0) > 0
    if not film:
        for key in IRIDESCENCE_SLOTS:
            infos[key] = None
    elif iridescence.get("iridescenceThicknessMinimum", 100) == iridescence.get("iridescenceThicknessMaximum", 400):
        infos["iridescenceThicknessTexture"] = None
    anisotropic = anisotropy is not None and not unlit and anisotropy.get("anisotropyStrength", 0) > 0
    if not anisotropic:
        infos["anisotropyTexture"] = None
    names = {prim.GetName() for prim in Usd.PrimRange(stage.GetPrimAtPath(path))}
    if not coated:
        check.expect(not {name for name in names if name.startswith("clearcoat")},
                     f"{label} authors a clearcoat it does not have")
    if not transmits:
        check.expect(not {name for name in names if name.startswith("transmission")},
                     f"{label} authors a transmission it does not have")
    if not thick:
        check.expect(not {name for name in names if name.startswith("thickness")},
                     f"{label} authors a thickness it does not have")
    if not any(infos[key] for key in SPECULAR_SLOTS):
        check.expect(not {name for name in names if name.startswith("specular")},
                     f"{label} authors a specular texture it does not read")
    if not any(infos[key] for key in SHEEN_SLOTS):
        check.expect(not {name for name in names if name.startswith("sheen")},
                     f"{label} authors a sheen texture it does not read")
    if not any(infos[key] for key in IRIDESCENCE_SLOTS):
        check.expect(not {name for name in names if name.startswith("iridescence")},
                     f"{label} authors an iridescence texture it does not read")
    if not infos["anisotropyTexture"]:
        check.expect(not {name for name in names if name.startswith("anisotropy")},
                     f"{label} authors an anisotropy texture it does not read")
    for key, info in infos.items():
        if info:
            textures[key] = check_texture(check, asset, stage, path, key, info, label, images)

    base = pbr.get("baseColorFactor", (1, 1, 1, 1))
    metallic, roughness = pbr.get("metallicFactor", 1), pbr.get("roughnessFactor", 1)
    emissive = material.get("emissiveFactor", (0, 0, 0))

    def factor(name, expected, description):
        value = surface.GetInput(name).Get() if surface.GetInput(name) else None
        check.expect(value is not None, f"{label} Preview {description} is not authored")
        if value is not None:
            check.close(value, expected, f"{label} Preview {description}")

    def scale(key, channels, expected, description):
        shader = textures.get(key)
        if shader:
            values = shader.GetInput("scale").Get()
            check.close([values[channel] for channel in channels], expected, f"{label} Preview {description}")

    if unlit:
        # The base color is emitted, by a black metal in Preview and a dielectric without specular in MaterialX.
        factor("diffuseColor", (0, 0, 0), "unlit diffuse color")
        factor("metallic", 1, "unlit metallic")
        factor("roughness", 1, "unlit roughness")
        if infos["baseColorTexture"]:
            scale("baseColorTexture", (0, 1, 2), base[:3], "base color scale")
            emitted = connected_shader(surface.GetInput("emissiveColor"))
            image = textures.get("baseColorTexture")
            check.expect(emitted and image and emitted.GetPath() == image.GetPath(),
                         f"{label} Preview does not emit its base color texture")
        elif not has_colors:
            factor("emissiveColor", base[:3], "unlit base color")
        for name, expected in (("base_color", (0, 0, 0)), ("metallic", 0), ("specular", 0)):
            value = pbr_surface.GetInput(name).Get() if pbr_surface.GetInput(name) else None
            if check.expect(value is not None, f"{label} MaterialX unlit {name} is not authored"):
                check.close(value, expected, f"{label} MaterialX unlit {name}")
    else:
        if infos["baseColorTexture"]:
            scale("baseColorTexture", (0, 1, 2), base[:3], "base color scale")
        elif not has_colors:
            factor("diffuseColor", base[:3], "base color")
        if infos["metallicRoughnessTexture"]:
            scale("metallicRoughnessTexture", (2, 1), (metallic, roughness), "metallic and roughness scale")
        else:
            factor("metallic", metallic, "metallic")
            factor("roughness", roughness, "roughness")
        if infos["emissiveTexture"]:
            scale("emissiveTexture", (0, 1, 2), emissive, "emissive scale")
        elif any(emissive):
            factor("emissiveColor", emissive, "emissive")
        if infos["normalTexture"]:
            amount = infos["normalTexture"].get("scale", 1)
            scale("normalTexture", (0, 1, 2), (2 * amount, 2 * amount, 2), "normal scale")
        if infos["occlusionTexture"]:
            strength = infos["occlusionTexture"].get("strength", 1)
            check_occlusion(check, textures.get("occlusionTexture"), strength, label)

    check_clearcoat(check, surface, pbr_surface, clearcoat if coated else None, textures, names, label)
    check_transmission(check, surface, pbr_surface, transmission if transmits else None, textures, label)
    check_volume(check, surface, pbr_surface, volume if thick else None, textures, label)
    check_specular(check, surface, pbr_surface, specular, unlit, textures, label)
    check_sheen(check, surface, pbr_surface, sheen if sheened else None, textures, label)
    check_iridescence(check, surface, pbr_surface, iridescence if film else None, textures, label)
    check_anisotropy(check, surface, pbr_surface, anisotropy if anisotropic else None, textures, label)

    # Both shaders default to glTF's 1.5, an ior of 0 stands for a very large one, and an unlit material has none.
    ior = material.get("extensions", {}).get("KHR_materials_ior", {}).get("ior", 1.5)
    expected_ior = None if unlit or ior == 1.5 else (1e6 if ior == 0 else ior)
    for description, shader in (("Preview", surface), ("MaterialX", pbr_surface)):
        value = shader.GetInput("ior").Get() if shader.GetInput("ior") else None
        if expected_ior is None:
            check.expect(value is None, f"{label} {description} authors an ior of {value}, not the default")
        elif check.expect(value is not None, f"{label} {description} ior is not authored"):
            check.close(value, expected_ior, f"{label} {description} ior")

    mode = material.get("alphaMode", "OPAQUE")
    modes = {"OPAQUE": 0, "MASK": 1, "BLEND": 2}
    authored_mode = pbr_surface.GetInput("alpha_mode").Get() if pbr_surface.GetInput("alpha_mode") else 0
    check.expect(authored_mode == modes[mode], f"{label} MaterialX alpha_mode is {authored_mode}, not {modes[mode]}")
    threshold = surface.GetInput("opacityThreshold")
    if mode == "MASK":
        cutoff = material.get("alphaCutoff", 0.5)
        if check.expect(pbr_surface.GetInput("alpha_cutoff"), f"{label} MaterialX alpha_cutoff is not authored"):
            check.close(pbr_surface.GetInput("alpha_cutoff").Get(), cutoff, f"{label} MaterialX alpha_cutoff")
        if cutoff > 0 and check.expect(threshold, f"{label} Preview opacityThreshold is not authored"):
            check.close(threshold.Get(), cutoff, f"{label} Preview opacityThreshold")
    else:
        check.expect(not threshold or not threshold.Get(), f"{label} is {mode} but has a Preview opacityThreshold")
    opacity = surface.GetInput("opacity")
    if mode == "OPAQUE":
        check.expect(not opacity or opacity.Get() in (None, 1.0), f"{label} is OPAQUE but its Preview opacity is not 1")


def check_clearcoat(check, surface, pbr_surface, clearcoat, textures, names, label):
    """The clearcoat and its roughness in both paths, and its normal in MaterialX only.

    `clearcoat` is None for a material without an enabled clearcoat."""
    preview_inputs = {"clearcoatTexture": ("clearcoat", "clearcoatFactor", 0),
                      "clearcoatRoughnessTexture": ("clearcoatRoughness", "clearcoatRoughnessFactor", 1)}
    for key, (name, field, channel) in preview_inputs.items():
        value = surface.GetInput(name).Get() if surface.GetInput(name) else None
        if clearcoat is None:
            check.expect(value is None, f"{label} Preview authors {name} without a clearcoat")
        elif check.expect(value is not None, f"{label} Preview {name} is not authored"):
            # Preview's clearcoat roughness defaults to 0.01, so even glTF's default of 0 is authored.
            factor = clearcoat.get(field, 0)
            check.close(value, factor, f"{label} Preview {name}")
            if textures.get(key):
                check.close(textures[key].GetInput("scale").Get()[channel], factor, f"{label} Preview {name} scale")
    for name in ("clearcoat", "clearcoat_roughness"):
        check.expect(bool(pbr_surface.GetInput(name)) == (clearcoat is not None),
                     f"{label} MaterialX {name} differs from the clearcoat")
    has_normal = clearcoat is not None and "clearcoatNormalTexture" in clearcoat
    check.expect(bool(pbr_surface.GetInput("clearcoat_normal")) == has_normal and ("clearcoatNormalMap" in names)
                 == has_normal, f"{label} MaterialX clearcoat normal differs")


def check_transmission(check, surface, pbr_surface, transmission, textures, label):
    """The transmission in MaterialX only, its factor times its texture's R; Preview has none.

    `transmission` is None for a material without a transmission above 0."""
    check.expect(not surface.GetInput("transmission"), f"{label} Preview authors a transmission")
    authored = pbr_surface.GetInput("transmission")
    if transmission is None:
        check.expect(not authored, f"{label} MaterialX authors a transmission it does not have")
        return
    if not check.expect(authored, f"{label} MaterialX transmission is not authored"):
        return
    check_mtlx_channel(check, authored, transmission.get("transmissionFactor", 0),
                       textures.get("transmissionTexture"), 0, f"{label} MaterialX transmission")


def check_volume(check, surface, pbr_surface, volume, textures, label):
    """The volume in MaterialX only: its thickness times its texture's G, and its attenuation where
    the distance is finite. Preview has none.

    `volume` is None for a material without a transmission and a thickness above 0."""
    names = ("thickness", "attenuation_distance", "attenuation_color")
    check.expect(not any(surface.GetInput(name) for name in names), f"{label} Preview authors a volume")
    authored = pbr_surface.GetInput("thickness")
    distance = volume.get("attenuationDistance") if volume is not None else None
    if volume is None:
        check.expect(not any(pbr_surface.GetInput(name) for name in names),
                     f"{label} MaterialX authors a volume it does not have")
        return
    if check.expect(authored, f"{label} MaterialX thickness is not authored"):
        check_mtlx_channel(check, authored, volume.get("thicknessFactor", 0), textures.get("thicknessTexture"), 1,
                           f"{label} MaterialX thickness")
    # An infinite distance, glTF's default, attenuates nothing.
    for name, expected in (("attenuation_distance", distance),
                           ("attenuation_color", volume.get("attenuationColor", (1, 1, 1)) if distance else None)):
        attribute = pbr_surface.GetInput(name)
        if expected is None:
            check.expect(not attribute, f"{label} MaterialX authors {name} without an attenuation distance")
        elif check.expect(attribute, f"{label} MaterialX {name} is not authored"):
            check.close(attribute.Get(), expected, f"{label} MaterialX {name}")


def check_specular(check, surface, pbr_surface, specular, unlit, textures, label):
    """The specular weight, times its texture's A, and color, times its texture's RGB, in MaterialX only,
    each where it differs from the default of 1. Preview keeps the metallic workflow, which has neither."""
    check.expect(not surface.GetInput("specularColor") and not surface.GetInput("useSpecularWorkflow"),
                 f"{label} Preview authors a specular color")
    if unlit:
        # The unlit check compares the specular of 0 that removes the reflection.
        check.expect(not pbr_surface.GetInput("specular_color"), f"{label} MaterialX unlit authors a specular color")
        return
    specular = specular or {}
    weight, image = specular.get("specularFactor", 1), textures.get("specularTexture")
    authored = pbr_surface.GetInput("specular")
    if weight == 1 and not image:
        check.expect(not authored, f"{label} MaterialX authors a specular weight of 1")
    elif check.expect(authored, f"{label} MaterialX specular is not authored"):
        check_mtlx_channel(check, authored, weight, image, 3, f"{label} MaterialX specular")
    color, image = specular.get("specularColorFactor", (1, 1, 1)), textures.get("specularColorTexture")
    authored = pbr_surface.GetInput("specular_color")
    if tuple(color) == (1, 1, 1) and not image:
        check.expect(not authored, f"{label} MaterialX authors a white specular color")
        return
    if check.expect(authored, f"{label} MaterialX specular_color is not authored"):
        check_mtlx_color(check, authored, color, image, f"{label} MaterialX specular_color")


def check_sheen(check, surface, pbr_surface, sheen, textures, label):
    """The sheen color, times its texture's RGB, and roughness, times its texture's A, in MaterialX only;
    the roughness where it is not glTF's and gltf_pbr's default of 0. Preview has no sheen.

    `sheen` is None for a material without a sheen color other than black."""
    check.expect(not [i for i in surface.GetInputs() if "sheen" in i.GetBaseName().lower()],
                 f"{label} Preview authors a sheen")
    color, roughness = pbr_surface.GetInput("sheen_color"), pbr_surface.GetInput("sheen_roughness")
    if sheen is None:
        check.expect(not color and not roughness, f"{label} MaterialX authors a sheen it does not have")
        return
    if check.expect(color, f"{label} MaterialX sheen_color is not authored"):
        check_mtlx_color(check, color, sheen.get("sheenColorFactor", (0, 0, 0)), textures.get("sheenColorTexture"),
                         f"{label} MaterialX sheen_color")
    factor = sheen.get("sheenRoughnessFactor", 0)
    if not factor:
        check.expect(not roughness, f"{label} MaterialX authors a sheen roughness of 0")
    elif check.expect(roughness, f"{label} MaterialX sheen_roughness is not authored"):
        check_mtlx_channel(check, roughness, factor, textures.get("sheenRoughnessTexture"), 3,
                           f"{label} MaterialX sheen_roughness")


def check_anisotropy(check, surface, pbr_surface, anisotropy, textures, label):
    names = ("anisotropy_strength", "anisotropy_rotation")
    check.expect(not any(surface.GetInput(name) for name in names), f"{label} Preview authors anisotropy")
    if anisotropy is None:
        check.expect(not any(pbr_surface.GetInput(name) for name in names),
                     f"{label} MaterialX authors anisotropy it does not have")
        return
    strength, rotation = (pbr_surface.GetInput(name) for name in names)
    if not check.expect(strength and rotation, f"{label} MaterialX anisotropy is not authored"):
        return
    image = textures.get("anisotropyTexture")
    check_mtlx_channel(check, strength, anisotropy.get("anisotropyStrength", 0), image, 2, f"{label} anisotropy strength")
    # fastgltf stores floats; the converter reduces the angle to keep radian-to-degree math finite.
    expected = math.remainder(float(numpy.float32(anisotropy.get("anisotropyRotation", 0))), 2 * math.pi)
    check.close(rotation.Get(), expected, f"{label} anisotropy rotation")
    check.expect(not pbr_surface.GetInput("tangent"), f"{label} anisotropy does not use the documented renderer frame")
    selected = connected_shader(rotation)
    if not image:
        check.expect(not selected, f"{label} uniform anisotropy rotation is connected")
        return
    if expected:
        if not check.expect(selected and selected.GetShaderId() == "ND_add_float", f"{label} anisotropy rotation has no offset"):
            return
        check.close(selected.GetInput("in2").Get(), expected, f"{label} anisotropy angle offset")
        selected = connected_shader(selected.GetInput("in1"))
    if not check.expect(selected and selected.GetShaderId() == "ND_ifgreater_float", f"{label} anisotropy direction has no zero guard"):
        return
    check.close(selected.GetInput("value2").Get(), 0, f"{label} anisotropy zero test")
    check.close(selected.GetInput("in2").Get(), 0, f"{label} anisotropy zero direction")
    angle = connected_shader(selected.GetInput("in1"))
    if not check.expect(angle and angle.GetShaderId() == "ND_atan2_float", f"{label} anisotropy direction has no angle"):
        return
    for port, index in (("inx", 0), ("iny", 1)):
        channel = connected_shader(angle.GetInput(port))
        if not check.expect(channel and channel.GetInput("index").Get() == index, f"{label} anisotropy angle reads another channel"):
            continue
        remap = connected_shader(channel.GetInput("in"))
        if not check.expect(remap and remap.GetShaderId() == "ND_subtract_vector4", f"{label} anisotropy direction has no bias"):
            continue
        check.close(remap.GetInput("in2").Get(), (1, 1, 1, 1), f"{label} anisotropy direction bias")
        scale = connected_shader(remap.GetInput("in1"))
        if not check.expect(scale and scale.GetShaderId() == "ND_multiply_vector4", f"{label} anisotropy direction has no scale"):
            continue
        check.close(scale.GetInput("in2").Get(), (2, 2, 2, 2), f"{label} anisotropy direction scale")
        source = connected_shader(scale.GetInput("in1"))
        check.expect(source and source.GetPath() == image.GetPath(), f"{label} anisotropy direction reads another image")
    length = connected_shader(selected.GetInput("value1"))
    if check.expect(length and length.GetShaderId() == "ND_dotproduct_vector2", f"{label} anisotropy zero guard has no length"):
        for port in ("in1", "in2"):
            xy = connected_shader(length.GetInput(port))
            check.expect(xy and xy.GetShaderId() == "ND_convert_vector4_vector2", f"{label} anisotropy length is not RG")


def check_iridescence(check, surface, pbr_surface, film, textures, label):
    """MaterialX strength from R, uniform film ior, and a thickness range interpolated by G."""
    names = ("iridescence", "iridescence_ior", "iridescence_thickness")
    check.expect(not any(surface.GetInput(name) for name in names), f"{label} Preview authors iridescence")
    if film is None:
        check.expect(not any(pbr_surface.GetInput(name) for name in names),
                     f"{label} MaterialX authors iridescence it does not have")
        return
    strength, ior, thickness = (pbr_surface.GetInput(name) for name in names)
    if check.expect(strength, f"{label} MaterialX iridescence is not authored"):
        check_mtlx_channel(check, strength, film.get("iridescenceFactor", 0), textures.get("iridescenceTexture"),
                           0, f"{label} MaterialX iridescence")
    if check.expect(ior, f"{label} MaterialX iridescence ior is not authored"):
        from pxr import Sdf
        check.close(ior.Get(), film.get("iridescenceIor", 1.3), f"{label} film ior")
        check.expect(ior.GetAttr().GetVariability() == Sdf.VariabilityUniform, f"{label} film ior is not uniform")
    if not check.expect(thickness, f"{label} MaterialX iridescence thickness is not authored"):
        return
    minimum, maximum = film.get("iridescenceThicknessMinimum", 100), film.get("iridescenceThicknessMaximum", 400)
    check.close(thickness.Get(), maximum, f"{label} film thickness fallback")
    image = textures.get("iridescenceThicknessTexture")
    mix = connected_shader(thickness)
    if not image:
        check.expect(not mix, f"{label} constant film thickness has a connection")
        return
    if not check.expect(mix and mix.GetShaderId() == "ND_mix_float", f"{label} film thickness has no range mix"):
        return
    check.close(mix.GetInput("bg").Get(), minimum, f"{label} film minimum")
    check.close(mix.GetInput("fg").Get(), maximum, f"{label} film maximum")
    channel = connected_shader(mix.GetInput("mix"))
    check.expect(channel and channel.GetInput("index").Get() == 1, f"{label} film thickness does not read G")
    source = connected_shader(channel.GetInput("in")) if channel else None
    check.expect(source and source.GetPath() == image.GetPath(), f"{label} film thickness reads another image")


def check_mtlx_color(check, authored, factor, image, description):
    """A MaterialX color input of a factor, times the RGB of its sRGB texture if it has one."""
    check.close(authored.Get(), factor, description)
    if not image:
        return
    source = connected_shader(authored)
    # A factor of 1 reads the color directly; otherwise a multiply scales it.
    if source and source.GetShaderId() == "ND_multiply_color3":
        check.close(source.GetInput("in2").Get(), factor, f"{description} factor")
        source = connected_shader(source.GetInput("in1"))
    else:
        check.expect(tuple(factor) == (1, 1, 1), f"{description} is not scaled by its factor")
    rgb_image = connected_shader(source.GetInput("in")) if source and source.GetInput("in") else None
    check.expect(source and source.GetShaderId() == "ND_convert_color4_color3" and rgb_image
                 and rgb_image.GetPath() == image.GetPath(), f"{description} does not read its texture")


def check_mtlx_channel(check, authored, factor, image, index, description):
    """A MaterialX input of a factor, times one channel of its texture if it has one."""
    check.close(authored.Get(), factor, description)
    if not image:
        return
    source = connected_shader(authored)
    # A factor of 1 reads the channel directly; otherwise a multiply scales it.
    if source and source.GetShaderId() == "ND_multiply_float":
        check.close(source.GetInput("in2").Get(), factor, f"{description} factor")
        source = connected_shader(source.GetInput("in1"))
    else:
        check.expect(factor == 1, f"{description} is not scaled by its factor")
    check.expect(source and source.GetInput("index") and source.GetInput("index").Get() == index,
                 f"{description} does not read channel {'RGBA'[index]}")
    channel_image = connected_shader(source.GetInput("in")) if source and source.GetInput("in") else None
    check.expect(channel_image and channel_image.GetPath() == image.GetPath(),
                 f"{description} does not read its texture")


def check_occlusion(check, shader, strength, label):
    """Occlusion is 1 + strength * (sample - 1), which a texture's scale and bias express."""
    if shader:
        values = [shader.GetInput("scale").Get()[0], shader.GetInput("bias").Get()[0] if shader.GetInput("bias") else 0]
        check.close(values, (strength, 1 - strength), f"{label} Preview occlusion strength")


ANIMATED_ATTRIBUTES = {"translation": "xformOp:translate", "rotation": "xformOp:orient",
                       "scale": "xformOp:scale"}
TIME_CODES_PER_SECOND = 24


def sampled(times, values, interpolation, path, t):
    """glTF's value of a sampler at t seconds. values has one row per output element."""
    rotation = path == "rotation"
    if interpolation == "CUBICSPLINE":
        ins, vertices, outs = values[0::3], values[1::3], values[2::3]
    else:
        vertices = values
    if rotation and interpolation != "CUBICSPLINE":
        vertices = vertices / numpy.linalg.norm(vertices, axis=1, keepdims=True)
    if t <= times[0] or len(times) == 1:
        return vertices[0]
    if t >= times[-1]:
        return vertices[-1]
    k = int(numpy.searchsorted(times, t, side="right") - 1)
    if t == times[k] or interpolation == "STEP":
        return vertices[k]
    delta = times[k + 1] - times[k]
    u = (t - times[k]) / delta
    if interpolation == "LINEAR":
        if not rotation:
            return vertices[k] + u * (vertices[k + 1] - vertices[k])
        a, b = vertices[k], vertices[k + 1]
        dot = float(numpy.dot(a, b))
        if dot < 0:
            b, dot = -b, -dot
        theta = math.acos(min(1.0, dot))
        if theta < 1e-9:
            return a
        return (math.sin((1 - u) * theta) * a + math.sin(u * theta) * b) / math.sin(theta)
    u2, u3 = u * u, u * u * u
    value = ((2 * u3 - 3 * u2 + 1) * vertices[k] + (u3 - 2 * u2 + u) * delta * outs[k] +
             (-2 * u3 + 3 * u2) * vertices[k + 1] + (u3 - u2) * delta * ins[k + 1])
    return value / numpy.linalg.norm(value) if rotation else value


def check_animation(check, stage, asset, node_paths):
    """Chooses each animation's variant and compares its channels on the selected nodes with glTF's
    values at each keyframe, just before it and inside each segment, checks that the properties it
    does not animate have no time samples there, and checks each animation's end and the stage's
    timeline."""
    from pxr import Usd

    animations = asset.json.get("animations", [])
    if not animations:
        return
    root = stage.GetPrimAtPath("/Asset")
    variants = root.GetVariantSets().GetVariantSet("animation")
    names = [f"Animation_{index}" for index in range(len(animations))]
    if not check.expect(sorted(variants.GetVariantNames()) == sorted(names),
                        f"{len(variants.GetVariantNames())} animation variants, expected {len(animations)}"):
        return
    check.expect(variants.GetVariantSelection() == "Animation_0", "Animation_0 is not selected")

    def targets(animation):
        for number, channel in enumerate(animation["channels"]):
            target = channel["target"]
            node, path = target.get("node"), target.get("path")
            if node in node_paths and path in ANIMATED_ATTRIBUTES:
                yield number, channel, node, path

    animated = {(node, path) for animation in animations for _, _, node, path in targets(animation)}
    end = None
    for index, animation in enumerate(animations):
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            variants.SetVariantSelection(names[index])
        own_end = 0.0
        own = set()
        for number, channel, node, path in targets(animation):
            own.add((node, path))
            sampler = animation["samplers"][channel["sampler"]]
            interpolation = sampler.get("interpolation", "LINEAR")
            times = asset.accessor(sampler["input"])[:, 0].astype(numpy.float64)
            values = asset.accessor(sampler["output"]).astype(numpy.float64)
            label = f"animation {index} channel {number} on Node_{node}"
            own_end = max(own_end, float(times[-1]))
            if not stage.GetPrimAtPath(node_paths[node]):
                # A joint without a prim is compared through skinning.
                continue
            attribute = stage.GetAttributeAtPath(f"{node_paths[node]}.{ANIMATED_ATTRIBUTES[path]}")
            if not check.expect(attribute and attribute.GetNumTimeSamples() > 0, f"{label} is not authored"):
                continue
            if interpolation == "CUBICSPLINE":
                # The plugin samples a cubic within 1e-4 of its largest value, or of a quaternion's.
                vertices = values[1::3]
                limit = 2e-4 * (1.0 if path == "rotation" else max(1e-9, float(numpy.abs(vertices).max())))
            else:
                limit = TOLERANCE * (1.0 if path == "rotation" else max(1.0, float(numpy.abs(values).max())))
            query = [times[0] - 1, times[-1] + 1]
            for k, t in enumerate(times):
                query.append(t)
                if k + 1 < len(times):
                    following = times[k + 1]
                    query += [t + (following - t) * f for f in (0.25, 0.5, 0.75)]
                    query.append(following - 1e-6)
            worst = 0.0
            for t in query:
                expected = sampled(times, values, interpolation, path, t)
                value = attribute.Get(t * TIME_CODES_PER_SECOND)
                if path == "rotation":
                    actual = numpy.array([*value.GetImaginary(), value.GetReal()], numpy.float64)
                    if numpy.dot(actual, expected) < 0:
                        actual = -actual
                else:
                    actual = numpy.array(value, numpy.float64)
                worst = max(worst, float(numpy.abs(actual - expected).max()))
            check.expect(worst <= limit, f"{label}: values differ by {worst:.3g}")
        for node, path in sorted(animated - own):
            if not stage.GetPrimAtPath(node_paths[node]):
                continue
            attribute = stage.GetAttributeAtPath(f"{node_paths[node]}.{ANIMATED_ATTRIBUTES[path]}")
            check.expect(attribute and attribute.GetNumTimeSamples() == 0,
                         f"animation {index} moves {path} of Node_{node}, which it does not animate")
        data = root.GetCustomDataByKey("gltf") or {}
        check.close(data.get("endTimeCode", -1.0), own_end * TIME_CODES_PER_SECOND, f"animation {index} end")
        name = animation.get("name") or None
        check.expect(data.get("name") == name, f"animation {index} is named {data.get('name')!r}, not {name!r}")
        if own:
            end = max(end or 0.0, own_end)
    stage.GetSessionLayer().Clear()
    if end is not None:
        timeline = (stage.GetTimeCodesPerSecond(), stage.GetFramesPerSecond(), stage.GetStartTimeCode(),
                    stage.GetEndTimeCode())
        check.close(timeline, (TIME_CODES_PER_SECOND, TIME_CODES_PER_SECOND, 0, end * TIME_CODES_PER_SECOND),
                    "animation timeline")


def check_skinning(check, stage, asset, plan, node_paths, placements):
    from pxr import Usd, UsdGeom, UsdSkel, Vt
    from probe_skins import Pose, reference_points

    scene = f"/Asset/scenes/Scene_{plan.scene}"
    skeletons = {}
    for index, skin in plan.skeletons.items():
        space = node_paths[skin["space"]] if skin["space"] is not None else scene
        skeleton = UsdSkel.Skeleton.Get(stage, f"{space}/Skeleton_{index}")
        if check.expect(skeleton, f"missing Skeleton_{index}"):
            joints = skeleton.GetJointsAttr().Get()
            check.expect(list(joints) == [plan.token(j) for j in skin["joints"]],
                         f"Skeleton_{index} joint order differs")
            skeletons[index] = skeleton

    meshes = []
    for prim, primitive, node, index in placements:
        if index not in skeletons:
            continue
        mesh = UsdGeom.Mesh(prim)
        binding = UsdSkel.BindingAPI(prim)
        check.expect(binding.GetSkeletonRel().GetTargets() == [skeletons[index].GetPath()],
                     f"{prim.GetPath()}: wrong skeleton binding")
        attrs = primitive["attributes"]
        sets = range(sum(name.startswith("JOINTS_") for name in attrs))
        weights = numpy.concatenate([asset.accessor(attrs[f"WEIGHTS_{i}"]) for i in sets], axis=1)
        meshes.append((mesh, node, index, {
            "points": asset.accessor(attrs["POSITION"]),
            "joints": numpy.concatenate([asset.accessor(attrs[f"JOINTS_{i}"]) for i in sets], axis=1),
            "weights": weights / weights.sum(axis=1, keepdims=True),
        }))

    def compare(animation, seconds, half_joints, cache):
        time = Usd.TimeCode.Default() if animation is None else Usd.TimeCode(seconds * TIME_CODES_PER_SECOND)
        pose = Pose(asset, plan.parent, animation, seconds)
        rounded = Pose(asset, plan.parent, animation, seconds, half_joints)
        xforms = UsdGeom.XformCache(time)
        skinning = {index: cache.GetSkelQuery(skeleton).ComputeSkinningTransforms(time)
                    for index, skeleton in skeletons.items()}
        for mesh, node, index, data in meshes:
            query = cache.GetSkinningQuery(mesh.GetPrim())
            points = Vt.Vec3fArray(mesh.GetPointsAttr().Get())
            label = f"{mesh.GetPath()} animation {animation} at {seconds:g}s"
            if not check.expect(query and query.ComputeSkinnedPoints(skinning[index], points, time),
                                f"{label}: UsdSkel did not compute skinning"):
                continue
            world = numpy.array(xforms.GetLocalToWorldTransform(skeletons[index].GetPrim()))
            actual = (numpy.c_[numpy.array(points), numpy.ones(len(points))] @ world)[:, :3]
            expected = reference_points(asset, data, node, index, pose, rounded, plan)
            scale = max(float(numpy.ptp(expected, axis=0).max()), 1e-6)
            error = float(numpy.abs(actual - expected).max() / scale)
            check.expect(error <= 1e-4, f"{label}: relative skinned point error {error:.3g}")

    roots = [UsdSkel.Root(prim) for prim in stage.Traverse() if prim.IsA(UsdSkel.Root)]

    def populate():
        cache = UsdSkel.Cache()
        for root in roots:
            cache.Populate(root, Usd.PrimDefaultPredicate)
        return cache

    compare(None, 0, (), populate())
    joints = {joint for skin in plan.skeletons.values() for joint in skin["joints"]}
    variants = stage.GetDefaultPrim().GetVariantSet("animation")
    for index, animation in enumerate(asset.get("animations")):
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            variants.SetVariantSelection(f"Animation_{index}")
        channels = [c for c in animation["channels"] if c["target"].get("node") in node_paths]
        moved = {c["target"]["node"] for c in channels}
        end = max((float(asset.accessor(animation["samplers"][c["sampler"]]["input"])[-1, 0])
                   for c in channels), default=0.0)
        cache = populate()
        for seconds in sorted({0, end}):
            compare(index, seconds, moved & joints, cache)
    stage.GetSessionLayer().Clear()


def check_meaning(stage, asset):
    """Returns what differs between the stage and the file, walking the selected scene."""
    from pxr import Usd, UsdGeom, UsdLux, UsdSkel

    check = Check()
    document = asset.json
    meta = stage.GetMetadata("upAxis"), stage.GetMetadata("metersPerUnit")
    check.expect(meta == ("Y", 1.0), f"stage metrics are {meta}")
    scenes = document.get("scenes", [])
    roots = scenes[document.get("scene", 0)].get("nodes", []) if scenes else []
    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    expected_paths = set()
    expected_cameras = set()
    expected_lights = set()
    lights = document.get("extensions", {}).get("KHR_lights_punctual", {}).get("lights", [])
    materials = {}
    node_paths = {}
    plan = None
    dropped = set()
    placements = []
    if roots and document.get("skins"):
        from probe_skins import Plan, VARIANTS

        plan = Plan(asset, VARIANTS[0])
        dropped = set(plan.dropped)

    def walk(index, parent_path, parent_world):
        node = document["nodes"][index]
        path = f"{parent_path}/Node_{index}"
        node_paths[index] = path
        prim = stage.GetPrimAtPath(path)
        world = node_matrix(node) @ parent_world
        if index in dropped:
            check.expect(not prim, f"{path} has a prim for a joint without attachments")
        elif check.expect(prim and (prim.IsA(UsdGeom.Xform) or prim.IsA(UsdSkel.Root)),
                          f"{path} is neither Xform nor SkelRoot"):
            check.close(numpy.array(cache.GetLocalToWorldTransform(prim)), world, f"Node_{index} world transform")
        if "mesh" in node:
            mesh_index = node["mesh"]
            for number, primitive in enumerate(document["meshes"][mesh_index]["primitives"]):
                mesh_path = f"{path}/Mesh_{mesh_index}/Primitive_{number}"
                expected_paths.add(mesh_path)
                label = f"mesh {mesh_index} primitive {number}"
                mesh_prim = stage.GetPrimAtPath(mesh_path)
                if check.expect(mesh_prim and mesh_prim.IsA(UsdGeom.Mesh), f"{mesh_path} is not a Mesh"):
                    bound, material, has_colors = check_primitive(check, asset, mesh_prim, primitive, label)
                    materials[bound] = (material, has_colors)
                    if "skin" in node:
                        placements.append((mesh_prim, primitive, index, node["skin"]))
        if "camera" in node:
            camera_index = node["camera"]
            camera_path = f"{path}/Camera_{camera_index}"
            expected_cameras.add(camera_path)
            camera_prim = stage.GetPrimAtPath(camera_path)
            if check.expect(camera_prim and camera_prim.IsA(UsdGeom.Camera), f"{camera_path} is not a Camera"):
                # Hydra views through the inverse of the prim's whole transform.
                actual = numpy.linalg.inv(numpy.array(cache.GetLocalToWorldTransform(camera_prim))) @ numpy.array(
                    UsdGeom.Camera(camera_prim).GetCamera().frustum.ComputeProjectionMatrix())
                placement = camera_view(world)
                view = numpy.linalg.inv(placement)
                projection = gltf_projection(document["cameras"][camera_index])
                # The view's last row multiplies the rotation, read as floats, by the translation, so
                # its rounding grows with the camera's distance from the origin.
                magnitude = numpy.ones((4, 4))
                magnitude[3, :3] += numpy.abs(placement[3, :3]).sum()
                check.close(actual, view @ projection, f"camera {camera_index} on Node_{index}",
                            magnitude @ numpy.abs(projection))
        if "KHR_lights_punctual" in node.get("extensions", {}):
            light_index = node["extensions"]["KHR_lights_punctual"]["light"]
            light_path = f"{path}/Light_{light_index}"
            expected_lights.add(light_path)
            check_light(check, stage.GetPrimAtPath(light_path), lights[light_index], world,
                        f"light {light_index} on Node_{index}")
        for child in node.get("children", []):
            walk(child, path, world)

    for root in roots:
        walk(root, f"/Asset/scenes/Scene_{document.get('scene', 0)}", numpy.identity(4))
    check_animation(check, stage, asset, node_paths)
    if placements:
        check_skinning(check, stage, asset, plan, node_paths, placements)
    actual = {str(prim.GetPath()) for prim in stage.Traverse() if prim.IsA(UsdGeom.Mesh)}
    check.expect(actual == expected_paths, f"{len(actual)} meshes authored, expected {len(expected_paths)}")
    cameras = {str(prim.GetPath()) for prim in stage.Traverse() if prim.IsA(UsdGeom.Camera)}
    check.expect(cameras == expected_cameras, f"{len(cameras)} cameras authored, expected {len(expected_cameras)}")
    authored_lights = {str(prim.GetPath()) for prim in stage.Traverse() if prim.HasAPI(UsdLux.LightAPI)}
    check.expect(authored_lights == expected_lights,
                 f"{len(authored_lights)} lights authored, expected {len(expected_lights)}")
    images = {}
    for path, (material, has_colors) in sorted(materials.items()):
        check_material(check, asset, stage, path, material, has_colors, images)
    return check.mismatches, len(expected_paths)


def diagnostics_from(text, root):
    """Reduces USD's diagnostic text to its messages, without source locations or local paths."""
    messages = []
    for line in text.splitlines():
        match = re.search(r" at line \d+ in file .*? : '(.*)'?$", line) or \
            re.search(r" at line \d+ of .*? -- (.*)$", line)
        if match:
            line = match[1]
        elif "glTF" not in line and "usdGltfFileFormat" not in line:
            continue
        line = line.strip().rstrip("'").strip()
        line = line.replace(str(root), "<samples>").replace(str(root).replace("\\", "/"), "<samples>")
        messages.append(line.replace("\\", "/"))
    return messages


def author_view(stage):
    """Adds a camera that frames everything from an angle, because the default one can miss a model:
    it sees a flat plane edge on, and its clipping range loses a model a few millimeters across."""
    from pxr import Gf, Usd, UsdGeom

    box = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_]).ComputeWorldBound(
        stage.GetPseudoRoot()).ComputeAlignedRange()
    radius = box.GetSize().GetLength() / 2 or 1.0
    focal, aperture = 50.0, 36.0
    distance = radius * 1.1 * 2 * focal / aperture
    camera = UsdGeom.Camera.Define(stage, CAMERA)
    camera.CreateFocalLengthAttr(focal)
    camera.CreateHorizontalApertureAttr(aperture)
    camera.CreateVerticalApertureAttr(aperture)
    camera.CreateClippingRangeAttr(Gf.Vec2f(distance / 100, distance + radius * 2))
    camera.AddTranslateOp(opSuffix="center").Set(box.GetMidpoint())
    camera.AddRotateYOp().Set(30.0)
    camera.AddRotateXOp().Set(-30.0)
    camera.AddTranslateOp(opSuffix="distance").Set(Gf.Vec3d(0, 0, distance))


def inspect(path, root, directory):
    """Runs in a process of its own, and prints a result for this file."""
    from pxr import Ar, Sdf, Tf, Usd, UsdShade

    result = {"opened": True, "diagnostics": []}
    try:
        stage = Usd.Stage.Open(str(path))
        if not stage:
            raise RuntimeError("the stage did not open")
    except (Tf.ErrorException, RuntimeError) as error:
        result = {"opened": False, "diagnostics": diagnostics_from(str(error), root)}
        print(RESULT_MARKER + json.dumps(result), flush=True)
        return

    asset = Asset(path)
    used = asset.json.get("extensionsUsed", [])
    result["ignored"] = sorted(set(used) - IMPLEMENTED_EXTENSIONS)
    # Embedded images share a parsed package only inside a resolver cache scope.
    # Bound the scope to this inspection, so another file or reload sees fresh bytes.
    with Ar.ResolverScopedCache():
        result["mismatches"], result["meshes"] = check_meaning(stage, asset)

    for shading in PATHS:
        session = Sdf.Layer.CreateAnonymous(".usda")
        edit = Usd.Stage.Open(stage.GetRootLayer(), session)
        edit.SetEditTarget(session)
        author_view(edit)
        for prim in edit.Traverse():
            if shading == "preview" and prim.IsA(UsdShade.Material):
                output = UsdShade.Material(prim).GetSurfaceOutput("mtlx")
                if output:
                    output.GetAttr().SetConnections([])
        session.Export(str(directory / f"{shading}_session.usda"))
    print(RESULT_MARKER + json.dumps(result), flush=True)


def plugin_environment(build):
    environment = dict(os.environ)
    resources = build / "plugin" / "resources" / "usdGltfFileFormat"
    if not resources.is_dir():
        sys.exit(f"{resources} does not exist. Run ost build first.")
    environment["PXR_PLUGINPATH_NAME"] = str(resources)
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def open_file(key, path, root, build, output):
    """Opens one file in a process of its own, so that a crash or hang costs only this file."""
    directory = output / key.replace("/", "__")
    directory.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(pathlib.Path(__file__).resolve()), "--inspect", str(path), str(root),
               str(directory)]
    try:
        process = subprocess.run(command, env=plugin_environment(build), capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return {"opened": "timeout"}
    (directory / "inspect.log").write_text(process.stdout + process.stderr, encoding="utf-8")
    payload = next((line[len(RESULT_MARKER):] for line in reversed(process.stdout.splitlines())
                    if line.startswith(RESULT_MARKER)), None)
    if process.returncode or payload is None:
        tail = (process.stderr.strip().splitlines() or ["no output"])[-1]
        return {"opened": "crashed", "exit": process.returncode, "detail": tail[:200]}
    found = json.loads(payload)
    warnings = sorted(set(diagnostics_from(process.stderr, root)))
    if not found["opened"]:
        diagnostic = rejection(found["diagnostics"], warnings)
        causes = rejection_causes(Asset(path).json)
        return {"opened": "rejected", "diagnostic": diagnostic, "causes": causes,
                "diagnostic_fits": diagnostic_fits(diagnostic, causes)}
    mismatches = found["mismatches"]
    # What the plugin ignores it must say it ignores, so that nothing is dropped silently.
    warned = {match[1] for match in map(OPTIONAL_WARNING.fullmatch, warnings) if match}
    mismatches += [f"{name} is ignored without a warning" for name in found["ignored"] if name not in warned]
    warnings = [warning for warning in warnings if not OPTIONAL_WARNING.fullmatch(warning)]
    if mismatches:
        meaning = "differs"
    elif found["ignored"]:
        meaning = "partial"
    else:
        meaning = "verified"
    entry = {"opened": "opened", "meaning": meaning, "meshes": found["meshes"], "warnings": warnings}
    if found["ignored"]:
        entry["ignored"] = found["ignored"]
    if mismatches:
        entry["mismatches"] = mismatches[:MAXIMUM_MISMATCHES]
        entry["mismatch_count"] = len(mismatches)
    return entry


def rejection(errors, warnings):
    """Returns the message that caused a rejection, rather than the generic one USD adds after it."""
    for message in [*errors, *warnings]:
        if "glTF" in message or "usdGltfFileFormat" in message:
            return message
    return errors[0] if errors else "no diagnostic"


def rejection_causes(document):
    """Lists everything in a file that is unsupported, read from the file and not the diagnostic."""
    required = set(document.get("extensionsRequired", [])) - IMPLEMENTED_EXTENSIONS
    causes = {f"requires {name}" for name in required}
    nodes = document.get("nodes", [])
    scenes = document.get("scenes", [])
    pending = list(scenes[document.get("scene", 0)].get("nodes", [])) if scenes else []
    seen = set()
    while pending:
        index = pending.pop()
        if index in seen:
            continue
        seen.add(index)
        node = nodes[index]
        pending += node.get("children", [])
        if "skin" in node:
            skin = document["skins"][node["skin"]]
            if "inverseBindMatrices" in skin and "sparse" in document["accessors"][skin["inverseBindMatrices"]]:
                causes.add("sparse accessor")
        if "weights" in node:
            causes.add("morph targets")
        if "mesh" in node:
            causes |= mesh_causes(document, document["meshes"][node["mesh"]])
    # Every animation is authored, but only on the selected scene.
    for animation in document.get("animations", []):
        for channel in animation.get("channels", []):
            node = channel["target"].get("node")
            if node in seen and channel["target"].get("path") == "weights":
                causes.add("morph targets")
            elif node in seen and "matrix" in nodes[node]:
                causes.add("animated matrix")
    return sorted(causes)


def mesh_causes(document, mesh):
    causes = {"morph targets"} if "weights" in mesh else set()
    for primitive in mesh.get("primitives", []):
        if primitive.get("mode", 4) not in (4, 5, 6):
            causes.add("points or lines")
        if primitive.get("targets"):
            causes.add("morph targets")
        accessors = [*primitive.get("attributes", {}).values()]
        if "indices" in primitive:
            accessors.append(primitive["indices"])
        # Compressed primitives keep their data in the extension, not a bufferView.
        compressed = primitive.get("extensions", {}).keys() & {"KHR_draco_mesh_compression"}
        for index in accessors:
            accessor = document["accessors"][index]
            if "sparse" in accessor:
                causes.add("sparse accessor")
            elif "bufferView" not in accessor and not compressed:
                causes.add("accessor without a bufferView")
    return causes


def diagnostic_fits(diagnostic, causes):
    """Returns whether a diagnostic names a cause that the file really has."""
    required = REQUIRED_DIAGNOSTIC.search(diagnostic)
    if required:
        named = {f"requires {name}" for name in required.group(1).split(", ")}
        return named == {cause for cause in causes if cause.startswith("requires ")}
    for pattern, kinds in DIAGNOSTIC_CAUSES.items():
        if pattern in diagnostic:
            return any(cause.startswith(kinds) for cause in causes)
    return False


def coverage(image_path):
    """Returns the fraction of pixels a render covered, by alpha or by differing from its corner."""
    from PySide6 import QtGui

    image = QtGui.QImage(str(image_path)).convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
    if image.isNull():
        return None
    pixels = numpy.frombuffer(image.constBits(), numpy.uint8).reshape(image.height(), image.bytesPerLine() // 4, 4)
    differs = numpy.any(numpy.abs(pixels.astype(numpy.int16) - pixels[0, 0].astype(numpy.int16)) > 8, axis=2)
    return float(numpy.mean(differs))


def render_file(key, path, build, output, usdrecord):
    """Renders one opened file through each path. This shows that Storm draws, and no more."""
    results = {}
    directory = output / key.replace("/", "__")
    for shading in PATHS:
        image_path = directory / f"{shading}.png"
        image_path.unlink(missing_ok=True)
        session = str(directory / f"{shading}_session.usda")
        command = [usdrecord, "--camera", CAMERA, "--sessionLayer", session, "--imageWidth", str(RENDER_WIDTH),
                   str(path), str(image_path)]
        try:
            process = subprocess.run(command, env=plugin_environment(build), capture_output=True, text=True,
                                     encoding="utf-8", errors="replace", timeout=TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            results[shading] = "timeout"
            continue
        text = process.stdout + process.stderr
        (directory / f"{shading}.log").write_text(text, encoding="utf-8")
        errors = [line for line in text.splitlines() if "Error" in line or "Failed to" in line]
        covered = coverage(image_path) if image_path.exists() else None
        if process.returncode:
            results[shading] = "failed"
        elif covered is None:
            results[shading] = "no image"
        elif covered < MINIMUM_COVERAGE:
            results[shading] = "blank"
        else:
            results[shading] = "drawn with errors" if errors else "drawn"
    return results


def sample_files(root):
    """Lists every file on disk, because the repository's index omits some."""
    models = root / "Models"
    return {path.relative_to(models).as_posix(): path for path in sorted(models.rglob("*"))
            if path.suffix in (".gltf", ".glb")}


def sample_commit(root):
    return subprocess.run(["git", "--git-dir", str(root / ".git"), "rev-parse", "HEAD"], check=True, capture_output=True,
                          text=True).stdout.strip()


def write_baseline(commit, results):
    """Writes one line per file, so that a change shows as a small diff."""
    lines = [f"    {json.dumps(key)}: {json.dumps(results[key], sort_keys=True)}" for key in sorted(results)]
    BASELINE.write_text('{\n  "commit": ' + json.dumps(commit) + ',\n  "files": {\n' + ",\n".join(lines) + "\n  }\n}\n",
                        encoding="utf-8", newline="\n")


def summarize(results):
    tally = {}
    for entry in results.values():
        fields = [f"opened: {entry['opened']}"]
        if "meaning" in entry:
            fields.append(f"meaning: {entry['meaning']}")
        fields += [f"rendered {shading}: {outcome}" for shading, outcome in entry.get("rendered", {}).items()]
        for field in fields:
            tally[field] = tally.get(field, 0) + 1
    for field in sorted(tally):
        print(f"{tally[field]:4} {field}")
    causes = {}
    for key, entry in results.items():
        if entry["opened"] == "rejected":
            cause = ", ".join(entry["causes"]) or "no cause found"
            causes[cause] = causes.get(cause, 0) + 1
            if not entry["diagnostic_fits"]:
                print(f"     {key}: the diagnostic does not fit: {entry['diagnostic']}")
    for cause, count in sorted(causes.items(), key=lambda item: (-item[1], item[0])):
        print(f"{count:4} rejected: {cause}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--inspect":
        inspect(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]), pathlib.Path(sys.argv[4]))
        return 0

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("build", type=pathlib.Path, help="the directory of a built workspace")
    parser.add_argument("samples", type=pathlib.Path, help="a checkout of glTF-Sample-Assets")
    parser.add_argument("--render", action="store_true", help="also render, which needs a GPU")
    parser.add_argument("--update", action="store_true", help="accept the results as the new baseline")
    parser.add_argument("--only", help="check the files whose key contains this text")
    parser.add_argument("--jobs", type=int, default=max((os.cpu_count() or 2) // 2, 1), help="files to open at once")
    arguments = parser.parse_args()

    build = arguments.build.resolve()
    root = arguments.samples.resolve()
    output = build / "sample_check"
    output.mkdir(parents=True, exist_ok=True)
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {"commit": None, "files": {}}
    commit = sample_commit(root)
    if baseline["commit"] and commit != baseline["commit"] and not arguments.update:
        sys.exit(f"The samples are at {commit}, and the baseline is for {baseline['commit']}.")
    usdrecord = shutil.which("usdrecord")
    if arguments.render and not usdrecord:
        sys.exit("usdrecord was not found. Activate the lookdev runtime first.")

    files = sample_files(root)
    if arguments.only:
        files = {key: path for key, path in files.items() if arguments.only in key}
    with concurrent.futures.ThreadPoolExecutor(arguments.jobs) as pool:
        opened = pool.map(lambda item: open_file(item[0], item[1], root, build, output), files.items())
        results = dict(zip(files, opened))
    if arguments.render:
        # One at a time, because the renders share one GPU.
        for key, path in files.items():
            if results[key]["opened"] == "opened":
                results[key]["rendered"] = render_file(key, path, build, output, usdrecord)

    summarize(results)
    if arguments.update:
        merged = {**baseline["files"], **results} if arguments.only else results
        if not arguments.render:
            for key, entry in merged.items():
                if "rendered" in baseline["files"].get(key, {}):
                    entry["rendered"] = baseline["files"][key]["rendered"]
        write_baseline(commit, merged)
        print(f"Wrote {BASELINE.relative_to(ROOT)} with {len(merged)} files at {commit}.")
        return 0

    differences = []
    for key, entry in results.items():
        expected = baseline["files"].get(key)
        if expected is None:
            differences.append(f"{key}: not in the baseline")
            continue
        if not arguments.render:
            expected = {name: value for name, value in expected.items() if name != "rendered"}
        if entry != expected:
            differences.append(f"{key}:\n  baseline {json.dumps(expected, sort_keys=True)}\n"
                               f"  now      {json.dumps(entry, sort_keys=True)}")
    if not arguments.only:
        differences += [f"{key}: in the baseline but not the samples" for key in baseline["files"]
                        if key not in results]
    print("\n".join(differences) if differences else f"All {len(results)} files match the baseline.")
    return 1 if differences else 0


if __name__ == "__main__":
    sys.exit(main())
