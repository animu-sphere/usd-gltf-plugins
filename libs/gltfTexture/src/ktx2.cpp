// SPDX-License-Identifier: Apache-2.0
#include "gltfTexture/ktx2.h"

#include "imageStructure.h"

#include <basisu_transcoder.h>

#include <algorithm>
#include <array>
#include <cstring>
#include <limits>
#include <mutex>
#include <new>

namespace gltfTexture
{
namespace
{

// A small file can declare a very large image, and decoding allocates 4 bytes per
// pixel. The transcoder's limit, which init() enforces, keeps this near 1 GiB.
static_assert(basist::BASISU_MAX_SUPPORTED_TEXTURE_DIMENSION <= kMaxImageDimension);

bool
openKtx2(basist::ktx2_transcoder& transcoder, std::span<const std::byte> bytes, std::string& error)
{
    static std::once_flag initialized;
    std::call_once(initialized, [] { basist::basisu_transcoder_init(); });

    if (bytes.size() > std::numeric_limits<std::uint32_t>::max() ||
        !transcoder.init(bytes.data(), std::uint32_t(bytes.size())))
    {
        error = "KTX2 image is damaged or is not Basis Universal";
        return false;
    }
    if (!transcoder.is_etc1s() && !transcoder.is_uastc())
    {
        error = "KTX2 image is not ETC1S or UASTC";
        return false;
    }
    if (transcoder.get_faces() != 1 || transcoder.get_layers() > 1 ||
        std::uint32_t(transcoder.get_header().m_pixel_depth) > 1)
    {
        error = "KTX2 image is not a single 2D image";
        return false;
    }
    const auto scheme = std::uint32_t(transcoder.get_header().m_supercompression_scheme);
    const bool supportedScheme = transcoder.is_etc1s() ? scheme == basist::KTX2_SS_BASISLZ
                                                       : scheme == basist::KTX2_SS_NONE ||
                                                             scheme == basist::KTX2_SS_ZSTANDARD;
    if (!supportedScheme)
    {
        error = "KTX2 image has an invalid supercompression scheme";
        return false;
    }
    if (scheme == basist::KTX2_SS_ZSTANDARD)
    {
        // The transcoder allocates the declared length before inflating it. Each 4x4 UASTC
        // block occupies 16 bytes after inflation, even at the edge of a mip level.
        auto width = transcoder.get_width();
        auto height = transcoder.get_height();
        for (const auto& level : transcoder.get_level_index())
        {
            const auto blocksWide = (width + 3) / 4;
            const auto blocksHigh = (height + 3) / 4;
            if (level.m_uncompressed_byte_length.get_uint64() != 16ull * blocksWide * blocksHigh)
            {
                error = "KTX2 image has an invalid uncompressed level size";
                return false;
            }
            width = std::max(width / 2, 1u);
            height = std::max(height / 2, 1u);
        }
    }
    // KHR_texture_basisu requires this orientation, which matches PNG's row order.
    if (const auto* orientation = transcoder.find_key("KTXorientation"))
    {
        if (orientation->size() < 2 || (*orientation)[0] != 'r' || (*orientation)[1] != 'd')
        {
            error = "KTX2 image orientation is not supported";
            return false;
        }
    }
    return true;
}

constexpr std::array<std::uint32_t, 256>
makeCrcTable()
{
    std::array<std::uint32_t, 256> table{};
    for (std::uint32_t i = 0; i < table.size(); ++i)
    {
        auto value = i;
        for (int bit = 0; bit < 8; ++bit)
        {
            value = (value & 1) ? 0xEDB88320u ^ (value >> 1) : value >> 1;
        }
        table[i] = value;
    }
    return table;
}

std::uint32_t
adler32(std::span<const std::byte> bytes)
{
    // The largest run that cannot overflow 32 bits before the modulo.
    constexpr std::size_t kRun = 5552;
    constexpr std::uint32_t kBase = 65521;
    std::uint32_t a = 1;
    std::uint32_t b = 0;
    while (!bytes.empty())
    {
        const auto run = bytes.first(std::min(bytes.size(), kRun));
        for (const auto byte : run)
        {
            a += std::to_integer<std::uint32_t>(byte);
            b += a;
        }
        a %= kBase;
        b %= kBase;
        bytes = bytes.subspan(run.size());
    }
    return (b << 16) | a;
}

void
appendBigEndian(std::vector<std::byte>& out, std::uint32_t value)
{
    for (int shift = 24; shift >= 0; shift -= 8)
    {
        out.push_back(std::byte((value >> shift) & 0xFF));
    }
}

// The chunk's data must already follow its reserved header in `out`.
void
finishChunk(std::vector<std::byte>& out, std::size_t start, const char (&type)[5])
{
    const auto length = out.size() - start - 8;
    for (int i = 0; i < 4; ++i)
    {
        out[start + i] = std::byte((length >> (24 - 8 * i)) & 0xFF);
        out[start + 4 + i] = std::byte(type[i]);
    }
    appendBigEndian(out, Crc32(std::span(out).subspan(start + 4, length + 4)));
}

} // namespace

std::uint32_t
Crc32(std::span<const std::byte> bytes)
{
    static constexpr auto table = makeCrcTable();
    std::uint32_t value = 0xFFFFFFFFu;
    for (const auto byte : bytes)
    {
        value = table[(value ^ std::to_integer<std::uint32_t>(byte)) & 0xFF] ^ (value >> 8);
    }
    return value ^ 0xFFFFFFFFu;
}

bool
CheckKtx2(std::span<const std::byte> bytes, std::string& error)
{
    basist::ktx2_transcoder transcoder;
    return openKtx2(transcoder, bytes, error);
}

bool
DecodeKtx2ToPng(std::span<const std::byte> bytes, std::vector<std::byte>& png, std::string& error)
{
    basist::ktx2_transcoder transcoder;
    if (!openKtx2(transcoder, bytes, error))
    {
        return false;
    }
    basist::ktx2_image_level_info level;
    if (!transcoder.start_transcoding() || !transcoder.get_image_level_info(level, 0, 0, 0))
    {
        error = "KTX2 image is damaged";
        return false;
    }
    try
    {
        const auto pixels = std::size_t(level.m_orig_width) * level.m_orig_height;
        std::vector<std::uint8_t> rgba(pixels * 4);
        if (!transcoder.transcode_image_level(0, 0, 0, rgba.data(), std::uint32_t(pixels),
                                              basist::transcoder_texture_format::cTFRGBA32))
        {
            error = "KTX2 image is damaged";
            return false;
        }
        png = EncodePng(level.m_orig_width, level.m_orig_height, rgba);
    }
    catch (const std::bad_alloc&)
    {
        error = "not enough memory to decode KTX2 image";
        return false;
    }
    return true;
}

std::vector<std::byte>
EncodePng(std::uint32_t width, std::uint32_t height, std::span<const std::uint8_t> rgba)
{
    static constexpr std::size_t kStoredBlock = 65535;
    const auto rowBytes = std::size_t(width) * 4;

    // Each row starts with filter type 0, which is what the zeroed bytes hold.
    std::vector<std::byte> rows((rowBytes + 1) * height);
    for (std::size_t row = 0; row < height; ++row)
    {
        std::memcpy(rows.data() + row * (rowBytes + 1) + 1, rgba.data() + row * rowBytes, rowBytes);
    }

    static constexpr std::array<unsigned char, 8> signature = {0x89, 'P',  'N',  'G',
                                                               '\r', '\n', 0x1A, '\n'};
    std::vector<std::byte> out;
    const auto blocks = (rows.size() + kStoredBlock - 1) / kStoredBlock;
    out.reserve(signature.size() + 25 + 12 + 6 + rows.size() + 5 * blocks + 12);
    for (const auto byte : signature)
    {
        out.push_back(std::byte(byte));
    }

    auto start = out.size();
    out.resize(start + 8);
    appendBigEndian(out, width);
    appendBigEndian(out, height);
    for (const std::uint8_t field : {8, 6, 0, 0, 0}) // 8-bit RGBA, not interlaced.
    {
        out.push_back(std::byte(field));
    }
    finishChunk(out, start, "IHDR");

    start = out.size();
    out.resize(start + 8);
    out.push_back(std::byte(0x78));
    out.push_back(std::byte(0x01));
    for (std::size_t offset = 0; offset < rows.size(); offset += kStoredBlock)
    {
        const auto size = std::min(rows.size() - offset, kStoredBlock);
        out.push_back(std::byte(offset + size == rows.size() ? 1 : 0));
        out.push_back(std::byte(size & 0xFF));
        out.push_back(std::byte(size >> 8));
        out.push_back(std::byte(~size & 0xFF));
        out.push_back(std::byte((~size >> 8) & 0xFF));
        out.insert(out.end(), rows.begin() + std::ptrdiff_t(offset),
                   rows.begin() + std::ptrdiff_t(offset + size));
    }
    appendBigEndian(out, adler32(rows));
    finishChunk(out, start, "IDAT");

    start = out.size();
    out.resize(start + 8);
    finishChunk(out, start, "IEND");
    return out;
}

} // namespace gltfTexture
