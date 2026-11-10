# SPDX-License-Identifier: Apache-2.0
"""Probe how glTF animation samplers can be authored as USD time samples, before the importer converts any.

Run in the usd environment:
    python tests/probe_animation.py build/cy2026-windows-x86_64-py313-usd [--samples <glTF-Sample-Assets>]

This does not use the plugin. For every translation, rotation and scale sampler of the Khronos
samples that have animations, and of synthetic samplers that the samples lack, it authors each
candidate below as time samples of a `translate`, `orient` or `scale` operation, asks USD for the
value at times inside every segment, just before and at every keyframe and outside the range, and
compares it with glTF's interpolation. It also checks the adopted candidate through a reference
from a stage with other time codes per second and a layer offset, after writing the layer as usda
and usdc, and the local transform of every animated node with all three operations together. It is
the experiment behind docs/adr/0029-node-animation.md, not a test of the importer.
A JSON report goes in animation_probe under the build directory.
"""

import argparse
import json
import math
import pathlib
import sys

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from probe_skins import Gltf, trs_matrix  # noqa: E402

TIME_CODES_PER_SECOND = 24
# Times just before a keyframe at which values are asked, in time codes.
BEFORE_KEY = (1e-2, 1e-3, 1e-4, 1e-5, 1e-6, 5e-7, 1e-7, 1e-8)
INTERIOR = 32

# --- glTF's interpolation, in double precision ----------------------------------------------------


def normalize(q):
    return q / np.linalg.norm(q)


def slerp(a, b, u):
    """Spherical linear interpolation along the shorter path, as glTF specifies."""
    d = float(np.dot(a, b))
    if d < 0:
        b, d = -b, -d
    if d > 1 - 1e-15:
        return normalize(a + u * (b - a))
    theta = math.acos(d)
    return (math.sin((1 - u) * theta) * a + math.sin(u * theta) * b) / math.sin(theta)


class Sampler:
    def __init__(self, name, path, interpolation, times, values):
        self.name, self.path, self.interpolation = name, path, interpolation
        self.times = np.asarray(times, dtype=np.float64)
        values = np.asarray(values, dtype=np.float64)
        width = 4 if path == "rotation" else 3
        if interpolation == "CUBICSPLINE":
            values = values.reshape(len(self.times), 3, width)
            self.ins, self.vertices, self.outs = values[:, 0], values[:, 1], values[:, 2]
        else:
            self.vertices = values.reshape(len(self.times), width)
        if path == "rotation" and interpolation != "CUBICSPLINE":
            self.vertices = np.array([normalize(v) for v in self.vertices])

    def __call__(self, t):
        times, v = self.times, self.vertices
        if t <= times[0] or len(times) == 1:
            return v[0]
        if t >= times[-1]:
            return v[-1]
        k = int(np.searchsorted(times, t, side="right") - 1)
        if t == times[k]:
            return v[k]
        if self.interpolation == "STEP":
            return v[k]
        delta = times[k + 1] - times[k]
        u = (t - times[k]) / delta
        if self.interpolation == "LINEAR":
            if self.path == "rotation":
                return slerp(v[k], v[k + 1], u)
            return v[k] + u * (v[k + 1] - v[k])
        u2, u3 = u * u, u * u * u
        p = ((2 * u3 - 3 * u2 + 1) * v[k] + (u3 - 2 * u2 + u) * delta * self.outs[k] +
             (-2 * u3 + 3 * u2) * v[k + 1] + (u3 - u2) * delta * self.ins[k + 1])
        return normalize(p) if self.path == "rotation" else p

    def scale(self):
        """What translation and scale errors are relative to: the largest value's magnitude."""
        return max(1e-9, float(np.max(np.abs(self.vertices))))

    def query_times(self):
        """Seconds: inside every segment, just before and at each keyframe, and outside the range."""
        out = [self.times[0] - 1.0, self.times[-1] + 1.0]
        for k, t in enumerate(self.times):
            out.append(t)
            if k + 1 < len(self.times):
                nxt = self.times[k + 1]
                out += list(np.linspace(t, nxt, INTERIOR + 2)[1:-1])
                out += [nxt - d / TIME_CODES_PER_SECOND for d in BEFORE_KEY
                        if nxt - d / TIME_CODES_PER_SECOND > t]
        return out


# --- Candidates: (time code, value) pairs for one sampler ------------------------------------------


def keys(s):
    """Each keyframe's value at its time; CUBICSPLINE keeps its vertices, as usd-vrm-plugins does."""
    return [(t * TIME_CODES_PER_SECOND, v) for t, v in zip(s.times, s.vertices)]


def hold(epsilon):
    """STEP: also the previous value epsilon time codes before each keyframe."""
    def candidate(s):
        out = []
        for k, (t, v) in enumerate(zip(s.times, s.vertices)):
            code = t * TIME_CODES_PER_SECOND
            if k > 0 and code - epsilon > out[-1][0]:
                out.append((code - epsilon, s.vertices[k - 1]))
            out.append((code, v))
        return out
    candidate.__name__ = f"hold {epsilon:g}"
    return candidate


def subdivide(n):
    """CUBICSPLINE: n samples per segment, at equal fractions of it, evaluated by glTF's equation."""
    def candidate(s):
        out = []
        for k, t in enumerate(s.times):
            out.append((t * TIME_CODES_PER_SECOND, s.vertices[k]))
            if k + 1 < len(s.times):
                for i in range(1, n):
                    u = t + (s.times[k + 1] - t) * i / n
                    out.append((u * TIME_CODES_PER_SECOND, s(u)))
        return out
    candidate.__name__ = f"{n} per segment"
    return candidate


def segment_count(s, k, tolerance):
    """How many equal pieces keep linear interpolation of segment k within tolerance of the curve,
    times the sampler's scale for a translation or scale. The curve's second derivative is linear in
    the segment's parameter, so its largest norm is at an end, and a piece of length h deviates from
    it by at most that norm times h^2 / 8."""
    delta = s.times[k + 1] - s.times[k]
    v0, v1 = s.vertices[k], s.vertices[k + 1]
    b0, a1 = delta * s.outs[k], delta * s.ins[k + 1]
    start = -6 * v0 - 4 * b0 + 6 * v1 - 2 * a1
    end = 6 * v0 + 2 * b0 - 6 * v1 + 4 * a1
    largest = max(np.linalg.norm(start), np.linalg.norm(end))
    limit = tolerance * (1.0 if s.path == "rotation" else s.scale())
    return max(1, math.ceil(math.sqrt(largest / (8 * limit))))


def bounded(tolerance):
    """CUBICSPLINE: each segment in as many equal pieces as segment_count gives."""
    def candidate(s):
        out = []
        for k, t in enumerate(s.times):
            out.append((t * TIME_CODES_PER_SECOND, s.vertices[k]))
            if k + 1 < len(s.times):
                n = segment_count(s, k, tolerance)
                for i in range(1, n):
                    u = t + (s.times[k + 1] - t) * i / n
                    out.append((u * TIME_CODES_PER_SECOND, s(u)))
        return out
    candidate.__name__ = f"bound {tolerance:g}"
    return candidate


ADOPTED_EPSILON = 1e-7
ADOPTED_TOLERANCE = 1e-4


def adopted(s):
    if s.interpolation == "STEP":
        return hold(ADOPTED_EPSILON)(s)
    if s.interpolation == "CUBICSPLINE":
        return bounded(ADOPTED_TOLERANCE)(s)
    return keys(s)


CANDIDATES = {
    "STEP": [keys, hold(1e-3), hold(1e-5), hold(9e-7), hold(ADOPTED_EPSILON)],
    "LINEAR": [keys],
    "CUBICSPLINE": [keys] + [subdivide(n) for n in (4, 16, 64)] +
                   [bounded(t) for t in (1e-3, ADOPTED_TOLERANCE, 1e-5)],
}

# --- USD ------------------------------------------------------------------------------------------


ORIENT = {"quatf": (Gf.Quatf, UsdGeom.XformOp.PrecisionFloat),
          "quatd": (Gf.Quatd, UsdGeom.XformOp.PrecisionDouble)}
ADOPTED_ORIENT = "quatf"


def to_usd(path, v, orient=ADOPTED_ORIENT):
    if path == "rotation":
        x, y, z, w = normalize(np.asarray(v, dtype=np.float64))
        return ORIENT[orient][0](float(w), float(x), float(y), float(z))
    if path == "translation":
        return Gf.Vec3d(*map(float, v))
    return Gf.Vec3f(*map(float, v))


def from_usd(path, value):
    if path == "rotation":
        i = value.GetImaginary()
        return np.array([i[0], i[1], i[2], value.GetReal()], dtype=np.float64)
    return np.array(value, dtype=np.float64)


def add_op(xform, path, orient=ADOPTED_ORIENT):
    if path == "translation":
        return xform.AddTranslateOp()
    if path == "rotation":
        return xform.AddOrientOp(ORIENT[orient][1])
    return xform.AddScaleOp(UsdGeom.XformOp.PrecisionFloat)


def new_stage():
    stage = Usd.Stage.CreateInMemory()
    stage.SetTimeCodesPerSecond(TIME_CODES_PER_SECOND)
    stage.SetFramesPerSecond(TIME_CODES_PER_SECOND)
    return stage


def error(s, expected, actual):
    if s.path == "rotation":
        d = min(1.0, abs(float(np.dot(normalize(expected), normalize(actual)))))
        return math.degrees(2 * math.acos(d))
    return float(np.max(np.abs(expected - actual))) / s.scale()


def matrix_angle(expected, matrix):
    """Degrees between the rotation of a unit quaternion and the rotation part of a Gf matrix."""
    x, y, z, w = normalize(expected)
    r = np.array(matrix)[:3, :3].T  # Gf matrices use row vectors.
    q = trs_matrix([0, 0, 0], [x, y, z, w], [1, 1, 1])[:3, :3]
    c = (np.trace(q.T @ r) - 1) / 2
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def measure(s, attribute, op=None):
    """The largest error over the query times and the time it occurs at, in seconds, and for a
    rotation the largest error of the operation's matrix."""
    worst, at, through_matrix = 0.0, None, 0.0
    for t in s.query_times():
        code = t * TIME_CODES_PER_SECOND
        e = error(s, s(t), from_usd(s.path, attribute.Get(code)))
        if e > worst:
            worst, at = e, float(t)
        if op is not None and s.path == "rotation":
            through_matrix = max(through_matrix, matrix_angle(s(t), op.GetOpTransform(code)))
    return worst, at, through_matrix


def author(s, candidate, orient=ADOPTED_ORIENT):
    stage = new_stage()
    op = add_op(UsdGeom.Xform.Define(stage, "/X"), s.path, orient)
    samples = candidate(s)
    for code, v in samples:
        op.Set(to_usd(s.path, v, orient), code)
    return stage, op, len(samples)


def composed(s, layer_text_format):
    """The adopted samples in a usda or usdc file, referenced from a stage at 30 time codes per second
    with an offset and scale. Returns the largest error."""
    stage, _, _ = author(s, adopted)  # noqa: F841 - kept alive while exported
    directory = OUTPUT / "composed"
    directory.mkdir(exist_ok=True)
    path = directory / f"{safe(s.name)}.{layer_text_format}"
    stage.GetRootLayer().Export(str(path))
    host = Usd.Stage.CreateInMemory()
    host.SetTimeCodesPerSecond(30)
    prim = host.DefinePrim("/Host")
    offset = Sdf.LayerOffset(12.5, 0.5)
    prim.GetReferences().AddReference(str(path), "/X", offset)
    attribute = prim.GetAttribute(add_op_name(s.path))
    # Host time = layer time in the host's time codes, scaled and offset.
    scale = 30 / TIME_CODES_PER_SECOND * offset.scale

    worst = 0.0
    for t in s.query_times():
        host_code = t * TIME_CODES_PER_SECOND * scale + offset.offset
        worst = max(worst, error(s, s(t), from_usd(s.path, attribute.Get(host_code))))
    return worst


def add_op_name(path):
    return {"translation": "xformOp:translate", "rotation": "xformOp:orient",
            "scale": "xformOp:scale"}[path]


def safe(name):
    return "".join(c if c.isalnum() else "_" for c in name)


# --- Samplers -------------------------------------------------------------------------------------


def quat(axis, degrees):
    axis = np.asarray(axis, dtype=np.float64) / np.linalg.norm(axis)
    h = math.radians(degrees) / 2
    return list(axis * math.sin(h)) + [math.cos(h)]


def synthetic():
    """Cases the samples lack: uneven spacing, steep tangents, large angles, opposite hemispheres, a
    single keyframe and a range that does not start at 0."""
    times = [0.0, 0.01, 1.0, 1.5, 10.0]
    rng = np.random.default_rng(29)
    out = []
    for interpolation in ("STEP", "LINEAR", "CUBICSPLINE"):
        for path in ("translation", "rotation", "scale"):
            width = 4 if path == "rotation" else 3
            if path == "rotation":
                vertices = [quat(rng.normal(size=3), a) for a in (0, 170, -120, 90, 179)]
                if interpolation == "CUBICSPLINE":
                    # glTF interpolates a cubic rotation's components as they are, so an author
                    # keeps neighbours in one hemisphere.
                    for i in range(1, len(vertices)):
                        if np.dot(vertices[i - 1], vertices[i]) < 0:
                            vertices[i] = [-c for c in vertices[i]]
                else:
                    # Every other one in the opposite hemisphere, which glTF and USD must both undo.
                    vertices = [v if i % 2 == 0 else [-c for c in v] for i, v in enumerate(vertices)]
            else:
                vertices = rng.normal(scale=2.0, size=(len(times), width)).tolist()
            if interpolation == "CUBICSPLINE":
                # Tangents of up to three times the slope of the neighbouring keyframes, different on
                # each side of a keyframe, as an exporter's eased or broken tangents are.
                v = np.asarray(vertices)
                values = []
                for i in range(len(times)):
                    lo, hi = max(0, i - 1), min(len(times) - 1, i + 1)
                    slope = (v[hi] - v[lo]) / (times[hi] - times[lo])
                    values += [(slope * rng.uniform(0, 3)).tolist(), vertices[i],
                               (slope * rng.uniform(0, 3)).tolist()]
            else:
                values = vertices
            out.append(Sampler(f"synthetic/{interpolation}/{path}", path, interpolation, times, values))
            late = [t + 2.0 for t in times]
            out.append(Sampler(f"synthetic late/{interpolation}/{path}", path, interpolation, late, values))
        # glTF requires two keyframes of a CUBICSPLINE sampler.
        if interpolation != "CUBICSPLINE":
            out.append(Sampler(f"synthetic single/{interpolation}/rotation", "rotation", interpolation,
                               [0.5], [quat([0, 1, 0], 45)]))
    return out


def sample_files(root):
    """One file per model, the plain glTF where there is one."""
    models = root / "Models"
    out = []
    for model in sorted(p for p in models.iterdir() if p.is_dir()):
        for variant in ("glTF", "glTF-Binary"):
            files = sorted((model / variant).glob("*.gltf")) + sorted((model / variant).glob("*.glb"))
            if files:
                out.append(files[0])
                break
    return out


def gltf_samplers(g, label):
    """Every translation, rotation and scale sampler, and the animated nodes' channels."""
    samplers, nodes = [], []
    for a, animation in enumerate(g.get("animations")):
        channels = {}
        for c, channel in enumerate(animation["channels"]):
            target = channel["target"]
            if target.get("path") not in ("translation", "rotation", "scale") or "node" not in target:
                continue
            spec = animation["samplers"][channel["sampler"]]
            times = g.accessor(spec["input"])[:, 0]
            values = g.accessor(spec["output"])
            s = Sampler(f"{label}/animation {a}/channel {c}", target["path"],
                        spec.get("interpolation", "LINEAR"), times, values)
            samplers.append(s)
            channels.setdefault(target["node"], {})[target["path"]] = s
        for node, by_path in channels.items():
            nodes.append((f"{label}/animation {a}/node {node}", g.get("nodes")[node], by_path))
    return samplers, nodes


def node_check(node, by_path, orient=ADOPTED_ORIENT):
    """All three operations on one Xform against glTF's T * R * S, as the largest difference of any
    matrix element relative to the matrix's largest element."""
    stage = new_stage()
    xform = UsdGeom.Xform.Define(stage, "/X")
    rest = {"translation": node.get("translation", [0, 0, 0]),
            "rotation": node.get("rotation", [0, 0, 0, 1]), "scale": node.get("scale", [1, 1, 1])}
    for path in ("translation", "rotation", "scale"):
        op = add_op(xform, path, orient)
        if path in by_path:
            for code, v in adopted(by_path[path]):
                op.Set(to_usd(path, v, orient), code)
        else:
            op.Set(to_usd(path, rest[path], orient))
    times = sorted({t for s in by_path.values() for t in s.query_times()})
    worst = 0.0
    for t in times:
        values = {p: (by_path[p](t) if p in by_path else np.asarray(rest[p], dtype=np.float64))
                  for p in rest}
        expected = trs_matrix(values["translation"], values["rotation"], values["scale"])
        # Gf matrices use row vectors.
        actual = np.array(xform.GetLocalTransformation(t * TIME_CODES_PER_SECOND)).T
        worst = max(worst, float(np.max(np.abs(expected - actual)) / max(1e-9, np.max(np.abs(expected)))))
    return worst


def main():
    global OUTPUT
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("build", type=pathlib.Path)
    parser.add_argument("--samples", type=pathlib.Path, help="a glTF-Sample-Assets checkout")
    args = parser.parse_args()
    OUTPUT = args.build / "animation_probe"
    OUTPUT.mkdir(parents=True, exist_ok=True)

    samplers, nodes = synthetic(), []
    if args.samples:
        for path in sample_files(args.samples):
            g = Gltf(path)
            if g.get("animations"):
                found, animated = gltf_samplers(g, path.parent.parent.name)
                samplers += found
                nodes += animated

    report = {"samplers": len(samplers), "candidates": {}, "composed": {}, "nodes": {}}
    for s in samplers:
        source = "synthetic" if s.name.startswith("synthetic") else "samples"
        group = f"{source:9} {s.interpolation} {s.path}"
        orients = ORIENT if s.path == "rotation" else [ADOPTED_ORIENT]
        for candidate in CANDIDATES[s.interpolation]:
            for orient in orients:
                name = candidate.__name__ + ("" if s.path != "rotation" else f" {orient}")
                _, op, count = author(s, candidate, orient)
                worst, at, through_matrix = measure(s, op.GetAttr(), op)
                entry = report["candidates"].setdefault(group, {}).setdefault(
                    name, {"error": 0.0, "matrix": 0.0, "samples": 0, "keys": 0, "sampler": None,
                           "at": None})
                entry["samples"] += count
                entry["keys"] += len(s.times)
                entry["matrix"] = max(entry["matrix"], through_matrix)
                if worst >= entry["error"]:
                    entry.update(error=worst, sampler=s.name, at=at)
        for kind in ("usda", "usdc"):
            key = f"{group} {kind}"
            report["composed"][key] = max(report["composed"].get(key, 0.0), composed(s, kind))
    pieces = [(segment_count(s, k, ADOPTED_TOLERANCE), s.name) for s in samplers
              if s.interpolation == "CUBICSPLINE" for k in range(len(s.times) - 1)]
    report["most pieces"] = max(pieces, default=None)
    for orient in ORIENT:
        worst = (None, 0.0)
        for name, node, by_path in nodes:
            e = node_check(node, by_path, orient)
            report["nodes"].setdefault(name, {})[orient] = e
            if e >= worst[1]:
                worst = (name, e)
        report[f"worst node {orient}"] = worst

    (OUTPUT / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"{len(samplers)} samplers, {len(nodes)} animated nodes")
    print("Translation and scale errors are relative to the largest value; rotation errors are degrees.")
    for group, candidates in sorted(report["candidates"].items()):
        for name, entry in candidates.items():
            matrix = f" matrix {entry['matrix']:.3g}" if "rotation" in group else ""
            print(f"  {group:34} {name:22} error {entry['error']:.3g}{matrix} samples/keys "
                  f"{entry['samples'] / max(1, entry['keys']):.2f}  worst {entry['sampler']} at {entry['at']}")
    print(f"Most pieces of a CUBICSPLINE segment at the adopted tolerance: {report['most pieces']}")
    print("Adopted, referenced at 30 time codes per second with offset 12.5 and scale 0.5:")
    for group, worst in sorted(report["composed"].items()):
        print(f"  {group:40} error {worst:.3g}")
    print("Animated nodes' local transforms against T * R * S, relative to the largest element:")
    for orient in ORIENT:
        print(f"  {orient}: worst {report[f'worst node {orient}']}")


OUTPUT = None

if __name__ == "__main__":
    main()
