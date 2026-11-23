// SPDX-License-Identifier: Apache-2.0
#include "gltfCore/parse.h"
#include "gltfCore/xmp.h"
#include "gltf_test_data.h"

#include <cstddef>
#include <iostream>
#include <string>
#include <string_view>
#include <vector>

namespace
{

constexpr std::string_view kXmp = R"({
  "asset": {"version": "2.0", "extensions": {"KHR_xmp_json_ld": {"packet": 1}}},
  "extensionsUsed": ["KHR_xmp_json_ld"],
  "extensions": {"KHR_xmp_json_ld": {"packets": [
    {"@context": {"dc": "http://purl.org/dc/elements/1.1/"}, "dc:title": "Zero"},
    {"@context": {"dc": "http://purl.org/dc/elements/1.1/"}, "z": null, "a": [1, 2.5, true],
     "n": 18446744073709551615, "o": {"k": -1}}
  ]}},
  "scenes": [{}, {"extensions": {"KHR_xmp_json_ld": {"packet": 0}}}],
  "materials": [{}, {}, {"extensions": {"KHR_xmp_json_ld": {"packet": 1}}}]
})";

bool
check(bool condition, std::string_view label)
{
    if (!condition)
    {
        std::cerr << label << '\n';
    }
    return condition;
}

bool
readsPackets(std::string_view format, const std::vector<std::byte>& bytes)
{
    using Kind = gltfCore::JsonValue::Kind;
    std::string error;
    gltfCore::XmpMetadata metadata;
    if (!check(gltfCore::Parse(bytes.data(), bytes.size(), error).error() == fastgltf::Error::None,
               error) ||
        !check(gltfCore::ReadXmpMetadata(bytes.data(), bytes.size(), metadata, error), error))
    {
        return false;
    }
    const auto& packet = metadata.packets.at(1);
    const auto& members = packet.members;
    const bool ok =
        check(metadata.packets.size() == 2, "packet count") &&
        check(metadata.asset == 1u, "asset packet") &&
        check(metadata.scenes.size() == 2 && !metadata.scenes[0] && metadata.scenes[1] == 0u,
              "scene packets") &&
        check(metadata.materials.size() == 3 &&
                  gltfCore::XmpPacketOf(metadata.materials, 2) == 1u &&
                  !gltfCore::XmpPacketOf(metadata.materials, 7),
              "material packets") &&
        check(metadata.nodes.empty() && metadata.images.empty(), "kinds without references") &&
        // Members keep the file's order.
        check(members.size() == 5 && members[0].key == "@context" && members[1].key == "z" &&
                  members[2].key == "a" && members[3].key == "n" && members[4].key == "o",
              "member order") &&
        check(members[1].value.kind == Kind::Null && members[1].value.string == "null", "null") &&
        check(members[2].value.kind == Kind::Array && members[2].value.string == "[1,2.5,true]" &&
                  members[2].value.elements.size() == 3 &&
                  members[2].value.elements[0].kind == Kind::Integer &&
                  members[2].value.elements[1].kind == Kind::Real &&
                  members[2].value.elements[2].kind == Kind::Boolean,
              "array") &&
        check(members[3].value.kind == Kind::Real &&
                  members[3].value.real == 18446744073709551615.0,
              "unsigned beyond int64") &&
        check(members[4].value.kind == Kind::Object &&
                  members[4].value.members.at(0).value.integer == -1,
              "object");
    if (!ok)
    {
        std::cerr << format << '\n';
    }
    return ok;
}

bool
rejects(std::string_view label, const std::string& json, std::string_view message)
{
    const auto bytes = gltfTestData::bytesOf(json);
    std::string parseError;
    std::string readError;
    gltfCore::XmpMetadata metadata;
    const bool parseFailed =
        gltfCore::Parse(bytes.data(), bytes.size(), parseError).error() != fastgltf::Error::None;
    const bool readFailed =
        !gltfCore::ReadXmpMetadata(bytes.data(), bytes.size(), metadata, readError);
    return check(parseFailed && parseError.find(message) != std::string::npos, label) &&
           check(readFailed && readError.find(message) != std::string::npos, label) &&
           check(metadata.packets.empty(), label);
}

} // namespace

int
main()
{
    const std::string base = R"({"asset":{"version":"2.0"},"extensionsUsed":["KHR_xmp_json_ld"],)";
    std::string error;
    gltfCore::XmpMetadata metadata;
    const auto plain = gltfTestData::bytesOf(R"({"asset":{"version":"2.0"}})");
    const bool none =
        check(gltfCore::ReadXmpMetadata(plain.data(), plain.size(), metadata, error) &&
                  metadata.packets.empty() && !metadata.asset,
              "file without the extension");
    const bool results =
        readsPackets("glTF", gltfTestData::bytesOf(kXmp)) &&
        readsPackets("GLB", gltfTestData::glbOf(std::string(kXmp))) &&
        rejects("empty packets", base + R"("extensions":{"KHR_xmp_json_ld":{"packets":[]}}})",
                "nonempty packets array") &&
        rejects("no context", base + R"("extensions":{"KHR_xmp_json_ld":{"packets":[{}]}}})",
                "packet 0 has no @context object") &&
        rejects("node reference",
                base + R"("extensions":{"KHR_xmp_json_ld":{"packets":[{"@context":{}}]}},)" +
                    R"("nodes":[{},{"extensions":{"KHR_xmp_json_ld":{"packet":3}}}]})",
                "glTF node 1: KHR_xmp_json_ld packet 3 is out of range") &&
        rejects(
            "undeclared",
            R"({"asset":{"version":"2.0"},"extensions":{"KHR_xmp_json_ld":{"packets":[{"@context":{}}]}}})",
            "must be declared in extensionsUsed");
    return none && results ? 0 : 1;
}
