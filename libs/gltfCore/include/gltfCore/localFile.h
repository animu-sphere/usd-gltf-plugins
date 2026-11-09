// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <fastgltf/types.hpp>

#include <filesystem>
#include <string>

namespace gltfCore
{

// Applies the external resource policy of docs/architecture/IMPORT_PIPELINE.md.
// `target` is the canonical regular file and `relative` the decoded UTF-8 path
// below the source directory. `kind` names the resource in error messages.
[[nodiscard]] bool ResolveLocalFile(const fastgltf::URI& uri, const std::string& sourcePath,
                                    const char* kind, std::filesystem::path& target,
                                    std::string& relative, std::string& error);

} // namespace gltfCore
