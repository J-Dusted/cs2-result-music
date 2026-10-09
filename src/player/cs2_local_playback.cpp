#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif

#include <windows.h>
#include <mfapi.h>
#include <mferror.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <mmsystem.h>
#include <shellapi.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cwctype>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "command_parser.h"

namespace {

template <typename T>
class ComPtr {
public:
    ComPtr() = default;
    ~ComPtr() {
        if (value_ != nullptr) {
            value_->Release();
        }
    }
    ComPtr(const ComPtr&) = delete;
    ComPtr& operator=(const ComPtr&) = delete;
    T* Get() const { return value_; }
    T** Put() {
        Reset();
        return &value_;
    }
    T* operator->() const { return value_; }
    explicit operator bool() const { return value_ != nullptr; }
    void Reset() {
        if (value_ != nullptr) {
            value_->Release();
            value_ = nullptr;
        }
    }

private:
    T* value_ = nullptr;
};

void EnsureMediaFoundation() {
    static std::once_flag once;
    std::call_once(once, []() {
        MFStartup(MF_VERSION, MFSTARTUP_LITE);
    });
}

class ComInitialization {
public:
    ComInitialization() {
        result_ = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        owns_ = SUCCEEDED(result_);
    }
    ~ComInitialization() {
        if (owns_) {
            CoUninitialize();
        }
    }
    bool usable() const {
        return SUCCEEDED(result_) || result_ == RPC_E_CHANGED_MODE;
    }

private:
    HRESULT result_ = E_FAIL;
    bool owns_ = false;
};

class SourceReader {
public:
    ~SourceReader() = default;

    bool Open(const std::wstring& path, std::string& error) {
        EnsureMediaFoundation();
        HRESULT result = S_OK;
        result = MFCreateSourceReaderFromURL(
            path.c_str(),
            nullptr,
            reader_.Put());
        if (FAILED(result) || !reader_) {
            error = "Could not open audio file with Media Foundation";
            return false;
        }
        reader_->SetStreamSelection(
            static_cast<DWORD>(MF_SOURCE_READER_ALL_STREAMS),
            FALSE);
        reader_->SetStreamSelection(
            static_cast<DWORD>(MF_SOURCE_READER_FIRST_AUDIO_STREAM),
            TRUE);

        std::uint32_t native_rate = 0;
        ComPtr<IMFMediaType> native_type;
        if (SUCCEEDED(reader_->GetNativeMediaType(
                static_cast<DWORD>(MF_SOURCE_READER_FIRST_AUDIO_STREAM),
                0,
                native_type.Put()))) {
            native_type->GetUINT32(
                MF_MT_AUDIO_SAMPLES_PER_SECOND,
                &native_rate);
        }
        sample_rate_ = native_rate > 0 ? native_rate : 48000;

        use_float_ = true;
        if (!SetOutputType(32, MFAudioFormat_Float)) {
            use_float_ = false;
            if (!SetOutputType(16, MFAudioFormat_PCM)) {
                error = "No PCM decoder output type is available";
                return false;
            }
        }
        return true;
    }

    std::uint32_t sample_rate() const { return sample_rate_; }
    bool use_float() const { return use_float_; }

    bool ReadFrames(
        std::vector<float>& interleaved_stereo,
        bool& end_of_stream,
        std::string& error) {
        interleaved_stereo.clear();
        end_of_stream = false;
        while (interleaved_stereo.size() < 4096 * 2) {
            DWORD flags = 0;
            LONGLONG timestamp = 0;
            ComPtr<IMFSample> sample;
            const HRESULT result = reader_->ReadSample(
                static_cast<DWORD>(MF_SOURCE_READER_FIRST_AUDIO_STREAM),
                0,
                nullptr,
                &flags,
                &timestamp,
                sample.Put());
            if (FAILED(result)) {
                error = "Media Foundation read failed";
                return false;
            }
            if ((flags & MF_SOURCE_READERF_ENDOFSTREAM) != 0) {
                end_of_stream = true;
                return true;
            }
            if (!sample) {
                continue;
            }
            ComPtr<IMFMediaBuffer> buffer;
            if (FAILED(sample->ConvertToContiguousBuffer(buffer.Put())) || !buffer) {
                error = "Media Foundation sample conversion failed";
                return false;
            }
            BYTE* data = nullptr;
            DWORD length = 0;
            if (FAILED(buffer->Lock(&data, nullptr, &length)) || data == nullptr) {
                error = "Media Foundation sample lock failed";
                return false;
            }
            if (use_float_) {
                const std::size_t count = length / sizeof(float);
                const auto* source = reinterpret_cast<const float*>(data);
                interleaved_stereo.insert(
                    interleaved_stereo.end(),
                    source,
                    source + count);
            } else {
                const std::size_t count = length / sizeof(std::int16_t);
                const auto* source =
                    reinterpret_cast<const std::int16_t*>(data);
                const std::size_t old_size = interleaved_stereo.size();
                interleaved_stereo.resize(old_size + count);
                for (std::size_t index = 0; index < count; ++index) {
                    interleaved_stereo[old_size + index] =
                        static_cast<float>(source[index]) / 32768.0f;
                }
            }
            buffer->Unlock();
        }
        return true;
    }

private:
    bool SetOutputType(std::uint16_t bits, REFGUID subtype) {
        ComPtr<IMFMediaType> type;
        if (FAILED(MFCreateMediaType(type.Put()))) {
            return false;
        }
        type->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Audio);
        type->SetGUID(MF_MT_SUBTYPE, subtype);
        type->SetUINT32(MF_MT_AUDIO_NUM_CHANNELS, 2);
        type->SetUINT32(MF_MT_AUDIO_SAMPLES_PER_SECOND, sample_rate_);
        type->SetUINT32(MF_MT_AUDIO_BLOCK_ALIGNMENT, bits / 8 * 2);
        type->SetUINT32(
            MF_MT_AUDIO_AVG_BYTES_PER_SECOND,
            sample_rate_ * (bits / 8) * 2);
        type->SetUINT32(
            MF_MT_ALL_SAMPLES_INDEPENDENT,
            static_cast<UINT32>(TRUE));
        type->SetUINT32(MF_MT_AUDIO_BITS_PER_SAMPLE, bits);
        type->SetUINT32(MF_MT_AUDIO_VALID_BITS_PER_SAMPLE, bits);
        return SUCCEEDED(reader_->SetCurrentMediaType(
            static_cast<DWORD>(MF_SOURCE_READER_FIRST_AUDIO_STREAM),
            nullptr,
            type.Get()));
    }

    ComPtr<IMFSourceReader> reader_;
    std::uint32_t sample_rate_ = 48000;
    bool use_float_ = true;
};

struct WaveOutput {
    HWAVEOUT handle = nullptr;
    WAVEFORMATEX format{};
};

bool OpenWaveOutput(
    const std::wstring& requested_device,
    std::uint32_t sample_rate,
    WaveOutput& output,
    std::string& error) {
    UINT device_id = WAVE_MAPPER;
    if (!requested_device.empty()) {
        const UINT count = waveOutGetNumDevs();
        bool found = false;
        for (UINT index = 0; index < count; ++index) {
            WAVEOUTCAPSW caps{};
            if (waveOutGetDevCapsW(index, &caps, sizeof(caps)) != MMSYSERR_NOERROR) {
                continue;
            }
            std::wstring name = caps.szPname;
            std::wstring wanted = requested_device;
            std::transform(
                name.begin(),
                name.end(),
                name.begin(),
                [](wchar_t value) {
                    return static_cast<wchar_t>(std::towlower(value));
                });
            std::transform(
                wanted.begin(),
                wanted.end(),
                wanted.begin(),
                [](wchar_t value) {
                    return static_cast<wchar_t>(std::towlower(value));
                });
            if (name.find(wanted) != std::wstring::npos) {
                device_id = index;
                found = true;
                break;
            }
        }
        if (!found) {
            error = "Requested output device was not found";
            return false;
        }
    }

    output.format.wFormatTag = WAVE_FORMAT_PCM;
    output.format.nChannels = 2;
    output.format.nSamplesPerSec = sample_rate;
    output.format.wBitsPerSample = 16;
    output.format.nBlockAlign = 4;
    output.format.nAvgBytesPerSec = sample_rate * 4;
    output.format.cbSize = 0;
    const MMRESULT result = waveOutOpen(
        &output.handle,
        device_id,
        &output.format,
        0,
        0,
        CALLBACK_NULL);
    if (result != MMSYSERR_NOERROR) {
        error = "Could not open the requested audio output";
        return false;
    }
    return true;
}

std::uint64_t OutputPositionBytes(HWAVEOUT handle) {
    MMTIME time{};
    time.wType = TIME_BYTES;
    if (waveOutGetPosition(handle, &time, sizeof(time)) != MMSYSERR_NOERROR ||
        time.wType != TIME_BYTES) {
        return 0;
    }
    return time.u.cb;
}

class WaveStream {
public:
    static constexpr std::size_t kBufferCount = 4;
    static constexpr std::size_t kFramesPerBuffer = 4096;

    explicit WaveStream(HWAVEOUT handle) : handle_(handle) {
        for (auto& slot : slots_) {
            slot.samples.resize(kFramesPerBuffer * 2);
            slot.header.lpData =
                reinterpret_cast<LPSTR>(slot.samples.data());
            slot.header.dwBufferLength = static_cast<DWORD>(
                slot.samples.size() * sizeof(std::int16_t));
            slot.header.dwUser = reinterpret_cast<DWORD_PTR>(&slot);
        }
    }

    ~WaveStream() {
        Close();
    }

    void Close() {
        if (handle_ == nullptr) {
            return;
        }
        waveOutReset(handle_);
        for (auto& slot : slots_) {
            if ((slot.header.dwFlags & WHDR_PREPARED) != 0) {
                waveOutUnprepareHeader(handle_, &slot.header, sizeof(slot.header));
            }
        }
        waveOutClose(handle_);
        handle_ = nullptr;
    }

    bool Queue(
        std::size_t slot_index,
        const float* source,
        std::size_t sample_count,
        float gain) {
        if (handle_ == nullptr) {
            return false;
        }
        WaveSlot& slot = slots_[slot_index];
        if ((slot.header.dwFlags & WHDR_PREPARED) != 0) {
            const MMRESULT unprepare = waveOutUnprepareHeader(
                handle_,
                &slot.header,
                sizeof(slot.header));
            if (unprepare != MMSYSERR_NOERROR && unprepare != WAVERR_STILLPLAYING) {
                return false;
            }
            slot.header.dwFlags = 0;
        }
        sample_count = std::min(sample_count, slot.samples.size());
        for (std::size_t index = 0; index < sample_count; ++index) {
            float value = source[index] * gain;
            value = std::clamp(value, -1.0f, 1.0f);
            slot.samples[index] = static_cast<std::int16_t>(
                std::lround(value * 32767.0f));
        }
        for (std::size_t index = sample_count; index < slot.samples.size(); ++index) {
            slot.samples[index] = 0;
        }
        slot.header.dwBufferLength = static_cast<DWORD>(
            sample_count * sizeof(std::int16_t));
        if (waveOutPrepareHeader(handle_, &slot.header, sizeof(slot.header)) !=
            MMSYSERR_NOERROR) {
            return false;
        }
        if (waveOutWrite(handle_, &slot.header, sizeof(slot.header)) !=
            MMSYSERR_NOERROR) {
            return false;
        }
        return true;
    }

    bool IsDone(std::size_t slot_index) const {
        return (slots_[slot_index].header.dwFlags & WHDR_DONE) != 0;
    }

    bool AllDone() const {
        for (const auto& slot : slots_) {
            if (slot.header.dwBufferLength > 0 &&
                (slot.header.dwFlags & WHDR_DONE) == 0) {
                return false;
            }
        }
        return true;
    }

private:
    struct WaveSlot {
        WAVEHDR header{};
        std::vector<std::int16_t> samples;
    };

    HWAVEOUT handle_ = nullptr;
    WaveSlot slots_[kBufferCount];
};

struct Arguments {
    std::wstring file;
    std::wstring watch_command;
    std::wstring output_device;
    std::uint64_t request_id = 0;
    float gain_db = 0.0f;
    bool valid = false;
};

bool ParseArguments(Arguments& arguments, std::string& error) {
    int count = 0;
    wchar_t** values = CommandLineToArgvW(GetCommandLineW(), &count);
    if (values == nullptr) {
        error = "Could not parse command line";
        return false;
    }
    bool has_file = false;
    bool has_request = false;
    for (int index = 1; index + 1 < count; index += 2) {
        const std::wstring key = values[index];
        const std::wstring value = values[index + 1];
        if (key == L"--file") {
            arguments.file = value;
            has_file = true;
        } else if (key == L"--watch-command") {
            arguments.watch_command = value;
        } else if (key == L"--output-device") {
            arguments.output_device = value;
        } else if (key == L"--gain-db") {
            try {
                arguments.gain_db = std::stof(value);
            } catch (...) {
                error = "Invalid gain value";
                LocalFree(values);
                return false;
            }
        } else if (key == L"--request-id") {
            try {
                arguments.request_id = std::stoull(value);
                has_request = true;
            } catch (...) {
                error = "Invalid request id";
                LocalFree(values);
                return false;
            }
        }
    }
    LocalFree(values);
    if (!has_file) {
        error = "Missing --file";
        return false;
    }
    if (!has_request) {
        error = "Missing --request-id";
        return false;
    }
    arguments.valid = true;
    return true;
}

bool CommandStillMatches(const Arguments& arguments) {
    if (arguments.watch_command.empty()) {
        return true;
    }
    cs2music::CommandRequest command;
    std::string error;
    if (!cs2music::ReadCommandFile(arguments.watch_command, command, error)) {
        return true;
    }
    return command.op == L"play" && command.request == arguments.request_id;
}

bool Play(
    const Arguments& arguments,
    std::string& error) {
    ComInitialization com;
    if (!com.usable()) {
        error = "COM initialization failed";
        return false;
    }
    SourceReader reader;
    if (!reader.Open(arguments.file, error)) {
        return false;
    }

    WaveOutput output;
    if (!OpenWaveOutput(
            arguments.output_device,
            reader.sample_rate(),
            output,
            error)) {
        return false;
    }
    WaveStream stream(output.handle);
    const float gain = std::pow(10.0f, arguments.gain_db / 20.0f);

    std::vector<float> frames;
    bool end_of_stream = false;
    std::size_t next_slot = 0;
    std::size_t frame_offset = 0;
    std::uint64_t queued_bytes = 0;
    bool source_done = false;
    bool stopped = false;

    const auto queue_next = [&]() -> bool {
        while (frame_offset >= frames.size()) {
            frames.clear();
            frame_offset = 0;
            if (!reader.ReadFrames(frames, end_of_stream, error)) {
                return false;
            }
            if (end_of_stream && frames.empty()) {
                source_done = true;
                return false;
            }
        }
        const std::size_t sample_count = std::min(
            frames.size() - frame_offset,
            WaveStream::kFramesPerBuffer * 2);
        if (!stream.Queue(
                next_slot,
                frames.data() + frame_offset,
                sample_count,
                gain)) {
            error = "Could not queue audio to the output device";
            return false;
        }
        queued_bytes += sample_count * sizeof(std::int16_t);
        frame_offset += sample_count;
        next_slot = (next_slot + 1) % WaveStream::kBufferCount;
        return true;
    };

    auto output_stalled = [&](std::uint64_t& last_position,
                              std::chrono::steady_clock::time_point& last_progress) {
        const std::uint64_t position = OutputPositionBytes(output.handle);
        const auto now = std::chrono::steady_clock::now();
        if (position > last_position) {
            last_position = position;
            last_progress = now;
        }
        return now - last_progress > std::chrono::seconds(5);
    };

    for (std::size_t index = 0; index < WaveStream::kBufferCount; ++index) {
        if (!queue_next()) {
            if (!source_done) {
                return false;
            }
            break;
        }
    }

    std::uint64_t last_position = OutputPositionBytes(output.handle);
    auto last_progress = std::chrono::steady_clock::now();
    while (!source_done) {
        if (!CommandStillMatches(arguments)) {
            stopped = true;
            break;
        }
        if (!stream.IsDone(next_slot)) {
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
            if (output_stalled(last_position, last_progress)) {
                error = "The output device stopped responding";
                return false;
            }
            continue;
        }
        if (!queue_next() && !source_done) {
            return false;
        }
        last_position = OutputPositionBytes(output.handle);
        last_progress = std::chrono::steady_clock::now();
    }

    if (!stopped) {
        last_position = OutputPositionBytes(output.handle);
        last_progress = std::chrono::steady_clock::now();
        while (!stream.AllDone() &&
               OutputPositionBytes(output.handle) < queued_bytes) {
            if (!CommandStillMatches(arguments)) {
                stopped = true;
                break;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
            if (output_stalled(last_position, last_progress)) {
                error = "The output device stopped responding";
                return false;
            }
        }
    }

    return true;
}

}  // namespace

int WINAPI wWinMain(
    HINSTANCE,
    HINSTANCE,
    PWSTR,
    int) {
    Arguments arguments;
    std::string error;
    if (!ParseArguments(arguments, error)) {
        return 2;
    }
    if (!Play(arguments, error)) {
        return 1;
    }
    return 0;
}
