// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <fastgltf/types.hpp>

#include <cstddef>
#include <span>
#include <string>
#include <unordered_map>
#include <vector>

class ExternalBufferLoader {
public:
    explicit ExternalBufferLoader(std::string sourcePath);

    [[nodiscard]] bool Load(const fastgltf::Buffer& buffer,
                            std::span<const std::byte>& bytes, std::string& error);

private:
    std::string sourcePath_;
    std::unordered_map<const fastgltf::Buffer*, std::vector<std::byte>> buffers_;
};
