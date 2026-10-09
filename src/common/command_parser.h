#pragma once

#include <cstdint>
#include <string>
#include <string_view>

namespace cs2music {

struct CommandRequest {
    std::uint64_t request = 0;
    std::wstring op;
    std::wstring file;
    float gain_db = -6.0f;
};

bool ParseCommandJson(
    std::string_view text,
    CommandRequest& request,
    std::string& error);

bool ReadCommandFile(
    const std::wstring& path,
    CommandRequest& request,
    std::string& error);

bool IsSupportedAudioPath(const std::wstring& path);
bool IsAbsolutePath(const std::wstring& path);

std::string Utf8FromWide(const std::wstring& value);
std::wstring WideFromUtf8(std::string_view value);

bool WriteTextFileAtomic(const std::wstring& path, std::string_view text);

}  // namespace cs2music
