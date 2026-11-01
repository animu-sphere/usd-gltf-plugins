// SPDX-License-Identifier: Apache-2.0
#include "externalBuffer.h"

#include <simdjson.h>

#include <filesystem>
#include <fstream>
#include <limits>
#include <new>
#include <stdexcept>
#include <string_view>
#include <system_error>
#include <utility>
#include <variant>

namespace
{

namespace fs = std::filesystem;

int
hexDigit(char c)
{
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'a' && c <= 'f')
        return c - 'a' + 10;
    if (c >= 'A' && c <= 'F')
        return c - 'A' + 10;
    return -1;
}

bool
relativePath(const fastgltf::URI& uri, fs::path& result)
{
    const auto raw = uri.path();
    if (!uri.valid() || !uri.scheme().empty() || raw.empty() || uri.string() != raw)
    {
        return false;
    }
    std::string decoded;
    decoded.reserve(raw.size());
    for (std::size_t i = 0; i < raw.size(); ++i)
    {
        unsigned char c = static_cast<unsigned char>(raw[i]);
        if (c == '%')
        {
            if (i + 2 >= raw.size())
                return false;
            const int high = hexDigit(raw[i + 1]);
            const int low = hexDigit(raw[i + 2]);
            if (high < 0 || low < 0)
                return false;
            c = static_cast<unsigned char>((high << 4) | low);
            i += 2;
        }
        if (c < 0x20 || c == 0x7f || c == '\\' || c == ':')
            return false;
        decoded.push_back(static_cast<char>(c));
    }
    if (decoded.front() == '/' || !simdjson::validate_utf8(decoded))
        return false;
    try
    {
        result = fs::u8path(decoded);
    }
    catch (const std::filesystem::filesystem_error&)
    {
        return false;
    }
    catch (const std::range_error&)
    {
        return false;
    }
    if (result.is_absolute() || result.has_root_name() || result.has_root_directory())
        return false;
    for (const auto& component : result)
    {
        if (component == "..")
            return false;
    }
    return true;
}

bool
isInside(const fs::path& root, const fs::path& path)
{
    auto part = path.begin();
    for (const auto& component : root)
    {
        if (part == path.end() || *part != component)
            return false;
        ++part;
    }
    return part != path.end();
}

} // namespace

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
    fs::path relative;
    if (!source || source->fileByteOffset != 0 || !relativePath(*source->uri, relative))
    {
        error = "unsafe or unsupported external buffer URI";
        return false;
    }
    std::error_code ec;
    fs::path sourcePath;
    try
    {
        sourcePath = fs::absolute(fs::u8path(sourcePath_), ec);
    }
    catch (const std::filesystem::filesystem_error& exception)
    {
        error =
            "cannot decode local source path for external buffer: " + std::string(exception.what());
        return false;
    }
    catch (const std::range_error& exception)
    {
        error =
            "cannot decode local source path for external buffer: " + std::string(exception.what());
        return false;
    }
    if (ec)
    {
        error = "cannot resolve local source path for external buffer: " + ec.message();
        return false;
    }
    const auto root = fs::canonical(sourcePath.parent_path(), ec);
    if (ec)
    {
        error = "cannot resolve local source directory for external buffer: " + ec.message();
        return false;
    }
    const auto target = fs::canonical(root / relative, ec);
    if (ec)
    {
        error = "cannot resolve external buffer '" + std::string(source->uri->string()) +
                "': " + ec.message();
        return false;
    }
    if (!isInside(root, target))
    {
        error = "external buffer escapes the source directory";
        return false;
    }
    if (!fs::is_regular_file(target, ec) || ec)
    {
        error = "external buffer is not an accessible regular file";
        return false;
    }
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
