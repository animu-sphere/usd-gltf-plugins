# Development

For the basic CMake build, installation, and plugin registration, see the
[quick start](../README.md#quick-start).

The repository is an OpenStrata plugin workspace: three libraries under `libs/` and the plugin
bundle under `plugins/`, each a CMake project of its own
([workspace architecture](architecture/WORKSPACE.md)). It builds the same way with or without
OpenStrata.

Run the tests with the matching OpenUSD `pxr` modules and runtime libraries on
the environment's search paths:

```sh
ctest --test-dir build -C Release --output-on-failure
```

Or use the plain-CMake preset, which uses Ninja and the compiler of the current shell (on Windows, a
Developer PowerShell), with `OPENUSD_ROOT` set to the OpenUSD install:

```sh
cmake --preset plain
cmake --build --preset plain
ctest --preset plain
```

With OpenStrata and its cy2026 USD runtime (versions are pinned in the
[CI matrix](../openstrata.ci.yaml)), build and test the workspace, the standalone
plugin bundle, or one library:

```sh
ost plugin test --workspace --graph-only
ost build
ost test

ost plugin build plugins/usdGltfFileFormat
ost plugin test plugins/usdGltfFileFormat --up-to 2

ost library build libs/gltfUsd
ost library test libs/gltfUsd
```

To check display with Storm after `ost build`, which needs a GPU and the lookdev runtime
and is not part of `ost test`:

```powershell
ost env cy2026 --profile lookdev --shell powershell | Out-String | Invoke-Expression
python tests\check_display.py build\cy2026-windows-x86_64-py313-usd
```

It exits nonzero if a comparison fails, and keeps images and logs in `display_check` in
the build directory.

To compare every Khronos sample asset with the committed baseline, in the same environment and with
a [glTF-Sample-Assets](https://github.com/KhronosGroup/glTF-Sample-Assets) checkout at the baseline's
commit:

```powershell
python tests\check_samples.py build\cy2026-windows-x86_64-py313-usd <glTF-Sample-Assets> --render
```

It exits nonzero if a result differs, and keeps images and logs in `sample_check` in the build
directory. `--update` accepts the new results. Without `--render` it needs no GPU.

For a visual check in usdview, open Fox from an adjacent glTF-Sample-Assets checkout.

```powershell
ost plugin build plugins/usdGltfFileFormat --profile lookdev
$fox = (Resolve-Path ../glTF-Sample-Assets/Models/Fox/glTF-Binary/Fox.glb).Path
ost plugin view plugins/usdGltfFileFormat $fox --profile lookdev
```

Use the timeline to play the clip and the `animation` variant set on `/Root` to choose another.

To measure import time, peak resident memory and repeated mesh point/topology arrays, in the
matching USD environment and without a GPU:

```sh
python tests/measure_import.py build/cy2026-windows-x86_64-py313-usd model.glb --output build/import.json
```

Each of three serial runs opens the asset in a fresh process. OS file caches are not flushed.
The JSON report records the source and plugin hashes, runtime and individual measurements;
identical points and topology alone do not establish that meshes can share all their data.
