// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "accessor.h"

#include <fastgltf/types.hpp>

#include <cstddef>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace gltfTexture
{

enum class ImageFormat
{
    Png,
    Jpeg,
    Ktx2,
    Webp
};

// Checks the signature only.
[[nodiscard]] bool DetectImageFormat(std::span<const std::byte> bytes, ImageFormat& format);

// Checks an image's structure without decoding its pixels, according to its format.
[[nodiscard]] bool CheckImage(std::span<const std::byte> bytes, ImageFormat format,
                              std::string& error);

// The format consumers read: KTX2 and WebP are converted to PNG.
[[nodiscard]] ImageFormat ServedFormat(ImageFormat format);

// Decodes a KTX2 or WebP image to the PNG it is served as.
[[nodiscard]] bool ConvertToPng(std::span<const std::byte> bytes, ImageFormat format,
                                std::vector<std::byte>& png, std::string& error);

// Names a packaged image inside its .gltf or .glb package: images/<index>.<ext>,
// with the extension of its served format. Parsing returns that format.
[[nodiscard]] std::string MakePackageEntry(std::size_t imageIndex, ImageFormat format);
[[nodiscard]] bool ParsePackageEntry(std::string_view entry, std::size_t& imageIndex,
                                     ImageFormat& format);

// Bytes of a bufferView or data URI image, or of a local KTX2 or WebP file. They are owned
// by the asset, by the buffer loader or, for a file, by `owned`.
[[nodiscard]] bool GetPackagedImage(const fastgltf::Asset& asset, std::size_t imageIndex,
                                    const std::string& sourcePath,
                                    const gltfCore::BufferLoader& loadBuffer,
                                    std::vector<std::byte>& owned,
                                    std::span<const std::byte>& bytes, ImageFormat& format,
                                    std::string& error);

struct ImageLocation
{
    // A package entry when packaged, otherwise a path relative to the source.
    std::string path;
    bool packaged;
    ImageFormat format;
};

// Finds and checks an image without decoding it. External images follow the local
// file policy. Those read directly must have the file extension of their format,
// and KTX2 and WebP files are served through the package.
[[nodiscard]] bool LocateImage(const fastgltf::Asset& asset, std::size_t imageIndex,
                               const std::string& sourcePath,
                               const gltfCore::BufferLoader& loadBuffer, ImageLocation& location,
                               std::string& error);

} // namespace gltfTexture
