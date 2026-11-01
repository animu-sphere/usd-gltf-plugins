// SPDX-License-Identifier: Apache-2.0
#include "localFile.h"

#include <simdjson.h>

#include <stdexcept>
#include <string_view>
#include <system_error>

namespace gltfCore
{
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
relativePath(const fastgltf::URI& uri, fs::path& result, std::string& decoded)
{
    const auto raw = uri.path();
    if (!uri.valid() || !uri.scheme().empty() || raw.empty() || uri.string() != raw)
    {
        return false;
    }
    decoded.clear();
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

bool
ResolveLocalFile(const fastgltf::URI& uri, const std::string& sourcePath, const char* kind,
                 fs::path& target, std::string& relative, std::string& error)
{
    const std::string name(kind);
    fs::path relativeFile;
    if (!relativePath(uri, relativeFile, relative))
    {
        error = "unsafe or unsupported " + name + " URI";
        return false;
    }
    std::error_code ec;
    fs::path source;
    try
    {
        source = fs::absolute(fs::u8path(sourcePath), ec);
    }
    catch (const std::filesystem::filesystem_error& exception)
    {
        error = "cannot decode local source path for " + name + ": " + exception.what();
        return false;
    }
    catch (const std::range_error& exception)
    {
        error = "cannot decode local source path for " + name + ": " + exception.what();
        return false;
    }
    if (ec)
    {
        error = "cannot resolve local source path for " + name + ": " + ec.message();
        return false;
    }
    const auto root = fs::canonical(source.parent_path(), ec);
    if (ec)
    {
        error = "cannot resolve local source directory for " + name + ": " + ec.message();
        return false;
    }
    target = fs::canonical(root / relativeFile, ec);
    if (ec)
    {
        error = "cannot resolve " + name + " '" + std::string(uri.string()) + "': " + ec.message();
        return false;
    }
    if (!isInside(root, target))
    {
        error = name + " escapes the source directory";
        return false;
    }
    if (!fs::is_regular_file(target, ec) || ec)
    {
        error = name + " is not an accessible regular file";
        return false;
    }
    return true;
}

} // namespace gltfCore
