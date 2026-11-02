# Layer authoring decision

Decision record, revised 2026-11-01. The evidence below was gathered locally on
Windows with OpenUSD 26.08 when the decision was made. The
[capability matrix](../reference/CAPABILITY_MATRIX.md) records current results.

## Decision

Build the converted scene on an anonymous, temporary layer on one worker thread
that lives as long as the process and runs one conversion at a time. Geometry, material networks and bindings use schema APIs
(`UsdGeom`, `UsdShade`) on a stage opened over that layer. The worker must not
be inside the caller's `SdfChangeBlock`. Transport its `Tf` errors back to the
calling thread before returning. The buffer-loader callback executes on that
worker thread and must not rely on the caller's thread-local state. Only after
conversion succeeds, transfer the layer's contents to the layer provided to
the file-format `Read` method. On conversion failure, discard the temporary
layer, emit a diagnostic and return failure without transferring partial
results. The conversion library, not the plugin entry point, owns scene
authoring.

This is a publication boundary, not a general transaction guarantee:
conversion must check all failures before transfer, and a failure during
transfer itself is not guaranteed to be atomic. Texture files and other
external resources are not covered by this layer-level approach; see
[TEXTURES](../design/TEXTURES.md#cache-and-asset-lifetime).

## Investigation

The initial decision favored a temporary `UsdStage`. `tests/test_layer_authoring.py`
compares directly authored `Sdf` specs with
the corresponding `UsdGeom` stage authoring for a small triangle, including
the default prim, hierarchy, geometry, up axis and units. On the tested
runtime both produce identical USDA content. The test transfers the staged
content into an anonymous layer using the registered glTF file format and
checks replacement of prior content and visibility on an already-open stage.
It also checks that discarding an unpublished staged scene preserves the
destination and creates no files in the working directory.

The first C++ integration revealed that a file-format `Read` during layer
reload can execute inside an `SdfChangeBlock`: defining prims on the temporary
stage failed. OpenUSD documents that using `Usd` or other downstream APIs
inside a change block is unsafe because their change notifications are
deferred. Initially, direct `Sdf` authoring avoided relying on downstream
composition during `Read`. To use schema APIs safely, authoring now runs on
another thread, outside the reload thread's change block, following the
approach used by usd-vrm-plugins. Its stage and temporary layer are not exposed
until the worker finishes, and the calling thread retains responsibility for
publication and diagnostics.

Moving geometry from direct `Sdf` specs to `UsdGeom` then removed the last reason
for two authoring styles. Exported from the reflection fixture, the result differs
only by a new `extent` and by `subdivisionScheme` becoming `uniform`, as its schema
requires; the direct specs had omitted the first and left the second varying.
Opening a generated 5000-node scene took 1.21 s before and 1.27 s after on the
same machine. The stage was already open during geometry authoring, so its change
processing is not new.

A new thread for each read first crashed intermittently on macOS arm64 CI, on the authoring
thread, while `UsdStage::DefinePrim` ran prim indexing tasks in TBB. Each row is 120 runs of the
test suite on a `macos-15` runner, three at a time:

| Authoring thread | Crashes |
| --- | --- |
| New for each read, default stack | 6, 7 and 8, in three probes |
| New for each read, 1 MiB stack | 9 |
| New for each read, 8 MiB stack | 11 |
| One for the process | 0, and 0 of 360 for the implementation |
| New for each read, `PXR_WORK_THREAD_LIMIT=1` | 0 |

The same sequence without this plugin, from Python and on a new thread each time, crashed once in
120 runs of 1500 threads, with the same stack. So it comes from OpenUSD and TBB under thread
turnover, and is not specific to this plugin's authoring. Fault addresses were small invalid values
and not the stack's limit. The mechanism inside TBB was not found. It was not seen on Windows or
Linux, where a short-lived thread is also used.

Conversions therefore run one at a time, which limits a process reading many glTF files in
parallel. A conversion that reaches another glTF read runs it on the same thread rather than
waiting for itself.

The end-to-end plugin tests exercise both formats, failed reload without
publication, successful reload on an existing stage, and material reload
inside an explicit change block. They do not prove recovery from every USD
error.
