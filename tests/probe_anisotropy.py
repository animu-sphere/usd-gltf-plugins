# SPDX-License-Identifier: Apache-2.0
"""Probe MaterialX anisotropy and the retained glTF tangent frame.

Run in the lookdev environment with a built workspace:
    python tests/probe_anisotropy.py build/cy2026-windows-x86_64-py313-usd

This authors shader inputs in a USD session layer AFTER import. It is an opt-in
renderer experiment, not a test or implementation of KHR_materials_anisotropy.
Images, flattened USD scenes and a JSON report go in anisotropy_probe under the
specified build directory.
"""

import argparse
import base64
import json
import math
import pathlib
import struct

from pxr import Gf, Plug, Sdf, Usd, UsdGeom, UsdShade, Vt

from check_display import (ILLUMINANCE, Render, Renderer, Report, engine_image,
                           offscreen_context, pbr, quad_document)


def document(tangent):
    result = quad_document([pbr(baseColorFactor=[0, 0, 0, 1], metallicFactor=0,
                                roughnessFactor=0.35)])
    data = base64.b64decode(result["buffers"][0]["uri"].split(",", 1)[1])
    data += b"\0" * (-len(data) % 4)
    result["bufferViews"].append({"buffer": 0, "byteOffset": len(data), "byteLength": 64})
    result["accessors"].append({"bufferView": len(result["bufferViews"]) - 1,
                                "componentType": 5126, "count": 4, "type": "VEC4"})
    result["meshes"][0]["primitives"][0]["attributes"]["TANGENT"] = len(result["accessors"]) - 1
    data += struct.pack("<16f", *(tuple(tangent) * 4))
    result["buffers"][0] = {"byteLength": len(data), "uri": "data:application/octet-stream;base64,"
                            + base64.b64encode(data).decode()}
    result["extensionsUsed"] = ["KHR_lights_punctual"]
    result["extensions"] = {"KHR_lights_punctual": {"lights": [
        {"type": "point", "intensity": ILLUMINANCE * 4}]}}
    result["nodes"].append({"translation": [0.85, 0.7, 1],
                            "extensions": {"KHR_lights_punctual": {"light": 0}}})
    result["scenes"][0]["nodes"].append(1)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("build", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    arguments = parser.parse_args()
    plugin = (arguments.build / "plugin/resources/usdGltfFileFormat").resolve()
    if not Plug.Registry().RegisterPlugins(str(plugin)) and not Sdf.FileFormat.FindByExtension("gltf"):
        parser.error(f"No plugin found in {plugin}; build the workspace first")
    output = (arguments.output or arguments.build / "anisotropy_probe").resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = Report(True)
    renderer = Renderer(output, plugin, report)
    qt = offscreen_context(report, "anisotropy probe")
    if not qt:
        return 1
    samples = {}
    theta = math.pi / 6
    x, y = (1, 0, 0), (0, 1, 0)
    positive = (math.cos(theta), math.sin(theta), 0)
    negative = (math.cos(theta), -math.sin(theta), 0)

    def render(name, strength=0.85, rotation=0, shader_tangent=None, gltf_tangent=(1, 0, 0, 1), path="mtlx"):
        asset = output / f"{name}.gltf"
        asset.write_text(json.dumps(document(gltf_tangent)), encoding="utf-8")
        _, stage = renderer.session(name, asset, path, None)
        # Only the MaterialX surface is changed, even when rendering the Preview control.
        shader = next(UsdShade.Shader(prim) for prim in stage.Traverse()
                      if prim.IsA(UsdShade.Shader) and
                      UsdShade.Shader(prim).GetIdAttr().Get() == "ND_gltf_pbr_surfaceshader")
        shader.CreateInput("anisotropy_strength", Sdf.ValueTypeNames.Float).Set(strength)
        shader.CreateInput("anisotropy_rotation", Sdf.ValueTypeNames.Float).Set(rotation)
        if shader_tangent is not None:
            mesh = next(prim for prim in stage.Traverse() if prim.IsA(UsdGeom.Mesh))
            UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
                "probe_tangent", Sdf.ValueTypeNames.Vector3fArray, UsdGeom.Tokens.vertex
            ).Set(Vt.Vec3fArray([Gf.Vec3f(*shader_tangent)] * 4))
            reader = UsdShade.Shader.Define(stage, shader.GetPath().GetParentPath().AppendChild("probeTangent"))
            reader.CreateIdAttr("ND_geompropvalue_vector3")
            reader.CreateInput("geomprop", Sdf.ValueTypeNames.String).Set("probe_tangent")
            shader.CreateInput("tangent", Sdf.ValueTypeNames.Vector3f).ConnectToSource(
                reader.CreateOutput("out", Sdf.ValueTypeNames.Vector3f))
        if not stage.Export(str(output / f"{name}_{path}.usda")):
            raise RuntimeError(f"Could not export {name}")
        image = engine_image(stage, [], Gf.Vec4f(0, 0, 0, 1))
        if not image.save(str(output / f"{name}_{path}.png")):
            raise RuntimeError(f"Could not save {name}")
        sample = Render(f"{name} {path}", image, stage).sample(0)
        samples[f"{name}_{path}"] = sample
        return sample

    try:
        isotropic = render("isotropic", strength=0, shader_tangent=x)
        report.same("zero strength ignores the tangent", render("isotropic_y", strength=0, shader_tangent=y),
                    isotropic)
        along_x = render("along_x", shader_tangent=x)
        along_y = render("along_y", shader_tangent=y)
        report.different("anisotropy changes reflection", along_x, isotropic, minimum=5)
        report.different("the tangent selects the elongated axis", along_x, along_y, minimum=5)
        rotated = render("rotation", rotation=theta, shader_tangent=x)
        ccw = render("ccw_tangent", shader_tangent=positive)
        cw = render("cw_tangent", shader_tangent=negative)
        report.same("positive rotation is counter-clockwise about +Z", rotated, ccw)
        report.different("opposite rotations are distinguishable", ccw, cw)
        derived = render("derived", rotation=theta)
        report.same("the retained glTF tangent is not consumed",
                    render("gltf_rotated", rotation=theta, gltf_tangent=(*positive, 1)),
                    derived)
        report.same("retained glTF handedness is not consumed",
                    render("gltf_mirrored", rotation=theta, gltf_tangent=(1, 0, 0, -1)),
                    derived)
        preview = render("preview_base", strength=0, path="preview")
        report.same("Preview ignores the prototype's MaterialX inputs",
                    render("preview_anisotropic", rotation=theta, shader_tangent=y, path="preview"), preview)
        report.different("ignoring supplied tangents loses a visible direction", along_x, ccw)
    finally:
        qt[2].doneCurrent()
    result = {"experiment": "USD session shader inputs, not glTF anisotropy import",
              "openusd": list(Usd.GetVersion()), "samples": samples,
              "passed": report.passed, "failures": report.failures}
    (output / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"{report.passed} comparisons passed, {len(report.failures)} failed. Results: {output}")
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
