#include "accessor.h"

#include <cstddef>
#include <iostream>
#include <limits>
#include <string>
#include <utility>
#include <vector>

namespace {

bool check(const fastgltf::Asset& asset, bool expected, const char* label)
{
    gltfCore::AccessorData result;
    std::string error;
    const bool success = gltfCore::GetAccessorData(
        asset, 0, fastgltf::AccessorType::Vec3, fastgltf::ComponentType::Float,
        12, true, result, error);
    if (success != expected || (!success && error.empty())) {
        std::cerr << label << ": unexpected accessor result: " << error << '\n';
        return false;
    }
    return true;
}

} // namespace

int main()
{
    fastgltf::Asset asset;
    fastgltf::Buffer buffer{};
    buffer.byteLength = 36;
    buffer.data = fastgltf::sources::Vector{std::vector<std::byte>(36)};
    asset.buffers.push_back(std::move(buffer));
    fastgltf::BufferView view{};
    view.bufferIndex = 0;
    view.byteLength = 36;
    asset.bufferViews.push_back(std::move(view));
    fastgltf::Accessor accessor{};
    accessor.bufferViewIndex = 0;
    accessor.count = 3;
    accessor.type = fastgltf::AccessorType::Vec3;
    accessor.componentType = fastgltf::ComponentType::Float;
    asset.accessors.push_back(std::move(accessor));

    bool ok = check(asset, true, "valid packed accessor");
    asset.accessors[0].count = std::numeric_limits<std::size_t>::max();
    ok = check(asset, false, "overflowing count") && ok;
    asset.accessors[0].count = 3;
    asset.accessors[0].byteOffset = std::numeric_limits<std::size_t>::max();
    ok = check(asset, false, "overflowing accessor offset") && ok;
    asset.accessors[0].byteOffset = 0;
    asset.bufferViews[0].byteOffset = std::numeric_limits<std::size_t>::max();
    ok = check(asset, false, "overflowing bufferView offset") && ok;
    asset.bufferViews[0].byteOffset = 0;
    asset.bufferViews[0].byteLength = std::numeric_limits<std::size_t>::max();
    ok = check(asset, false, "overflowing bufferView length") && ok;
    asset.bufferViews[0].byteLength = 36;
    asset.bufferViews[0].byteStride = 0;
    ok = check(asset, false, "zero stride") && ok;
    asset.bufferViews[0].byteStride = 8;
    ok = check(asset, false, "undersized stride") && ok;
    asset.bufferViews[0].byteStride = 12;
    ok = check(asset, true, "valid explicit stride") && ok;
    asset.buffers[0].byteLength = 37;
    ok = check(asset, false, "short backing data") && ok;
    return ok ? 0 : 1;
}
