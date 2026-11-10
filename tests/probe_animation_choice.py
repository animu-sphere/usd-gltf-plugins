# SPDX-License-Identifier: Apache-2.0
"""Probe how one of several glTF animations can be chosen, before the importer converts more than one.

Run in the usd environment:
    python tests/probe_animation_choice.py build/cy2026-windows-x86_64-py313-usd [--samples <glTF-Sample-Assets>]

With the plugin on PXR_PLUGINPATH_NAME, it also checks how file format arguments reach a glTF layer.

The prototype reads glTF itself and authors each candidate below with node transforms, skeletons and
one UsdSkelAnimation per glTF animation, at every keyframe. A host stage at 30 time codes per second
references the asset with a layer offset, chooses each animation in the candidate's way, and every
node's local transform and every skeleton joint's, through UsdSkelCache, is compared with glTF's pose
of that animation alone, at its keyframes, before its first and after its last. Interpolation between
keyframes is the subject of docs/adr/0029-node-animation.md, not of this probe. It is the experiment
behind docs/adr/0030-animation-choice.md, not a test of the importer. USD files and a JSON report go
in animation_choice_probe under the build directory.
"""

import argparse
import json
import pathlib
import subprocess
import sys
import time

import numpy as np
from pxr import Gf, Plug, Sdf, Tf, Usd, UsdGeom, UsdSkel, Vt

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from probe_animation import Sampler  # noqa: E402
from probe_skins import (Builder, Gltf, Plan, Variant, primitive_data, quat, sample_path,  # noqa: E402
                         trs_matrix, validate)

TIME_CODES_PER_SECOND = 24
HOST_TIME_CODES_PER_SECOND = 30
OFFSET, SCALE = 12.5, 0.5
SAMPLES = ("Fox", "InterpolationTest", "CesiumMan")
PATHS = ("translation", "rotation", "scale")


class Candidate:
    def __init__(self, name, kind, defaults="inside"):
        self.name = name
        self.kind = kind  # "variants" in one layer, or "layers", one per animation
        self.defaults = defaults  # where a variant's node values hold their defaults


CANDIDATES = (
    Candidate("variantSet", "variants"),
    Candidate("variantSetDefaultsOutside", "variants", defaults="outside"),
    Candidate("layerPerAnimation", "layers"),
)


# --- glTF ---------------------------------------------------------------------------------------

def channels(g, a):
    """{node: {path: Sampler}} of animation a's translation, rotation and scale channels."""
    anim = g.get("animations")[a]
    out = {}
    for c, channel in enumerate(anim["channels"]):
        target = channel["target"]
        if "node" not in target or target["path"] not in PATHS:
            continue
        spec = anim["samplers"][channel["sampler"]]
        out.setdefault(target["node"], {})[target["path"]] = Sampler(
            f"animation {a}/channel {c}", target["path"], spec.get("interpolation", "LINEAR"),
            g.accessor(spec["input"])[:, 0], g.accessor(spec["output"]))
    return out


def keys(by_node):
    """Every keyframe of an animation, in seconds."""
    return sorted({float(t) for by_path in by_node.values() for s in by_path.values() for t in s.times})


def rest_trs(node):
    return [np.array(node.get("translation", [0, 0, 0]), dtype=np.float64),
            np.array(node.get("rotation", [0, 0, 0, 1]), dtype=np.float64) /
            np.linalg.norm(node.get("rotation", [0, 0, 0, 1])),
            np.array(node.get("scale", [1, 1, 1]), dtype=np.float64)]


def trs_at(g, by_node, n, t):
    trs = rest_trs(g.get("nodes")[n])
    for k, path in enumerate(PATHS):
        if path in by_node.get(n, {}):
            v = np.asarray(by_node[n][path](t), dtype=np.float64)
            trs[k] = v / np.linalg.norm(v) if path == "rotation" else v
    return trs


def local_at(g, by_node, n, t):
    node = g.get("nodes")[n]
    if "matrix" in node:
        return np.array(node["matrix"], dtype=np.float64).reshape(4, 4).T
    return trs_matrix(*trs_at(g, by_node, n, t))


# --- The prototype ------------------------------------------------------------------------------

def to_matrix(m):
    return Gf.Matrix4d(np.asarray(m).T.tolist())


def from_matrix(m):
    return np.array(m).T


def define_static(stage, g, plan, moved):
    """Nodes, meshes, skel bindings and skeletons, which no choice of animation changes. Nodes in
    `moved` get translate, orient and scale operations without values; others a matrix. Returns the
    prim path of each kept node and of each skeleton."""
    nodes = g.get("nodes")
    asset = UsdGeom.Xform.Define(stage, "/Asset")
    stage.SetDefaultPrim(asset.GetPrim())
    UsdGeom.Scope.Define(stage, "/Asset/scenes")
    scene = Sdf.Path(f"/Asset/scenes/Scene_{plan.scene}")
    path = {}
    for n in plan.order:
        if n in plan.kept:
            path[n] = (scene if plan.parent[n] is None else path[plan.parent[n]]).AppendChild(f"Node_{n}")
    skeleton_path = {s: (scene if k["space"] is None else path[k["space"]]).AppendChild(f"Skeleton_{s}")
                     for s, k in plan.skeletons.items()}
    roots = set()
    for s, k in plan.skeletons.items():
        members = [path[u].AppendChild(f"Mesh_{nodes[u]['mesh']}") for u in k["users"]] + [skeleton_path[s]]
        prefixes = [list(p.GetPrefixes()) for p in members]
        common = scene
        for level in zip(*prefixes):
            if all(x == level[0] for x in level) and level[0].HasPrefix(scene):
                common = level[0]
            elif not all(x == level[0] for x in level):
                break
        roots.add(common)
    roots = {r for r in roots if not any(o != r and r.HasPrefix(o) for o in roots)}

    def define(p):
        return UsdSkel.Root.Define(stage, p) if p in roots else UsdGeom.Xform.Define(stage, p)

    define(scene)
    rest = {n: local_at(g, {}, n, 0) for n in plan.order}
    for n in plan.order:
        if n not in plan.kept:
            continue
        xform = define(path[n])
        if n in moved:
            xform.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble)
            xform.AddOrientOp(UsdGeom.XformOp.PrecisionFloat)
            xform.AddScaleOp(UsdGeom.XformOp.PrecisionFloat)
        else:
            xform.AddTransformOp().Set(to_matrix(rest[n]))
        node = nodes[n]
        if "mesh" not in node:
            continue
        mesh_path = path[n].AppendChild(f"Mesh_{node['mesh']}")
        define(mesh_path)
        for p, primitive in enumerate(g.get("meshes")[node["mesh"]]["primitives"]):
            data = primitive_data(g, primitive)
            if data is None:
                continue
            mesh = UsdGeom.Mesh.Define(stage, mesh_path.AppendChild(f"Primitive_{p}"))
            mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(data["points"].astype(np.float32)))
            mesh.CreateFaceVertexCountsAttr(Vt.IntArray([3] * (len(data["indices"]) // 3)))
            mesh.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(data["indices"].astype(np.int32)))
            skin = node.get("skin")
            if skin is not None and data["joints"] is not None:
                k = plan.skeletons[skin]
                remap = np.array([k["joints"].index(j) for j in g.get("skins")[skin]["joints"]])
                binding = UsdSkel.BindingAPI.Apply(mesh.GetPrim())
                binding.CreateSkeletonRel().SetTargets([skeleton_path[skin]])
                width = data["joints"].shape[1]
                binding.CreateJointIndicesPrimvar(False, width).Set(
                    Vt.IntArray.FromNumpy(remap[data["joints"]].reshape(-1).astype(np.int32)))
                binding.CreateJointWeightsPrimvar(False, width).Set(
                    Vt.FloatArray.FromNumpy(data["weights"].reshape(-1).astype(np.float32)))
                binding.CreateGeomBindTransformAttr(Gf.Matrix4d(1))
    for s, k in plan.skeletons.items():
        skel = UsdSkel.Skeleton.Define(stage, skeleton_path[s])
        skin = g.get("skins")[s]
        inverse_bind = dict(zip(skin["joints"], g.matrices(skin["inverseBindMatrices"])))
        skel.CreateJointsAttr([plan.token(j) for j in k["joints"]])
        skel.CreateRestTransformsAttr(Vt.Matrix4dArray([to_matrix(rest[j]) for j in k["joints"]]))
        world = {}
        for j in plan.order:
            world[j] = rest[j] if plan.parent[j] is None else world[plan.parent[j]] @ rest[j]
        space = np.eye(4) if k["space"] is None else world[k["space"]]
        skel.CreateBindTransformsAttr(Vt.Matrix4dArray(
            [to_matrix(np.linalg.inv(inverse_bind[j]) if j in inverse_bind else np.linalg.inv(space) @ world[j])
             for j in k["joints"]]))
        UsdSkel.BindingAPI.Apply(skel.GetPrim())
    return path, skeleton_path


def author_animation(stage, g, plan, a, path, skeleton_path, moved, defaults=True):
    """Animation a's values in the current edit target: each node in `moved` gets its own values as
    defaults when `defaults`, and a's channels as time samples; joints a moves get a SkelAnimation
    that every skeleton is bound to."""
    nodes = g.get("nodes")
    by_node = channels(g, a)
    for n in moved:
        if n not in path:
            continue
        ops = UsdGeom.Xformable(stage.GetPrimAtPath(path[n])).GetOrderedXformOps()
        rest = rest_trs(nodes[n])
        for op, path_name, value in zip(ops, PATHS, rest):
            if defaults:
                op.Set(usd_value(path_name, value))
            sampler = by_node.get(n, {}).get(path_name)
            if sampler is not None:
                for t in sampler.times:
                    op.Set(usd_value(path_name, trs_at(g, by_node, n, t)[PATHS.index(path_name)]),
                           float(t) * TIME_CODES_PER_SECOND)
    joints = sorted({j for k in plan.skeletons.values() for j in k["joints"] if j in by_node},
                    key=plan.token)
    if not joints:
        return None
    UsdGeom.Scope.Define(stage, "/Asset/animations")
    anim = UsdSkel.Animation.Define(stage, f"/Asset/animations/Animation_{a}")
    anim.CreateJointsAttr([plan.token(j) for j in joints])
    for t in keys({j: by_node[j] for j in joints}):
        trs = [trs_at(g, by_node, j, t) for j in joints]
        code = t * TIME_CODES_PER_SECOND
        anim.CreateTranslationsAttr().Set(Vt.Vec3fArray([Gf.Vec3f(*v[0]) for v in trs]), code)
        anim.CreateRotationsAttr().Set(Vt.QuatfArray([Gf.Quatf(v[1][3], *v[1][:3]) for v in trs]), code)
        anim.CreateScalesAttr().Set(Vt.Vec3hArray([Gf.Vec3h(*v[2]) for v in trs]), code)
    for s in plan.skeletons:
        UsdSkel.BindingAPI(stage.GetPrimAtPath(skeleton_path[s])).CreateAnimationSourceRel().SetTargets(
            [anim.GetPath()])
    return anim.GetPath()


def usd_value(path, v):
    if path == "translation":
        return Gf.Vec3d(*v)
    if path == "rotation":
        return Gf.Quatf(float(v[3]), *[float(x) for x in v[:3]])
    return Gf.Vec3f(*v)


def end_of(g, a):
    return max(keys(channels(g, a)), default=0.0) * TIME_CODES_PER_SECOND


def new_stage(file):
    file.unlink(missing_ok=True)
    stage = Usd.Stage.CreateNew(str(file))
    stage.SetTimeCodesPerSecond(TIME_CODES_PER_SECOND)
    stage.SetFramesPerSecond(TIME_CODES_PER_SECOND)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    stage.SetStartTimeCode(0)
    return stage


def author(g, candidate, output, label):
    """Writes the candidate's layers and returns the file of each animation's choice."""
    plan = Plan(g, Variant("adopted"))
    count = len(g.get("animations"))
    moved_by = [set(channels(g, a)) & plan.kept for a in range(count)]
    if candidate.kind == "layers":
        files = []
        for a in range(count):
            file = output / f"{label}_{a}.usdc"
            stage = new_stage(file)
            path, skeleton_path = define_static(stage, g, plan, moved_by[a])
            author_animation(stage, g, plan, a, path, skeleton_path, moved_by[a])
            stage.SetEndTimeCode(end_of(g, a))
            stage.GetRootLayer().Save()
            files.append(file)
        return files
    file = output / f"{label}.usdc"
    stage = new_stage(file)
    moved = set().union(*moved_by)
    path, skeleton_path = define_static(stage, g, plan, moved)
    if candidate.defaults == "outside":
        for n in moved:
            ops = UsdGeom.Xformable(stage.GetPrimAtPath(path[n])).GetOrderedXformOps()
            for op, name, value in zip(ops, PATHS, rest_trs(g.get("nodes")[n])):
                op.Set(usd_value(name, value))
    asset = stage.GetPrimAtPath("/Asset")
    variant_set = asset.GetVariantSets().AddVariantSet("animation")
    for a in range(count):
        variant_set.AddVariant(f"Animation_{a}")
        variant_set.SetVariantSelection(f"Animation_{a}")
        with variant_set.GetVariantEditContext():
            author_animation(stage, g, plan, a, path, skeleton_path, moved, defaults=candidate.defaults == "inside")
            asset.SetCustomDataByKey("gltf:endTimeCode", end_of(g, a))
    variant_set.SetVariantSelection("Animation_0")
    stage.SetEndTimeCode(max(end_of(g, a) for a in range(count)))
    stage.GetRootLayer().Save()
    return [file] * count


# --- Checks -------------------------------------------------------------------------------------

def host_stage(file, selection):
    """A stage at another rate that references the asset with an offset, and chooses `selection`, a
    variant name or None."""
    stage = Usd.Stage.CreateInMemory()
    stage.SetTimeCodesPerSecond(HOST_TIME_CODES_PER_SECOND)
    # A layer offset is scaled by the rate of the layer that authors it, so a choice made in the
    # session layer needs the host's rate there too.
    stage.GetSessionLayer().timeCodesPerSecond = HOST_TIME_CODES_PER_SECOND
    prim = stage.DefinePrim("/Host")
    prim.GetReferences().AddReference(Sdf.Reference(str(file), layerOffset=Sdf.LayerOffset(OFFSET, SCALE)))
    if selection is not None:
        prim.GetVariantSets().GetVariantSet("animation").SetVariantSelection(selection)
    return stage


def host_time(seconds):
    return OFFSET + SCALE * seconds * HOST_TIME_CODES_PER_SECOND


def pose_error(stage, g, a, root="/Host"):
    """The largest difference from glTF's pose of animation a alone, over every kept node's local
    transform and every joint's, relative to the largest element of each matrix."""
    plan = Plan(g, Variant("adopted"))
    by_node = channels(g, a)
    times = keys(by_node)
    times = sorted({0.0, *times, times[-1] + 1.0})
    scene = Sdf.Path(f"{root}/scenes/Scene_{plan.scene}")
    path = {}
    for n in plan.order:
        if n in plan.kept:
            path[n] = (scene if plan.parent[n] is None else path[plan.parent[n]]).AppendChild(f"Node_{n}")
    cache = UsdSkel.Cache()
    skeletons = []
    for prim in stage.Traverse():
        if prim.IsA(UsdSkel.Root):
            cache.Populate(UsdSkel.Root(prim), Usd.PrimDefaultPredicate)
    for prim in stage.Traverse():
        if prim.IsA(UsdSkel.Skeleton):
            skeletons.append(cache.GetSkelQuery(UsdSkel.Skeleton(prim)))
    worst, at = 0.0, None
    time_of = host_time if root == "/Host" else (lambda t: t * TIME_CODES_PER_SECOND)
    for t in times:
        code = time_of(t)
        for n, p in path.items():
            actual = from_matrix(UsdGeom.Xformable(stage.GetPrimAtPath(p)).GetLocalTransformation(code))
            expected = local_at(g, by_node, n, t)
            e = float(np.max(np.abs(actual - expected)) / max(1e-9, np.max(np.abs(expected))))
            if e > worst:
                worst, at = e, f"{p.name} at {t:.4g} s"
        for query in skeletons:
            locals_ = query.ComputeJointLocalTransforms(code)
            for token, m in zip(query.GetJointOrder(), locals_):
                n = int(str(token).split("/")[-1].split("_")[1])
                expected = local_at(g, by_node, n, t)
                e = float(np.max(np.abs(from_matrix(m) - expected)) / max(1e-9, np.max(np.abs(expected))))
                if e > worst:
                    worst, at = e, f"joint {token} at {t:.4g} s"
    sources = sorted({str(q.GetAnimQuery().GetPrim().GetPath()) if q.GetAnimQuery() else "none"
                      for q in skeletons})
    return worst, at, sources


def check(g, candidate, files):
    count = len(g.get("animations"))
    result = {"animations": count, "selections": {}}
    for a in range(count):
        selection = f"Animation_{a}" if candidate.kind == "variants" else None
        stage = host_stage(files[a], selection)
        worst, at, sources = pose_error(stage, g, a)
        direct = Usd.Stage.Open(str(files[a]))
        if selection:
            with Usd.EditContext(direct, direct.GetSessionLayer()):
                direct.GetPrimAtPath("/Asset").GetVariantSets().GetVariantSet("animation").SetVariantSelection(
                    selection)
        result["selections"][a] = {
            "error": worst, "at": at, "animationSources": sources,
            "expectedEnd": end_of(g, a), "stageEnd": direct.GetEndTimeCode(),
            "variantEnd": direct.GetPrimAtPath("/Asset").GetCustomDataByKey("gltf:endTimeCode"),
            "validation": validate(direct)}
    # No selection anywhere but the asset's own.
    result["unchosen"] = pose_error(host_stage(files[0], None), g, 0)[:2]
    if candidate.kind == "variants":
        stage = host_stage(files[0], "Animation_99")
        mark = Tf.Error.Mark()
        moved = next((p for p in stage.Traverse()
                      if UsdGeom.Xformable(p) and UsdGeom.Xformable(p).GetOrderedXformOps()
                      and UsdGeom.Xformable(p).GetOrderedXformOps()[0].GetOpType() == UsdGeom.XformOp.TypeTranslate),
                     None)
        if moved is not None:
            m = from_matrix(UsdGeom.Xformable(moved).GetLocalTransformation(0))
            expected = local_at(g, {}, int(moved.GetName().split("_")[1]), 0)
            result["unknownSelection"] = {"node": moved.GetName(), "identity": bool(np.allclose(m, np.eye(4))),
                                          "rest": bool(np.allclose(m, expected, atol=1e-6)),
                                          "errors": len(list(mark.GetErrors()))}
        result["unknownSelectionSources"] = pose_error(stage, g, 0)[2]
        mark.Clear()
        # Switching on an open stage, in its session layer.
        stage = host_stage(files[0], None)
        start = time.perf_counter()
        for a in range(count):
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                stage.GetPrimAtPath("/Host").GetVariantSets().GetVariantSet("animation").SetVariantSelection(
                    f"Animation_{a}")
            worst, _, _ = pose_error(stage, g, a)
            result.setdefault("switched", []).append(worst)
        result["switchSeconds"] = time.perf_counter() - start
    else:
        stage = host_stage(files[0], None)
        start = time.perf_counter()
        for a in range(count):
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                stage.GetPrimAtPath("/Host").GetReferences().SetReferences(
                    [Sdf.Reference(str(files[a]), layerOffset=Sdf.LayerOffset(OFFSET, SCALE))])
            worst, _, _ = pose_error(stage, g, a)
            result.setdefault("switched", []).append(worst)
        result["switchSeconds"] = time.perf_counter() - start
    result["bytes"] = sum(f.stat().st_size for f in sorted(set(files)))
    result["bytesOpenedForOne"] = files[0].stat().st_size
    return result


# --- Format arguments through the plugin ------------------------------------------------------

def rigid_fixture():
    """Two animations of a box, which the plugin converts today: it does not convert skins yet."""
    b = Builder()
    box = b.node(name="box")
    b.doc["scenes"] = [{"nodes": [box]}]
    b.doc["nodes"][box]["mesh"] = b.box(0.5)
    animate(b, [(box, "translation", [[0, 0, 0], [0, 1, 0]])], [0, 1])
    animate(b, [(box, "rotation", [quat([0, 1, 0], 0), quat([0, 1, 0], 90)])], [0, 2])
    return b.finish()


def plugin_checks(output, glb, fixture):
    """How `animation` arguments reach a glTF layer, without the plugin reading them yet."""
    out = {}
    with_args = f"{glb}:SDF_FORMAT_ARGS:animation=1"
    plain = Sdf.Layer.FindOrOpen(str(glb))
    layer = Sdf.Layer.FindOrOpen(with_args)
    out["open"] = bool(layer) and layer.GetFileFormatArguments() == {"animation": "1"}
    out["distinctLayers"] = bool(plain) and plain is not layer and plain.identifier != layer.identifier
    out["reloadKeepsArguments"] = layer.Reload(force=True) and layer.GetFileFormatArguments() == {"animation": "1"}
    stage = Usd.Stage.Open(with_args)
    out["stageOpen"] = bool(stage) and stage.GetRootLayer().GetFileFormatArguments() == {"animation": "1"}

    host = output / "host.usda"
    host.write_text('#usda 1.0\n(\n    defaultPrim = "Host"\n)\n\ndef "Host" (\n'
                    f'    prepend references = @./{fixture.name}:SDF_FORMAT_ARGS:animation=1@\n)\n{{\n}}\n',
                    encoding="utf-8")
    stage = Usd.Stage.Open(str(host))
    used = [l for l in stage.GetUsedLayers() if l.GetFileFormat().formatId == "gltf"]
    out["reference"] = [(pathlib.PurePath(l.identifier.split(":SDF_FORMAT_ARGS:")[0]).name,
                         l.GetFileFormatArguments()) for l in used]
    out["referenceComposes"] = bool(stage.GetPrimAtPath("/Host/scenes"))

    package = output / "host.usdz"
    package.unlink(missing_ok=True)
    writer = Sdf.ZipFileWriter.CreateNew(str(package))
    writer.AddFile(str(host), "host.usda")
    writer.AddFile(str(fixture), fixture.name)
    writer.Save()
    stage = Usd.Stage.Open(str(package))
    used = [l for l in stage.GetUsedLayers() if l.GetFileFormat().formatId == "gltf"]
    out["package"] = [(l.identifier.split("host.usdz")[-1], l.GetFileFormatArguments()) for l in used]
    out["packageComposes"] = bool(stage.GetPrimAtPath("/Host/scenes"))

    printed = output / "usdcat.usda"
    printed.unlink(missing_ok=True)
    usdcat = subprocess.run(["usdcat", with_args, "--out", str(printed)], capture_output=True, text=True,
                            check=False)
    out["usdcat"] = usdcat.returncode == 0 and printed.exists() and "def" in printed.read_text(encoding="utf-8")
    return out


# --- Fixture ------------------------------------------------------------------------------------

def animate(b, channel_list, times, interpolation="LINEAR"):
    times = np.asarray(times, dtype=np.float64).reshape(-1, 1)
    source = b.accessor(times, 5126, "SCALAR", min=[float(times.min())], max=[float(times.max())])
    anim = {"channels": [], "samplers": []}
    for node, path, values in channel_list:
        kind = "VEC4" if path == "rotation" else "VEC3"
        anim["samplers"].append({"input": source, "output": b.accessor(np.array(values), 5126, kind),
                                 "interpolation": interpolation})
        anim["channels"].append({"sampler": len(anim["samplers"]) - 1, "target": {"node": node, "path": path}})
    b.doc["animations"].append(anim)


def choice_fixture():
    """Three animations over one skinned strip and two rigid boxes: they move the same node in
    different properties, a node only one of them moves, the same joints, and have timelines of
    different lengths, one that starts after 0."""
    b = Builder()
    root = b.node(name="root")
    joint0 = b.node(name="j0")
    joint1 = b.node(name="j1", translation=[0, 1, 0])
    skinned = b.node(name="skinned")
    box = b.node(name="box", translation=[2, 0, 0], rotation=quat([0, 1, 0], 10))
    child = b.node(name="child", translation=[0, 1, 0], scale=[1, 2, 1])
    b.doc["nodes"][root]["children"] = [joint0, skinned, box]
    b.doc["nodes"][joint0]["children"] = [joint1]
    b.doc["nodes"][box]["children"] = [child]
    b.doc["scenes"] = [{"nodes": [root]}]
    b.doc["nodes"][skinned]["mesh"] = b.strip((0, 0, 0), 2, [0, 1])
    b.doc["nodes"][skinned]["skin"] = b.skin([joint0, joint1], {})
    b.doc["nodes"][box]["mesh"] = b.box(0.3)
    b.doc["nodes"][child]["mesh"] = b.box(0.2)
    animate(b, [(box, "rotation", [quat([0, 1, 0], 0), quat([0, 1, 0], 90), quat([0, 1, 0], 170)]),
                (joint1, "rotation", [quat([0, 0, 1], 0), quat([0, 0, 1], 40), quat([0, 0, 1], 80)])],
            [0, 0.5, 1])
    animate(b, [(box, "translation", [[2, 0, 0], [2, 1, 0], [3, 1, 0]]),
                (child, "scale", [[1, 1, 1], [1.5, 1, 1], [1, 0.5, 1]]),
                (joint0, "translation", [[0, 0, 0], [0.5, 0, 0], [0.5, 0.5, 0]])],
            [0.5, 1.5, 3])
    animate(b, [(joint1, "rotation", [quat([1, 0, 0], 0), quat([1, 0, 0], -60)])], [0, 2], "STEP")
    return b.finish()


# --- Driver -------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("build", type=pathlib.Path)
    parser.add_argument("--samples", type=pathlib.Path, help="a glTF-Sample-Assets checkout")
    args = parser.parse_args()
    output = args.build / "animation_choice_probe"
    output.mkdir(parents=True, exist_ok=True)

    fixture = output / "choice.gltf"
    fixture.write_text(json.dumps(choice_fixture()), encoding="utf-8")
    cases = [("choice", Gltf(fixture))]
    if args.samples:
        for name in SAMPLES:
            path = sample_path(args.samples, name)
            if path:
                cases.append((name, Gltf(path)))

    report = {}
    for name, g in cases:
        for candidate in CANDIDATES:
            label = f"{name}_{candidate.name}"
            start = time.perf_counter()
            files = author(g, candidate, output, label)
            seconds = time.perf_counter() - start
            result = check(g, candidate, files)
            result["authorSeconds"] = seconds
            report[label] = result
            errors = [s["error"] for s in result["selections"].values()]
            ends = [(s["stageEnd"], s["variantEnd"], s["expectedEnd"]) for s in result["selections"].values()]
            invalid = sum(len(s["validation"]) for s in result["selections"].values())
            print(f"{label}: worst pose error {max(errors):.3g}, unchosen {result['unchosen'][0]:.3g}, "
                  f"switched {max(result['switched']):.3g} in {result['switchSeconds']:.3f} s, "
                  f"validation {invalid}, bytes {result['bytes']} (one {result['bytesOpenedForOne']}), "
                  f"authored in {seconds:.3f} s", flush=True)
            print(f"    stage end, variant end, glTF end: {ends}")
            print(f"    animation sources: {[s['animationSources'] for s in result['selections'].values()]}")
            if "unknownSelection" in result:
                print(f"    unknown selection: {result['unknownSelection']}")

    if Plug.Registry().GetPluginWithName("UsdGltfFileFormat"):
        rigid = output / "rigid.gltf"
        rigid.write_text(json.dumps(rigid_fixture()), encoding="utf-8")
        glb = sample_path(args.samples, "InterpolationTest") if args.samples else None
        report["plugin"] = plugin_checks(output, glb or rigid, rigid)
        print(f"plugin: {json.dumps(report['plugin'])}")
    else:
        print("plugin: not on PXR_PLUGINPATH_NAME, format arguments not checked")
    (output / "report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
