# SPDX-License-Identifier: Apache-2.0
"""Probe how animated glTF material properties can reach Storm, before the importer converts them.

Run in the lookdev environment, with a built workspace:
    python tests/probe_material_animation.py build/cy2026-windows-x86_64-py313-usd

Each case is a one-quad glTF whose material property moves linearly between two keyframes, at 0 and
2 seconds. The plugin converts the static file at each value, and each candidate below animates the
converted networks in a session layer. usdrecord renders the animated stage at several time codes in
one run, as a player would, and every frame is compared with the static file at that time's value,
converted and rendered on its own. It is the experiment behind
docs/adr/0044-animated-material-properties.md, not a test of the importer. Images, USD files and a JSON report go in material_animation_probe under the build
directory.

Candidates:
- keyframes: every input whose converted value differs between the keyframes gets their two values
  as time samples, so USD interpolates the converted value linearly.
- pivot: a texture transform is converted about the point (0, 1), so that each glTF value maps to
  one input by an affine function, and gets only its keyframes.
- dense: the converted values at 16 equal pieces of the segment.
- timeNode: MaterialX only; base_color is driven by a `time` node, without time samples.
"""

import argparse
import base64
import copy
import json
import math
import pathlib
import struct
import subprocess
import sys
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import check_display as display  # noqa: E402
from pxr import Gf, Plug, Sdf, Usd, UsdShade  # noqa: E402

END_SECONDS = 2.0
TIME_CODES_PER_SECOND = 24
TIMES = (0, 6, 12, 24, 36, 47, 48)
DENSE_PIECES = 16
GRID = [(x, y) for x in (0.2, 0.5, 0.8) for y in (0.2, 0.5, 0.8)]
STRENGTH = "KHR_materials_emissive_strength"
TRANSFORM = "KHR_texture_transform"


def gradient_png():
    """An 8 by 8 image whose red rises to the right and green downwards, so a transform shows."""

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\0" + b"".join(bytes((x * 36, y * 36, 128, 255)) for x in range(8)) for y in range(8))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def lerp(a, b, f):
    if isinstance(a, (list, tuple)):
        return [x + (y - x) * f for x, y in zip(a, b)]
    return a + (b - a) * f


class Case:
    """`material(f)` is the glTF material at fraction f of the segment."""

    def __init__(self, name, material, *, images=(), extensions=(), transform=None):
        self.name = name
        self.material = material
        self.images = images
        self.extensions = extensions
        # (offset, rotation, scale) at fraction f, for the pivot candidate.
        self.transform = transform

    def document(self, f):
        document = display.quad_document([self.material(f)])
        if self.images:
            document["images"] = [{"uri": "data:image/png;base64," + base64.b64encode(data).decode()}
                                  for data in self.images]
            document["textures"] = [{"source": index} for index in range(len(self.images))]
        if self.extensions:
            document["extensionsUsed"] = list(self.extensions)
        return document


def textured_transform(offset, rotation, scale):
    def material(f):
        return display.pbr(metallicFactor=0, roughnessFactor=1, baseColorTexture={
            "index": 0, "extensions": {TRANSFORM: {"offset": lerp(*offset, f), "rotation": lerp(*rotation, f),
                                                   "scale": lerp(*scale, f)}}})

    def transform(f):
        return lerp(*offset, f), lerp(*rotation, f), lerp(*scale, f)

    return material, transform


RED, GREEN = [0.8, 0.05, 0.05, 1], [0.05, 0.8, 0.05, 1]
ROTATION = textured_transform(([0, 0], [0, 0]), (0.2, 1.8), ([1, 1], [1, 1]))
COMBINED = textured_transform(([0.1, 0.2], [0.4, -0.3]), (0.2, 1.8), ([1, 1], [2, 0.5]))
CASES = (
    Case("baseColor", lambda f: display.pbr(metallicFactor=0, roughnessFactor=1, baseColorFactor=lerp(RED, GREEN, f))),
    Case("baseColorTexture", lambda f: display.pbr(metallicFactor=0, roughnessFactor=1, baseColorFactor=lerp(RED, GREEN, f),
                                                   baseColorTexture={"index": 0}),
         images=(display.png((255, 255, 255, 255)),)),
    Case("emissiveProduct", lambda f: {**display.pbr(metallicFactor=0, roughnessFactor=1, baseColorFactor=[0, 0, 0, 1]),
                                       "emissiveFactor": lerp([0.1, 0.1, 0.1], [0.5, 0.5, 0.5], f),
                                       "extensions": {STRENGTH: {"emissiveStrength": lerp(1.2, 1.8, f)}}},
         extensions=(STRENGTH,)),
    Case("rotation", ROTATION[0], images=(gradient_png(),), extensions=(TRANSFORM,), transform=ROTATION[1]),
    Case("transform", COMBINED[0], images=(gradient_png(),), extensions=(TRANSFORM,), transform=COMBINED[1]),
)


def convert(output, name, document):
    asset = output / f"{name}.gltf"
    asset.write_text(json.dumps(document), encoding="utf-8")
    return asset


def material_inputs(stage):
    """{(shader path, input name): (value, connections)} of every shader under /Root/Materials."""
    inputs = {}
    for prim in Usd.PrimRange(stage.GetPrimAtPath("/Root/Materials")):
        shader = UsdShade.Shader(prim)
        if not shader:
            continue
        for shader_input in shader.GetInputs():
            attribute = shader_input.GetAttr()
            inputs[(str(prim.GetPath()), shader_input.GetBaseName())] = (
                attribute.Get(), tuple(str(c) for c in attribute.GetConnections()))
    return inputs


def changing_inputs(stages):
    """Inputs whose value differs across the stages, which must share their structure."""
    first = material_inputs(stages[0])
    for stage in stages[1:]:
        other = material_inputs(stage)
        if other.keys() != first.keys() or any(other[k][1] != first[k][1] for k in first):
            raise RuntimeError("the converted networks differ in structure")
    keys = [k for k in first if any(material_inputs(s)[k][0] != first[k][0] for s in stages[1:])]
    return {k: [material_inputs(s)[k][0] for s in stages] for k in keys}


def author_samples(stage, inputs, times):
    count = 0
    for (path, name), values in inputs.items():
        attribute = UsdShade.Shader(stage.GetPrimAtPath(path)).GetInput(name).GetAttr()
        for time, value in zip(times, values):
            attribute.Set(value, Usd.TimeCode(time))
            count += 1
    return count


def author_pivot(stage, case):
    """Inserts a translation by (0, -1) before each transform, and samples its glTF values."""
    count = 0
    keys = (0, END_SECONDS * TIME_CODES_PER_SECOND)
    values = [case.transform(f) for f in (0, 1)]
    for prim in list(Usd.PrimRange(stage.GetPrimAtPath("/Root/Materials"))):
        shader = UsdShade.Shader(prim)
        identifier = shader.GetShaderId() if shader else None
        if identifier not in ("UsdTransform2d", "ND_multiply_vector2") or (
                identifier == "ND_multiply_vector2" and not prim.GetName().endswith("ImageScale")):
            continue
        source_input = shader.GetInput("in" if identifier == "UsdTransform2d" else "in1")
        source = source_input.GetAttr().GetConnections()[0]
        pivot_path = prim.GetPath().GetParentPath().AppendChild(prim.GetName() + "Pivot")
        pivot = UsdShade.Shader.Define(stage, pivot_path)
        float2 = Sdf.ValueTypeNames.Float2
        if identifier == "UsdTransform2d":
            pivot.CreateIdAttr("UsdTransform2d")
            pivot.CreateInput("in", float2).GetAttr().SetConnections([source])
            pivot.CreateInput("translation", float2).Set(Gf.Vec2f(0, -1))
            source_input.GetAttr().SetConnections([pivot.CreateOutput("result", float2).GetAttr().GetPath()])
            for key, (offset, rotation, scale) in zip(keys, values):
                shader.GetInput("scale").Set(Gf.Vec2f(*scale), key)
                shader.GetInput("rotation").Set(math.degrees(rotation), key)
                shader.GetInput("translation").Set(Gf.Vec2f(offset[0], 1 - offset[1]), key)
                count += 3
        else:
            graph = prim.GetPath().GetParentPath()
            stem = prim.GetName().removesuffix("Scale")
            rotate = UsdShade.Shader(stage.GetPrimAtPath(graph.AppendChild(stem + "Rotate")))
            translate = UsdShade.Shader(stage.GetPrimAtPath(graph.AppendChild(stem + "Translate")))
            pivot.CreateIdAttr("ND_add_vector2")
            pivot.CreateInput("in1", float2).GetAttr().SetConnections([source])
            pivot.CreateInput("in2", float2).Set(Gf.Vec2f(0, -1))
            source_input.GetAttr().SetConnections([pivot.CreateOutput("out", float2).GetAttr().GetPath()])
            for key, (offset, rotation, scale) in zip(keys, values):
                shader.GetInput("in2").Set(Gf.Vec2f(*scale), key)
                rotate.GetInput("amount").Set(-math.degrees(rotation), key)
                translate.GetInput("in2").Set(Gf.Vec2f(offset[0], 1 - offset[1]), key)
                count += 3
    return count


def author_time_node(stage):
    """base_color = mix(red, green, clamp(time / 2)), from MaterialX's time node."""
    count = 0
    for prim in list(Usd.PrimRange(stage.GetPrimAtPath("/Root/Materials"))):
        shader = UsdShade.Shader(prim)
        if not shader or shader.GetShaderId() != "ND_gltf_pbr_surfaceshader":
            continue
        graph = prim.GetPath().GetParentPath()
        float1, color3 = Sdf.ValueTypeNames.Float, Sdf.ValueTypeNames.Color3f

        def node(name, identifier):
            result = UsdShade.Shader.Define(stage, graph.AppendChild(name))
            result.CreateIdAttr(identifier)
            return result

        time = node("probeTime", "ND_time_float")
        time.CreateInput("fps", float1).Set(float(TIME_CODES_PER_SECOND))
        fraction = node("probeFraction", "ND_divide_float")
        fraction.CreateInput("in1", float1).ConnectToSource(time.CreateOutput("out", float1))
        fraction.CreateInput("in2", float1).Set(END_SECONDS)
        clamp = node("probeClamp", "ND_clamp_float")
        clamp.CreateInput("in", float1).ConnectToSource(fraction.CreateOutput("out", float1))
        mix = node("probeMix", "ND_mix_color3")
        mix.CreateInput("bg", color3).Set(Gf.Vec3f(*RED[:3]))
        mix.CreateInput("fg", color3).Set(Gf.Vec3f(*GREEN[:3]))
        mix.CreateInput("mix", float1).ConnectToSource(clamp.CreateOutput("out", float1))
        shader.GetInput("base_color").GetAttr().SetConnections([mix.CreateOutput("out", color3).GetAttr().GetPath()])
        count += 1
    return count


def record_frames(renderer, name, asset, path, author):
    """Renders the animated stage at TIMES in one usdrecord run; returns {time: Render} and samples."""
    session_path, stage = renderer.session(name, asset, path, None)
    samples = author(stage)
    stage.GetSessionLayer().Export(str(session_path))
    pattern = renderer.output / f"{name}_{path}.###.png"
    for time in TIMES:
        (renderer.output / f"{name}_{path}.{time:03d}.png").unlink(missing_ok=True)
    result = subprocess.run(
        [renderer.usdrecord, "--camera", display.CAMERA, "--sessionLayer", str(session_path), "--imageWidth",
         str(display.IMAGE_WIDTH), "--frames", ",".join(str(t) for t in TIMES), str(asset), str(pattern)],
        env=renderer.environment, capture_output=True, text=True, encoding="utf-8", errors="replace")
    (renderer.output / f"{name}_{path}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    errors = [line for line in (result.stdout + result.stderr).splitlines() if "Error" in line or "Failed to" in line]
    renderer.report.expect(f"{name} {path} renders", result.returncode == 0, f"exit code {result.returncode}")
    renderer.report.expect(f"{name} {path} reports no errors", not errors, errors[0] if errors else "none")
    frames = {}
    for time in TIMES:
        image = display.QtGui.QImage(str(renderer.output / f"{name}_{path}.{time:03d}.png"))
        frames[time] = None if image.isNull() else display.Render(f"{name} {path} at {time}", image, stage)
    return frames, samples


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("build", type=pathlib.Path)
    parser.add_argument("--cases", nargs="*", help="names of the cases to run; all by default")
    arguments = parser.parse_args()
    plugin = (arguments.build / "plugin" / "resources" / "usdGltfFileFormat").resolve()
    if not Plug.Registry().RegisterPlugins(str(plugin)) and not Sdf.FileFormat.FindByExtension("gltf"):
        sys.exit(f"No plugin was found in {plugin}. Build the workspace first.")
    output = (arguments.build / "material_animation_probe").resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = display.Report(verbose=False)
    renderer = display.Renderer(output, plugin, report)
    end = END_SECONDS * TIME_CODES_PER_SECOND
    results = []

    for case in CASES:
        if arguments.cases and case.name not in arguments.cases:
            continue
        fractions = [i / DENSE_PIECES for i in range(DENSE_PIECES + 1)]
        assets = {f: convert(output, f"{case.name}_{f:.4f}", case.document(f)) for f in fractions}
        stages = {f: Usd.Stage.Open(str(asset)) for f, asset in assets.items()}
        keyframe_inputs = changing_inputs([stages[0], stages[1]])
        dense_inputs = changing_inputs([stages[f] for f in fractions])
        candidates = {
            "keyframes": lambda stage: author_samples(stage, keyframe_inputs, (0, end)),
            "dense": lambda stage: author_samples(stage, dense_inputs, [f * end for f in fractions]),
        }
        if case.transform:
            candidates["pivot"] = lambda stage: author_pivot(stage, case)
        if case.name == "baseColor":
            candidates["timeNode"] = author_time_node

        references = {}
        for path in display.PATHS:
            for time in TIMES:
                f = time / end
                asset = convert(output, f"{case.name}_reference_{time:03d}", case.document(f))
                references[(path, time)] = renderer.record(f"{case.name}_reference_{time:03d}", asset, path)

        for candidate, author in candidates.items():
            for path in display.PATHS:
                if candidate == "timeNode" and path == "preview":
                    continue
                frames, samples = record_frames(renderer, f"{case.name}_{candidate}", assets[0], path, author)
                for time in TIMES:
                    render, reference = frames[time], references[(path, time)]
                    if not render or not reference:
                        continue
                    worst = max(abs(x - y) for point in GRID
                                for x, y in zip(render.sample(0, point), reference.sample(0, point)))
                    start = references[(path, 0)]
                    from_start = max(abs(x - y) for point in GRID
                                     for x, y in zip(render.sample(0, point), start.sample(0, point)))
                    results.append({"case": case.name, "candidate": candidate, "path": path, "time": time,
                                    "worst": worst, "fromStart": from_start, "timeSamples": samples})
                    print(f"{case.name:16} {candidate:9} {path:7} t={time:2}  worst {worst:3}  "
                          f"from t=0 {from_start:3}  samples {samples}")

    (output / "report.json").write_text(json.dumps({"results": results, "failures": report.failures}, indent=1),
                                        encoding="utf-8")
    print(f"{len(report.failures)} render failures. Report in {output / 'report.json'}")
    for failure in report.failures:
        print("  ", failure)
    return 0


if __name__ == "__main__":
    sys.exit(main())
