#include "command_parser.h"
#include "mic_injector_core.h"

#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif

#include <windows.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <string>
#include <vector>

namespace {

constexpr std::int32_t kEffectMagic = 0x56737450;

struct AEffectProbe;
using AudioMasterProbe = std::intptr_t (*)(
    AEffectProbe*,
    std::int32_t,
    std::int32_t,
    std::intptr_t,
    void*,
    float);

struct AEffectProbe {
    std::int32_t magic = 0;
    std::intptr_t (*dispatcher)(
        AEffectProbe*,
        std::int32_t,
        std::int32_t,
        std::intptr_t,
        void*,
        float) = nullptr;
    void (*process)(AEffectProbe*, float**, float**, std::int32_t) = nullptr;
    void (*set_parameter)(AEffectProbe*, std::int32_t, float) = nullptr;
    float (*get_parameter)(AEffectProbe*, std::int32_t) = nullptr;
    std::int32_t num_programs = 0;
    std::int32_t num_params = 0;
    std::int32_t num_inputs = 0;
    std::int32_t num_outputs = 0;
    std::int32_t flags = 0;
    void* ptr1 = nullptr;
    void* ptr2 = nullptr;
    std::int32_t initial_delay = 0;
    std::int32_t empty3a = 0;
    std::int32_t empty3b = 0;
    float unknown_float = 0.0f;
    void* ptr3 = nullptr;
    void* user = nullptr;
    std::int32_t unique_id = 0;
    std::int32_t version = 0;
    void (*process_replacing)(
        AEffectProbe*,
        float**,
        float**,
        std::int32_t) = nullptr;
};

int failures = 0;

void Check(bool condition, const char* message) {
    if (!condition) {
        std::cerr << "FAIL: " << message << "\n";
        ++failures;
    }
}

void CheckNear(float actual, float expected, const char* message) {
    if (std::fabs(actual - expected) > 0.0001f) {
        std::cerr << "FAIL: " << message << " expected=" << expected
                  << " actual=" << actual << "\n";
        ++failures;
    }
}

std::filesystem::path CreateTempDirectory() {
    std::filesystem::path directory =
        std::filesystem::current_path() / L"work" / L"native-temp";
    directory /= L"CS2MicInjectorTests-" +
                 std::to_wstring(GetCurrentProcessId()) + L"-" +
                 std::to_wstring(GetTickCount64());
    std::filesystem::create_directories(directory);
    return directory;
}

std::shared_ptr<cs2music::DecodedTrack> Track(
    std::uint64_t request,
    std::vector<float> samples,
    float gain = 1.0f) {
    auto track = std::make_shared<cs2music::DecodedTrack>();
    track->request = request;
    track->path = L"C:\\test.wav";
    track->samples = std::move(samples);
    track->gain = gain;
    return track;
}

void TestPassthrough() {
    cs2music::MicInjectorCore core(
        L"C:\\nonexistent\\command.txt",
        false);
    float input_left[4] = {0.1f, 0.2f, -0.3f, 0.4f};
    float input_right[4] = {0.5f, -0.6f, 0.7f, -0.8f};
    float output_left[4] = {};
    float output_right[4] = {};
    float* inputs[2] = {input_left, input_right};
    float* outputs[2] = {output_left, output_right};
    core.Process(inputs, outputs, 4);
    for (int index = 0; index < 4; ++index) {
        CheckNear(output_left[index], input_left[index], "left passthrough");
        CheckNear(output_right[index], input_right[index], "right passthrough");
    }
}

void TestMixAndSwitch() {
    cs2music::MicInjectorCore core(
        L"C:\\nonexistent\\command.txt",
        false);
    core.SetTrackForTesting(Track(1, {0.25f, 0.25f, 0.25f, 0.25f}));
    float input_left[4] = {0.1f, 0.1f, 0.1f, 0.1f};
    float input_right[4] = {0.2f, 0.2f, 0.2f, 0.2f};
    float output_left[4] = {};
    float output_right[4] = {};
    float* inputs[2] = {input_left, input_right};
    float* outputs[2] = {output_left, output_right};
    core.Process(inputs, outputs, 4);
    CheckNear(output_left[0], 0.35f, "left music mix");
    CheckNear(output_right[0], 0.45f, "right music mix");

    core.SetTrackForTesting(Track(2, {0.5f, 0.5f}));
    float next_input_left[2] = {0.0f, 0.0f};
    float next_input_right[2] = {0.0f, 0.0f};
    float next_output_left[2] = {};
    float next_output_right[2] = {};
    float* next_inputs[2] = {next_input_left, next_input_right};
    float* next_outputs[2] = {next_output_left, next_output_right};
    core.Process(next_inputs, next_outputs, 2);
    CheckNear(next_output_left[0], 0.5f, "new request starts at first sample");
}

void TestEndAndLimiter() {
    cs2music::MicInjectorCore core(
        L"C:\\nonexistent\\command.txt",
        false);
    core.SetTrackForTesting(Track(3, {1.5f}));
    float input[3] = {0.0f, 0.2f, 0.3f};
    float output[3] = {};
    float* inputs[2] = {input, nullptr};
    float* outputs[2] = {output, nullptr};
    core.Process(inputs, outputs, 3);
    Check(output[0] > 0.8f && output[0] < 1.0f, "soft limiter bounds peak");
    CheckNear(output[1], 0.2f, "track end falls back to passthrough");
    CheckNear(output[2], 0.3f, "track end keeps passthrough");
}

void TestCommandParsing() {
    cs2music::CommandRequest request;
    std::string error;
    const std::string json =
        R"({"request":42,"op":"play","file":"C:\\Music\\victory.mp3","gain_db":-7.5})";
    Check(
        cs2music::ParseCommandJson(json, request, error),
        "valid command parses");
    Check(request.request == 42, "request id parses");
    Check(request.file == L"C:\\Music\\victory.mp3", "backslash path parses");
    Check(std::fabs(request.gain_db + 7.5f) < 0.001f, "gain parses");

    const std::string malformed = R"({"request":42,"op":"play"})";
    Check(
        !cs2music::ParseCommandJson(malformed, request, error),
        "missing file is rejected");
    const std::string traversal =
        R"({"request":43,"op":"play","file":"..\\victory.mp3"})";
    Check(
        !cs2music::ParseCommandJson(traversal, request, error),
        "relative path is rejected");
}

bool WriteTestWave(const std::filesystem::path& path) {
    constexpr std::uint32_t sample_rate = 48000;
    constexpr std::uint16_t channels = 1;
    constexpr std::uint16_t bits = 16;
    constexpr std::uint32_t frames = 4800;
    const std::uint32_t data_size = frames * channels * bits / 8;
    std::ofstream file(path, std::ios::binary);
    if (!file) {
        return false;
    }
    const auto write_u16 = [&file](std::uint16_t value) {
        file.write(reinterpret_cast<const char*>(&value), sizeof(value));
    };
    const auto write_u32 = [&file](std::uint32_t value) {
        file.write(reinterpret_cast<const char*>(&value), sizeof(value));
    };
    file.write("RIFF", 4);
    write_u32(36 + data_size);
    file.write("WAVE", 4);
    file.write("fmt ", 4);
    write_u32(16);
    write_u16(1);
    write_u16(channels);
    write_u32(sample_rate);
    write_u32(sample_rate * channels * bits / 8);
    write_u16(channels * bits / 8);
    write_u16(bits);
    file.write("data", 4);
    write_u32(data_size);
    for (std::uint32_t frame = 0; frame < frames; ++frame) {
        const double phase =
            2.0 * 3.14159265358979323846 * 440.0 * frame / sample_rate;
        const auto sample = static_cast<std::int16_t>(
            std::sin(phase) * 12000.0);
        file.write(reinterpret_cast<const char*>(&sample), sizeof(sample));
    }
    return file.good();
}

void TestMediaFoundationDecode(const std::filesystem::path& directory) {
    const std::filesystem::path wave_path = directory / L"tone.wav";
    if (!WriteTestWave(wave_path)) {
        Check(false, "could create test wave");
        return;
    }
    std::shared_ptr<const cs2music::DecodedTrack> track;
    std::string error;
    const bool decoded =
        cs2music::DecodeAudioFile(wave_path.wstring(), 48000.0, track, error);
    Check(decoded, error.empty() ? "Media Foundation decodes wav" : error.c_str());
    if (decoded && track) {
        Check(track->samples.size() > 4000, "decoded sample count");
        const auto peak = *std::max_element(
            track->samples.begin(),
            track->samples.end(),
            [](float left, float right) {
                return std::fabs(left) < std::fabs(right);
            });
        Check(std::fabs(peak) > 0.2f, "decoded waveform is audible");
    }
}

std::intptr_t ProbeHostCallback(
    AEffectProbe*,
    std::int32_t,
    std::int32_t,
    std::intptr_t,
    void*,
    float) {
    return 0;
}

void TestVstEntryPoint() {
    wchar_t module_path[MAX_PATH]{};
    const DWORD length = GetModuleFileNameW(
        nullptr,
        module_path,
        static_cast<DWORD>(std::size(module_path)));
    if (length == 0 || length >= std::size(module_path)) {
        Check(false, "could locate native test executable");
        return;
    }
    const std::filesystem::path plugin_path =
        std::filesystem::path(module_path).parent_path() /
        L"CS2MicInjector.dll";
    HMODULE library = LoadLibraryW(plugin_path.c_str());
    Check(library != nullptr, "VST DLL loads");
    if (library == nullptr) {
        return;
    }
    using PluginMain = AEffectProbe* (*)(AudioMasterProbe);
    const auto plugin_main = reinterpret_cast<PluginMain>(
        GetProcAddress(library, "VSTPluginMain"));
    Check(plugin_main != nullptr, "VSTPluginMain export exists");
    if (plugin_main == nullptr) {
        FreeLibrary(library);
        return;
    }

    AEffectProbe* effect = plugin_main(&ProbeHostCallback);
    Check(effect != nullptr, "VSTPluginMain returns an effect");
    if (effect != nullptr) {
        Check(effect->magic == kEffectMagic, "VST magic is valid");
        Check(effect->num_inputs == 2, "VST declares two inputs");
        Check(effect->num_outputs == 2, "VST declares two outputs");
        Check(effect->process_replacing != nullptr, "VST has processReplacing");
        effect->dispatcher(effect, 10, 0, 0, nullptr, 48000.0f);
        effect->dispatcher(effect, 11, 0, 512, nullptr, 0.0f);
        float left_in[2] = {0.1f, 0.2f};
        float right_in[2] = {0.3f, 0.4f};
        float left_out[2] = {};
        float right_out[2] = {};
        float* inputs[2] = {left_in, right_in};
        float* outputs[2] = {left_out, right_out};
        effect->process_replacing(effect, inputs, outputs, 2);
        CheckNear(left_out[0], left_in[0], "VST passthrough left");
        CheckNear(right_out[1], right_in[1], "VST passthrough right");
        effect->dispatcher(effect, 1, 0, 0, nullptr, 0.0f);
    }
    FreeLibrary(library);
}

}  // namespace

int main() {
    const std::filesystem::path temporary = CreateTempDirectory();
    TestPassthrough();
    TestMixAndSwitch();
    TestEndAndLimiter();
    TestCommandParsing();
    TestMediaFoundationDecode(temporary);
    TestVstEntryPoint();
    std::error_code cleanup_error;
    std::filesystem::remove_all(temporary, cleanup_error);
    if (failures == 0) {
        std::cout << "All native tests passed.\n";
        return 0;
    }
    std::cerr << failures << " native test(s) failed.\n";
    return 1;
}
