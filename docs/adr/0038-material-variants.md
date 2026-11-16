# 0038 Material variants

Decision record, 2026-11-16. The [material design](../design/MATERIALS.md#material-variants)
specifies the result, and the [capability matrix](../reference/CAPABILITY_MATRIX.md) records
verification.

## Context

[`KHR_materials_variants`](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Khronos/KHR_materials_variants)
lists choices at the document root and maps them to materials on each primitive. One choice applies
to the whole asset. An unmapped primitive uses its original material, which may be glTF's implicit
default. A choice can change textures, vertex color products and sidedness as well as the material
target.

## Decision

Use one variant set on the asset's default prim. USD tools can select it on an open stage or on a
reference, and references can make different choices while using the same converted layer.
Per-primitive sets would allow combinations that the file does not define. File format arguments
would require another conversion and would not expose the choices in the composed stage.

[A local binding is stronger than a binding inside a USD variant](https://openusd.org/22.08/tut_authoring_variants.html).
Keep the opinions for each mapped primitive entirely inside the choices, including its original
binding when no mapping applies. Keep geometry and material networks outside. This follows the
[animation decision](0030-animation-choice.md) about opinion strength without coupling the two
choices or copying geometry for each one.

Check the source mappings before using the parsed mappings. Fastgltf stores only one material for
each primitive and variant, so the parsed result alone cannot distinguish a repeated index from a
valid mapping.

## Consequences

The layer authors a binding per mapped primitive placement and choice. Unmapped primitives need
only their original binding. The importer prepares and validates the materials for all choices
used in the selected scene on open. A broken alternate material fails the import even when the
original choice would render.
