# SPDX-License-Identifier: Apache-2.0
"""Measure stage opening and repeated mesh geometry in fresh processes, without rendering.

Activate the matching USD runtime, then run:
    python tests/measure_import.py <build> <asset> [<asset> ...] --output <report.json>

Runs are serial. OS file caches are not flushed. Peak resident memory covers the
worker process up to the end of Stage.Open, including Python and USD libraries.
Geometry counts are collected afterwards; matching points and topology do not
imply that materials, normals, UVs or other primvars can be shared.
"""

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import platform
import subprocess
import sys
import time


def peak_resident_bytes():
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                    "PagefileUsage", "PeakPagefileUsage")]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.PeakWorkingSetSize
    import resource
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def inspect_asset(path):
    from pxr import Usd, UsdGeom

    started = time.perf_counter()
    stage = Usd.Stage.Open(str(path))
    elapsed = time.perf_counter() - started
    peak = peak_resident_bytes()
    if not stage:
        raise RuntimeError("Stage.Open returned no stage")
    meshes = points = geometry_bytes = 0
    unique = {}
    # Include instance proxies so the counts describe scene occurrences.
    for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()):
        if not prim.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(prim)
        arrays = (mesh.GetPointsAttr().Get(), mesh.GetFaceVertexCountsAttr().Get(),
                  mesh.GetFaceVertexIndicesAttr().Get())
        digest = hashlib.sha256()
        size = 0
        for array in arrays:
            data = memoryview(array).cast("B")
            digest.update(len(data).to_bytes(8, "little"))
            digest.update(data)
            size += len(data)
        meshes += 1
        points += len(arrays[0])
        geometry_bytes += size
        unique[digest.hexdigest()] = size
    return {"open_seconds": elapsed, "peak_resident_bytes": peak,
            "mesh_occurrences": meshes, "point_occurrences": points,
            "unique_point_topology_arrays": len(unique), "point_topology_bytes": geometry_bytes,
            "unique_point_topology_bytes": sum(unique.values()),
            "usd_version": list(Usd.GetVersion())}


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        print(json.dumps(inspect_asset(pathlib.Path(sys.argv[2]))))
        return 0
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("build", type=pathlib.Path)
    parser.add_argument("assets", type=pathlib.Path, nargs="+")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=600, help="seconds per worker, including inspection")
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    if args.runs < 1 or args.timeout <= 0:
        parser.error("runs and timeout must be positive")
    build = args.build.resolve()
    plugin = build / "plugin" / "resources" / "usdGltfFileFormat"
    if not (plugin / "plugInfo.json").is_file():
        parser.error(f"no built plugin at {plugin}")
    registration = json.loads((plugin / "plugInfo.json").read_text(encoding="utf-8"))
    library = next(item for item in registration["Plugins"] if item["Name"] == "UsdGltfFileFormat")
    binary = (plugin / library.get("Root", ".") / library["LibraryPath"]).resolve(strict=True)
    environment = dict(os.environ, PXR_PLUGINPATH_NAME=str(plugin), PYTHONIOENCODING="utf-8")
    report = {"recorded_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "plugin_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "platform": platform.platform(), "python": sys.version,
              "processor": platform.processor(), "build": str(build), "runs": args.runs,
              "timeout_seconds": args.timeout, "assets": []}
    failed = False
    for source in args.assets:
        path = source.resolve(strict=True)
        entry = {"path": str(path), "file_bytes": path.stat().st_size,
                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "measurements": []}
        report["assets"].append(entry)
        for run in range(args.runs):
            try:
                result = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve()),
                                         "--worker", str(path)], env=environment, capture_output=True,
                                        text=True, encoding="utf-8", errors="replace",
                                        timeout=args.timeout, check=False)
                if result.returncode:
                    measurement = {"error": result.stderr, "returncode": result.returncode}
                else:
                    measurement = json.loads(result.stdout)
            except subprocess.TimeoutExpired:
                measurement = {"error": "timeout"}
            entry["measurements"].append(measurement)
            failed |= "error" in measurement
            print(f"{path.name} run {run + 1}: {json.dumps(measurement)}", flush=True)
            # Preserve completed measurements if a later run is interrupted.
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
