# SPDX-License-Identifier: Apache-2.0
"""Compare Storm shading with the Khronos Sample Renderer's tangent-frame shaders.

Run with the OpenStrata lookdev runtime, PyOpenGL, NumPy and Node.js. Pass a built
workspace and a checkout of the Khronos glTF-Sample-Renderer at the revision
listed in the development guide. The probe compares directions with a shading
control in Storm, without comparing lighting across renderers.
"""

import argparse
import base64
import ctypes
import json
import math
import pathlib
import struct
import subprocess

from OpenGL import GL
import numpy as np
from pxr import Gf, Glf, Plug, Sdf, Usd, UsdGeom, UsdShade, Vt
from PySide6 import QtOpenGL

import check_display as display


POINTS = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
TRIANGLES = (0, 1, 2, 0, 2, 3)
NORMAL = (0, 0, 1)
NORMAL_TEXEL = (160, 218, 218, 255)
ROTATION = math.pi / 6
STRENGTH = 0.85
CASE_SPECS = (
    ("supplied", False, (0, 1, 0, 1), (1, 1, 1)),
    ("negative_w", False, (0, 1, 0, -1), (1, 1, 1)),
    ("generated", False, None, (1, 1, 1)),
    ("mirrored_uv", True, None, (1, 1, 1)),
    ("supplied_nonuniform", False, (math.sqrt(0.5), math.sqrt(0.5), 0, -1), (2, 0.5, 1)),
    ("generated_nonuniform", False, None, (2, 0.5, 1)),
)


def coordinates(mirrored):
    # A diagonal parameterization makes a nonuniform scale change the tangent direction.
    return [(1 - (x + y) / 2 if mirrored else (x + y) / 2, (1 + x - y) / 2)
            for x, y, _ in POINTS]


def reference_tangents(directory, uvs):
    """Use the renderer's Wasm module and the handedness conversion in primitive.js."""
    data = [[component for index in TRIANGLES for component in values[index]]
            for values in (POINTS, [NORMAL] * 4, uvs)]
    script = """
import {readFileSync} from 'node:fs';
const [js, wasm] = process.argv.slice(1);
const source = readFileSync(js).toString('base64');
const {default: init, generateTangents} = await import('data:text/javascript;base64,' + source);
await init(readFileSync(wasm));
const data = JSON.parse(readFileSync(0, 'utf8')).map(a => Float32Array.from(a));
const result = generateTangents(...data);
for (let i = 3; i < result.length; i += 4) result[i] = -result[i];
process.stdout.write(JSON.stringify(Array.from(result)));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(directory / "mikktspace.js"),
         str(directory / "mikktspace_bg.wasm")],
        input=json.dumps(data), encoding="utf-8", capture_output=True, check=True)
    return np.asarray(json.loads(result.stdout), dtype=np.float32).reshape(-1, 4)


def reference_directions(source, uvs, tangents, scale):
    """Draw the vertex TBN and fragment normal/anisotropy math from the reference renderer."""
    vertex, textures, functions, material = source
    version = "#version 330 core\n"
    defines = """#define HAS_NORMAL_VEC3
#define HAS_TANGENT_VEC4
#define HAS_TEXCOORD_0_VEC2
#define HAS_NORMAL_MAP
#define MATERIAL_ANISOTROPY
#define DEBUG 0
#define DEBUG_TANGENT_W 1
#define DEBUG_VERT 0
#define DEBUG_VERT_TANGENT_W 1
"""
    # The fixture has no animation, skinning, or morphing. The corresponding upstream include
    # contributes no active code. Use the reference's debug normal encoding for both directions.
    vertex = vertex.replace("#include <animation.glsl>", "")
    fragment = """
uniform int showAxis;
out vec4 color;
void main() {
    NormalInfo frame = getNormalInfo(vec3(0, 0, 1));
    MaterialInfo info;
    info = getAnisotropyInfo(info, frame);
    vec3 direction = showAxis == 1 ? normalize(info.anisotropicT) : frame.n;
    color = vec4(direction * 0.5 + 0.5, 1);
}
"""
    program = QtOpenGL.QOpenGLShaderProgram()
    for stage, code in ((QtOpenGL.QOpenGLShader.Vertex, vertex),
                        (QtOpenGL.QOpenGLShader.Fragment, textures + functions + material + fragment)):
        if not program.addShaderFromSourceCode(stage, version + defines + code):
            raise RuntimeError(program.log())
    if not program.link() or not program.bind():
        raise RuntimeError(program.log())

    size = 32
    format_ = QtOpenGL.QOpenGLFramebufferObjectFormat()
    format_.setInternalTextureFormat(GL.GL_RGBA32F)
    framebuffer = QtOpenGL.QOpenGLFramebufferObject(size, size, format_)
    if not framebuffer.isValid() or not framebuffer.bind():
        raise RuntimeError("Could not bind floating-point framebuffer")
    vao = GL.glGenVertexArrays(1)
    GL.glBindVertexArray(vao)
    buffers = GL.glGenBuffers(4)
    texture = GL.glGenTextures(1)
    try:
        for buffer, name, values in zip(buffers, ("a_position", "a_normal", "a_texcoord_0", "a_tangent"),
                                        (np.asarray(POINTS)[list(TRIANGLES)], [NORMAL] * 6,
                                         np.asarray(uvs)[list(TRIANGLES)], tangents)):
            array = np.ascontiguousarray(values, dtype=np.float32)
            index = program.attributeLocation(name)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, buffer)
            GL.glBufferData(GL.GL_ARRAY_BUFFER, array.nbytes, array, GL.GL_STATIC_DRAW)
            GL.glEnableVertexAttribArray(index)
            GL.glVertexAttribPointer(index, array.shape[1], GL.GL_FLOAT, False, 0, ctypes.c_void_p(0))
        model = np.diag([*scale, 1]).astype(np.float32)
        projection = np.diag([1.5 / scale[0], 1.5 / scale[1], 1, 1]).astype(np.float32)
        projection[:2, 3] = -0.75
        for name, matrix in (("u_ModelMatrix", model), ("u_NormalMatrix", np.linalg.inv(model).T),
                             ("u_ViewProjectionMatrix", projection)):
            GL.glUniformMatrix4fv(program.uniformLocation(name), 1, True, matrix)
        GL.glUniform1f(program.uniformLocation("u_NormalScale"), 1.0)
        GL.glUniform3f(program.uniformLocation("u_Anisotropy"), math.cos(ROTATION), math.sin(ROTATION), STRENGTH)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, 1, 1, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE,
                        bytes(NORMAL_TEXEL))
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
        GL.glUniform1i(program.uniformLocation("u_NormalSampler"), 0)
        GL.glViewport(0, 0, size, size)
        GL.glDisable(GL.GL_BLEND)
        GL.glDisable(GL.GL_CULL_FACE)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_FRAMEBUFFER_SRGB)
        results = []
        for axis in (0, 1):
            GL.glUniform1i(program.uniformLocation("showAxis"), axis)
            GL.glClearColor(0, 0, 0, 0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, 6)
            rgb = GL.glReadPixels(18, 14, 1, 1, GL.GL_RGBA, GL.GL_FLOAT)[0][0]
            if rgb[3] != 1:
                raise RuntimeError("Reference quad did not cover the sample")
            results.append(tuple(float(v) for v in rgb[:3] * 2 - 1))
        return results
    finally:
        GL.glDeleteTextures([texture])
        GL.glDeleteBuffers(4, buffers)
        GL.glDeleteVertexArrays(1, [vao])
        framebuffer.release()
        program.release()


def fixture(uvs, tangent, scale, mode, control=None):
    anisotropy = mode == "anisotropy"
    material = display.pbr(baseColorFactor=[0, 0, 0, 1] if anisotropy else [0.8, 0.8, 0.8, 1],
                           metallicFactor=0, roughnessFactor=0.35 if anisotropy else 1)
    if anisotropy:
        material["extensions"] = {"KHR_materials_anisotropy": {
            "anisotropyStrength": STRENGTH, "anisotropyRotation": ROTATION}}
        # Establish the UV frame while keeping the geometric normal for the lobe.
        material["normalTexture"] = {"index": 0, "scale": 0}
    elif control is None:
        material["normalTexture"] = {"index": 0}
    # Convert the reference world normal to a local normal for the geometric control.
    normal = NORMAL if control is None or anisotropy else tuple(np.asarray(control) * scale)
    normal = tuple(Gf.Vec3d(*normal).GetNormalized())
    doc = display.quad_document([material], [NORMAL_TEXEL] if "normalTexture" in material else [], normal)
    data = bytearray(base64.b64decode(doc["buffers"][0]["uri"].split(",", 1)[1]))
    uv_offset = doc["bufferViews"][2]["byteOffset"]
    struct.pack_into("<8f", data, uv_offset, *(v for uv in uvs for v in uv))
    if tangent is not None:
        data.extend(b"\0" * (-len(data) % 4))
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(data), "byteLength": 64})
        doc["accessors"].append({"bufferView": len(doc["bufferViews"]) - 1, "componentType": 5126,
                                 "count": 4, "type": "VEC4"})
        doc["meshes"][0]["primitives"][0]["attributes"]["TANGENT"] = len(doc["accessors"]) - 1
        data.extend(struct.pack("<16f", *(tangent * 4)))
    doc["buffers"][0] = {"byteLength": len(data), "uri": "data:application/octet-stream;base64,"
                         + base64.b64encode(data).decode()}
    doc["nodes"][0]["scale"] = scale
    doc["extensionsUsed"] = ["KHR_lights_punctual"]
    if anisotropy:
        doc["extensionsUsed"].append("KHR_materials_anisotropy")
    doc["extensions"] = {"KHR_lights_punctual": {"lights": [
        {"type": "point", "intensity": display.ILLUMINANCE * (4 if anisotropy else 0.5)}]}}
    doc["nodes"].append({"translation": [scale[0] / 2 + 0.35, scale[1] / 2 + 0.2, 1],
                         "extensions": {"KHR_lights_punctual": {"light": 0}}})
    doc["scenes"][0]["nodes"].append(1)
    return doc


def connect_tangent(stage, direction, transform=False):
    """Give the MaterialX anisotropy control an explicit axis, optionally in object space."""
    mesh = next(p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh))
    UsdGeom.PrimvarsAPI(mesh).CreatePrimvar("reference_tangent", Sdf.ValueTypeNames.Vector3fArray,
                                          UsdGeom.Tokens.vertex).Set(Vt.Vec3fArray([Gf.Vec3f(*direction)] * 4))
    surface = UsdShade.Shader.Get(stage, "/Root/Materials/Material_0/mtlx/surface")
    reader = UsdShade.Shader.Define(stage, surface.GetPath().GetParentPath().AppendChild("referenceTangent"))
    reader.CreateIdAttr("ND_geompropvalue_vector3")
    reader.CreateInput("geomprop", Sdf.ValueTypeNames.String).Set("reference_tangent")
    source = reader.CreateOutput("out", Sdf.ValueTypeNames.Vector3f)
    if transform:
        vector = UsdShade.Shader.Define(stage, surface.GetPath().GetParentPath().AppendChild("worldTangent"))
        vector.CreateIdAttr("ND_transformvector_vector3")
        vector.CreateInput("fromspace", Sdf.ValueTypeNames.String).Set("object")
        vector.CreateInput("tospace", Sdf.ValueTypeNames.String).Set("world")
        vector.CreateInput("in", Sdf.ValueTypeNames.Vector3f).ConnectToSource(source)
        source = vector.CreateOutput("out", Sdf.ValueTypeNames.Vector3f)
    normal = UsdShade.Shader.Define(stage, surface.GetPath().GetParentPath().AppendChild("unitTangent"))
    normal.CreateIdAttr("ND_normalize_vector3")
    normal.CreateInput("in", Sdf.ValueTypeNames.Vector3f).ConnectToSource(source)
    surface.CreateInput("tangent", Sdf.ValueTypeNames.Vector3f).ConnectToSource(
        normal.CreateOutput("out", Sdf.ValueTypeNames.Vector3f))
    surface.GetInput("anisotropy_rotation").Set(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("build", type=pathlib.Path)
    parser.add_argument("reference", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()
    plugin = (args.build / "plugin/resources/usdGltfFileFormat").resolve()
    if not Plug.Registry().RegisterPlugins(str(plugin)) and not Sdf.FileFormat.FindByExtension("gltf"):
        parser.error(f"No plugin found in {plugin}; build the workspace first")
    shaders = args.reference / "source/Renderer/shaders"
    source = [(shaders / filename).read_text(encoding="utf-8") for filename in
              ("primitive.vert", "textures.glsl", "functions.glsl", "material_info.glsl")]
    output = (args.output or args.build / "tangent_frame_probe").resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = display.Report(True)
    renderer = display.Renderer(output, plugin, report)
    qt = display.offscreen_context(report, "tangent frame")
    if not qt:
        return 1
    display.IMAGE_WIDTH = 300
    results = {}

    def render(label, path, mode, doc, axis=None, transform=False):
        asset = output / f"{label}_{path}.gltf"
        asset.write_text(json.dumps(doc), encoding="utf-8")
        _, stage = renderer.session(label, asset, path, None)
        if axis is not None:
            connect_tangent(stage, axis, transform)
        lights = []
        if path == "preview" and mode == "normal":
            # Storm's Preview path receives a simple light through the imaging engine.
            scale = doc["nodes"][0]["scale"]
            light = Glf.SimpleLight()
            light.position = Gf.Vec4f(scale[0] / 2 + 0.35, scale[1] / 2 + 0.2, 1, 1)
            lights.append(light)
        image = display.engine_image(stage, lights, Gf.Vec4f(0, 0, 0, 1))
        image.save(str(output / f"{label}_{path}.png"))
        return display.Render(label, image, stage).sample(0)

    try:
        for name, mirrored, tangent, scale in CASE_SPECS:
            uvs = coordinates(mirrored)
            tangents = ([tangent] * 6 if tangent is not None else
                        reference_tangents(args.reference / "source/libs", uvs))
            normal, axis = reference_directions(source, uvs, tangents, scale)
            case = {"reference_normal": normal, "reference_anisotropy_axis": axis,
                    "reference_tangents": np.asarray(tangents).tolist(), "storm": {}}
            if tangent is None:
                asset = output / f"{name}_frame.gltf"
                asset.write_text(json.dumps(fixture(uvs, tangent, scale, "normal")), encoding="utf-8")
                stage = Usd.Stage.Open(str(asset))
                mesh = next(p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh))
                generated = UsdGeom.PrimvarsAPI(mesh).GetPrimvar("gltf_generatedTangent")
                expected = np.asarray(tangents)
                actual = np.asarray(generated.Get()) if generated else np.empty((0, 4))
                report.expect(f"{name} USD corner tangents agree with Khronos Wasm",
                              actual.shape == expected.shape and np.allclose(actual, expected, atol=1e-6),
                              f"{actual.tolist()} vs {expected.tolist()}")
            for path in display.PATHS:
                for mode, direction in (("normal", normal), ("anisotropy", axis)):
                    label = f"{name}_{mode}"
                    pixel = render(label, path, mode, fixture(uvs, tangent, scale, mode))
                    control_doc = fixture(uvs, tangent, scale, mode, direction)
                    if mode == "anisotropy" and path == "preview":
                        del control_doc["materials"][0]["extensions"]
                    control = render(label + "_control", path, mode, control_doc,
                                     axis if mode == "anisotropy" and path == "mtlx" else None)
                    delta = max(abs(a - b) for a, b in zip(pixel, control))
                    case["storm"][f"{path}_{mode}"] = {"imported": pixel, "control": control, "delta": delta}
                    expected_difference = (
                        (tangent is not None and (mode == "normal" or path == "mtlx")) or
                        (mirrored and path == "mtlx" and mode == "anisotropy"))
                    report.expect(f"{name} {path} {mode}", (delta > display.SAME) == expected_difference,
                                  f"{pixel} vs {control}, delta {delta}")
            results[name] = case
        scale = (2, 0.5, 1)
        local = (math.sqrt(0.5), math.sqrt(0.5), 0)
        world = tuple(a * b for a, b in zip(local, scale))
        doc = fixture(coordinates(False), (*local, 1), scale, "anisotropy")
        local_pixel = render("local_axis", "mtlx", "anisotropy", doc, local)
        world_pixel = render("world_axis", "mtlx", "anisotropy", doc, world)
        graph_pixel = render("transformed_axis", "mtlx", "anisotropy", doc, local, transform=True)
        report.same("Storm transformvector matches the unchanged local axis", graph_pixel, local_pixel)
        report.different("The transformed axis is distinguishable", graph_pixel, world_pixel, minimum=5)
    finally:
        qt[2].doneCurrent()
    (output / "report.json").write_text(json.dumps({"openusd": list(Usd.GetVersion()), "cases": results,
                                                  "transformvector": [local_pixel, world_pixel, graph_pixel],
                                                  "passed": report.passed, "failures": report.failures},
                                                 indent=2) + "\n", encoding="utf-8")
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
