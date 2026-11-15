// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "pxr/base/vt/types.h"

namespace gltfUsd
{

// Returns mesh-local glTF tangent XYZ and sign in triangle corner order.
// An empty array means the primitive has no usable generated frame.
pxr::VtVec4fArray GenerateTangents(const pxr::VtVec3fArray& points,
                                   const pxr::VtVec3fArray& normals,
                                   const pxr::VtVec2fArray& texcoords,
                                   const pxr::VtIntArray& indices, bool flatNormals);

} // namespace gltfUsd
