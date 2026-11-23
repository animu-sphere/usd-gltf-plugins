// SPDX-License-Identifier: Apache-2.0
#include "gltfTexture/imageBasedLight.h"

#include "gltfTexture/image.h"
#include "imageStructure.h"

// stb_image decodes the faces, with only the formats glTF allows and the image limit of
// docs/adr/0006-image-failures.md. Its functions are static to this file, so that they cannot bind
// to the copy that OpenUSD's Hio links. See docs/adr/0046-image-based-lighting.md.
#define STB_IMAGE_IMPLEMENTATION
#define STB_IMAGE_STATIC
#define STBI_ONLY_PNG
#define STBI_ONLY_JPEG
#define STBI_NO_STDIO
#define STBI_NO_LINEAR
#define STBI_NO_HDR
#define STBI_MAX_DIMENSIONS 16384
#include <stb_image.h>

#include <algorithm>
#include <charconv>
#include <climits>
#include <cmath>
#include <memory>
#include <new>
#include <numbers>
#include <system_error>

namespace gltfTexture
{
namespace
{

constexpr std::string_view kEntryPrefix = "lights/";
constexpr std::string_view kEntrySuffix = ".hdr";
constexpr std::uint32_t kMaxLatlongWidth = 8192;
// Babylon.js's power, which ADR 0046 decodes with rather than the exact sRGB curve.
constexpr double kGamma = 2.2;
// The shortest and longest scanlines that Radiance's run-length encoding allows.
constexpr std::uint32_t kMinEncodedWidth = 8;
constexpr std::uint32_t kMaxEncodedWidth = 32767;

// Each channel's linear value for every 8-bit and 16-bit code.
const std::vector<float>&
linearTable(bool sixteen)
{
    static const auto make = [](std::uint32_t max)
    {
        std::vector<float> table(max + 1);
        for (std::uint32_t code = 0; code <= max; ++code)
        {
            table[code] = float(std::pow(double(code) / max, kGamma));
        }
        return table;
    };
    static const std::vector<float> eight = make(255);
    static const std::vector<float> sixteenBit = make(65535);
    return sixteen ? sixteenBit : eight;
}

struct StbFree
{
    void
    operator()(void* pixels) const
    {
        stbi_image_free(pixels);
    }
};

// A face decoded to RGBA, with alpha at its maximum where the image has none.
class Face
{
  public:
    bool
    Decode(std::span<const std::byte> bytes, std::uint32_t size, std::string& error)
    {
        ImageFormat format;
        ImageSize declared;
        if (!DetectImageFormat(bytes, format) ||
            (format != ImageFormat::Png && format != ImageFormat::Jpeg))
        {
            error = "image is not PNG or JPEG";
            return false;
        }
        if (!(format == ImageFormat::Png ? CheckPng(bytes, error, &declared)
                                         : CheckJpeg(bytes, error, &declared)))
        {
            return false;
        }
        if (declared.width != size || declared.height != size)
        {
            error = "image is not " + std::to_string(size) + " pixels square";
            return false;
        }
        if (bytes.size() > std::size_t(INT_MAX))
        {
            error = "image is larger than 2 GiB";
            return false;
        }
        const auto* data = reinterpret_cast<const stbi_uc*>(bytes.data());
        const auto length = int(bytes.size());
        int width = 0;
        int height = 0;
        int channels = 0;
        _sixteen = stbi_is_16_bit_from_memory(data, length) != 0;
        // stb_image tries each decoder in turn, and some failures set no reason, so a reason left by
        // another decoder's test, or by an earlier image on this thread, could be reported instead.
        stbi__g_failure_reason = nullptr;
        _pixels.reset(_sixteen ? static_cast<void*>(stbi_load_16_from_memory(data, length, &width,
                                                                             &height, &channels, 4))
                               : static_cast<void*>(stbi_load_from_memory(data, length, &width,
                                                                          &height, &channels, 4)));
        if (!_pixels)
        {
            const char* reason = stbi_failure_reason();
            error = std::string(format == ImageFormat::Png ? "PNG" : "JPEG") +
                    " image could not be decoded: " + (reason ? reason : "its data is damaged");
            return false;
        }
        if (std::uint32_t(width) != size || std::uint32_t(height) != size)
        {
            _pixels.reset();
            error = "image decoded to another size than it declares";
            return false;
        }
        _size = size;
        _table = &linearTable(_sixteen);
        return true;
    }

    // The value at a texel, with rows reversed as ADR 0046 found.
    void
    Texel(std::uint32_t column, std::uint32_t row, float* rgb) const
    {
        const auto offset = (std::size_t(row) * _size + column) * 4;
        std::uint32_t codes[4];
        for (std::size_t channel = 0; channel < 4; ++channel)
        {
            codes[channel] = _sixteen
                                 ? static_cast<const stbi_us*>(_pixels.get())[offset + channel]
                                 : static_cast<const stbi_uc*>(_pixels.get())[offset + channel];
        }
        if (codes[3] == 0)
        {
            rgb[0] = rgb[1] = rgb[2] = 0;
            return;
        }
        // RGBD divides the color by the alpha channel, read from 0 to 1.
        const float max = _sixteen ? 65535.0f : 255.0f;
        const float divisor = float(codes[3]) / max;
        for (std::size_t channel = 0; channel < 3; ++channel)
        {
            rgb[channel] = (*_table)[codes[channel]] / divisor;
        }
    }

    // Samples bilinearly at a point of the face, from 0 to 1 in s and t as OpenGL's cube maps
    // define them, clamped at the face's edges.
    void
    Sample(double s, double t, float* rgb) const
    {
        const double last = double(_size - 1);
        const double column = std::clamp(s * _size - 0.5, 0.0, last);
        // Row 0 is the top of the stored image, which is t = 1 once the rows are reversed.
        const double row = std::clamp((1 - t) * _size - 0.5, 0.0, last);
        const auto c0 = std::uint32_t(column);
        const auto r0 = std::uint32_t(row);
        const auto c1 = std::min(c0 + 1, _size - 1);
        const auto r1 = std::min(r0 + 1, _size - 1);
        const auto fc = float(column - c0);
        const auto fr = float(row - r0);
        float corners[4][3];
        Texel(c0, r0, corners[0]);
        Texel(c1, r0, corners[1]);
        Texel(c0, r1, corners[2]);
        Texel(c1, r1, corners[3]);
        for (std::size_t channel = 0; channel < 3; ++channel)
        {
            const float top =
                corners[0][channel] + (corners[1][channel] - corners[0][channel]) * fc;
            const float bottom =
                corners[2][channel] + (corners[3][channel] - corners[2][channel]) * fc;
            rgb[channel] = top + (bottom - top) * fr;
        }
    }

    void
    Release()
    {
        _pixels.reset();
    }

  private:
    std::unique_ptr<void, StbFree> _pixels;
    std::uint32_t _size = 0;
    bool _sixteen = false;
    const std::vector<float>* _table = nullptr;
};

// The face that OpenGL's cube maps sample in a direction, and the point on it.
struct FacePoint
{
    std::size_t face;
    double s;
    double t;
};

FacePoint
facePoint(double x, double y, double z)
{
    const double ax = std::abs(x);
    const double ay = std::abs(y);
    const double az = std::abs(z);
    FacePoint result{};
    double major = 0;
    double sc = 0;
    double tc = 0;
    if (ax >= ay && ax >= az)
    {
        result.face = x >= 0 ? 0 : 1;
        major = ax;
        sc = x >= 0 ? -z : z;
        tc = -y;
    }
    else if (ay >= az)
    {
        result.face = y >= 0 ? 2 : 3;
        major = ay;
        sc = x;
        tc = y >= 0 ? z : -z;
    }
    else
    {
        result.face = z >= 0 ? 4 : 5;
        major = az;
        sc = z >= 0 ? x : -x;
        tc = -y;
    }
    result.s = (sc / major + 1) / 2;
    result.t = (tc / major + 1) / 2;
    return result;
}

// Greg Ward's shared exponent, rounded to the nearest mantissa.
void
toRgbe(const float* rgb, std::uint8_t* rgbe)
{
    const float largest = std::max({rgb[0], rgb[1], rgb[2]});
    if (!(largest >= 1e-32f))
    {
        rgbe[0] = rgbe[1] = rgbe[2] = rgbe[3] = 0;
        return;
    }
    int exponent = 0;
    const float scale = std::frexp(largest, &exponent) * 256.0f / largest;
    for (std::size_t channel = 0; channel < 3; ++channel)
    {
        rgbe[channel] = std::uint8_t(std::min(255.0f, rgb[channel] * scale + 0.5f));
    }
    rgbe[3] = std::uint8_t(exponent + 128);
}

void
append(std::vector<std::byte>& out, std::uint8_t value)
{
    out.push_back(std::byte(value));
}

// One channel of a scanline in Radiance's run-length encoding: runs of at least four equal bytes,
// and literal stretches between them. Ported from Bruce Walter's rgbe.c, which the format's other
// readers follow.
void
encodeChannel(const std::vector<std::uint8_t>& data, std::vector<std::byte>& out)
{
    constexpr std::size_t kMinRun = 4;
    const std::size_t count = data.size();
    std::size_t current = 0;
    while (current < count)
    {
        std::size_t runStart = current;
        std::size_t run = 0;
        std::size_t previousRun = 0;
        while (run < kMinRun && runStart < count)
        {
            runStart += run;
            previousRun = run;
            run = 1;
            while (runStart + run < count && run < 127 && data[runStart] == data[runStart + run])
            {
                ++run;
            }
        }
        // A short run just before the next long one is written as a run.
        if (previousRun > 1 && previousRun == runStart - current)
        {
            append(out, std::uint8_t(128 + previousRun));
            append(out, data[current]);
            current = runStart;
        }
        while (current < runStart)
        {
            const auto literal = std::min<std::size_t>(runStart - current, 128);
            append(out, std::uint8_t(literal));
            for (std::size_t i = 0; i < literal; ++i)
            {
                append(out, data[current + i]);
            }
            current += literal;
        }
        if (run >= kMinRun)
        {
            append(out, std::uint8_t(128 + run));
            append(out, data[runStart]);
            current += run;
        }
    }
}

std::vector<std::byte>
encodeRgbe(std::uint32_t width, std::uint32_t height, const std::vector<std::uint8_t>& rgbe)
{
    const auto header = "#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y " + std::to_string(height) +
                        " +X " + std::to_string(width) + "\n";
    std::vector<std::byte> out;
    out.reserve(header.size() + rgbe.size());
    for (const char c : header)
    {
        out.push_back(std::byte(c));
    }
    // A reader takes a scanline outside these widths as flat pixels.
    if (width < kMinEncodedWidth || width > kMaxEncodedWidth)
    {
        for (const auto value : rgbe)
        {
            append(out, value);
        }
        return out;
    }
    std::vector<std::uint8_t> channel(width);
    for (std::uint32_t row = 0; row < height; ++row)
    {
        append(out, 2);
        append(out, 2);
        append(out, std::uint8_t(width >> 8));
        append(out, std::uint8_t(width & 0xFF));
        const auto* line = rgbe.data() + std::size_t(row) * width * 4;
        for (std::size_t component = 0; component < 4; ++component)
        {
            for (std::uint32_t column = 0; column < width; ++column)
            {
                channel[column] = line[std::size_t(column) * 4 + component];
            }
            encodeChannel(channel, out);
        }
    }
    return out;
}

const char*
faceName(std::size_t face)
{
    static constexpr const char* names[] = {"+X", "-X", "+Y", "-Y", "+Z", "-Z"};
    return names[face];
}

} // namespace

std::string
MakeLightEntry(std::size_t lightIndex)
{
    return std::string(kEntryPrefix) + std::to_string(lightIndex) + std::string(kEntrySuffix);
}

bool
ParseLightEntry(std::string_view entry, std::size_t& lightIndex)
{
    if (!entry.starts_with(kEntryPrefix) || !entry.ends_with(kEntrySuffix))
    {
        return false;
    }
    entry.remove_prefix(kEntryPrefix.size());
    entry.remove_suffix(kEntrySuffix.size());
    const auto parsed = std::from_chars(entry.data(), entry.data() + entry.size(), lightIndex);
    return parsed.ec == std::errc{} && parsed.ptr == entry.data() + entry.size() &&
           !(entry.size() > 1 && entry[0] == '0');
}

void
LatlongSize(std::uint32_t faceSize, std::uint32_t& width, std::uint32_t& height)
{
    width = std::min<std::uint64_t>(std::uint64_t(faceSize) * 4, kMaxLatlongWidth);
    height = width / 2;
}

bool
CheckImageBasedLight(const fastgltf::Asset& asset, const gltfCore::ImageBasedLight& light,
                     const std::string& sourcePath, const gltfCore::BufferReader& buffers,
                     std::string& error)
{
    for (std::size_t level = 0; level < light.specularImages.size(); ++level)
    {
        const auto size = light.specularImageSize >> level;
        for (std::size_t face = 0; face < 6; ++face)
        {
            const auto image = light.specularImages[level][face];
            const auto fail = [&](const std::string& reason)
            {
                error = "specularImages level " + std::to_string(level) + " face " +
                        faceName(face) + " (image " + std::to_string(image) + "): " + reason;
                return false;
            };
            std::vector<std::byte> owned;
            std::span<const std::byte> bytes;
            ImageFormat format;
            std::string reason;
            if (!ReadImageBytes(asset, image, sourcePath, buffers, owned, bytes, format, reason))
            {
                return fail(reason);
            }
            if (format != ImageFormat::Png && format != ImageFormat::Jpeg)
            {
                return fail("image is not PNG or JPEG");
            }
            ImageSize declared;
            if (!(format == ImageFormat::Png ? CheckPng(bytes, reason, &declared)
                                             : CheckJpeg(bytes, reason, &declared)))
            {
                return fail(reason);
            }
            if (declared.width != size || declared.height != size)
            {
                return fail("image is " + std::to_string(declared.width) + " by " +
                            std::to_string(declared.height) + " pixels, not " +
                            std::to_string(size) + " square");
            }
        }
    }
    return true;
}

bool
ConvertCubeToRgbe(const std::array<std::span<const std::byte>, 6>& faces, std::uint32_t faceSize,
                  std::vector<std::byte>& hdr, std::string& error)
{
    if (faceSize == 0)
    {
        error = "face size is 0";
        return false;
    }
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    LatlongSize(faceSize, width, height);
    try
    {
        // Each pixel's direction by OpenEXR's latlong at its center: longitude pi at the left edge,
        // 0 (+Z) in the middle and pi / 2 (+X) at a quarter, and latitude pi / 2 (+Y) at the top.
        std::vector<double> sinLongitude(width);
        std::vector<double> cosLongitude(width);
        std::vector<double> sinLatitude(height);
        std::vector<double> cosLatitude(height);
        for (std::uint32_t column = 0; column < width; ++column)
        {
            const double longitude = std::numbers::pi * (1 - 2 * (column + 0.5) / width);
            sinLongitude[column] = std::sin(longitude);
            cosLongitude[column] = std::cos(longitude);
        }
        for (std::uint32_t row = 0; row < height; ++row)
        {
            const double latitude = std::numbers::pi * (0.5 - (row + 0.5) / height);
            sinLatitude[row] = std::sin(latitude);
            cosLatitude[row] = std::cos(latitude);
        }
        std::vector<std::uint8_t> rgbe(std::size_t(width) * height * 4);
        // One face at a time, so that at most one is decoded.
        for (std::size_t index = 0; index < 6; ++index)
        {
            Face face;
            if (!face.Decode(faces[index], faceSize, error))
            {
                error = std::string("face ") + faceName(index) + ": " + error;
                return false;
            }
            for (std::uint32_t row = 0; row < height; ++row)
            {
                for (std::uint32_t column = 0; column < width; ++column)
                {
                    const auto point =
                        facePoint(sinLongitude[column] * cosLatitude[row], sinLatitude[row],
                                  cosLongitude[column] * cosLatitude[row]);
                    if (point.face != index)
                    {
                        continue;
                    }
                    float rgb[3];
                    face.Sample(point.s, point.t, rgb);
                    toRgbe(rgb, rgbe.data() + (std::size_t(row) * width + column) * 4);
                }
            }
        }
        hdr = encodeRgbe(width, height, rgbe);
    }
    catch (const std::bad_alloc&)
    {
        error = "not enough memory to convert the faces";
        return false;
    }
    return true;
}

bool
ConvertImageBasedLight(const fastgltf::Asset& asset, const gltfCore::ImageBasedLight& light,
                       const std::string& sourcePath, const gltfCore::BufferReader& buffers,
                       std::vector<std::byte>& hdr, std::string& error)
{
    if (light.specularImages.empty())
    {
        error = "the light has no specular images";
        return false;
    }
    std::array<std::vector<std::byte>, 6> owned;
    std::array<std::span<const std::byte>, 6> faces;
    for (std::size_t face = 0; face < 6; ++face)
    {
        ImageFormat format;
        const auto image = light.specularImages.front()[face];
        if (!ReadImageBytes(asset, image, sourcePath, buffers, owned[face], faces[face], format,
                            error))
        {
            error = std::string("face ") + faceName(face) + " (image " + std::to_string(image) +
                    "): " + error;
            return false;
        }
    }
    return ConvertCubeToRgbe(faces, light.specularImageSize, hdr, error);
}

} // namespace gltfTexture
