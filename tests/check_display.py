# SPDX-License-Identifier: Apache-2.0
"""Renders the display fixtures with Storm through both shader paths and compares pixels.

Run this in the OpenStrata lookdev environment, with the directory of a built workspace:

    python tests/check_display.py build/cy2026-windows-x86_64-py313-usd

It needs a GPU, so it is not part of CTest. See docs/adr/0007-display-check.md for what it
compares and why, and the capability matrix for what has been run.
"""

import argparse
import base64
import json
import pathlib
import os
import shutil
import struct
import subprocess
import sys
import zlib

try:
    from pxr import Gf, Glf, Plug, Sdf, Usd, UsdGeom, UsdImagingGL, UsdShade, CameraUtil
    from PySide6 import QtGui, QtOpenGL
except ImportError as error:
    sys.exit(f"{error}. Activate the lookdev runtime first: ost env cy2026 --profile lookdev")

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
CAMERA = "/DisplayCheckCamera"
VIEW_CAMERA = "/Cam"
IMAGE_WIDTH = 1500
SAMPLE_RADIUS = 2

# A channel this high or low counts as on or off in a named color. Storm's lighting keeps a
# channel that should be full well above HIGH, and leaves a little light on black.
HIGH = 170
LOW = 70
# Pixels that should be the same image data reached by two routes.
SAME = 3
# Pixels that should differ, as a minimum, so that a comparison cannot pass by being flat.
DIFFERENT = 12

COLORS = {"R": (1, 0, 0), "G": (0, 1, 0), "B": (0, 0, 1), "W": (1, 1, 1), "Y": (1, 1, 0), "K": (0, 0, 0)}
# The test image has red, green, blue and white quadrants, from upper left to lower right.
UPPER_LEFT, UPPER_RIGHT, LOWER_LEFT, LOWER_RIGHT = (0.25, 0.75), (0.75, 0.75), (0.25, 0.25), (0.75, 0.25)
QUADRANTS = (UPPER_LEFT, UPPER_RIGHT, LOWER_LEFT, LOWER_RIGHT)
PATHS = ("mtlx", "preview")


class Report:
    def __init__(self, verbose):
        self.verbose = verbose
        self.passed = 0
        self.failures = []

    def expect(self, label, passed, detail):
        if passed:
            self.passed += 1
        else:
            self.failures.append(f"{label}: {detail}")
        if self.verbose or not passed:
            print(f"{'ok  ' if passed else 'FAIL'} {label}: {detail}")

    def color(self, label, render, mesh, point, name):
        rgb = render.sample(mesh, point)[:3]
        passed = all(c >= HIGH if on else c <= LOW for c, on in zip(rgb, COLORS[name]))
        self.expect(f"{render.label} {label}", passed, f"{rgb}, expected {name}")

    def image(self, label, render, mesh, names):
        for point, name in zip(QUADRANTS, names):
            self.color(f"{label} at {point}", render, mesh, point, name)

    def same(self, label, a, b, tolerance=SAME):
        worst = max(abs(x - y) for x, y in zip(a, b))
        self.expect(label, worst <= tolerance, f"{a} and {b}, within {tolerance}")

    def different(self, label, a, b, minimum=DIFFERENT):
        worst = max(abs(x - y) for x, y in zip(a, b))
        self.expect(label, worst >= minimum, f"{a} and {b}, apart by at least {minimum}")


class Render:
    """An image and what is needed to find a mesh in it."""

    def __init__(self, label, image, stage, camera=CAMERA):
        self.label = label
        self.image = image
        self.stage = stage
        self.frustum = UsdGeom.Camera(stage.GetPrimAtPath(camera)).GetCamera().frustum
        self.meshes = [prim for prim in stage.Traverse() if prim.IsA(UsdGeom.Mesh)]
        self.bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])

    def pixel(self, mesh, point):
        """Maps a fraction of the mesh's bounds, from its lower left, to a pixel."""
        box = self.bounds.ComputeWorldBound(self.meshes[mesh]).ComputeAlignedRange()
        low, size = box.GetMin(), box.GetSize()
        world = Gf.Vec3d(low[0] + size[0] * point[0], low[1] + size[1] * point[1], low[2] + size[2] * 0.5)
        ndc = (self.frustum.ComputeViewMatrix() * self.frustum.ComputeProjectionMatrix()).Transform(world)
        return round((ndc[0] + 1) / 2 * self.image.width()), round((1 - ndc[1]) / 2 * self.image.height())

    def sample(self, mesh, point=(0.5, 0.5)):
        """Returns the mean RGBA around a point, in 8-bit display values."""
        x, y = self.pixel(mesh, point)
        colors = [self.image.pixelColor(x + dx, y + dy)
                  for dx in range(-SAMPLE_RADIUS, SAMPLE_RADIUS + 1) for dy in range(-SAMPLE_RADIUS, SAMPLE_RADIUS + 1)]
        return tuple(round(sum(getattr(c, channel)() for c in colors) / len(colors))
                     for channel in ("red", "green", "blue", "alpha"))


class Renderer:
    def __init__(self, output, plugin, report):
        self.output = output
        self.report = report
        self.environment = dict(os.environ)
        self.environment["PXR_PLUGINPATH_NAME"] = os.pathsep.join(
            [str(plugin), self.environment.get("PXR_PLUGINPATH_NAME", "")])
        self.usdrecord = shutil.which("usdrecord")
        if not self.usdrecord:
            sys.exit("usdrecord was not found. Activate the lookdev runtime first.")

    def session(self, name, asset, path, view):
        """Authors the camera, and for UsdPreviewSurface blocks the MaterialX surfaces."""
        session_path = self.output / f"{name}_{path}.usda"
        session = Sdf.Layer.CreateAnonymous(".usda")
        stage = Usd.Stage.Open(Sdf.Layer.FindOrOpen(str(asset)), session)
        stage.SetEditTarget(session)
        if view:
            session.subLayerPaths.append(str(view).replace("\\", "/"))
        else:
            author_framing_camera(stage)
        materials = [UsdShade.Material(prim) for prim in stage.Traverse() if prim.IsA(UsdShade.Material)]
        for material in materials:
            if path == "preview":
                output = material.GetSurfaceOutput("mtlx")
                if output:
                    output.GetAttr().SetConnections([])
            source, _, _ = material.ComputeSurfaceSource("mtlx")
            shader = source.GetShaderId() or ""
            if (path == "preview") != (shader == "UsdPreviewSurface"):
                self.report.expect(f"{name} {path}", False, f"{material.GetPath()} uses {shader}")
        session.Export(str(session_path))
        return session_path, stage

    def record(self, name, asset, path, view=None):
        session_path, stage = self.session(name, asset, path, view)
        camera = VIEW_CAMERA if view else CAMERA
        image_path = self.output / f"{name}_{path}.png"
        image_path.unlink(missing_ok=True)
        result = subprocess.run(
            [self.usdrecord, "--camera", camera, "--sessionLayer", str(session_path), "--imageWidth", str(IMAGE_WIDTH),
             str(asset), str(image_path)],
            env=self.environment, capture_output=True, text=True, encoding="utf-8", errors="replace")
        (self.output / f"{name}_{path}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        label = f"{name} {path}"
        errors = [line for line in (result.stdout + result.stderr).splitlines()
                  if "Error" in line or "Failed to" in line]
        self.report.expect(f"{label} renders", result.returncode == 0 and image_path.exists(),
                           f"usdrecord exit code {result.returncode}")
        self.report.expect(f"{label} reports no errors", not errors, errors[0] if errors else "none")
        image = QtGui.QImage(str(image_path))
        if image.isNull():
            return None
        return Render(label, image, stage, camera)


def author_framing_camera(stage):
    """Adds a camera that faces -Z and frames everything, so that pixels can be computed."""
    box = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_]).ComputeWorldBound(
        stage.GetPseudoRoot()).ComputeAlignedRange()
    width, height = box.GetSize()[0] * 1.1, box.GetSize()[1] * 1.1
    focal, aperture = 50.0, 36.0
    center = box.GetMidpoint()
    camera = UsdGeom.Camera.Define(stage, CAMERA)
    camera.CreateFocalLengthAttr(focal)
    camera.CreateHorizontalApertureAttr(aperture)
    camera.CreateVerticalApertureAttr(aperture * height / width)
    camera.CreateClippingRangeAttr(Gf.Vec2f(0.01, 10000))
    distance = width * focal / aperture + box.GetSize()[2]
    camera.AddTranslateOp().Set(Gf.Vec3d(center[0], center[1], box.GetMax()[2] + distance))


def check_alpha_modes(renderer, report):
    renders = {}
    for path in PATHS:
        render = renders[path] = renderer.record("alpha_modes", FIXTURES / "alpha_modes.gltf", path)
        if not render:
            continue
        # Vertex alpha falls from 1 at the left of each quad to 0 at the right, over a red backdrop.
        for mesh, label in ((0, "MASK"), (1, "MASK with a factor")):
            report.color(f"{label} left of its cut", render, mesh, (0.25, 0.5), "G")
            report.color(f"{label} right of its cut", render, mesh, (0.75, 0.5), "R")
        for mesh, label in ((2, "MASK with cutoff 0"), (5, "OPAQUE with a factor")):
            report.color(f"{label} left", render, mesh, (0.1, 0.5), "G")
            report.color(f"{label} right", render, mesh, (0.9, 0.5), "G")
        report.color("BLEND opaque end", render, 3, (0.02, 0.5), "G")
        report.color("BLEND clear end", render, 3, (0.98, 0.5), "R")
        report.different(f"{render.label} BLEND fades", render.sample(3, (0.25, 0.5)), render.sample(3, (0.75, 0.5)))
        report.different(f"{render.label} BLEND factor halves the opaque end",
                         render.sample(3, (0.02, 0.5)), render.sample(4, (0.02, 0.5)))
        report.color("BLEND with a factor clear end", render, 4, (0.98, 0.5), "R")
        report.same(f"{render.label} yellow factor leaves green", render.sample(0, (0.25, 0.5)),
                    render.sample(1, (0.25, 0.5)))
    compare_paths(report, "alpha_modes", renders, [(mesh, (0.25, 0.5)) for mesh in range(6)], tolerance=8)


def check_base_color_textures(renderer, report):
    renders = {}
    for path in PATHS:
        render = renders[path] = renderer.record("base_color_textures", FIXTURES / "base_color_textures.gltf", path)
        if not render:
            continue
        report.image("data URI", render, 0, "RGBW")
        report.image("bufferView with a yellow factor", render, 1, "RGKY")
        report.image("external PNG", render, 2, "RGBW")
        report.image("second UV set", render, 7, "BWRG")
        # Its UVs run from 0 to 2: columns mirror, and rows stay on the image's last row past 1.
        for x, upper, lower in ((0.125, "R", "B"), (0.375, "G", "W"), (0.625, "G", "W"), (0.875, "R", "B")):
            report.color(f"mirrored in S at {x}", render, 6, (x, 0.875), upper)
            report.color(f"clamped in T at {x}", render, 6, (x, 0.25), lower)
        # The JPEG is a light ellipse on dark, without alpha, so BLEND hides the gray backdrop.
        report.color("external JPEG under BLEND, corner", render, 3, (0.1, 0.9), "K")
        report.different(f"{render.label} external JPEG shows its ellipse", render.sample(3), render.sample(3, (0.1, 0.9)))
        # This image is green on the left and clear on the right.
        backdrop = render.sample(9, (0.554, 0.5))
        report.color("MASK keeps image alpha", render, 4, (0.25, 0.5), "G")
        report.same(f"{render.label} MASK cuts image alpha", render.sample(4, (0.75, 0.5)), backdrop)
        report.different(f"{render.label} BLEND factor mixes with the backdrop", render.sample(5, (0.25, 0.5)),
                         render.sample(4, (0.25, 0.5)))
        report.different(f"{render.label} BLEND factor leaves some image", render.sample(5, (0.25, 0.5)), backdrop)
        report.same(f"{render.label} BLEND cuts image alpha", render.sample(5, (0.75, 0.5)), backdrop)
    # A green COLOR_0 multiplies the image through MaterialX only, the documented difference.
    if renders["mtlx"]:
        report.image("image times COLOR_0", renders["mtlx"], 8, "KGKG")
    if renders["preview"]:
        report.image("image without COLOR_0", renders["preview"], 8, "RGBW")
    compare_paths(report, "base_color_textures", renders,
                  [(mesh, point) for mesh in (0, 2, 7) for point in QUADRANTS], tolerance=10)


def check_pbr_textures(renderer, report):
    renders = {}
    for path in PATHS:
        render = renders[path] = renderer.record("pbr_textures", FIXTURES / "pbr_textures.gltf", path)
        if not render:
            continue
        report.different(f"{render.label} metal lower half is darker", render.sample(0, UPPER_LEFT),
                         render.sample(0, LOWER_LEFT))
        report.different(f"{render.label} roughness changes left to right", render.sample(0, LOWER_LEFT),
                         render.sample(0, LOWER_RIGHT))
        report.same(f"{render.label} metallic factor 0 removes the metal edge", render.sample(1, UPPER_RIGHT),
                    render.sample(1, LOWER_RIGHT))
        report.different(f"{render.label} roughness factor 0.5 keeps the edge", render.sample(1, UPPER_LEFT),
                         render.sample(1, UPPER_RIGHT), 5)
        report.image("emissive upright", render, 4, "RGBW")
        report.image("emissive with a yellow factor, TEXCOORD_1", render, 5, "KYRG")
    if renders["preview"]:
        render = renders["preview"]
        report.different("preview occlusion darkens the right", render.sample(2, (0.25, 0.5)), render.sample(2, (0.75, 0.5)))
        report.expect("preview full occlusion is black", max(render.sample(2, (0.75, 0.5))[:3]) <= LOW,
                      f"{render.sample(2, (0.75, 0.5))}")
        between = render.sample(3, (0.75, 0.5))[0]
        report.expect("preview occlusion at strength 0.5 is midway",
                      render.sample(2, (0.75, 0.5))[0] + DIFFERENT <= between <= render.sample(3, (0.25, 0.5))[0] - DIFFERENT,
                      f"{between}")
    if renders["mtlx"]:
        render = renders["mtlx"]
        for mesh in (2, 3):
            report.same(f"mtlx occlusion {mesh} has no visible effect", render.sample(mesh, (0.25, 0.5)),
                        render.sample(mesh, (0.75, 0.5)))


def check_normal_textures(renderer, report):
    view = FIXTURES / "normal_textures_view.usda"
    for path in PATHS:
        map_render = renderer.record("normal_textures", FIXTURES / "normal_textures.gltf", path, view)
        reference = renderer.record("normal_textures_reference", FIXTURES / "normal_textures_reference.gltf", path, view)
        if not map_render or not reference:
            continue
        for mesh in range(15):
            report.same(f"{path} normal map {mesh} matches its reference", map_render.sample(mesh),
                        reference.sample(mesh), tolerance=2)
        report.different(f"{path} opposite X tilts differ", map_render.sample(1), map_render.sample(2))
        report.different(f"{path} opposite Y tilts differ", map_render.sample(3), map_render.sample(4))


def check_ktx2_textures(renderer, report):
    for path in PATHS:
        render = renderer.record("ktx2_textures", FIXTURES / "ktx2_textures.gltf", path)
        if not render:
            continue
        for mesh, label in ((0, "PNG"), (1, "ETC1S"), (2, "ETC1S with mips"), (3, "UASTC with Zstandard")):
            report.image(label, render, mesh, "RGBW")
        for mesh in (1, 2, 3):
            for point in QUADRANTS:
                report.same(f"{render.label} KTX2 {mesh} matches the PNG at {point}", render.sample(mesh, point),
                            render.sample(0, point), tolerance=6)
        for point in QUADRANTS:
            report.same(f"{render.label} alpha quads agree at {point}", render.sample(4, point), render.sample(5, point))
        report.color("ETC1S alpha upper half", render, 4, UPPER_LEFT, "R")
        report.different(f"{render.label} alpha of 0.25 in the lower half", render.sample(4, LOWER_RIGHT),
                         render.sample(1, LOWER_RIGHT))


def check_texture_transforms(renderer, report):
    """Each quad is compared with its reference, where the specification's matrix is baked into UVs."""
    grid = [(x, y) for x in (0.125, 0.375, 0.625, 0.875) for y in (0.125, 0.375, 0.625, 0.875)]
    # Baking a scale of 0 makes UVs degenerate, which turns the reference's MaterialX path black.
    scale_of_0 = 10
    # Storm's MaterialX path derives the frame from the primvar, without the rotation or mirror.
    frame_differs = (16, 17, 18, 21)
    for path in PATHS:
        render = renderer.record("texture_transforms", FIXTURES / "texture_transforms.gltf", path)
        reference = renderer.record("texture_transforms_reference", FIXTURES / "texture_transforms_reference.gltf", path)
        if not render or not reference:
            continue
        report.expect(f"{path} fixture and reference have the same quads", len(render.meshes) == len(reference.meshes) == 22,
                      f"{len(render.meshes)} and {len(reference.meshes)}")
        for mesh in range(len(render.meshes)):
            samples = [(render.sample(mesh, point), reference.sample(mesh, point)) for point in grid]
            worst = max(abs(x - y) for a, b in samples for x, y in zip(a, b))
            if mesh == scale_of_0 and path == "mtlx":
                continue
            if mesh in frame_differs and path == "mtlx":
                report.expect(f"{path} transform {mesh} differs from its reference, the documented limit", worst >= 5,
                              f"apart by {worst}")
            else:
                report.expect(f"{path} transform {mesh} matches its reference", worst <= SAME, f"apart by {worst}")
        # What a reference that was itself wrong could not hide.
        for mesh, label, names in ((0, "every field at its default", "RGBW"), (1, "offset in U", "GRWB"),
                                   (2, "offset in V", "BWRG"), (3, "scale about the upper left", "RRRR"),
                                   (4, "rotation", "BRWG"), (7, "negative scale", "GRWB"), (9, "clamp", "WWWW"),
                                   (10, "scale of 0", "RRRR"), (11, "override of texCoord", "BWRG"),
                                   (12, "override with a transform", "WBGR"), (14, "emissive", "BRWG")):
            report.image(label, render, mesh, names)


def png(color):
    """Encodes a uniform 4 by 4 RGBA image."""

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = (b"\0" + bytes(color) * 4) * 4
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def quad_document(materials, images=(), normal=(0, 0, 1)):
    """A document with one material per primitive, sharing one POSITION accessor.

    Each mesh then authors the whole vertex array, so its bounds span every quad.
    """
    positions = []
    for index in range(len(materials)):
        left = index * 1.25
        positions += [left, 0, 0, left + 1, 0, 0, left + 1, 1, 0, left, 1, 0]
    normals = list(normal) * (len(positions) // 3)
    uvs = [0, 1, 1, 1, 1, 0, 0, 0] * len(materials)
    data = struct.pack(f"<{len(positions)}f", *positions) + struct.pack(f"<{len(normals)}f", *normals)
    data += struct.pack(f"<{len(uvs)}f", *uvs)
    vertices = len(positions) // 3
    views = [{"buffer": 0, "byteOffset": 0, "byteLength": vertices * 12},
             {"buffer": 0, "byteOffset": vertices * 12, "byteLength": vertices * 12},
             {"buffer": 0, "byteOffset": vertices * 24, "byteLength": vertices * 8}]
    accessors = [{"bufferView": 0, "componentType": 5126, "count": vertices, "type": "VEC3",
                  "min": [min(positions[0::3]), min(positions[1::3]), 0],
                  "max": [max(positions[0::3]), max(positions[1::3]), 0]},
                 {"bufferView": 1, "componentType": 5126, "count": vertices, "type": "VEC3"},
                 {"bufferView": 2, "componentType": 5126, "count": vertices, "type": "VEC2"}]
    primitives = []
    for index in range(len(materials)):
        indices = struct.pack("<6H", *(index * 4 + i for i in (0, 1, 2, 0, 2, 3)))
        views.append({"buffer": 0, "byteOffset": len(data), "byteLength": len(indices)})
        accessors.append({"bufferView": len(views) - 1, "componentType": 5123, "count": 6, "type": "SCALAR"})
        data += indices + b"\0\0"
        primitives.append({"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
                           "indices": len(accessors) - 1, "material": index})
    document = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
                "meshes": [{"primitives": primitives}], "materials": list(materials),
                "accessors": accessors, "bufferViews": views,
                "buffers": [{"byteLength": len(data),
                             "uri": "data:application/octet-stream;base64," + base64.b64encode(data).decode()}]}
    if images:
        document["images"] = [{"uri": "data:image/png;base64," + base64.b64encode(png(c)).decode()} for c in images]
        document["textures"] = [{"source": index} for index in range(len(images))]
    return document


def pbr(**values):
    return {"pbrMetallicRoughness": values}


# An image value of 188 is 0.737 read as data, and 0.503 read as sRGB.
DATA, SRGB = 188 / 255, ((188 / 255 + 0.055) / 1.055) ** 2.4
ROUGH_DIELECTRIC = {"metallicFactor": 0, "roughnessFactor": 1}
GRAY_BASE = {"baseColorFactor": [0.8, 0.8, 0.8, 1]}


def check_measurements(renderer, report):
    """Each material is rendered alone, so that geometry and view are the same."""
    for path in PATHS:

        def render(name, material, images=(), normal=(0, 0, 1)):
            asset = renderer.output / f"measure_{name}.gltf"
            asset.write_text(json.dumps(quad_document([material], images, normal)), encoding="utf-8")
            result = renderer.record(f"measure_{name}", asset, path)
            return result.sample(0) if result else (0, 0, 0, 0)

        image = {"index": 0}
        base_image = render("base_image", pbr(baseColorTexture=image, **ROUGH_DIELECTRIC), [(188, 188, 188, 255)])
        base_srgb = render("base_srgb", pbr(baseColorFactor=[SRGB, SRGB, SRGB, 1], **ROUGH_DIELECTRIC))
        base_data = render("base_data", pbr(baseColorFactor=[DATA, DATA, DATA, 1], **ROUGH_DIELECTRIC))
        report.same(f"{path} base color image reads as sRGB", base_image, base_srgb)
        report.different(f"{path} base color image does not read as data", base_image, base_data)

        rough_image = render("rough_image", pbr(metallicRoughnessTexture=image, metallicFactor=1, roughnessFactor=1,
                                                **GRAY_BASE), [(0, 188, 0, 255)])
        rough_data = render("rough_data", pbr(metallicFactor=0, roughnessFactor=DATA, **GRAY_BASE))
        rough_srgb = render("rough_srgb", pbr(metallicFactor=0, roughnessFactor=SRGB, **GRAY_BASE))
        report.same(f"{path} roughness from G reads as data", rough_image, rough_data)
        report.different(f"{path} roughness from G does not read as sRGB", rough_image, rough_srgb, 5)

        metal_image = render("metal_image", pbr(metallicRoughnessTexture=image, metallicFactor=1, roughnessFactor=1,
                                                **GRAY_BASE), [(0, 255, 188, 255)])
        metal_data = render("metal_data", pbr(metallicFactor=DATA, roughnessFactor=1, **GRAY_BASE))
        metal_srgb = render("metal_srgb", pbr(metallicFactor=SRGB, roughnessFactor=1, **GRAY_BASE))
        report.same(f"{path} metallic from B reads as data", metal_image, metal_data)
        report.different(f"{path} metallic from B does not read as sRGB", metal_image, metal_srgb, 5)

        shared_image = render("shared_image", pbr(baseColorTexture=image, metallicRoughnessTexture=image,
                                                  metallicFactor=1, roughnessFactor=1), [(188, 188, 188, 255)])
        shared_factor = render("shared_factor", pbr(baseColorFactor=[SRGB, SRGB, SRGB, 1], metallicFactor=DATA,
                                                    roughnessFactor=DATA))
        report.same(f"{path} one image reads as both color and data", shared_image, shared_factor)

        black = {"baseColorFactor": [0, 0, 0, 1], "metallicFactor": 0}
        emissive_image = render("emissive_image", {**pbr(**black), "emissiveFactor": [1, 1, 1], "emissiveTexture": image},
                                [(188, 188, 188, 255)])
        emissive_factor = render("emissive_factor", {**pbr(**black), "emissiveFactor": [SRGB, SRGB, SRGB]})
        report.same(f"{path} emissive image reads as sRGB", emissive_image, emissive_factor)

        occlusion_none = render("occlusion_none", pbr(metallicFactor=0, **GRAY_BASE))
        occlusion_image = render("occlusion_image", {**pbr(metallicFactor=0, **GRAY_BASE), "occlusionTexture": image},
                                 [(188, 188, 188, 255)])
        occlusion_black = render("occlusion_black", {**pbr(metallicFactor=0, **GRAY_BASE),
                                                     "occlusionTexture": {"index": 0, "strength": 1 - DATA}},
                                 [(0, 0, 0, 255)])
        report.same(f"{path} occlusion reads as data", occlusion_image, occlusion_black)
        if path == "preview":
            report.different("preview occlusion darkens", occlusion_none, occlusion_image)
        else:
            report.same("mtlx occlusion has no visible effect", occlusion_none, occlusion_image)

        # normal_textures barely separates scales from its off-axis view. This image tilts the
        # normal 45 degrees toward +X, and the light is at the camera, so brightness follows the tilt.
        tilt = [c / 127.5 - 1 for c in (218, 128, 218)]
        half = (tilt[0] * 0.5, tilt[1] * 0.5, tilt[2])
        unit = tuple(c / sum(v * v for v in half) ** 0.5 for c in half)
        matte = pbr(metallicFactor=0, roughnessFactor=1, **GRAY_BASE)
        scale_half = render("normal_scale_half", {**matte, "normalTexture": {"index": 0, "scale": 0.5}},
                            [(218, 128, 218, 255)])
        scale_full = render("normal_scale_full", {**matte, "normalTexture": {"index": 0}}, [(218, 128, 218, 255)])
        scale_reference = render("normal_scale_reference", matte, normal=unit)
        report.same(f"{path} normal scale 0.5 matches its reference", scale_half, scale_reference)
        report.different(f"{path} normal scale changes the result", scale_half, scale_full)

        shared = renderer.output / "shared_positions.gltf"
        red, blue = pbr(baseColorFactor=[1, 0, 0, 1], metallicFactor=0), pbr(baseColorFactor=[0, 0, 1, 1], metallicFactor=0)
        shared.write_text(json.dumps(quad_document([red, blue])), encoding="utf-8")
        positions = renderer.record("shared_positions", shared, path)
        if positions:
            report.color("first primitive sharing POSITION", positions, 0, (0.2, 0.5), "R")
            report.color("second primitive sharing POSITION", positions, 1, (0.8, 0.5), "B")


def check_reflections(renderer, report):
    """Repeats usdview's backface culling, which usdrecord cannot do, with the same engine call."""
    application = QtGui.QGuiApplication.instance() or QtGui.QGuiApplication(["check_display"])
    surface = QtGui.QOffscreenSurface()
    surface.create()
    context = QtGui.QOpenGLContext()
    if not context.create() or not context.makeCurrent(surface):
        report.expect("reflections", False, "no OpenGL context")
        return
    print(f"OpenGL renderer: {context.functions().glGetString(0x1F01)}", flush=True)

    session = Sdf.Layer.CreateAnonymous(".usda")
    stage = Usd.Stage.Open(Sdf.Layer.FindOrOpen(str(FIXTURES / "reflections.gltf")), session)
    stage.SetEditTarget(session)
    author_framing_camera(stage)
    camera = UsdGeom.Camera(stage.GetPrimAtPath(CAMERA)).GetCamera()
    width, height = IMAGE_WIDTH, round(IMAGE_WIDTH / camera.aspectRatio)

    for cull, name in ((UsdImagingGL.CullStyle.CULL_STYLE_BACK_UNLESS_DOUBLE_SIDED, "culled"),
                       (UsdImagingGL.CullStyle.CULL_STYLE_NOTHING, "unculled")):
        framebuffer_format = QtOpenGL.QOpenGLFramebufferObjectFormat()
        framebuffer_format.setAttachment(QtOpenGL.QOpenGLFramebufferObject.Attachment.Depth)
        framebuffer = QtOpenGL.QOpenGLFramebufferObject(width, height, framebuffer_format)
        framebuffer.bind()
        functions = context.functions()
        functions.glViewport(0, 0, width, height)
        functions.glClearColor(0, 0, 0, 1)
        functions.glClear(0x4100)  # Color and depth.

        engine = UsdImagingGL.Engine()
        engine.SetRendererAov("color")
        engine.SetRenderBufferSize(Gf.Vec2i(width, height))
        engine.SetFraming(CameraUtil.Framing(Gf.Rect2i(Gf.Vec2i(0, 0), width, height)))
        engine.SetCameraPath(CAMERA)
        light = Glf.SimpleLight()
        position = camera.transform.ExtractTranslation()
        light.position = Gf.Vec4f(position[0], position[1], position[2], 1)
        engine.SetLightingState([light], Glf.SimpleMaterial(), Gf.Vec4f(0.1, 0.1, 0.1, 1))
        parameters = UsdImagingGL.RenderParams()
        parameters.cullStyle = cull
        parameters.gammaCorrectColors = False
        parameters.enableSampleAlphaToCoverage = False
        parameters.clearColor = Gf.Vec4f(0, 0, 0, 1)
        engine.Render(stage.GetPseudoRoot(), parameters)
        framebuffer.bind()
        image = framebuffer.toImage()
        image.save(str(renderer.output / f"reflections_{name}.png"))
        del engine

        render = Render(f"reflections {name}", image, stage)
        # The first four triangles face the camera, and the fifth is reversed.
        for mesh in range(4):
            report.expect(f"{render.label} front-facing {mesh} is visible", max(render.sample(mesh)[:3]) >= LOW,
                          f"{render.sample(mesh)}")
            if mesh:
                # The light is a point at the camera, so position changes the result a little.
                report.same(f"{render.label} {mesh} lit like the first", render.sample(mesh), render.sample(0), 8)
        control = max(render.sample(4)[:3])
        if name == "culled":
            report.expect(f"{render.label} reversed control disappears", control == 0, f"{render.sample(4)}")
        else:
            report.expect(f"{render.label} reversed control is visible", control >= LOW, f"{render.sample(4)}")
    context.doneCurrent()
    del application


def compare_paths(report, name, renders, samples, tolerance):
    if not all(renders.values()):
        return
    for mesh, point in samples:
        report.same(f"{name} paths agree on mesh {mesh} at {point}", renders["mtlx"].sample(mesh, point),
                    renders["preview"].sample(mesh, point), tolerance)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("build", type=pathlib.Path, help="the build directory holding plugin/resources")
    parser.add_argument("--output", type=pathlib.Path, help="where images go; defaults to display_check in the build")
    parser.add_argument("--verbose", action="store_true", help="print every comparison, not only failures")
    arguments = parser.parse_args()

    plugin = (arguments.build / "plugin" / "resources" / "usdGltfFileFormat").resolve()
    if not Plug.Registry().RegisterPlugins(str(plugin)) and not Sdf.FileFormat.FindByExtension("gltf"):
        sys.exit(f"No plugin was found in {plugin}. Build the workspace first.")
    output = (arguments.output or arguments.build / "display_check").resolve()
    output.mkdir(parents=True, exist_ok=True)

    report = Report(arguments.verbose)
    renderer = Renderer(output, plugin, report)
    for check in (check_reflections, check_alpha_modes, check_base_color_textures, check_pbr_textures,
                  check_normal_textures, check_texture_transforms, check_ktx2_textures, check_measurements):
        print(check.__name__.removeprefix("check_"), flush=True)
        check(renderer, report)

    print(f"{report.passed} comparisons passed, {len(report.failures)} failed. Images and logs are in {output}")
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
