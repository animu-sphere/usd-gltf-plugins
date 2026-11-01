#include "pxr/pxr.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/tf/registryManager.h"
#include "pxr/base/tf/type.h"
#include "pxr/usd/sdf/fileFormat.h"

#include <string>
#include <vector>

PXR_NAMESPACE_OPEN_SCOPE

class UsdGltfFileFormat : public SdfFileFormat
{
public:
    bool CanRead(const std::string&) const override { return false; }

    bool Read(SdfLayer*, const std::string& path, bool) const override
    {
        TF_RUNTIME_ERROR("usdGltfFileFormat: importing '%s' is not implemented",
                         path.c_str());
        return false;
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
