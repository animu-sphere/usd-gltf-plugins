#include <fastgltf/core.hpp>

#include <cstddef>
#include <cstdint>
#include <iostream>
#include <string>
#include <string_view>
#include <vector>

namespace {

void appendUint32(std::vector<std::byte>& bytes, std::uint32_t value)
{
    for (unsigned int shift = 0; shift < 32; shift += 8) {
        bytes.push_back(static_cast<std::byte>((value >> shift) & 0xff));
    }
}

bool checkParse(const std::vector<std::byte>& bytes, bool expectSuccess)
{
    auto data = fastgltf::GltfDataBuffer::FromBytes(bytes.data(), bytes.size());
    if (data.error() != fastgltf::Error::None) {
        std::cerr << "Failed to allocate input buffer\n";
        return false;
    }

    fastgltf::Parser parser;
    auto asset = parser.loadGltf(data.get(), {}, fastgltf::Options::None);
    if ((asset.error() == fastgltf::Error::None) != expectSuccess) {
        std::cerr << "Unexpected parse result: " << fastgltf::getErrorMessage(asset.error()) << '\n';
        return false;
    }
    if (expectSuccess && (asset->nodes.size() != 1 || asset->nodes[0].name != "Root")) {
        std::cerr << "Parsed asset did not preserve the node\n";
        return false;
    }
    return true;
}

std::vector<std::byte> bytesOf(std::string_view text)
{
    const auto* begin = reinterpret_cast<const std::byte*>(text.data());
    return {begin, begin + text.size()};
}

std::vector<std::byte> glbOf(std::string text)
{
    while (text.size() % 4 != 0) {
        text.push_back(' ');
    }
    std::vector<std::byte> bytes;
    appendUint32(bytes, 0x46546c67);
    appendUint32(bytes, 2);
    appendUint32(bytes, static_cast<std::uint32_t>(20 + text.size()));
    appendUint32(bytes, static_cast<std::uint32_t>(text.size()));
    appendUint32(bytes, 0x4e4f534a);
    auto json = bytesOf(text);
    bytes.insert(bytes.end(), json.begin(), json.end());
    return bytes;
}

} // namespace

int main()
{
    const std::string json = R"({"asset":{"version":"2.0"},"nodes":[{"name":"Root"}]})";
    const std::string requiredExtension =
        R"({"asset":{"version":"2.0"},"extensionsUsed":["UNKNOWN_extension"],"extensionsRequired":["UNKNOWN_extension"]})";
    const auto glb = glbOf(json);
    auto truncatedGlb = glb;
    truncatedGlb.pop_back();
    return checkParse(bytesOf(json), true) && checkParse(glb, true) &&
                   checkParse(bytesOf("{"), false) && checkParse(truncatedGlb, false) &&
                   checkParse(bytesOf(requiredExtension), false)
               ? 0
               : 1;
}
