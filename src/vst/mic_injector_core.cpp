#include "mic_injector_core.h"

#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif

#include <windows.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <mferror.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <ctime>
#include <limits>
#include <sstream>
#include <utility>

namespace cs2music {
namespace {

constexpr wchar_t kDefaultCommandPath[] =
    L"C:\\ProgramData\\CS2ResultAudio\\command.txt";
constexpr double kMaximumTrackSeconds = 15.0 * 60.0;

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
        const HRESULT result = MFStartup(MF_VERSION, MFSTARTUP_LITE);
        if (FAILED(result)) {
            OutputDebugStringW(L"CS2MicInjector: MFStartup failed.\n");
        }
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

HRESULT SetReaderOutputType(
    IMFSourceReader* reader,
    std::uint32_t sample_rate,
    std::uint16_t bits,
    REFGUID subtype) {
    ComPtr<IMFMediaType> type;
    HRESULT result = MFCreateMediaType(type.Put());
    if (FAILED(result)) {
        return result;
    }
    type->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Audio);
    type->SetGUID(MF_MT_SUBTYPE, subtype);
    type->SetUINT32(MF_MT_AUDIO_NUM_CHANNELS, 1);
    type->SetUINT32(MF_MT_AUDIO_SAMPLES_PER_SECOND, sample_rate);
    type->SetUINT32(
        MF_MT_AUDIO_BLOCK_ALIGNMENT,
        static_cast<UINT32>(bits / 8));
    type->SetUINT32(
        MF_MT_AUDIO_AVG_BYTES_PER_SECOND,
        sample_rate * static_cast<std::uint32_t>(bits / 8));
    type->SetUINT32(
        MF_MT_ALL_SAMPLES_INDEPENDENT,
        static_cast<UINT32>(TRUE));
    type->SetUINT32(MF_MT_AUDIO_BITS_PER_SAMPLE, bits);
    type->SetUINT32(MF_MT_AUDIO_VALID_BITS_PER_SAMPLE, bits);
    return reader->SetCurrentMediaType(
        static_cast<DWORD>(MF_SOURCE_READER_FIRST_AUDIO_STREAM),
        nullptr,
        type.Get());
}

bool ReadNativeSampleRate(
    IMFSourceReader* reader,
    std::uint32_t& sample_rate) {
    ComPtr<IMFMediaType> type;
    HRESULT result = reader->GetNativeMediaType(
        static_cast<DWORD>(MF_SOURCE_READER_FIRST_AUDIO_STREAM),
        0,
        type.Put());
    if (FAILED(result) || !type) {
        return false;
    }
    return SUCCEEDED(
        type->GetUINT32(MF_MT_AUDIO_SAMPLES_PER_SECOND, &sample_rate));
}

bool CopySampleToFloat(
    IMFSample* sample,
    bool use_float,
    std::vector<float>& output,
    std::size_t& maximum_samples,
    std::string& error) {
    ComPtr<IMFMediaBuffer> buffer;
    HRESULT result = sample->ConvertToContiguousBuffer(buffer.Put());
    if (FAILED(result) || !buffer) {
        error = "Media Foundation returned an unusable sample";
        return false;
    }

    BYTE* data = nullptr;
    DWORD current_length = 0;
    result = buffer->Lock(&data, nullptr, &current_length);
    if (FAILED(result) || data == nullptr) {
        error = "Media Foundation sample lock failed";
        return false;
    }

    bool ok = true;
    if (use_float) {
        const std::size_t count = current_length / sizeof(float);
        if (count > maximum_samples ||
            output.size() > maximum_samples - count) {
            error = "Audio track exceeds the 15 minute safety limit";
            ok = false;
        } else {
            const auto* samples = reinterpret_cast<const float*>(data);
            output.insert(output.end(), samples, samples + count);
            maximum_samples -= count;
        }
    } else {
        const std::size_t count = current_length / sizeof(std::int16_t);
        if (count > maximum_samples ||
            output.size() > maximum_samples - count) {
            error = "Audio track exceeds the 15 minute safety limit";
            ok = false;
        } else {
            const auto* samples = reinterpret_cast<const std::int16_t*>(data);
            const std::size_t old_size = output.size();
            output.resize(old_size + count);
            for (std::size_t index = 0; index < count; ++index) {
                output[old_size + index] =
                    static_cast<float>(samples[index]) / 32768.0f;
            }
            maximum_samples -= count;
        }
    }
    buffer->Unlock();
    return ok;
}

std::string JsonEscape(const std::string& value) {
    std::string result;
    result.reserve(value.size() + 8);
    for (const unsigned char character : value) {
        switch (character) {
            case '"':
                result += "\\\"";
                break;
            case '\\':
                result += "\\\\";
                break;
            case '\b':
                result += "\\b";
                break;
            case '\f':
                result += "\\f";
                break;
            case '\n':
                result += "\\n";
                break;
            case '\r':
                result += "\\r";
                break;
            case '\t':
                result += "\\t";
                break;
            default:
                if (character < 0x20) {
                    constexpr char hex[] = "0123456789abcdef";
                    result += "\\u00";
                    result.push_back(hex[(character >> 4) & 0x0f]);
                    result.push_back(hex[character & 0x0f]);
                } else {
                    result.push_back(static_cast<char>(character));
                }
                break;
        }
    }
    return result;
}

std::wstring StatusPathFor(const std::wstring& command_path) {
    const std::size_t slash = command_path.find_last_of(L"\\/");
    if (slash == std::wstring::npos) {
        return L"status.txt";
    }
    return command_path.substr(0, slash + 1) + L"status.txt";
}

}  // namespace

float SoftLimit(float value) noexcept {
    constexpr float knee = 0.80f;
    if (!std::isfinite(value)) {
        return 0.0f;
    }
    const float magnitude = std::fabs(value);
    if (magnitude <= knee) {
        return value;
    }
    const float excess = (magnitude - knee) / (1.0f - knee);
    const float limited = knee + (1.0f - knee) * std::tanh(excess);
    return std::copysign(limited, value);
}

bool DecodeAudioFile(
    const std::wstring& path,
    double target_sample_rate,
    std::shared_ptr<const DecodedTrack>& track,
    std::string& error) {
    error.clear();
    track.reset();

    if (!IsAbsolutePath(path) || !IsSupportedAudioPath(path)) {
        error = "Audio path is not an absolute supported media path";
        return false;
    }
    const DWORD file_attributes = GetFileAttributesW(path.c_str());
    if (file_attributes == INVALID_FILE_ATTRIBUTES ||
        (file_attributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
        error = "Audio file does not exist";
        return false;
    }

    EnsureMediaFoundation();
    ComInitialization com;
    if (!com.usable()) {
        error = "COM initialization failed";
        return false;
    }

    ComPtr<IMFSourceReader> reader;
    HRESULT result =
        MFCreateSourceReaderFromURL(path.c_str(), nullptr, reader.Put());
    if (FAILED(result) || !reader) {
        error = "Media Foundation could not open the audio file";
        return false;
    }
    result = reader->SetStreamSelection(
        static_cast<DWORD>(MF_SOURCE_READER_ALL_STREAMS),
        FALSE);
    if (SUCCEEDED(result)) {
        result = reader->SetStreamSelection(
            static_cast<DWORD>(MF_SOURCE_READER_FIRST_AUDIO_STREAM),
            TRUE);
    }
    if (FAILED(result)) {
        error = "The file does not contain a usable audio stream";
        return false;
    }

    std::uint32_t native_rate = 0;
    ReadNativeSampleRate(reader.Get(), native_rate);
    const std::uint32_t desired_rate = static_cast<std::uint32_t>(std::clamp(
        target_sample_rate > 0.0 ? target_sample_rate : 48000.0,
        8000.0,
        384000.0));
    (void)native_rate;

    bool use_float = true;
    result = SetReaderOutputType(
        reader.Get(),
        desired_rate,
        32,
        MFAudioFormat_Float);
    if (FAILED(result)) {
        use_float = false;
        result = SetReaderOutputType(
            reader.Get(),
            desired_rate,
            16,
            MFAudioFormat_PCM);
    }
    if (FAILED(result)) {
        error = "Media Foundation could not create a PCM decoder";
        return false;
    }

    auto decoded = std::make_shared<DecodedTrack>();
    decoded->path = path;
    std::size_t maximum_samples = static_cast<std::size_t>(
        std::ceil(desired_rate * kMaximumTrackSeconds));
    decoded->samples.reserve(std::min<std::size_t>(maximum_samples, desired_rate * 60));

    while (true) {
        DWORD flags = 0;
        LONGLONG timestamp = 0;
        ComPtr<IMFSample> sample;
        result = reader->ReadSample(
            static_cast<DWORD>(MF_SOURCE_READER_FIRST_AUDIO_STREAM),
            0,
            nullptr,
            &flags,
            &timestamp,
            sample.Put());
        if (FAILED(result)) {
            error = "Media Foundation failed while reading audio";
            return false;
        }
        if ((flags & MF_SOURCE_READERF_ENDOFSTREAM) != 0) {
            break;
        }
        if (sample) {
            if (!CopySampleToFloat(
                    sample.Get(),
                    use_float,
                    decoded->samples,
                    maximum_samples,
                    error)) {
                return false;
            }
        }
    }

    if (decoded->samples.empty()) {
        error = "The decoded audio track is empty";
        return false;
    }
    track = std::move(decoded);
    return true;
}

MicInjectorCore::MicInjectorCore(
    std::wstring command_path,
    bool start_workers)
    : command_path_(std::move(command_path)),
      status_path_(StatusPathFor(command_path_)) {
    if (start_workers) {
        worker_thread_ = std::thread(&MicInjectorCore::WorkerMain, this);
        decoder_thread_ = std::thread(&MicInjectorCore::DecoderMain, this);
    }
}

MicInjectorCore::~MicInjectorCore() {
    StopWorkers();
}

std::wstring MicInjectorCore::DefaultCommandPath() {
    return kDefaultCommandPath;
}

void MicInjectorCore::SetSampleRate(double sample_rate) noexcept {
    if (sample_rate > 0.0 && std::isfinite(sample_rate)) {
        sample_rate_.store(sample_rate, std::memory_order_relaxed);
    }
}

void MicInjectorCore::SetBlockSize(std::uint32_t block_size) noexcept {
    if (block_size > 0) {
        block_size_.store(block_size, std::memory_order_relaxed);
    }
}

void MicInjectorCore::Process(
    float** inputs,
    float** outputs,
    std::int32_t frame_count) noexcept {
    if (inputs == nullptr || outputs == nullptr || frame_count <= 0) {
        return;
    }

    for (int channel = 0; channel < 2; ++channel) {
        if (outputs[channel] == nullptr) {
            continue;
        }
        if (inputs[channel] != nullptr && inputs[channel] != outputs[channel]) {
            std::memcpy(
                outputs[channel],
                inputs[channel],
                static_cast<std::size_t>(frame_count) * sizeof(float));
        } else if (inputs[channel] == nullptr) {
            std::memset(
                outputs[channel],
                0,
                static_cast<std::size_t>(frame_count) * sizeof(float));
        }
    }

    auto track = current_track_.load(std::memory_order_acquire);
    if (!track || track->samples.empty()) {
        return;
    }
    if (play_request_ != track->request) {
        play_request_ = track->request;
        play_cursor_ = 0;
    }

    const std::size_t available = track->samples.size();
    for (std::int32_t frame = 0; frame < frame_count; ++frame) {
        if (play_cursor_ >= available) {
            auto expected = track;
            current_track_.compare_exchange_strong(
                expected,
                nullptr,
                std::memory_order_acq_rel,
                std::memory_order_acquire);
            active_request_.store(0, std::memory_order_relaxed);
            break;
        }

        const float music = track->samples[play_cursor_++] * track->gain;
        if (outputs[0] != nullptr) {
            outputs[0][frame] = SoftLimit(outputs[0][frame] + music);
        }
        if (outputs[1] != nullptr) {
            outputs[1][frame] = SoftLimit(outputs[1][frame] + music);
        }
    }
}

void MicInjectorCore::SetTrackForTesting(
    std::shared_ptr<const DecodedTrack> track) {
    current_track_.store(std::move(track), std::memory_order_release);
    play_request_ = 0;
    play_cursor_ = 0;
}

void MicInjectorCore::ClearForTesting() {
    current_track_.store(nullptr, std::memory_order_release);
    play_request_ = 0;
    play_cursor_ = 0;
}

std::shared_ptr<const DecodedTrack>
MicInjectorCore::CurrentTrackForTesting() const {
    return current_track_.load(std::memory_order_acquire);
}

void MicInjectorCore::WorkerMain() {
    std::string last_error;
    std::chrono::steady_clock::time_point last_heartbeat{};
    while (running_.load(std::memory_order_relaxed)) {
        CommandRequest request;
        std::string error;
        if (ReadCommandFile(command_path_, request, error)) {
            if (request.request > last_seen_request_.load(std::memory_order_relaxed)) {
                last_seen_request_.store(request.request, std::memory_order_relaxed);
                if (request.op == L"stop") {
                    current_track_.store(nullptr, std::memory_order_release);
                    active_request_.store(0, std::memory_order_relaxed);
                    PublishStatus("stopped", request.request, L"", "");
                } else if (request.op == L"play") {
                    const DWORD attributes =
                        GetFileAttributesW(request.file.c_str());
                    if (attributes == INVALID_FILE_ATTRIBUTES ||
                        (attributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
                        PublishStatus(
                            "error",
                            request.request,
                            request.file,
                            "Audio file does not exist");
                    } else {
                        current_track_.store(nullptr, std::memory_order_release);
                        active_request_.store(0, std::memory_order_relaxed);
                        QueueRequest(request);
                        PublishStatus(
                            "decoding",
                            request.request,
                            request.file,
                            "");
                    }
                }
            }
        } else if (error != last_error) {
            // A temporary sharing error during atomic replacement is normal.
            if (GetFileAttributesW(command_path_.c_str()) !=
                INVALID_FILE_ATTRIBUTES) {
                last_error = error;
            }
        }

        const auto now = std::chrono::steady_clock::now();
        if (last_heartbeat.time_since_epoch().count() == 0 ||
            now - last_heartbeat >= std::chrono::seconds(2)) {
            last_heartbeat = now;
            std::string state;
            std::uint64_t heartbeat_request = 0;
            std::wstring path;
            std::string heartbeat_error;
            {
                std::lock_guard<std::mutex> lock(status_mutex_);
                state = last_status_;
                heartbeat_request = last_status_request_;
                path = last_status_path_;
                heartbeat_error = last_status_error_;
            }
            PublishStatus(
                state,
                heartbeat_request,
                path,
                heartbeat_error);
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
}

void MicInjectorCore::DecoderMain() {
    while (running_.load(std::memory_order_relaxed)) {
        CommandRequest request;
        std::uint64_t generation = 0;
        {
            std::unique_lock<std::mutex> lock(pending_mutex_);
            pending_cv_.wait_for(
                lock,
                std::chrono::milliseconds(100),
                [this]() {
                    return !running_.load(std::memory_order_relaxed) ||
                           has_pending_;
                });
            if (!running_.load(std::memory_order_relaxed)) {
                break;
            }
            if (!has_pending_) {
                continue;
            }
            request = pending_;
            generation = pending_generation_;
            has_pending_ = false;
        }

        std::shared_ptr<const DecodedTrack> decoded;
        std::string error;
        const double target_rate = sample_rate_.load(std::memory_order_relaxed);
        if (!DecodeAudioFile(request.file, target_rate, decoded, error)) {
            if (queued_generation_.load(std::memory_order_acquire) == generation) {
                PublishStatus("error", request.request, request.file, error);
            }
            continue;
        }
        if (queued_generation_.load(std::memory_order_acquire) != generation ||
            !running_.load(std::memory_order_relaxed)) {
            continue;
        }

        auto mutable_track =
            std::make_shared<DecodedTrack>(*decoded);
        mutable_track->request = request.request;
        mutable_track->gain = std::pow(10.0f, request.gain_db / 20.0f);
        current_track_.store(mutable_track, std::memory_order_release);
        active_request_.store(request.request, std::memory_order_relaxed);
        PublishStatus("playing", request.request, request.file, "");
    }
}

void MicInjectorCore::StopWorkers() {
    if (!running_.exchange(false, std::memory_order_acq_rel)) {
        return;
    }
    pending_cv_.notify_all();
    if (worker_thread_.joinable()) {
        worker_thread_.join();
    }
    if (decoder_thread_.joinable()) {
        decoder_thread_.join();
    }
}

void MicInjectorCore::QueueRequest(const CommandRequest& request) {
    {
        std::lock_guard<std::mutex> lock(pending_mutex_);
        pending_ = request;
        pending_generation_ = queued_generation_.fetch_add(
                                  1,
                                  std::memory_order_acq_rel) +
                              1;
        has_pending_ = true;
    }
    pending_cv_.notify_one();
}

void MicInjectorCore::PublishStatus(
    const std::string& state,
    std::uint64_t request,
    const std::wstring& path,
    const std::string& error) {
    std::ostringstream json;
    json << "{\"plugin\":\"CS2MicInjector\""
         << ",\"pid\":" << GetCurrentProcessId()
         << ",\"state\":\"" << JsonEscape(state) << "\""
         << ",\"request\":" << request
         << ",\"sample_rate\":" << sample_rate_.load(std::memory_order_relaxed)
         << ",\"block_size\":" << block_size_.load(std::memory_order_relaxed)
         << ",\"active_request\":"
         << active_request_.load(std::memory_order_relaxed)
         << ",\"file\":\"" << JsonEscape(Utf8FromWide(path)) << "\""
         << ",\"error\":\"" << JsonEscape(error) << "\""
         << ",\"timestamp\":" << std::time(nullptr) << "}\n";
    const std::string text = json.str();
    WriteTextFileAtomic(status_path_, text);

    std::lock_guard<std::mutex> lock(status_mutex_);
    last_status_ = state;
    last_status_request_ = request;
    last_status_path_ = path;
    last_status_error_ = error;
    last_status_write_ = std::chrono::steady_clock::now();
}

}  // namespace cs2music
