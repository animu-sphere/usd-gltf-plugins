// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "gltfCore/xmp.h"

#include "pxr/base/vt/value.h"
#include "pxr/usd/usd/prim.h"

#include <cstddef>
#include <map>
#include <optional>

namespace gltfUsd
{

// The KHR_xmp_json_ld packets as USD dictionaries. See docs/adr/0045-xmp-metadata.md.
class XmpPackets
{
  public:
    // `metadata` must outlive this.
    explicit XmpPackets(const gltfCore::XmpMetadata& metadata);

    [[nodiscard]] const gltfCore::XmpMetadata&
    Metadata() const
    {
        return _metadata;
    }

    // The packet as a value holding a dictionary, converted at its first use, which warns of each
    // value authored as JSON text. Null if `packet` is empty.
    [[nodiscard]] const pxr::VtValue* Get(std::optional<std::size_t> packet) const;

    // Authors the packet, if any, as the prim's assetInfo["xmp"].
    [[nodiscard]] bool Author(const pxr::UsdPrim& prim, std::optional<std::size_t> packet) const;

  private:
    const gltfCore::XmpMetadata& _metadata;
    mutable std::map<std::size_t, pxr::VtValue> _converted;
};

} // namespace gltfUsd
