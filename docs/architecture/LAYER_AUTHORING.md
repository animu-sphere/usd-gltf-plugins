# Layer authoring decision

Status: revised and implemented for the current scene subset. Evaluated
locally on Windows with OpenUSD 26.08.

## Decision

Build the converted scene directly as `Sdf` specs on an anonymous, temporary
layer. Only after conversion succeeds, transfer its contents to the layer
provided to the file-format `Read` method. On conversion failure, discard the
temporary layer, emit a diagnostic and return failure without transferring
partial results. The conversion library, not the plugin entry point, owns
scene authoring.

This is a publication boundary, not a general transaction guarantee:
conversion must check all failures before transfer, and a failure during
transfer itself is not guaranteed to be atomic. Texture files and other
external resources are not covered by this layer-level approach; their
lifetime and failure handling remain open.

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
deferred. Direct `Sdf` authoring avoids relying on downstream composition
during `Read`, at the cost of explicitly maintaining schema-compatible specs,
attribute types, defaults and metadata.

The end-to-end plugin tests now exercise both formats, failed reload without
publication, and successful reload on an existing stage. They cover only the
current node/transform subset and do not prove recovery from every USD error.
External asset handling and results on other platforms remain unverified.
