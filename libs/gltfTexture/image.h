// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "accessor.h"

#include <fastgltf/types.hpp>

#include <cstddef>
#include <span>
#include <string>
#include <string_view>

namespace gltfTexture
{

enum class ImageFormat
{
    Png,
    Jpeg
};

// Checks the signature only; a damaged image body is found when it is decoded.
[[nodiscard]] bool DetectImageFormat(std::span<const std::byte> bytes, ImageFormat& format);

// Names an embedded image inside its .gltf or .glb package: images/<index>.<ext>.
[[nodiscard]] std::string MakePackageEntry(std::size_t imageIndex, ImageFormat format);
[[nodiscard]] bool ParsePackageEntry(std::string_view entry, std::size_t& imageIndex,
                                     ImageFormat& format);

// Bytes of a bufferView or data URI image. They are owned by the asset or by
// the buffer loader.
[[nodiscard]] bool GetEmbeddedImage(const fastgltf::Asset& asset, std::size_t imageIndex,
                                    const gltfCore::BufferLoader& loadBuffer,
                                    std::span<const std::byte>& bytes, ImageFormat& format,
                                    std::string& error);

struct ImageLocation
{
    // A package entry when embedded, otherwise a path relative to the source.
    std::string path;
    bool embedded;
};

// Finds and checks an image without decoding it. External images follow the
// local file policy and must have the file extension of their format.
[[nodiscard]] bool LocateImage(const fastgltf::Asset& asset, std::size_t imageIndex,
                               const std::string& sourcePath,
                               const gltfCore::BufferLoader& loadBuffer, ImageLocation& location,
                               std::string& error);

} // namespace gltfTexture
