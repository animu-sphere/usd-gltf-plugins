// SPDX-License-Identifier: Apache-2.0
#include "externalBuffer.h"

#include "localFile.h"

#include <filesystem>
#include <fstream>
#include <limits>
#include <new>
#include <system_error>
#include <utility>
#include <variant>

namespace fs = std::filesystem;

ExternalBufferLoader::ExternalBufferLoader(std::string sourcePath)
    : sourcePath_(std::move(sourcePath))
{
}

bool
ExternalBufferLoader::Load(const fastgltf::Buffer& buffer, std::span<const std::byte>& bytes,
                           std::string& error)
{
    const auto cached = buffers_.find(&buffer);
    if (cached != buffers_.end())
    {
        bytes = cached->second;
        return true;
    }
    const auto* source = std::get_if<fastgltf::sources::URI>(&buffer.data);
    if (!source || source->fileByteOffset != 0)
    {
        error = "unsafe or unsupported external buffer URI";
        return false;
    }
    fs::path target;
    std::string relative;
    if (!gltfCore::ResolveLocalFile(*source->uri, sourcePath_, "external buffer", target, relative,
                                    error))
    {
        return false;
    }
    std::error_code ec;
    const auto size = fs::file_size(target, ec);
    if (ec || size < buffer.byteLength)
    {
        error = "external buffer is unavailable or shorter than its declared byteLength";
        return false;
    }
    if (buffer.byteLength > static_cast<std::size_t>(std::numeric_limits<std::streamsize>::max()))
    {
        error = "external buffer exceeds the supported size";
        return false;
    }
    std::ifstream input(target, std::ios::binary);
    if (!input)
    {
        error = "cannot open external buffer";
        return false;
    }
    std::vector<std::byte> data;
    if (buffer.byteLength > data.max_size())
    {
        error = "external buffer exceeds the supported size";
        return false;
    }
    try
    {
        data.resize(buffer.byteLength);
    }
    catch (const std::bad_alloc&)
    {
        error = "cannot allocate memory for external buffer";
        return false;
    }
    if (!input.read(reinterpret_cast<char*>(data.data()),
                    static_cast<std::streamsize>(data.size())))
    {
        error = "cannot read complete external buffer";
        return false;
    }
    const auto inserted = buffers_.emplace(&buffer, std::move(data));
    bytes = inserted.first->second;
    return true;
}
