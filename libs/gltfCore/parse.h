// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <fastgltf/core.hpp>

#include <cstddef>
#include <string>
#include <string_view>

namespace gltfCore
{

// Whether conversion applies this extension, rather than ignoring or rejecting it.
[[nodiscard]] bool IsImplementedExtension(std::string_view extension);

// On failure, `error` says why, naming any required extensions that are not supported.
[[nodiscard]] fastgltf::Expected<fastgltf::Asset> Parse(const std::byte* bytes, std::size_t size,
                                                        std::string& error);

} // namespace gltfCore
