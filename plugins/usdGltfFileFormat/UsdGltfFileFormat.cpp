// SPDX-License-Identifier: Apache-2.0
#include "parse.h"
#include "scene.h"
#include "externalBuffer.h"

#include "pxr/pxr.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/tf/errorMark.h"
#include "pxr/base/tf/registryManager.h"
#include "pxr/base/tf/type.h"
#include "pxr/usd/ar/asset.h"
#include "pxr/usd/ar/resolver.h"
#include "pxr/usd/ar/resolvedPath.h"
#include "pxr/usd/sdf/fileFormat.h"
#include "pxr/usd/sdf/layer.h"

#include <cstddef>
#include <string>
#include <vector>

PXR_NAMESPACE_OPEN_SCOPE

class UsdGltfFileFormat : public SdfFileFormat
{
public:
    bool CanRead(const std::string& path) const override
    {
        const auto resolved = ArGetResolver().Resolve(path);
        const auto input = ArGetResolver().OpenAsset(resolved);
        if (!input || input->GetSize() == 0) {
            return false;
        }
        const auto bytes = input->GetBuffer();
        if (!bytes) {
            return false;
        }
        return gltfCore::Parse(reinterpret_cast<const std::byte*>(bytes.get()),
                               input->GetSize()).error() == fastgltf::Error::None;
    }

    bool Read(SdfLayer* layer, const std::string& path, bool) const override
    {
        const auto input = ArGetResolver().OpenAsset(ArResolvedPath(path));
        if (!input || input->GetSize() == 0) {
            TF_RUNTIME_ERROR("usdGltfFileFormat: cannot open or empty asset '%s'", path.c_str());
            return false;
        }
        const auto bytes = input->GetBuffer();
        if (!bytes) {
            TF_RUNTIME_ERROR("usdGltfFileFormat: cannot read asset '%s'", path.c_str());
            return false;
        }
        auto asset = gltfCore::Parse(
            reinterpret_cast<const std::byte*>(bytes.get()), input->GetSize());
        if (asset.error() != fastgltf::Error::None) {
            TF_RUNTIME_ERROR("usdGltfFileFormat: cannot parse '%s': %s", path.c_str(),
                             fastgltf::getErrorMessage(asset.error()).data());
            return false;
        }
        ExternalBufferLoader loader(path);
        auto converted = gltfUsd::ConvertScene(
            asset.get(), [&loader](const fastgltf::Buffer& buffer,
                                   std::span<const std::byte>& data, std::string& error) {
                return loader.Load(buffer, data, error);
            });
        if (!converted) {
            return false;
        }
        TfErrorMark mark;
        layer->TransferContent(converted);
        return mark.IsClean();
    }

protected:
    SDF_FILE_FORMAT_FACTORY_ACCESS;
    UsdGltfFileFormat()
        : SdfFileFormat(TfToken("gltf"), TfToken("1.0"), TfToken("usd"),
                        std::vector<std::string>{"gltf", "glb"})
    {
    }
    ~UsdGltfFileFormat() override = default;
};

TF_REGISTRY_FUNCTION(TfType)
{
    SDF_DEFINE_FILE_FORMAT(UsdGltfFileFormat, SdfFileFormat);
}

PXR_NAMESPACE_CLOSE_SCOPE
