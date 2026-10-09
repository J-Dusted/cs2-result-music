#include "command_parser.h"

#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif

#include <windows.h>

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cwctype>
#include <limits>
#include <utility>
#include <vector>

namespace cs2music {
namespace {

enum class JsonKind {
    Null,
    Boolean,
    Number,
    String,
    Object,
    Array,
};

struct JsonValue {
    JsonKind kind = JsonKind::Null;
    bool boolean = false;
    double number = 0.0;
    std::string string;
    std::vector<std::pair<std::string, JsonValue>> object;
    std::vector<JsonValue> array;
};

void AppendUtf8(std::string& output, std::uint32_t code_point) {
    if (code_point <= 0x7f) {
        output.push_back(static_cast<char>(code_point));
    } else if (code_point <= 0x7ff) {
        output.push_back(static_cast<char>(0xc0 | (code_point >> 6)));
        output.push_back(static_cast<char>(0x80 | (code_point & 0x3f)));
    } else if (code_point <= 0xffff) {
        output.push_back(static_cast<char>(0xe0 | (code_point >> 12)));
        output.push_back(static_cast<char>(0x80 | ((code_point >> 6) & 0x3f)));
        output.push_back(static_cast<char>(0x80 | (code_point & 0x3f)));
    } else {
        output.push_back(static_cast<char>(0xf0 | (code_point >> 18)));
        output.push_back(static_cast<char>(0x80 | ((code_point >> 12) & 0x3f)));
        output.push_back(static_cast<char>(0x80 | ((code_point >> 6) & 0x3f)));
        output.push_back(static_cast<char>(0x80 | (code_point & 0x3f)));
    }
}

class JsonParser {
public:
    explicit JsonParser(std::string_view text) : text_(text) {}

    bool Parse(JsonValue& value) {
        SkipWhitespace();
        if (!ParseValue(value)) {
            return false;
        }
        SkipWhitespace();
        return position_ == text_.size();
    }

    const std::string& error() const { return error_; }

private:
    bool ParseValue(JsonValue& value) {
        if (position_ >= text_.size()) {
            return Fail("Unexpected end of JSON");
        }
        switch (text_[position_]) {
            case '{':
                return ParseObject(value);
            case '[':
                return ParseArray(value);
            case '"':
                value.kind = JsonKind::String;
                return ParseString(value.string);
            case 't':
                return ParseLiteral("true", true, value);
            case 'f':
                return ParseLiteral("false", false, value);
            case 'n':
                return ParseLiteral("null", false, value, true);
            default:
                return ParseNumber(value);
        }
    }

    bool ParseObject(JsonValue& value) {
        value.kind = JsonKind::Object;
        ++position_;
        SkipWhitespace();
        if (Consume('}')) {
            return true;
        }
        while (true) {
            std::string key;
            if (!ParseString(key)) {
                return false;
            }
            SkipWhitespace();
            if (!Consume(':')) {
                return Fail("Expected ':' after object key");
            }
            SkipWhitespace();
            JsonValue child;
            if (!ParseValue(child)) {
                return false;
            }
            value.object.emplace_back(std::move(key), std::move(child));
            SkipWhitespace();
            if (Consume('}')) {
                return true;
            }
            if (!Consume(',')) {
                return Fail("Expected ',' or '}' in object");
            }
            SkipWhitespace();
        }
    }

    bool ParseArray(JsonValue& value) {
        value.kind = JsonKind::Array;
        ++position_;
        SkipWhitespace();
        if (Consume(']')) {
            return true;
        }
        while (true) {
            JsonValue child;
            if (!ParseValue(child)) {
                return false;
            }
            value.array.emplace_back(std::move(child));
            SkipWhitespace();
            if (Consume(']')) {
                return true;
            }
            if (!Consume(',')) {
                return Fail("Expected ',' or ']' in array");
            }
            SkipWhitespace();
        }
    }

    bool ParseString(std::string& value) {
        if (!Consume('"')) {
            return Fail("Expected string");
        }
        value.clear();
        while (position_ < text_.size()) {
            const unsigned char character =
                static_cast<unsigned char>(text_[position_++]);
            if (character == '"') {
                return true;
            }
            if (character < 0x20) {
                return Fail("Control character inside string");
            }
            if (character != '\\') {
                value.push_back(static_cast<char>(character));
                continue;
            }
            if (position_ >= text_.size()) {
                return Fail("Incomplete string escape");
            }
            const char escape = text_[position_++];
            switch (escape) {
                case '"':
                case '\\':
                case '/':
                    value.push_back(escape);
                    break;
                case 'b':
                    value.push_back('\b');
                    break;
                case 'f':
                    value.push_back('\f');
                    break;
                case 'n':
                    value.push_back('\n');
                    break;
                case 'r':
                    value.push_back('\r');
                    break;
                case 't':
                    value.push_back('\t');
                    break;
                case 'u': {
                    std::uint32_t code_point = 0;
                    if (!ParseHex4(code_point)) {
                        return false;
                    }
                    if (code_point >= 0xd800 && code_point <= 0xdbff) {
                        if (position_ + 1 >= text_.size() ||
                            text_[position_] != '\\' ||
                            text_[position_ + 1] != 'u') {
                            return Fail("High surrogate without low surrogate");
                        }
                        position_ += 2;
                        std::uint32_t low = 0;
                        if (!ParseHex4(low) || low < 0xdc00 || low > 0xdfff) {
                            return Fail("Invalid low surrogate");
                        }
                        code_point =
                            0x10000 + ((code_point - 0xd800) << 10) +
                            (low - 0xdc00);
                    } else if (code_point >= 0xdc00 && code_point <= 0xdfff) {
                        return Fail("Unexpected low surrogate");
                    }
                    AppendUtf8(value, code_point);
                    break;
                }
                default:
                    return Fail("Unsupported string escape");
            }
        }
        return Fail("Unterminated string");
    }

    bool ParseHex4(std::uint32_t& value) {
        if (position_ + 4 > text_.size()) {
            return Fail("Incomplete Unicode escape");
        }
        value = 0;
        for (int index = 0; index < 4; ++index) {
            const char character = text_[position_++];
            value <<= 4;
            if (character >= '0' && character <= '9') {
                value |= static_cast<std::uint32_t>(character - '0');
            } else if (character >= 'a' && character <= 'f') {
                value |= static_cast<std::uint32_t>(character - 'a' + 10);
            } else if (character >= 'A' && character <= 'F') {
                value |= static_cast<std::uint32_t>(character - 'A' + 10);
            } else {
                return Fail("Invalid Unicode escape");
            }
        }
        return true;
    }

    bool ParseLiteral(
        std::string_view literal,
        bool boolean,
        JsonValue& value,
        bool null_value = false) {
        if (text_.substr(position_, literal.size()) != literal) {
            return Fail("Invalid JSON literal");
        }
        position_ += literal.size();
        value.kind = null_value ? JsonKind::Null : JsonKind::Boolean;
        value.boolean = boolean;
        return true;
    }

    bool ParseNumber(JsonValue& value) {
        const std::size_t start = position_;
        if (position_ < text_.size() && text_[position_] == '-') {
            ++position_;
        }
        if (position_ >= text_.size()) {
            return Fail("Invalid number");
        }
        if (text_[position_] == '0') {
            ++position_;
        } else {
            if (!std::isdigit(static_cast<unsigned char>(text_[position_]))) {
                return Fail("Invalid number");
            }
            while (position_ < text_.size() &&
                   std::isdigit(static_cast<unsigned char>(text_[position_]))) {
                ++position_;
            }
        }
        if (position_ < text_.size() && text_[position_] == '.') {
            ++position_;
            const std::size_t fraction_start = position_;
            while (position_ < text_.size() &&
                   std::isdigit(static_cast<unsigned char>(text_[position_]))) {
                ++position_;
            }
            if (fraction_start == position_) {
                return Fail("Invalid number fraction");
            }
        }
        if (position_ < text_.size() &&
            (text_[position_] == 'e' || text_[position_] == 'E')) {
            ++position_;
            if (position_ < text_.size() &&
                (text_[position_] == '+' || text_[position_] == '-')) {
                ++position_;
            }
            const std::size_t exponent_start = position_;
            while (position_ < text_.size() &&
                   std::isdigit(static_cast<unsigned char>(text_[position_]))) {
                ++position_;
            }
            if (exponent_start == position_) {
                return Fail("Invalid number exponent");
            }
        }
        const std::string number_text(text_.substr(start, position_ - start));
        char* end = nullptr;
        const double number = std::strtod(number_text.c_str(), &end);
        if (end == nullptr || *end != '\0' || !std::isfinite(number)) {
            return Fail("Invalid finite number");
        }
        value.kind = JsonKind::Number;
        value.number = number;
        return true;
    }

    bool Consume(char expected) {
        if (position_ < text_.size() && text_[position_] == expected) {
            ++position_;
            return true;
        }
        return false;
    }

    void SkipWhitespace() {
        while (position_ < text_.size()) {
            const char character = text_[position_];
            if (character != ' ' && character != '\t' &&
                character != '\r' && character != '\n') {
                break;
            }
            ++position_;
        }
    }

    bool Fail(const char* message) {
        if (error_.empty()) {
            error_ = message;
        }
        return false;
    }

    std::string_view text_;
    std::size_t position_ = 0;
    std::string error_;
};

const JsonValue* FindMember(const JsonValue& object, std::string_view name) {
    if (object.kind != JsonKind::Object) {
        return nullptr;
    }
    for (const auto& member : object.object) {
        if (member.first == name) {
            return &member.second;
        }
    }
    return nullptr;
}

bool ReadUtf8File(
    const std::wstring& path,
    std::vector<char>& data,
    std::string& error) {
    HANDLE file = CreateFileW(
        path.c_str(),
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN,
        nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        error = "Could not open command file (Win32 error " +
                std::to_string(GetLastError()) + ")";
        return false;
    }
    LARGE_INTEGER size{};
    if (!GetFileSizeEx(file, &size) || size.QuadPart < 0 ||
        size.QuadPart > 64 * 1024) {
        error = "Command file is empty, invalid, or too large";
        CloseHandle(file);
        return false;
    }
    data.resize(static_cast<std::size_t>(size.QuadPart));
    DWORD total = 0;
    while (total < data.size()) {
        DWORD read = 0;
        if (!ReadFile(
                file,
                data.data() + total,
                static_cast<DWORD>(data.size() - total),
                &read,
                nullptr)) {
            error = "Could not read command file (Win32 error " +
                    std::to_string(GetLastError()) + ")";
            CloseHandle(file);
            return false;
        }
        if (read == 0) {
            break;
        }
        total += read;
    }
    data.resize(total);
    CloseHandle(file);
    if (data.empty()) {
        error = "Command file is empty";
        return false;
    }
    return true;
}

bool EndsWithExtension(
    const std::wstring& path,
    std::wstring_view extension) {
    if (path.size() < extension.size()) {
        return false;
    }
    const std::size_t offset = path.size() - extension.size();
    for (std::size_t index = 0; index < extension.size(); ++index) {
        if (std::towlower(path[offset + index]) !=
            std::towlower(extension[index])) {
            return false;
        }
    }
    return true;
}

}  // namespace

bool ParseCommandJson(
    std::string_view text,
    CommandRequest& request,
    std::string& error) {
    error.clear();
    JsonValue root;
    JsonParser parser(text);
    if (!parser.Parse(root)) {
        error = parser.error();
        return false;
    }
    if (root.kind != JsonKind::Object) {
        error = "Command JSON must be an object";
        return false;
    }

    const JsonValue* request_value = FindMember(root, "request");
    const JsonValue* op_value = FindMember(root, "op");
    if (request_value == nullptr || request_value->kind != JsonKind::Number ||
        request_value->number < 1 ||
        request_value->number >
            static_cast<double>(std::numeric_limits<std::uint64_t>::max())) {
        error = "Command request must be a positive integer";
        return false;
    }
    if (op_value == nullptr || op_value->kind != JsonKind::String) {
        error = "Command op must be a string";
        return false;
    }

    CommandRequest parsed;
    parsed.request = static_cast<std::uint64_t>(request_value->number);
    parsed.op = WideFromUtf8(op_value->string);
    std::transform(
        parsed.op.begin(),
        parsed.op.end(),
        parsed.op.begin(),
        [](wchar_t value) { return static_cast<wchar_t>(std::towlower(value)); });

    if (parsed.op != L"play" && parsed.op != L"stop") {
        error = "Command op must be play or stop";
        return false;
    }
    if (parsed.op == L"play") {
        const JsonValue* file_value = FindMember(root, "file");
        if (file_value == nullptr || file_value->kind != JsonKind::String) {
            error = "Play command requires a file string";
            return false;
        }
        parsed.file = WideFromUtf8(file_value->string);
        if (!IsAbsolutePath(parsed.file)) {
            error = "Audio file path must be absolute";
            return false;
        }
        if (!IsSupportedAudioPath(parsed.file)) {
            error = "Unsupported audio file extension";
            return false;
        }
        const JsonValue* gain_value = FindMember(root, "gain_db");
        if (gain_value != nullptr) {
            if (gain_value->kind != JsonKind::Number) {
                error = "gain_db must be a number";
                return false;
            }
            parsed.gain_db = static_cast<float>(gain_value->number);
            if (!std::isfinite(parsed.gain_db)) {
                error = "gain_db must be finite";
                return false;
            }
            parsed.gain_db = std::clamp(parsed.gain_db, -60.0f, 12.0f);
        }
    }

    request = std::move(parsed);
    return true;
}

bool ReadCommandFile(
    const std::wstring& path,
    CommandRequest& request,
    std::string& error) {
    std::vector<char> bytes;
    if (!ReadUtf8File(path, bytes, error)) {
        return false;
    }
    return ParseCommandJson(
        std::string_view(bytes.data(), bytes.size()),
        request,
        error);
}

bool IsSupportedAudioPath(const std::wstring& path) {
    return EndsWithExtension(path, L".mp3") ||
           EndsWithExtension(path, L".wav") ||
           EndsWithExtension(path, L".m4a") ||
           EndsWithExtension(path, L".aac") ||
           EndsWithExtension(path, L".flac");
}

bool IsAbsolutePath(const std::wstring& path) {
    if (path.size() >= 3 &&
        std::iswalpha(path[0]) != 0 &&
        path[1] == L':' &&
        (path[2] == L'\\' || path[2] == L'/')) {
        return true;
    }
    return path.size() >= 2 &&
           ((path[0] == L'\\' && path[1] == L'\\') ||
            (path[0] == L'/' && path[1] == L'/'));
}

std::string Utf8FromWide(const std::wstring& value) {
    if (value.empty()) {
        return {};
    }
    const int size = WideCharToMultiByte(
        CP_UTF8,
        WC_ERR_INVALID_CHARS,
        value.data(),
        static_cast<int>(value.size()),
        nullptr,
        0,
        nullptr,
        nullptr);
    if (size <= 0) {
        return {};
    }
    std::string result(static_cast<std::size_t>(size), '\0');
    if (WideCharToMultiByte(
            CP_UTF8,
            WC_ERR_INVALID_CHARS,
            value.data(),
            static_cast<int>(value.size()),
            result.data(),
            size,
            nullptr,
            nullptr) <= 0) {
        return {};
    }
    return result;
}

std::wstring WideFromUtf8(std::string_view value) {
    if (value.empty()) {
        return {};
    }
    const int size = MultiByteToWideChar(
        CP_UTF8,
        MB_ERR_INVALID_CHARS,
        value.data(),
        static_cast<int>(value.size()),
        nullptr,
        0);
    if (size <= 0) {
        return {};
    }
    std::wstring result(static_cast<std::size_t>(size), L'\0');
    if (MultiByteToWideChar(
            CP_UTF8,
            MB_ERR_INVALID_CHARS,
            value.data(),
            static_cast<int>(value.size()),
            result.data(),
            size) <= 0) {
        return {};
    }
    return result;
}

bool WriteTextFileAtomic(const std::wstring& path, std::string_view text) {
    const std::wstring directory = [&path]() {
        const std::size_t slash = path.find_last_of(L"\\/");
        return slash == std::wstring::npos ? std::wstring() : path.substr(0, slash);
    }();
    if (!directory.empty()) {
        CreateDirectoryW(directory.c_str(), nullptr);
    }

    const std::wstring temp_path =
        path + L".tmp." + std::to_wstring(GetCurrentProcessId()) + L"." +
        std::to_wstring(GetCurrentThreadId());
    HANDLE file = CreateFileW(
        temp_path.c_str(),
        GENERIC_WRITE,
        0,
        nullptr,
        CREATE_ALWAYS,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_WRITE_THROUGH,
        nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        return false;
    }

    bool ok = true;
    std::size_t total = 0;
    while (total < text.size()) {
        DWORD written = 0;
        const DWORD chunk = static_cast<DWORD>(
            std::min<std::size_t>(text.size() - total, 1u << 20));
        if (!WriteFile(file, text.data() + total, chunk, &written, nullptr) ||
            written == 0) {
            ok = false;
            break;
        }
        total += written;
    }
    if (ok) {
        ok = FlushFileBuffers(file) != FALSE;
    }
    CloseHandle(file);
    if (!ok) {
        DeleteFileW(temp_path.c_str());
        return false;
    }
    if (!MoveFileExW(
            temp_path.c_str(),
            path.c_str(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        DeleteFileW(temp_path.c_str());
        return false;
    }
    return true;
}

}  // namespace cs2music
