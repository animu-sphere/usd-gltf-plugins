// SPDX-License-Identifier: Apache-2.0
#include "parse.h"

namespace gltfCore
{

fastgltf::Expected<fastgltf::Asset>
Parse(const std::byte* bytes, std::size_t size)
{
    auto data = fastgltf::GltfDataBuffer::FromBytes(bytes, size);
    if (data.error() != fastgltf::Error::None)
    {
        return data.error();
    }
    // Parse light references so the scene converter can reject selected lights.
    // Enabling parsing does not mean the extension is supported.
    fastgltf::Parser parser(fastgltf::Extensions::KHR_lights_punctual);
    auto asset = parser.loadGltf(data.get(), {}, fastgltf::Options::None);
    if (asset.error() != fastgltf::Error::None)
    {
        return asset.error();
    }
    if (!asset->extensionsRequired.empty())
    {
        return fastgltf::Error::MissingExtensions;
    }
    return asset;
}

} // namespace gltfCore
