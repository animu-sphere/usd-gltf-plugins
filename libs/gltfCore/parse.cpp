// SPDX-License-Identifier: Apache-2.0
#include "parse.h"

namespace gltfCore {

fastgltf::Expected<fastgltf::Asset> Parse(const std::byte* bytes, std::size_t size)
{
    auto data = fastgltf::GltfDataBuffer::FromBytes(bytes, size);
    if (data.error() != fastgltf::Error::None) {
        return data.error();
    }
    fastgltf::Parser parser;
    return parser.loadGltf(data.get(), {}, fastgltf::Options::None);
}

} // namespace gltfCore
