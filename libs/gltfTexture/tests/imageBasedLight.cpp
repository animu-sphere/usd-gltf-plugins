// SPDX-License-Identifier: Apache-2.0
// The latlong of EXT_lights_image_based faces, as docs/adr/0046-image-based-lighting.md decides:
// decoding, orientation, edges, the Radiance encoding and its limits.
#include "gltfTexture/imageBasedLight.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <iterator>
#include <numbers>
#include <span>
#include <string>
#include <vector>

namespace
{

using Bytes = std::vector<std::byte>;

std::uint32_t
crc32(const std::byte* data, std::size_t size)
{
    std::uint32_t crc = 0xFFFFFFFF;
    for (std::size_t i = 0; i < size; ++i)
    {
        crc ^= std::to_integer<std::uint32_t>(data[i]);
        for (int bit = 0; bit < 8; ++bit)
        {
            crc = (crc >> 1) ^ (0xEDB88320 & (0 - (crc & 1)));
        }
    }
    return ~crc;
}

void
appendBigEndian(Bytes& out, std::uint32_t value)
{
    for (int shift = 24; shift >= 0; shift -= 8)
    {
        out.push_back(std::byte((value >> shift) & 0xFF));
    }
}

void
appendChunk(Bytes& out, const char* type, const Bytes& body)
{
    appendBigEndian(out, std::uint32_t(body.size()));
    Bytes typed;
    for (std::size_t i = 0; i < 4; ++i)
    {
        typed.push_back(std::byte(type[i]));
    }
    typed.insert(typed.end(), body.begin(), body.end());
    out.insert(out.end(), typed.begin(), typed.end());
    appendBigEndian(out, crc32(typed.data(), typed.size()));
}

// A PNG of `size` square with stored deflate blocks. `samples` gives each pixel's channels, in the
// order and depth of `color` (0 gray, 2 RGB, 4 gray and alpha, 6 RGBA) and `depth`.
Bytes
png(std::uint32_t size, int color, int depth,
    const std::function<std::vector<std::uint32_t>(std::uint32_t, std::uint32_t)>& samples)
{
    Bytes rows;
    for (std::uint32_t y = 0; y < size; ++y)
    {
        rows.push_back(std::byte(0));
        for (std::uint32_t x = 0; x < size; ++x)
        {
            for (const auto sample : samples(x, y))
            {
                if (depth == 16)
                {
                    rows.push_back(std::byte(sample >> 8));
                }
                rows.push_back(std::byte(sample & 0xFF));
            }
        }
    }
    Bytes zlib = {std::byte(0x78), std::byte(0x01)};
    for (std::size_t offset = 0; offset < rows.size(); offset += 65535)
    {
        const auto length = std::min<std::size_t>(rows.size() - offset, 65535);
        zlib.push_back(std::byte(offset + length == rows.size() ? 1 : 0));
        zlib.push_back(std::byte(length & 0xFF));
        zlib.push_back(std::byte(length >> 8));
        zlib.push_back(std::byte(~length & 0xFF));
        zlib.push_back(std::byte((~length >> 8) & 0xFF));
        zlib.insert(zlib.end(), rows.begin() + std::ptrdiff_t(offset),
                    rows.begin() + std::ptrdiff_t(offset + length));
    }
    std::uint32_t a = 1;
    std::uint32_t b = 0;
    for (const auto byte : rows)
    {
        a = (a + std::to_integer<std::uint32_t>(byte)) % 65521;
        b = (b + a) % 65521;
    }
    appendBigEndian(zlib, (b << 16) | a);
    Bytes out;
    static constexpr std::array<unsigned char, 8> signature = {0x89, 'P',  'N',  'G',
                                                               '\r', '\n', 0x1A, '\n'};
    for (const auto c : signature)
    {
        out.push_back(std::byte(c));
    }
    Bytes header;
    appendBigEndian(header, size);
    appendBigEndian(header, size);
    for (const int field : {depth, color, 0, 0, 0})
    {
        header.push_back(std::byte(field));
    }
    appendChunk(out, "IHDR", header);
    appendChunk(out, "IDAT", zlib);
    appendChunk(out, "IEND", {});
    return out;
}

// The PNG with its IHDR declaring another width and height, and a matching checksum.
Bytes
declaring(Bytes data, std::uint32_t width, std::uint32_t height)
{
    constexpr std::size_t header = 8 + 8;
    for (std::size_t i = 0; i < 4; ++i)
    {
        data[header + i] = std::byte((width >> (24 - 8 * i)) & 0xFF);
        data[header + 4 + i] = std::byte((height >> (24 - 8 * i)) & 0xFF);
    }
    const auto crc = crc32(data.data() + 12, 4 + 13);
    for (std::size_t i = 0; i < 4; ++i)
    {
        data[header + 13 + i] = std::byte((crc >> (24 - 8 * i)) & 0xFF);
    }
    return data;
}

Bytes
uniformPng(std::uint32_t size, int color, int depth, std::vector<std::uint32_t> pixel)
{
    return png(size, color, depth, [pixel](std::uint32_t, std::uint32_t) { return pixel; });
}

struct Latlong
{
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    std::vector<std::array<double, 3>> pixels;

    const std::array<double, 3>&
    at(std::uint32_t column, std::uint32_t row) const
    {
        return pixels[std::size_t(row) * width + column];
    }

    // The pixel nearest a direction, by OpenEXR's latlong.
    const std::array<double, 3>&
    toward(double x, double y, double z) const
    {
        const double length = std::sqrt(x * x + y * y + z * z);
        const double longitude = std::atan2(x / length, z / length);
        const double latitude = std::asin(y / length);
        const double u = (1 - longitude / std::numbers::pi) / 2;
        const double v = 0.5 - latitude / std::numbers::pi;
        return at(std::min(std::uint32_t(u * width), width - 1),
                  std::min(std::uint32_t(v * height), height - 1));
    }
};

// Reads a Radiance image as the format's readers do: run-length encoded scanlines between 8 and
// 32767 pixels wide that start with 2, 2, and flat pixels otherwise.
bool
readRgbe(const Bytes& data, Latlong& image, std::string& error)
{
    const std::string text(reinterpret_cast<const char*>(data.data()), data.size());
    const std::string magic = "#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y ";
    if (text.rfind(magic, 0) != 0)
    {
        error = "unexpected header";
        return false;
    }
    std::size_t position = magic.size();
    const auto end = text.find('\n', position);
    if (end == std::string::npos ||
        std::sscanf(text.c_str() + position, "%u +X %u", &image.height, &image.width) != 2)
    {
        error = "unexpected resolution line";
        return false;
    }
    position = end + 1;
    std::vector<std::uint8_t> rgbe(std::size_t(image.width) * image.height * 4);
    const auto byte = [&](std::size_t at) { return std::to_integer<std::uint8_t>(data.at(at)); };
    for (std::uint32_t row = 0; row < image.height; ++row)
    {
        auto* line = rgbe.data() + std::size_t(row) * image.width * 4;
        const bool encoded = image.width >= 8 && image.width <= 32767;
        if (!encoded)
        {
            for (std::size_t i = 0; i < std::size_t(image.width) * 4; ++i)
            {
                line[i] = byte(position++);
            }
            continue;
        }
        if (byte(position) != 2 || byte(position + 1) != 2 ||
            ((byte(position + 2) << 8) | byte(position + 3)) != int(image.width))
        {
            error = "scanline " + std::to_string(row) + " is not run-length encoded";
            return false;
        }
        position += 4;
        for (std::size_t channel = 0; channel < 4; ++channel)
        {
            for (std::uint32_t column = 0; column < image.width;)
            {
                const auto count = byte(position++);
                const bool run = count > 128;
                const std::uint32_t length = run ? count - 128 : count;
                if (length == 0 || column + length > image.width)
                {
                    error = "scanline " + std::to_string(row) + " overruns";
                    return false;
                }
                for (std::uint32_t i = 0; i < length; ++i)
                {
                    line[std::size_t(column + i) * 4 + channel] =
                        byte(run ? position : position + i);
                }
                position += run ? 1 : length;
                column += length;
            }
        }
    }
    if (position != data.size())
    {
        error = "trailing bytes";
        return false;
    }
    image.pixels.resize(std::size_t(image.width) * image.height);
    for (std::size_t i = 0; i < image.pixels.size(); ++i)
    {
        const auto* p = rgbe.data() + i * 4;
        const double scale = p[3] ? std::ldexp(1.0, int(p[3]) - 136) : 0.0;
        image.pixels[i] = {p[0] * scale, p[1] * scale, p[2] * scale};
    }
    return true;
}

bool
convert(const std::array<Bytes, 6>& faces, std::uint32_t size, Latlong& image, std::string& error)
{
    std::array<std::span<const std::byte>, 6> spans;
    for (std::size_t face = 0; face < 6; ++face)
    {
        spans[face] = faces[face];
    }
    Bytes hdr;
    return gltfTexture::ConvertCubeToRgbe(spans, size, hdr, error) && readRgbe(hdr, image, error);
}

double
decoded(std::uint32_t code, double max = 255)
{
    return std::pow(code / max, 2.2);
}

// RGBE shares one exponent, so each channel is within about 1/256 of the largest.
bool
near(const std::array<double, 3>& actual, const std::array<double, 3>& expected, const char* label)
{
    const double largest = std::max({expected[0], expected[1], expected[2]});
    for (std::size_t channel = 0; channel < 3; ++channel)
    {
        if (std::abs(actual[channel] - expected[channel]) > largest * 0.01 + 1e-9)
        {
            std::cerr << label << ": (" << actual[0] << ", " << actual[1] << ", " << actual[2]
                      << "), expected (" << expected[0] << ", " << expected[1] << ", "
                      << expected[2] << ")\n";
            return false;
        }
    }
    return true;
}

bool
checkEntries()
{
    bool ok = gltfTexture::MakeLightEntry(3) == "lights/3.hdr";
    std::size_t index = 99;
    ok = ok && gltfTexture::ParseLightEntry("lights/0.hdr", index) && index == 0;
    ok = ok && gltfTexture::ParseLightEntry("lights/12.hdr", index) && index == 12;
    for (const char* entry :
         {"lights/03.hdr", "lights/.hdr", "lights/1.png", "images/0.png", "lights/1.hdr.hdr",
          "lights/-1.hdr", "lights/1x.hdr", "lights/1", "light/1.hdr", "./lights/1.hdr"})
    {
        if (gltfTexture::ParseLightEntry(entry, index))
        {
            std::cerr << entry << ": expected to be rejected\n";
            ok = false;
        }
    }
    if (!ok)
    {
        std::cerr << "unexpected light entry result\n";
    }
    return ok;
}

bool
checkSizes()
{
    bool ok = true;
    for (const auto [face, width, height] : {std::array<std::uint32_t, 3>{1, 4, 2},
                                             {256, 1024, 512},
                                             {2048, 8192, 4096},
                                             {4096, 8192, 4096},
                                             {16384, 8192, 4096}})
    {
        std::uint32_t w = 0;
        std::uint32_t h = 0;
        gltfTexture::LatlongSize(face, w, h);
        if (w != width || h != height)
        {
            std::cerr << "faces of " << face << ": latlong " << w << " by " << h << "\n";
            ok = false;
        }
    }
    return ok;
}

// Each stored quadrant of each face has its own color. Toward a quadrant, the latlong must show it:
// rows are reversed, so the side faces have +Y at the bottom as stored, and the faces' stored
// right and down are the directions below, read from OpenGL's cube map table.
bool
checkOrientation()
{
    constexpr std::uint32_t size = 16;
    struct Frame
    {
        std::array<double, 3> axis;
        std::array<double, 3> right;
        std::array<double, 3> down;
    };
    const std::array<Frame, 6> frames = {{
        {{1, 0, 0}, {0, 0, -1}, {0, 1, 0}},
        {{-1, 0, 0}, {0, 0, 1}, {0, 1, 0}},
        {{0, 1, 0}, {1, 0, 0}, {0, 0, -1}},
        {{0, -1, 0}, {1, 0, 0}, {0, 0, 1}},
        {{0, 0, 1}, {1, 0, 0}, {0, 1, 0}},
        {{0, 0, -1}, {-1, 0, 0}, {0, 1, 0}},
    }};
    const auto code = [](std::size_t face, std::size_t quadrant)
    { return std::uint32_t(20 + face * 36 + quadrant * 9); };
    std::array<Bytes, 6> faces;
    for (std::size_t face = 0; face < 6; ++face)
    {
        faces[face] = png(size, 2, 8,
                          [&](std::uint32_t x, std::uint32_t y)
                          {
                              const auto quadrant =
                                  std::size_t(x >= size / 2) + 2 * (y >= size / 2);
                              return std::vector<std::uint32_t>{code(face, quadrant), 128, 64};
                          });
    }
    Latlong image;
    std::string error;
    if (!convert(faces, size, image, error))
    {
        std::cerr << "orientation: " << error << "\n";
        return false;
    }
    if (image.width != 64 || image.height != 32)
    {
        std::cerr << "orientation: unexpected size\n";
        return false;
    }
    bool ok = true;
    for (std::size_t face = 0; face < 6; ++face)
    {
        const auto& frame = frames[face];
        for (std::size_t quadrant = 0; quadrant < 4; ++quadrant)
        {
            const double across = quadrant % 2 ? 0.5 : -0.5;
            const double below = quadrant / 2 ? 0.5 : -0.5;
            std::array<double, 3> direction;
            for (std::size_t i = 0; i < 3; ++i)
            {
                direction[i] = frame.axis[i] + across * frame.right[i] + below * frame.down[i];
            }
            const auto label =
                "face " + std::to_string(face) + " quadrant " + std::to_string(quadrant);
            ok = near(image.toward(direction[0], direction[1], direction[2]),
                      {decoded(code(face, quadrant)), decoded(128), decoded(64)}, label.c_str()) &&
                 ok;
        }
    }
    return ok;
}

// Uniform faces of different colors: the latlong shows each at its face's center, and every pixel
// is one face's color, because sampling clamps within a face rather than blending across an edge.
bool
checkEdges()
{
    constexpr std::uint32_t size = 8;
    std::array<Bytes, 6> faces;
    std::array<std::array<double, 3>, 6> colors;
    for (std::size_t face = 0; face < 6; ++face)
    {
        const std::vector<std::uint32_t> pixel = {std::uint32_t(face & 1 ? 255 : 40),
                                                  std::uint32_t(face & 2 ? 255 : 40),
                                                  std::uint32_t(face & 4 ? 255 : 40)};
        faces[face] = uniformPng(size, 2, 8, pixel);
        colors[face] = {decoded(pixel[0]), decoded(pixel[1]), decoded(pixel[2])};
    }
    Latlong image;
    std::string error;
    if (!convert(faces, size, image, error))
    {
        std::cerr << "edges: " << error << "\n";
        return false;
    }
    const std::array<std::array<double, 3>, 6> axes = {
        {{1, 0, 0}, {-1, 0, 0}, {0, 1, 0}, {0, -1, 0}, {0, 0, 1}, {0, 0, -1}}};
    bool ok = true;
    for (std::size_t face = 0; face < 6; ++face)
    {
        const auto& axis = axes[face];
        ok = near(image.toward(axis[0], axis[1], axis[2]), colors[face],
                  ("center of face " + std::to_string(face)).c_str()) &&
             ok;
    }
    for (const auto& pixel : image.pixels)
    {
        if (std::none_of(colors.begin(), colors.end(),
                         [&](const auto& color)
                         {
                             const double largest = std::max({color[0], color[1], color[2]});
                             return std::equal(pixel.begin(), pixel.end(), color.begin(),
                                               [&](double a, double b)
                                               { return std::abs(a - b) <= largest * 0.01; });
                         }))
        {
            std::cerr << "edges: a pixel blends faces\n";
            return false;
        }
    }
    return ok;
}

// Each face is encoded differently, and each must decode as (c / max)^2.2 / (a / max).
bool
checkDecoding(const std::filesystem::path& fixtures)
{
    constexpr std::uint32_t size = 16;
    std::ifstream file(fixtures / "face.jpg", std::ios::binary);
    const std::vector<char> jpegText((std::istreambuf_iterator<char>(file)),
                                     std::istreambuf_iterator<char>());
    Bytes jpeg(jpegText.size());
    std::memcpy(jpeg.data(), jpegText.data(), jpeg.size());
    const std::array<Bytes, 6> faces = {
        // RGBD: 8-bit, with the color divided by an alpha of 64.
        uniformPng(size, 6, 8, {255, 128, 64, 64}),
        // RGBD at 16 bits.
        uniformPng(size, 6, 16, {65535, 32768, 0, 16384}),
        // LDR gray, and gray with alpha.
        uniformPng(size, 0, 8, {128}),
        uniformPng(size, 4, 8, {255, 128}),
        // Alpha 0 is black.
        uniformPng(size, 6, 8, {255, 255, 255, 0}),
        // A JPEG of (200, 100, 50).
        jpeg,
    };
    const std::array<std::array<double, 3>, 6> expected = {{
        {decoded(255) / (64 / 255.0), decoded(128) / (64 / 255.0), decoded(64) / (64 / 255.0)},
        {decoded(65535, 65535) / (16384 / 65535.0), decoded(32768, 65535) / (16384 / 65535.0), 0},
        {decoded(128), decoded(128), decoded(128)},
        {1 / (128 / 255.0), 1 / (128 / 255.0), 1 / (128 / 255.0)},
        {0, 0, 0},
        {decoded(200), decoded(100), decoded(50)},
    }};
    Latlong image;
    std::string error;
    if (!convert(faces, size, image, error))
    {
        std::cerr << "decoding: " << error << "\n";
        return false;
    }
    const std::array<std::array<double, 3>, 6> axes = {
        {{1, 0, 0}, {-1, 0, 0}, {0, 1, 0}, {0, -1, 0}, {0, 0, 1}, {0, 0, -1}}};
    const std::array<const char*, 6> labels = {"8-bit RGBD",     "16-bit RGBD", "gray",
                                               "gray and alpha", "alpha 0",     "JPEG"};
    bool ok = true;
    for (std::size_t face = 0; face < 6; ++face)
    {
        auto wanted = expected[face];
        if (face == 5)
        {
            // A JPEG decoder may differ from the encoder's by a code.
            const auto& actual = image.toward(axes[face][0], axes[face][1], axes[face][2]);
            for (std::size_t channel = 0; channel < 3; ++channel)
            {
                const std::uint32_t codes[] = {200, 100, 50};
                if (actual[channel] < decoded(codes[channel] - 2) * 0.99 ||
                    actual[channel] > decoded(codes[channel] + 2) * 1.01)
                {
                    std::cerr << "JPEG channel " << channel << ": " << actual[channel] << "\n";
                    ok = false;
                }
            }
            continue;
        }
        ok =
            near(image.toward(axes[face][0], axes[face][1], axes[face][2]), wanted, labels[face]) &&
            ok;
    }
    return ok;
}

// One pixel faces give a latlong of 4 by 2, narrower than a run-length encoded scanline can be.
bool
checkSmallest()
{
    std::array<Bytes, 6> faces;
    for (auto& face : faces)
    {
        face = uniformPng(1, 2, 8, {255, 255, 255});
    }
    std::array<std::span<const std::byte>, 6> spans;
    std::copy(faces.begin(), faces.end(), spans.begin());
    Bytes hdr;
    std::string error;
    Latlong image;
    if (!gltfTexture::ConvertCubeToRgbe(spans, 1, hdr, error) || !readRgbe(hdr, image, error))
    {
        std::cerr << "one pixel faces: " << error << "\n";
        return false;
    }
    const std::string header = "#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 2 +X 4\n";
    if (hdr.size() != header.size() + 4 * 2 * 4 ||
        std::any_of(image.pixels.begin(), image.pixels.end(),
                    [](const auto& pixel) { return pixel != std::array<double, 3>{1, 1, 1}; }))
    {
        std::cerr << "one pixel faces: unexpected latlong\n";
        return false;
    }
    return true;
}

bool
checkRejected()
{
    constexpr std::uint32_t size = 4;
    const auto valid = uniformPng(size, 2, 8, {10, 20, 30});
    auto damaged = valid;
    // The stored block's length no longer matches its complement, so only decoding finds it. The
    // chunk's checksum is fixed so that the structure check passes.
    const std::size_t idat = 8 + 12 + 13;
    const std::size_t block = idat + 8 + 2 + 1;
    damaged[block] ^= std::byte(0x01);
    const auto length = std::size_t(std::to_integer<std::uint32_t>(damaged[idat]) << 24 |
                                    std::to_integer<std::uint32_t>(damaged[idat + 1]) << 16 |
                                    std::to_integer<std::uint32_t>(damaged[idat + 2]) << 8 |
                                    std::to_integer<std::uint32_t>(damaged[idat + 3]));
    const auto crc = crc32(damaged.data() + idat + 4, length + 4);
    for (std::size_t i = 0; i < 4; ++i)
    {
        damaged[idat + 8 + length + i] = std::byte((crc >> (24 - 8 * i)) & 0xFF);
    }
    const Bytes webp = {std::byte('R'), std::byte('I'), std::byte('F'), std::byte('F'),
                        std::byte(4),   std::byte(0),   std::byte(0),   std::byte(0),
                        std::byte('W'), std::byte('E'), std::byte('B'), std::byte('P')};
    struct Case
    {
        const char* label;
        Bytes face;
        const char* message;
    };
    const std::array<Case, 6> cases = {{
        {"wrong size", uniformPng(size * 2, 2, 8, {10, 20, 30}), "image is not 4 pixels square"},
        {"not square", declaring(valid, size, size + 1), "image is not 4 pixels square"},
        {"WebP", webp, "image is not PNG or JPEG"},
        {"too large", declaring(valid, 16385, 16385), "wider or taller than 16384 pixels"},
        {"damaged structure", Bytes(valid.begin(), valid.end() - 12), "PNG image is damaged"},
        {"damaged body", damaged, "PNG image could not be decoded"},
    }};
    bool ok = true;
    for (const auto& item : cases)
    {
        std::array<Bytes, 6> faces;
        faces.fill(valid);
        faces[3] = item.face;
        Latlong image;
        std::string error;
        if (convert(faces, size, image, error))
        {
            std::cerr << item.label << ": expected an error\n";
            ok = false;
            continue;
        }
        if (error.find("face -Y: ") != 0 || error.find(item.message) == std::string::npos)
        {
            std::cerr << item.label << ": unexpected error '" << error << "'\n";
            ok = false;
        }
    }
    return ok;
}

} // namespace

int
main(int argc, char** argv)
{
    if (argc != 2)
    {
        std::cerr << "usage: image_based_light_test <fixtures>\n";
        return 2;
    }
    bool ok = checkEntries();
    ok = checkSizes() && ok;
    ok = checkOrientation() && ok;
    ok = checkEdges() && ok;
    ok = checkDecoding(argv[1]) && ok;
    ok = checkSmallest() && ok;
    ok = checkRejected() && ok;
    return ok ? 0 : 1;
}
