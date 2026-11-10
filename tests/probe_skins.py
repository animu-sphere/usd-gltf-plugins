# SPDX-License-Identifier: Apache-2.0
"""Probe where UsdSkel prims can go for glTF skins, before the importer converts any.

Run in the lookdev environment:
    python tests/probe_skins.py build/cy2026-windows-x86_64-py313-usd [--samples <glTF-Sample-Assets>] [--render]

This does not use the plugin. It reads glTF itself, authors each placement candidate as USD with
a small prototype, and checks the result with UsdSkel's validators, UsdSkelTopology, UsdSkelCache,
UsdSkelBakeSkinning against glTF's skinning equation, and with --render Storm against the baked
points. It is the experiment behind docs/adr/0028-skin-placement.md, not a test of the importer.
Animation is authored only at the times it checks, so it says nothing about interpolation.
USD scenes, images and a JSON report go in skin_probe under the build directory.
"""

import argparse
import base64
import json
import pathlib
import shutil
import struct
import subprocess

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, UsdSkel, UsdValidation, Vt

TIME_CODES_PER_SECOND = 24
TOLERANCE = 1e-4
SAMPLES = ("SimpleSkin", "RiggedSimple", "RiggedFigure", "CesiumMan", "Fox", "BrainStem", "RecursiveSkeletons")

# --- glTF reading -------------------------------------------------------------------------------

COMPONENTS = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32,
              5126: np.float32}
SIZES = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


class Gltf:
    def __init__(self, path=None, document=None):
        self.path = pathlib.Path(path) if path else None
        self.glb = None
        if document is not None:
            self.json = document
        else:
            data = self.path.read_bytes()
            if data[:4] == b"glTF":
                offset = 12
                while offset < len(data):
                    length, kind = struct.unpack_from("<II", data, offset)
                    chunk = data[offset + 8: offset + 8 + length]
                    if kind == 0x4E4F534A:
                        self.json = json.loads(chunk)
                    elif kind == 0x004E4942:
                        self.glb = chunk
                    offset += 8 + length
            else:
                self.json = json.loads(data)
        self._buffers = {}

    def get(self, key):
        return self.json.get(key, [])

    def buffer(self, index):
        if index not in self._buffers:
            uri = self.json["buffers"][index].get("uri")
            if uri is None:
                self._buffers[index] = self.glb
            elif uri.startswith("data:"):
                self._buffers[index] = base64.b64decode(uri.split(",", 1)[1])
            else:
                from urllib.parse import unquote
                self._buffers[index] = (self.path.parent / unquote(uri)).read_bytes()
        return self._buffers[index]

    def _view(self, view_index, offset, count, dtype, size):
        view = self.json["bufferViews"][view_index]
        data = self.buffer(view["buffer"])
        base = view.get("byteOffset", 0) + offset
        item = np.dtype(dtype).itemsize * size
        stride = view.get("byteStride", item)
        if stride == item:
            return np.frombuffer(data, dtype=dtype, count=count * size, offset=base).reshape(count, size).copy()
        return np.stack([np.frombuffer(data, dtype=dtype, count=size, offset=base + i * stride)
                         for i in range(count)])

    def accessor(self, index):
        acc = self.json["accessors"][index]
        dtype = COMPONENTS[acc["componentType"]]
        size = SIZES[acc["type"]]
        if "bufferView" in acc:
            out = self._view(acc["bufferView"], acc.get("byteOffset", 0), acc["count"], dtype, size)
        else:
            out = np.zeros((acc["count"], size), dtype=dtype)
        sparse = acc.get("sparse")
        if sparse:
            idx, val = sparse["indices"], sparse["values"]
            ids = self._view(idx["bufferView"], idx.get("byteOffset", 0), sparse["count"],
                             COMPONENTS[idx["componentType"]], 1)[:, 0]
            out[ids] = self._view(val["bufferView"], val.get("byteOffset", 0), sparse["count"], dtype, size)
        if acc.get("normalized"):
            info = np.iinfo(dtype)
            out = np.maximum(out.astype(np.float64) / info.max, -1.0)
        return out

    def matrices(self, index):
        return [m.reshape(4, 4).T for m in self.accessor(index).astype(np.float64)]


def trs_matrix(t, r, s):
    x, y, z, w = np.asarray(r, dtype=np.float64) / np.linalg.norm(r)
    m = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0],
                  [0, 0, 0, 1]])
    m[:3, :3] *= np.asarray(s, dtype=np.float64)[None, :]
    m[:3, 3] = t
    return m


def node_trs(node):
    return [np.array(node.get("translation", [0, 0, 0]), dtype=np.float64),
            np.array(node.get("rotation", [0, 0, 0, 1]), dtype=np.float64),
            np.array(node.get("scale", [1, 1, 1]), dtype=np.float64)]


def sample(g, sampler, t):
    """The sampler's value at t, which the probe only asks at keyframes or outside the range."""
    times = g.accessor(sampler["input"])[:, 0].astype(np.float64)
    values = g.accessor(sampler["output"]).astype(np.float64)
    if sampler.get("interpolation", "LINEAR") == "CUBICSPLINE":
        values = values.reshape(len(times), 3, -1)[:, 1, :]
    else:
        values = values.reshape(len(times), -1)
    k = int(np.clip(np.searchsorted(times, t, side="right") - 1, 0, len(times) - 1))
    return values[k]


class Pose:
    """Local and world matrices of every node at one time of one animation, column vectors."""

    def __init__(self, g, parent, animation, t, half_scales=()):
        nodes = g.get("nodes")
        self.trs = [None if "matrix" in n else node_trs(n) for n in nodes]
        if animation is not None:
            anim = g.get("animations")[animation]
            for channel in anim["channels"]:
                target = channel["target"]
                if "node" in target and target["path"] in ("translation", "rotation", "scale"):
                    slot = ("translation", "rotation", "scale").index(target["path"])
                    self.trs[target["node"]][slot] = sample(g, anim["samplers"][channel["sampler"]], t)
        # UsdSkelAnimation holds scales as half3[].
        for n in half_scales:
            self.trs[n][2] = self.trs[n][2].astype(np.float16).astype(np.float64)
        self.local = [np.array(n["matrix"], dtype=np.float64).reshape(4, 4).T if trs is None else trs_matrix(*trs)
                      for n, trs in zip(nodes, self.trs)]
        self.world = [None] * len(nodes)
        for i in range(len(nodes)):
            self._world(i, parent)

    def _world(self, i, parent):
        if self.world[i] is None:
            self.world[i] = self.local[i] if parent[i] is None else self._world(parent[i], parent) @ self.local[i]
        return self.world[i]


def primitive_data(g, primitive):
    """Points, triangle indices, and joint indices and weights with all their sets, or None."""
    if primitive.get("mode", 4) != 4:
        return None
    attributes = primitive["attributes"]
    points = g.accessor(attributes["POSITION"]).astype(np.float64)
    indices = (g.accessor(primitive["indices"])[:, 0].astype(np.int64) if "indices" in primitive
               else np.arange(len(points)))
    joints, weights, n = [], [], 0
    while f"JOINTS_{n}" in attributes:
        joints.append(g.accessor(attributes[f"JOINTS_{n}"]).astype(np.int64))
        weights.append(g.accessor(attributes[f"WEIGHTS_{n}"]).astype(np.float64))
        n += 1
    return {"points": points, "indices": indices,
            "joints": np.concatenate(joints, axis=1) if joints else None,
            "weights": np.concatenate(weights, axis=1) if weights else None}


# --- The prototype placement --------------------------------------------------------------------

class Variant:
    """A placement candidate. `name` is what the report calls it."""

    def __init__(self, name, skel_root="retype", nested=False, skeleton_inside=True, bind="inverse"):
        self.name = name
        self.skel_root = skel_root  # "retype" an existing prim, or "insert" SkelRoot_n below the scene
        self.nested = nested  # keep one root per skin even inside another, instead of merging
        self.skeleton_inside = skeleton_inside  # include the skeleton when finding the common ancestor
        self.bind = bind  # "inverse" of the inverse bind matrices, or the "rest" pose


VARIANTS = (
    Variant("adopted"),
    Variant("inserted", skel_root="insert"),
    Variant("nested", nested=True),
    Variant("skeletonOutside", skeleton_inside=False),
    Variant("restAsBind", bind="rest"),
)


def lca(chains):
    """The deepest common element of root-first chains, or None."""
    common = None
    for level in zip(*chains):
        if all(x == level[0] for x in level):
            common = level[0]
        else:
            break
    return common


def scene_nodes(g, scene):
    parent = [None] * len(g.get("nodes"))
    order = []
    stack = list(reversed(g.get("scenes")[scene]["nodes"]))
    while stack:
        n = stack.pop()
        order.append(n)
        for c in reversed(g.get("nodes")[n].get("children", [])):
            parent[c] = n
            stack.append(c)
    return parent, order


def chain(parent, n):
    out = []
    while n is not None:
        out.append(n)
        n = parent[n]
    return list(reversed(out))


class Plan:
    """Where each source object goes: the decisions under test, before any USD is authored."""

    def __init__(self, g, variant):
        self.g = g
        self.variant = variant
        nodes = g.get("nodes")
        self.scene = g.json.get("scene", 0)
        self.parent, self.order = scene_nodes(g, self.scene)
        in_scene = set(self.order)
        self.users = {}
        for n in self.order:
            if "skin" in nodes[n]:
                self.users.setdefault(nodes[n]["skin"], []).append(n)
        # A skeleton per used skin: its space, its joints (with the nodes between them) in an order
        # with parents first, and its tokens, which are the nodes' paths from the scene.
        self.skeletons = {}
        for s, users in sorted(self.users.items()):
            joints = g.get("skins")[s]["joints"]
            if not all(j in in_scene for j in joints):
                raise ValueError(f"skin {s} has joints outside the scene")
            top = lca([chain(self.parent, j) for j in joints])
            space = self.parent[top] if top in joints else top
            closure = set()
            for j in joints:
                n = j
                while n is not None and n != space:
                    closure.add(n)
                    n = self.parent[n]
            ordered = [n for n in self.order if n in closure]
            self.skeletons[s] = {"space": space, "joints": ordered, "users": users}
        in_closure = set().union(*(set(k["joints"]) for k in self.skeletons.values())) if self.skeletons else set()
        spaces = {k["space"] for k in self.skeletons.values()}
        # A node keeps a prim unless it is only a joint: no mesh, camera or light, no skeleton
        # below it, and no descendant that keeps a prim.
        self.kept = set()
        for n in reversed(self.order):
            node = nodes[n]
            content = any(k in node for k in ("mesh", "camera")) or "KHR_lights_punctual" in node.get("extensions", {})
            if (n not in in_closure or content or n in spaces
                    or any(c in self.kept for c in node.get("children", []))):
                self.kept.add(n)
        self.dropped = sorted(in_closure - self.kept)

    def token(self, n):
        return "/".join(f"Node_{i}" for i in chain(self.parent, n))


def author(g, variant, output):
    """Authors the plan as a USD layer, and returns it with what the checks need."""
    plan = Plan(g, variant)
    nodes = g.get("nodes")
    stage = Usd.Stage.CreateNew(str(output))
    stage.SetTimeCodesPerSecond(TIME_CODES_PER_SECOND)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    asset = UsdGeom.Xform.Define(stage, "/Asset")
    stage.SetDefaultPrim(asset.GetPrim())
    UsdGeom.Scope.Define(stage, "/Asset/scenes")
    scene_path = Sdf.Path(f"/Asset/scenes/Scene_{plan.scene}")
    times = probe_times(g)
    animation = 0 if g.get("animations") else None
    poses = {t: Pose(g, plan.parent, animation, t) for t in times}
    rest = Pose(g, plan.parent, None, 0)

    # Paths of kept nodes follow their glTF parents, which are kept too.
    path = {}
    for n in plan.order:
        if n in plan.kept:
            path[n] = (scene_path if plan.parent[n] is None else path[plan.parent[n]]).AppendChild(f"Node_{n}")
    skeleton_path = {s: (scene_path if k["space"] is None else path[k["space"]]).AppendChild(f"Skeleton_{s}")
                     for s, k in plan.skeletons.items()}

    # Skel roots: the common ancestor of each skin's skeleton and its skinned meshes.
    def prim_chain(p):
        return list(p.GetPrefixes())

    candidates = []
    for s, k in plan.skeletons.items():
        members = [path[u].AppendChild(f"Mesh_{nodes[u]['mesh']}") for u in k["users"]]
        if variant.skeleton_inside:
            members.append(skeleton_path[s])
        common = lca([prim_chain(p) for p in members])
        # Never above the scene.
        if common is None or not common.HasPrefix(scene_path):
            common = scene_path
        candidates.append(common)
    roots = sorted(set(candidates), key=lambda p: (p.pathElementCount, str(p)))
    if not variant.nested:
        roots = [r for r in roots if not any(o != r and r.HasPrefix(o) for o in roots)]

    # Inserting moves the root nodes below each inserted prim.
    moved = {}
    if variant.skel_root == "insert":
        renamed = []
        for index, r in enumerate([r for r in roots if r == scene_path]):
            inserted = scene_path.AppendChild(f"SkelRoot_{index}")
            members = set()
            for s, k in plan.skeletons.items():
                for n in k["users"] + ([k["joints"][0]] if variant.skeleton_inside else []):
                    members.add(chain(plan.parent, n)[0])
            for m in members:
                moved[m] = inserted
            renamed.append(inserted)
        roots = [r for r in roots if r != scene_path] + renamed

        def relocate(p):
            for m, inserted in moved.items():
                old = scene_path.AppendChild(f"Node_{m}")
                if p.HasPrefix(old):
                    return p.ReplacePrefix(old, inserted.AppendChild(f"Node_{m}"))
            return p

        path = {n: relocate(p) for n, p in path.items()}
        skeleton_path = {s: relocate(p) for s, p in skeleton_path.items()}
        # A skeleton whose space is the scene goes into the inserted prim, which has no transform.
        if renamed:
            skeleton_path = {s: renamed[0].AppendChild(p.name) if p.GetParentPath() == scene_path else p
                             for s, p in skeleton_path.items()}
        roots = [relocate(r) for r in roots]

    def define_xform(p):
        if p in roots:
            return UsdSkel.Root.Define(stage, p)
        return UsdGeom.Xform.Define(stage, p)

    define_xform(scene_path)
    for inserted in set(moved.values()):
        UsdSkel.Root.Define(stage, inserted)

    meshes = []  # (prim path, node, primitive data, skin or None)
    for n in plan.order:
        if n not in plan.kept:
            continue
        xform = define_xform(path[n])
        op = xform.AddTransformOp()
        animated = any(not np.allclose(poses[t].local[n], rest.local[n]) for t in times)
        if animated:
            for t in times:
                op.Set(Gf.Matrix4d(poses[t].local[n].T.tolist()), t * TIME_CODES_PER_SECOND)
        else:
            op.Set(Gf.Matrix4d(rest.local[n].T.tolist()))
        node = nodes[n]
        if "mesh" not in node:
            continue
        mesh_path = path[n].AppendChild(f"Mesh_{node['mesh']}")
        define_xform(mesh_path)
        skin = node.get("skin")
        for p, primitive in enumerate(g.get("meshes")[node["mesh"]]["primitives"]):
            data = primitive_data(g, primitive)
            if data is None:
                continue
            mesh = UsdGeom.Mesh.Define(stage, mesh_path.AppendChild(f"Primitive_{p}"))
            mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
            mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(data["points"].astype(np.float32)))
            mesh.CreateFaceVertexCountsAttr(Vt.IntArray([3] * (len(data["indices"]) // 3)))
            mesh.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(data["indices"].astype(np.int32)))
            mesh.CreateDisplayColorPrimvar().Set(Vt.Vec3fArray([color(len(meshes))]))
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
            meshes.append((mesh.GetPath(), n, data, skin if data["joints"] is not None else None))

    # Skeletons, and one animation whose tokens every skeleton shares.
    animated_tokens = {}
    for s, k in plan.skeletons.items():
        skel = UsdSkel.Skeleton.Define(stage, skeleton_path[s])
        skin = g.get("skins")[s]
        inverse_bind = dict(zip(skin["joints"], g.matrices(skin["inverseBindMatrices"])
                                if "inverseBindMatrices" in skin else [np.eye(4)] * len(skin["joints"])))
        space = np.eye(4) if k["space"] is None else rest.world[k["space"]]
        skel.CreateJointsAttr([plan.token(j) for j in k["joints"]])
        skel.CreateJointNamesAttr([nodes[j].get("name", f"Node_{j}") for j in k["joints"]])
        skel.CreateRestTransformsAttr(Vt.Matrix4dArray([Gf.Matrix4d(rest.local[j].T.tolist()) for j in k["joints"]]))
        binds = []
        for j in k["joints"]:
            if variant.bind == "inverse" and j in inverse_bind:
                binds.append(np.linalg.inv(inverse_bind[j]))
            else:
                binds.append(np.linalg.inv(space) @ rest.world[j])
        skel.CreateBindTransformsAttr(Vt.Matrix4dArray([Gf.Matrix4d(b.T.tolist()) for b in binds]))
        for j in k["joints"]:
            if any(not np.allclose(poses[t].local[j], rest.local[j]) for t in times):
                animated_tokens[plan.token(j)] = j
    if animated_tokens and animation is not None:
        animations = Sdf.Path("/Asset/animations")
        UsdGeom.Scope.Define(stage, animations)
        anim = UsdSkel.Animation.Define(stage, animations.AppendChild(f"Animation_{animation}"))
        tokens = sorted(animated_tokens)
        anim.CreateJointsAttr(tokens)
        for t in times:
            trs = [poses[t].trs[animated_tokens[tok]] for tok in tokens]
            code = t * TIME_CODES_PER_SECOND
            anim.CreateTranslationsAttr().Set(Vt.Vec3fArray([Gf.Vec3f(*v[0]) for v in trs]), code)
            # USD does not normalize a quaternion when making a matrix of it, and glTF's are only
            # nearly unit length: SimpleSkin's are up to 2.3e-4 off.
            rotations = [v[1] / np.linalg.norm(v[1]) for v in trs]
            anim.CreateRotationsAttr().Set(Vt.QuatfArray([Gf.Quatf(q[3], *q[:3]) for q in rotations]), code)
            anim.CreateScalesAttr().Set(Vt.Vec3hArray([Gf.Vec3h(*v[2]) for v in trs]), code)
        for s in plan.skeletons:
            UsdSkel.BindingAPI.Apply(stage.GetPrimAtPath(skeleton_path[s])).CreateAnimationSourceRel().SetTargets(
                [anim.GetPath()])
    stage.SetStartTimeCode(min(times) * TIME_CODES_PER_SECOND)
    stage.SetEndTimeCode(max(times) * TIME_CODES_PER_SECOND)
    stage.GetRootLayer().Save()
    return {"plan": plan, "roots": roots, "meshes": meshes, "times": times, "poses": poses,
            "skeletons": skeleton_path, "animatedJoints": sorted(animated_tokens.values()), "animation": animation}


def probe_times(g):
    """The first, last and two keyframes between of the first animation, in seconds."""
    if not g.get("animations"):
        return [0.0]
    anim = g.get("animations")[0]
    keys = sorted({float(t) for s in anim["samplers"] for t in g.accessor(s["input"])[:, 0]})
    return sorted({keys[0], keys[len(keys) // 3], keys[2 * len(keys) // 3], keys[-1]})


def color(i):
    hue = (i * 0.618033988749895) % 1.0
    r, g_, b = (abs(hue * 6 - 3) - 1, 2 - abs(hue * 6 - 2), 2 - abs(hue * 6 - 4))
    return Gf.Vec3f(*(min(max(c, 0.0), 1.0) * 0.8 + 0.1 for c in (r, g_, b)))


# --- Checks -------------------------------------------------------------------------------------

def reference_points(g, data, node, skin, pose, joint_pose=None, plan=None):
    """glTF's world points. With `joint_pose` and `plan`, joints below the skeleton's space use the
    joint pose's local matrices, as UsdSkel composes them from the animation."""
    points = np.c_[data["points"], np.ones(len(data["points"]))]
    if skin is None:
        return (pose.world[node] @ points.T).T[:, :3]
    s = g.get("skins")[skin]
    inverse_bind = (g.matrices(s["inverseBindMatrices"]) if "inverseBindMatrices" in s
                    else [np.eye(4)] * len(s["joints"]))

    def joint_world(j):
        if joint_pose is None:
            return pose.world[j]
        space = plan.skeletons[skin]["space"]
        m = np.eye(4)
        n = j
        while n != space:
            m = joint_pose.local[n] @ m
            n = plan.parent[n]
        return (np.eye(4) if space is None else pose.world[space]) @ m

    matrices = np.array([joint_world(j) @ m for j, m in zip(s["joints"], inverse_bind)])
    out = np.zeros((len(points), 3))
    for column in range(data["joints"].shape[1]):
        m = matrices[data["joints"][:, column]]
        out += data["weights"][:, column:column + 1] * np.einsum("vij,vj->vi", m, points)[:, :3]
    return out


def validate(stage):
    registry = UsdValidation.ValidationRegistry()
    validators = [registry.GetOrLoadValidatorByName(name) for name in (
        "usdSkelValidators:SkelBindingApiAppliedValidator", "usdSkelValidators:SkelBindingApiValidator")]
    return [e.GetMessage() for e in UsdValidation.ValidationContext(validators).Validate(stage)]


def check(g, authored, layer_path, baked_path):
    result = {}
    stage = Usd.Stage.Open(str(layer_path))
    result["validation"] = validate(stage)
    topology = []
    for s, p in authored["skeletons"].items():
        ok, reason = UsdSkel.Topology(UsdSkel.Skeleton(stage.GetPrimAtPath(p)).GetJointsAttr().Get()).Validate()
        if not ok:
            topology.append(f"Skeleton_{s}: {reason}")
    result["topology"] = topology
    # Which skinned meshes UsdSkel finds from the skel roots.
    cache = UsdSkel.Cache()
    found = set()
    for r in authored["roots"]:
        root = UsdSkel.Root(stage.GetPrimAtPath(r))
        cache.Populate(root, Usd.TraverseInstanceProxies())
        for binding in cache.ComputeSkelBindings(root, Usd.TraverseInstanceProxies()):
            for query in binding.GetSkinningTargets():
                found.add(query.GetPrim().GetPath())
    skinned = {p for p, _, _, skin in authored["meshes"] if skin is not None}
    result["skinnedMeshes"] = len(skinned)
    result["skinnedMeshesFound"] = len(skinned & found)

    # Bake through UsdSkel on a copy, and compare the world points with glTF's.
    baked_layer = Sdf.Layer.FindOrOpen(str(layer_path))
    copy = Sdf.Layer.CreateNew(str(baked_path))
    copy.TransferContent(baked_layer)
    baked = Usd.Stage.Open(copy)
    codes = [t * TIME_CODES_PER_SECOND for t in authored["times"]]
    interval = Gf.Interval(min(codes), max(codes))
    for r in authored["roots"]:
        prim = baked.GetPrimAtPath(r)
        if prim and prim.IsA(UsdSkel.Root):
            UsdSkel.BakeSkinning(UsdSkel.Root(prim), interval)
    copy.Save()
    worst = 0.0
    worst_half = 0.0
    worst_where = None
    plan = authored["plan"]
    half_poses = {t: Pose(g, plan.parent, authored["animation"], t, authored["animatedJoints"])
                  for t in authored["times"]}
    for prim_path, node, data, skin in authored["meshes"]:
        mesh = UsdGeom.Mesh(baked.GetPrimAtPath(prim_path))
        for t in authored["times"]:
            code = t * TIME_CODES_PER_SECOND
            points = np.array(mesh.GetPointsAttr().Get(code), dtype=np.float64)
            world = np.array(mesh.ComputeLocalToWorldTransform(code), dtype=np.float64).T
            usd = (world @ np.c_[points, np.ones(len(points))].T).T[:, :3]
            ref = reference_points(g, data, node, skin, authored["poses"][t])
            scale = max(np.ptp(ref, axis=0).max(), 1e-6)
            error = float(np.abs(usd - ref).max() / scale)
            if error > worst:
                worst, worst_where = error, f"{prim_path} at {t:g}s"
            half = reference_points(g, data, node, skin, authored["poses"][t], half_poses[t], plan)
            worst_half = max(worst_half, float(np.abs(usd - half).max() / scale))
    result["maxRelativeError"] = worst
    result["worstMesh"] = worst_where
    # The same, against glTF with the animated joints' scales rounded to half precision, as
    # UsdSkelAnimation stores them. A placement passes if this one is within the tolerance.
    result["maxRelativeErrorHalfScales"] = worst_half
    result["jointPrimsDropped"] = len(plan.dropped)
    result["skelRoots"] = [str(r) for r in authored["roots"]]
    result["ok"] = (not result["validation"] and not topology and result["skinnedMeshes"] == result["skinnedMeshesFound"]
                    and worst_half < TOLERANCE)
    return result


def pixels(image):
    """RGB rows of an image, copied while the converted image is alive."""
    from PySide6 import QtGui

    converted = image.convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
    data = np.array(converted.constBits(), dtype=np.uint8, copy=True)
    width, height, stride = converted.width(), converted.height(), converted.bytesPerLine()
    return data.reshape(height, stride)[:, :width * 4].reshape(-1, 4)[:, :3].astype(int)


def render(skinned, baked, times, output, name):
    """Renders the skinned and baked layers with Storm at each time, and counts differing pixels."""
    from PySide6 import QtGui

    usdrecord = shutil.which("usdrecord")
    stage = Usd.Stage.Open(str(baked))
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    box = Gf.Range3d()
    for t in times:
        cache.SetTime(t * TIME_CODES_PER_SECOND)
        box.UnionWith(cache.ComputeWorldBound(stage.GetPseudoRoot()).ComputeAlignedRange())
    session = Sdf.Layer.CreateNew(str(output / f"{name}_camera.usda"))
    camera_stage = Usd.Stage.Open(session)
    camera = UsdGeom.Camera.Define(camera_stage, "/ProbeCamera")
    size = box.GetSize()
    width, height = max(size[0], 1e-3) * 1.2, max(size[1], 1e-3) * 1.2
    camera.CreateProjectionAttr(UsdGeom.Tokens.orthographic)
    camera.CreateHorizontalApertureAttr(width * 10)
    camera.CreateVerticalApertureAttr(height * 10)
    camera.CreateClippingRangeAttr(Gf.Vec2f(0.001, size[2] * 4 + 10))
    center = box.GetMidpoint()
    camera.AddTranslateOp().Set(Gf.Vec3d(center[0], center[1], box.GetMax()[2] + size[2] + 1))
    session.Save()
    def differing(a, b):
        """The fraction of covered pixels that differ, or None without two comparable images."""
        if a.isNull() or b.isNull() or a.size() != b.size():
            return None
        pa, pb = pixels(a), pixels(b)
        # Pixels that either image covers, against the background in a corner.
        covered = int(((np.abs(pa - pa[0]).max(axis=1) > 8) | (np.abs(pb - pb[0]).max(axis=1) > 8)).sum())
        return round(int((np.abs(pa - pb).max(axis=1) > 8).sum()) / max(covered, 1), 4)

    differences = {}
    baked_images = []
    for t in times:
        code = t * TIME_CODES_PER_SECOND
        images = []
        for label, layer in (("skinned", skinned), ("baked", baked)):
            # usdrecord needs a frame number placeholder when given --frames.
            image = output / f"{name}_{label}.#####.###.png"
            produced = output / f"{name}_{label}.{code:09.3f}.png"
            produced.unlink(missing_ok=True)
            run = subprocess.run([usdrecord, "--camera", "/ProbeCamera", "--sessionLayer", str(session.realPath),
                                  "--frames", f"{code:g}", "--imageWidth", "600", str(layer), str(image)],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace")
            (output / f"{name}_{label}_{code:g}.log").write_text(run.stdout + run.stderr, encoding="utf-8")
            images.append(QtGui.QImage(str(produced)) if produced.exists() else QtGui.QImage())
        differences[f"{t:g}"] = differing(*images)
        baked_images.append(images[1])
    # A control: the most that baked images at two times differ, which is not 0 where anything moves.
    if len(baked_images) > 1:
        differences["control"] = max((differing(a, b) or 0) for i, a in enumerate(baked_images)
                                     for b in baked_images[i + 1:])
    return differences


# --- Synthetic fixtures -------------------------------------------------------------------------

class Builder:
    """Builds a glTF document with one embedded buffer."""

    def __init__(self):
        self.data = bytearray()
        self.doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [], "nodes": [], "meshes": [],
                    "skins": [], "animations": [], "accessors": [], "bufferViews": [], "buffers": []}

    def accessor(self, array, component, kind, **extra):
        array = np.ascontiguousarray(array, dtype=COMPONENTS[component])
        while len(self.data) % 4:
            self.data.append(0)
        self.doc["bufferViews"].append({"buffer": 0, "byteOffset": len(self.data), "byteLength": array.nbytes})
        self.data += array.tobytes()
        count = array.shape[0]
        self.doc["accessors"].append({"bufferView": len(self.doc["bufferViews"]) - 1, "componentType": component,
                                      "count": count, "type": kind, **extra})
        return len(self.doc["accessors"]) - 1

    def node(self, **fields):
        self.doc["nodes"].append(fields)
        return len(self.doc["nodes"]) - 1

    def strip(self, base, height, joints_by_height):
        """A vertical strip of quads, weighted between the joints listed by height."""
        rows = 7
        points, joints, weights = [], [], []
        for r in range(rows):
            y = height * r / (rows - 1)
            f = (len(joints_by_height) - 1) * r / (rows - 1)
            lo = int(np.floor(f))
            hi = min(lo + 1, len(joints_by_height) - 1)
            w = f - lo
            for x in (-0.25, 0.25):
                points.append([base[0] + x, base[1] + y, base[2]])
                joints.append([joints_by_height[lo], joints_by_height[hi], 0, 0])
                weights.append([1 - w, w, 0, 0])
        indices = []
        for r in range(rows - 1):
            a, b, c, d = 2 * r, 2 * r + 1, 2 * r + 2, 2 * r + 3
            indices += [a, b, d, a, d, c]
        points = np.array(points)
        attributes = {
            "POSITION": self.accessor(points, 5126, "VEC3", min=points.min(0).tolist(), max=points.max(0).tolist()),
            "JOINTS_0": self.accessor(np.array(joints), 5121, "VEC4"),
            "WEIGHTS_0": self.accessor(np.array(weights), 5126, "VEC4"),
        }
        self.doc["meshes"].append({"primitives": [{"attributes": attributes,
                                                   "indices": self.accessor(np.array(indices), 5123, "SCALAR")}]})
        return len(self.doc["meshes"]) - 1

    def box(self, size):
        points = np.array([[x, y, z] for x in (-size, size) for y in (-size, size) for z in (-size, size)])
        indices = [0, 1, 3, 0, 3, 2, 4, 6, 7, 4, 7, 5, 0, 4, 5, 0, 5, 1, 2, 3, 7, 2, 7, 6, 0, 2, 6, 0, 6, 4, 1, 5, 7,
                   1, 7, 3]
        self.doc["meshes"].append({"primitives": [{"attributes": {"POSITION": self.accessor(
            points, 5126, "VEC3", min=points.min(0).tolist(), max=points.max(0).tolist())},
            "indices": self.accessor(np.array(indices), 5123, "SCALAR")}]})
        return len(self.doc["meshes"]) - 1

    def skin(self, joints, bind_pose):
        """A skin whose inverse bind matrices are the inverse world matrices of a posed copy."""
        parent = [None] * len(self.doc["nodes"])
        for i, n in enumerate(self.doc["nodes"]):
            for c in n.get("children", []):
                parent[c] = i
        g = Gltf(document=self.doc)
        pose = Pose(g, parent, None, 0)
        for n, trs in bind_pose.items():
            pose.trs[n] = trs
        pose.local = [trs_matrix(*trs) if trs is not None else pose.local[i] for i, trs in enumerate(pose.trs)]
        pose.world = [None] * len(pose.local)
        for i in range(len(pose.local)):
            pose._world(i, parent)
        inverse = np.array([np.linalg.inv(pose.world[j]).T.reshape(-1) for j in joints])
        self.doc["skins"].append({"joints": joints, "inverseBindMatrices": self.accessor(inverse, 5126, "MAT4")})
        return len(self.doc["skins"]) - 1

    def animate(self, channels):
        times = np.array([[0.0], [0.5], [1.0]])
        source = self.accessor(times, 5126, "SCALAR", min=[0.0], max=[1.0])
        anim = {"channels": [], "samplers": []}
        for node, path, values in channels:
            kind = "VEC4" if path == "rotation" else "VEC3"
            anim["samplers"].append({"input": source, "output": self.accessor(np.array(values), 5126, kind)})
            anim["channels"].append({"sampler": len(anim["samplers"]) - 1, "target": {"node": node, "path": path}})
        self.doc["animations"].append(anim)

    def finish(self):
        self.doc["buffers"] = [{"byteLength": len(self.data), "uri": "data:application/octet-stream;base64,"
                                + base64.b64encode(bytes(self.data)).decode()}]
        for key in ("skins", "animations", "meshes"):
            if not self.doc[key]:
                del self.doc[key]
        return self.doc


def quat(axis, degrees):
    a = np.radians(degrees) / 2
    v = np.array(axis, dtype=np.float64) / np.linalg.norm(axis)
    return [*(v * np.sin(a)), np.cos(a)]


def split_fixture():
    """Joints and the skinned mesh below different root nodes; a node between joints; joints listed
    child first; a bind pose that is not the rest pose; a rigid mesh on a joint; animated parents of
    the skeleton and of the skinned mesh, which glTF ignores."""
    b = Builder()
    branch = b.node(name="meshBranch", translation=[5, 0, 0])
    skinned = b.node(name="skinnedNode", translation=[0, 3, 0])
    holder = b.node(name="skeletonParent", translation=[-2, 0, 0])
    root = b.node(name="jRoot")
    between = b.node(name="between", translation=[0, 1, 0])
    mid = b.node(name="jMid", translation=[0, 1, 0])
    tip = b.node(name="jTip", translation=[0, 1, 0])
    attached = b.node(name="attached", translation=[0.6, 0.2, 0])
    b.doc["nodes"][branch]["children"] = [skinned]
    b.doc["nodes"][holder]["children"] = [root]
    b.doc["nodes"][root]["children"] = [between, attached]
    b.doc["nodes"][between]["children"] = [mid]
    b.doc["nodes"][mid]["children"] = [tip]
    b.doc["scenes"] = [{"nodes": [branch, holder]}]
    mesh = b.strip((-2, 0, 0), 3, [2, 1, 0])
    b.doc["nodes"][skinned]["mesh"] = mesh
    b.doc["nodes"][attached]["mesh"] = b.box(0.2)
    b.doc["nodes"][skinned]["skin"] = b.skin([tip, mid, root],
                                             {between: [np.array([0, 1, 0.0]), np.array(quat([0, 0, 1], 20)),
                                                        np.ones(3)]})
    b.animate([
        (branch, "rotation", [quat([0, 1, 0], 0), quat([0, 1, 0], 40), quat([0, 1, 0], 90)]),
        (holder, "translation", [[-2, 0, 0], [-2, 1, 0], [-1, 1, 0]]),
        (between, "rotation", [quat([0, 0, 1], 0), quat([0, 0, 1], 30), quat([0, 0, 1], -30)]),
        (mid, "rotation", [quat([0, 0, 1], 0), quat([0, 0, 1], 45), quat([1, 0, 0], 60)]),
        (root, "scale", [[1, 1, 1], [1.5, 1, 1], [1, 0.5, 1]]),
    ])
    return b.finish()


def shared_fixture():
    """Two skins that share joints with different inverse bind matrices, one of them nested below a
    joint of the other; a skin that two nodes use, one below its own joints; a mesh that nodes with
    different skins use; and a third skin whose skinned node is below its own joint."""
    b = Builder()
    root = b.node(name="root")
    a0 = b.node(name="a0", translation=[0, 1, 0])
    a1 = b.node(name="a1", translation=[0, 1, 0])
    a2 = b.node(name="a2", translation=[0, 1, 0])
    b0 = b.node(name="b0", translation=[0.5, 0.5, 0])
    m0 = b.node(name="m0", translation=[3, 0, 0])
    m1 = b.node(name="m1", translation=[-3, 0, 0])
    m2 = b.node(name="m2", translation=[0, 0, 2])
    m3 = b.node(name="m3", translation=[1, 1, 1])
    b.doc["nodes"][root]["children"] = [a0, m0]
    b.doc["nodes"][a0]["children"] = [a1]
    b.doc["nodes"][a1]["children"] = [a2]
    b.doc["nodes"][a2]["children"] = [b0, m2]
    b.doc["nodes"][b0]["children"] = [m3]
    b.doc["scenes"] = [{"nodes": [root, m1]}]
    shared = b.strip((0, 1, 0), 2, [0, 1])
    lone = b.strip((0, 3, 0), 1, [0, 0])
    small = b.strip((0.5, 3.5, 0), 0.5, [0, 0])
    s0 = b.skin([a0, a1, a2], {})
    s1 = b.skin([a1, a2], {a1: [np.array([0, 1, 0.0]), np.array(quat([0, 0, 1], -25)), np.ones(3)]})
    s2 = b.skin([b0], {})
    for node, mesh, skin in ((m0, shared, s0), (m1, shared, s1), (m2, lone, s0), (m3, small, s2)):
        b.doc["nodes"][node]["mesh"] = mesh
        b.doc["nodes"][node]["skin"] = skin
    b.animate([
        (root, "translation", [[0, 0, 0], [0, 0.5, 0], [1, 0.5, 0]]),
        (a0, "rotation", [quat([0, 0, 1], 0), quat([0, 0, 1], 15), quat([0, 0, 1], 30)]),
        (a1, "rotation", [quat([0, 0, 1], 0), quat([0, 0, 1], -30), quat([1, 0, 0], 30)]),
        (a2, "rotation", [quat([0, 0, 1], 0), quat([0, 0, 1], 45), quat([0, 0, 1], 60)]),
        (b0, "translation", [[0.5, 0.5, 0], [0.5, 0.8, 0], [0.2, 0.5, 0]]),
        (m1, "translation", [[-3, 0, 0], [-3, 2, 0], [-6, 0, 0]]),
    ])
    return b.finish()


# --- Driver -------------------------------------------------------------------------------------

def sample_path(root, name):
    for variant in ("glTF", "glTF-Binary"):
        files = sorted((root / "Models" / name / variant).glob("*.gl*"))
        if files:
            return files[0]
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("build", type=pathlib.Path)
    parser.add_argument("--samples", type=pathlib.Path, help="a glTF-Sample-Assets checkout")
    parser.add_argument("--render", action="store_true", help="compare Storm with the baked points")
    parser.add_argument("--variants", nargs="*", default=[v.name for v in VARIANTS])
    args = parser.parse_args()
    output = args.build / "skin_probe"
    output.mkdir(parents=True, exist_ok=True)
    if args.render:
        from PySide6 import QtGui
        application = QtGui.QGuiApplication.instance() or QtGui.QGuiApplication(["probe_skins"])  # noqa: F841

    cases = [("split", Gltf(document=split_fixture())), ("shared", Gltf(document=shared_fixture()))]
    for name, case in cases:
        (output / f"{name}.gltf").write_text(json.dumps(case.json), encoding="utf-8")
    if args.samples:
        for name in SAMPLES:
            path = sample_path(args.samples, name)
            if path:
                cases.append((name, Gltf(path)))

    report = {}
    for name, g in cases:
        for variant in [v for v in VARIANTS if v.name in args.variants]:
            label = f"{name}_{variant.name}"
            layer = output / f"{label}.usda"
            baked = output / f"{label}_baked.usda"
            for p in (layer, baked):
                p.unlink(missing_ok=True)
            try:
                authored = author(g, variant, layer)
                result = check(g, authored, layer, baked)
                if args.render:
                    result["stormDifferingPixels"] = render(layer, baked, authored["times"], output, label)
            except Exception as error:  # noqa: BLE001 - a candidate may fail in any way
                result = {"ok": False, "exception": repr(error)}
            report[label] = result
            status = "ok  " if result["ok"] else "FAIL"
            summary = {k: (len(v) if k in ("validation", "topology") else v) for k, v in result.items()
                       if k not in ("ok", "worstMesh") and v not in ([], None)}
            print(f"{status} {label}: {json.dumps(summary)}", flush=True)
    (output / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
