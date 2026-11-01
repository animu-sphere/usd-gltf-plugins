# Layer authoring decision

Status: adopted for the initial importer; not yet implemented in the
file-format plugin. Evaluated locally on Windows with OpenUSD 26.08.

## Decision

Build the converted scene on an anonymous, temporary `UsdStage` using standard
USD schema APIs. Only after conversion succeeds, transfer its root layer's
contents to the layer provided to the file-format `Read` method. On failure,
discard the temporary stage, emit a diagnostic and return failure without
transferring partial results. The conversion library, not the plugin entry
point, should own scene authoring.

This is a publication boundary, not a general transaction guarantee:
conversion must check all failures before transfer. Texture files and other
external resources are not covered by this layer-level approach; their
lifetime and failure handling remain open.

## Investigation

`tests/test_layer_authoring.py` compares directly authored `Sdf` specs with
the corresponding `UsdGeom` stage authoring for a small triangle, including
the default prim, hierarchy, geometry, up axis and units. On the tested
runtime both produce identical USDA content. The test transfers the staged
content into an anonymous layer using the registered glTF file format and
checks replacement of prior content and visibility on an already-open stage.
It also checks that discarding an unpublished staged scene preserves the
destination and creates no files in the working directory.

Direct `SdfLayer` authoring can represent this example, but requires explicit
specs, attribute types and metadata. The `UsdStage` approach uses the schema
APIs needed for the planned geometry and materials and permits conversion to
be isolated from the destination before publication. It may add temporary
memory and composition overhead; profile representative assets before
optimizing. No performance comparison was performed.

These are focused Python-binding experiments, not an end-to-end file-format
`Read` test or proof of recovery from every USD error. The actual C++
implementation must test error propagation, repeated reads and failed
conversions through the plugin, as well as external asset handling. Results
on other runtimes and platforms are not yet verified.
