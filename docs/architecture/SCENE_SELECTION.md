# Scene selection decision

Status: adopted and connected to the file-format importer for the current
node-only subset.

glTF can contain multiple scenes and can omit the default scene. The importer
must choose one scene rather than combine scenes, which could change the
authored hierarchy. The fastgltf asset exposes a list of scenes and an
optional default scene index; parsing alone does not establish that the index
is in range.

The importer selects the declared default when it is valid. When scenes exist
but no default is declared, it selects index 0 and emits a diagnostic so the
fallback is visible. When no scenes exist, or the declared default is out of
range, it emits an error and fails. These choices do not imply that glTF
assets without scenes are invalid; they define the initial importer's scope.

The `gltfCore` selection result distinguishes a declared selection, a fallback
and the two failure cases. The converter produces the diagnostics before
returning a layer or failure. Unit and plugin tests exercise selection from
both JSON glTF and GLB input.

References: [glTF 2.0 specification](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html),
the vendored fastgltf `Asset` definition and parser, and the scene selection
policy chosen for this project.
