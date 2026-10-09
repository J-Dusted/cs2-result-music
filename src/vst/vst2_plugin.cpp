#include "mic_injector_core.h"

#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif

#include <windows.h>

#include <cstdint>
#include <cstring>
#include <new>

namespace {

constexpr std::int32_t kEffectMagic = 0x56737450;  // 'VstP'
constexpr std::int32_t kEffectCanReplacing = 1 << 4;
constexpr std::int32_t kPlugCategEffect = 1;

struct AEffect;
using AudioMasterCallback = std::intptr_t (*)(
    AEffect*,
    std::int32_t,
    std::int32_t,
    std::intptr_t,
    void*,
    float);

struct AEffect {
    std::int32_t magic = 0;
    std::intptr_t (*dispatcher)(
        AEffect*,
        std::int32_t,
        std::int32_t,
        std::intptr_t,
        void*,
        float) = nullptr;
    void (*process)(AEffect*, float**, float**, std::int32_t) = nullptr;
    void (*set_parameter)(AEffect*, std::int32_t, float) = nullptr;
    float (*get_parameter)(AEffect*, std::int32_t) = nullptr;
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
    void (*process_replacing)(AEffect*, float**, float**, std::int32_t) = nullptr;
};

constexpr std::int32_t effOpen = 0;
constexpr std::int32_t effClose = 1;
constexpr std::int32_t effSetSampleRate = 10;
constexpr std::int32_t effSetBlockSize = 11;
constexpr std::int32_t effMainsChanged = 12;
constexpr std::int32_t effGetPlugCategory = 35;
constexpr std::int32_t effGetEffectName = 45;
constexpr std::int32_t effGetVendorString = 47;
constexpr std::int32_t effGetProductString = 48;
constexpr std::int32_t effGetVendorVersion = 49;
constexpr std::int32_t effCanDo = 51;
constexpr std::int32_t effGetVstVersion = 58;
constexpr std::int32_t effStartProcess = 71;
constexpr std::int32_t effStopProcess = 72;

void CopyText(char* destination, const char* source) {
    if (destination == nullptr) {
        return;
    }
    std::strncpy(destination, source, 63);
    destination[63] = '\0';
}

class MicInjectorPlugin {
public:
    explicit MicInjectorPlugin(AudioMasterCallback host)
        : host_(host),
          core_(
              cs2music::MicInjectorCore::DefaultCommandPath(),
              true) {
        effect_.magic = kEffectMagic;
        effect_.dispatcher = &MicInjectorPlugin::DispatcherThunk;
        effect_.process = nullptr;
        effect_.set_parameter = nullptr;
        effect_.get_parameter = nullptr;
        effect_.num_programs = 1;
        effect_.num_params = 0;
        effect_.num_inputs = 2;
        effect_.num_outputs = 2;
        effect_.flags = kEffectCanReplacing;
        effect_.initial_delay = 0;
        effect_.unknown_float = 1.0f;
        effect_.ptr3 = this;
        effect_.unique_id = 0x4353324d;  // 'CS2M'
        effect_.version = 1000;
        effect_.process_replacing = &MicInjectorPlugin::ProcessReplacingThunk;
    }

    AEffect* effect() { return &effect_; }

private:
    static MicInjectorPlugin* Self(AEffect* effect) {
        return effect == nullptr
                   ? nullptr
                   : static_cast<MicInjectorPlugin*>(effect->ptr3);
    }

    static std::intptr_t DispatcherThunk(
        AEffect* effect,
        std::int32_t opcode,
        std::int32_t index,
        std::intptr_t value,
        void* ptr,
        float option) {
        MicInjectorPlugin* plugin = Self(effect);
        return plugin == nullptr
                   ? 0
                   : plugin->Dispatch(opcode, index, value, ptr, option);
    }

    static void ProcessReplacingThunk(
        AEffect* effect,
        float** inputs,
        float** outputs,
        std::int32_t frame_count) {
        MicInjectorPlugin* plugin = Self(effect);
        if (plugin != nullptr) {
            plugin->core_.Process(inputs, outputs, frame_count);
        }
    }

    std::intptr_t Dispatch(
        std::int32_t opcode,
        std::int32_t index,
        std::intptr_t value,
        void* ptr,
        float option) {
        switch (opcode) {
            case effOpen:
                return 1;
            case effClose:
                delete this;
                return 0;
            case effSetSampleRate:
                core_.SetSampleRate(option);
                return 1;
            case effSetBlockSize:
                core_.SetBlockSize(static_cast<std::uint32_t>(
                    value > 0 ? value : 0));
                return 1;
            case effMainsChanged:
            case effStartProcess:
            case effStopProcess:
                return 1;
            case effGetEffectName:
                CopyText(
                    static_cast<char*>(ptr),
                    "CS2 Result Music Mic Injector");
                return 1;
            case effGetVendorString:
                CopyText(static_cast<char*>(ptr), "CS2 Result Music");
                return 1;
            case effGetProductString:
                CopyText(
                    static_cast<char*>(ptr),
                    "CS2MicInjector");
                return 1;
            case effGetVendorVersion:
                return 1000;
            case effGetVstVersion:
                return 2400;
            case effGetPlugCategory:
                return kPlugCategEffect;
            case effCanDo: {
                const char* text = static_cast<const char*>(ptr);
                if (text != nullptr &&
                    (std::strcmp(text, "startStopProcess") == 0 ||
                     std::strcmp(text, "receiveVstEvents") == 0 ||
                     std::strcmp(text, "receiveVstMidiEvent") == 0)) {
                    return 1;
                }
                return 0;
            }
            default:
                (void)index;
                return 0;
        }
    }

    AudioMasterCallback host_ = nullptr;
    cs2music::MicInjectorCore core_;
    AEffect effect_{};
};

}  // namespace

extern "C" __declspec(dllexport) AEffect* VSTPluginMain(
    AudioMasterCallback callback) {
    auto* plugin = new (std::nothrow) MicInjectorPlugin(callback);
    return plugin == nullptr ? nullptr : plugin->effect();
}
