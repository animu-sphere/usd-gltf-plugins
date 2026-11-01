// SPDX-License-Identifier: Apache-2.0
#include "gltf_test_data.h"

#include <fastgltf/core.hpp>

#include <cstddef>
#include <iostream>
#include <string>
#include <vector>

namespace
{

bool
checkParse(const std::vector<std::byte>& bytes, bool expectSuccess)
{
    auto data = fastgltf::GltfDataBuffer::FromBytes(bytes.data(), bytes.size());
    if (data.error() != fastgltf::Error::None)
    {
        std::cerr << "Failed to allocate input buffer\n";
        return false;
    }

    fastgltf::Parser parser;
    auto asset = parser.loadGltf(data.get(), {}, fastgltf::Options::None);
    if ((asset.error() == fastgltf::Error::None) != expectSuccess)
    {
        std::cerr << "Unexpected parse result: " << fastgltf::getErrorMessage(asset.error())
                  << '\n';
        return false;
    }
    if (expectSuccess && (asset->nodes.size() != 1 || asset->nodes[0].name != "Root"))
    {
        std::cerr << "Parsed asset did not preserve the node\n";
        return false;
    }
    return true;
}

} // namespace

int
main()
{
    const std::string json = R"({"asset":{"version":"2.0"},"nodes":[{"name":"Root"}]})";
    const std::string requiredExtension =
        R"({"asset":{"version":"2.0"},"extensionsUsed":["UNKNOWN_extension"],"extensionsRequired":["UNKNOWN_extension"]})";
    const auto glb = gltfTestData::glbOf(json);
    auto truncatedGlb = glb;
    truncatedGlb.pop_back();
    return checkParse(gltfTestData::bytesOf(json), true) && checkParse(glb, true) &&
                   checkParse(gltfTestData::bytesOf("{"), false) &&
                   checkParse(truncatedGlb, false) &&
                   checkParse(gltfTestData::bytesOf(requiredExtension), false)
               ? 0
               : 1;
}
