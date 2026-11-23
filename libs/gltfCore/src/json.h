// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <simdjson.h>

#include <cstddef>
#include <string>
#include <string_view>

namespace gltfCore::detail
{

// The JSON of a glTF file, or of a GLB's JSON chunk. Empty if a GLB is too short to have one.
[[nodiscard]] std::string_view JsonOf(const std::byte* bytes, std::size_t size);

// What is malformed in the file's KHR_xmp_json_ld, or an empty string.
[[nodiscard]] std::string DescribeMalformedXmp(const simdjson::dom::element& root);

} // namespace gltfCore::detail
