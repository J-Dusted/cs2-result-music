#pragma once

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "command_parser.h"

namespace cs2music {

struct DecodedTrack {
    std::uint64_t request = 0;
    std::wstring path;
    std::vector<float> samples;
    float gain = 1.0f;
};

bool DecodeAudioFile(
    const std::wstring& path,
    double target_sample_rate,
    std::shared_ptr<const DecodedTrack>& track,
    std::string& error);

class MicInjectorCore {
public:
    explicit MicInjectorCore(
        std::wstring command_path = DefaultCommandPath(),
        bool start_workers = true);
    ~MicInjectorCore();

    MicInjectorCore(const MicInjectorCore&) = delete;
    MicInjectorCore& operator=(const MicInjectorCore&) = delete;

    static std::wstring DefaultCommandPath();

    void SetSampleRate(double sample_rate) noexcept;
    void SetBlockSize(std::uint32_t block_size) noexcept;
    void Process(
        float** inputs,
        float** outputs,
        std::int32_t frame_count) noexcept;

    void SetTrackForTesting(std::shared_ptr<const DecodedTrack> track);
    void ClearForTesting();
    std::shared_ptr<const DecodedTrack> CurrentTrackForTesting() const;

private:
    void WorkerMain();
    void DecoderMain();
    void StopWorkers();
    void PublishStatus(
        const std::string& state,
        std::uint64_t request,
        const std::wstring& path,
        const std::string& error);
    void QueueRequest(const CommandRequest& request);

    std::wstring command_path_;
    std::wstring status_path_;
    std::atomic<bool> running_{true};
    std::atomic<double> sample_rate_{48000.0};
    std::atomic<std::uint32_t> block_size_{512};
    std::atomic<std::uint64_t> queued_generation_{0};
    std::atomic<std::uint64_t> last_seen_request_{0};
    std::atomic<std::uint64_t> active_request_{0};
    std::atomic<std::shared_ptr<const DecodedTrack>> current_track_{};
    std::mutex pending_mutex_;
    std::condition_variable pending_cv_;
    bool has_pending_ = false;
    std::uint64_t pending_generation_ = 0;
    CommandRequest pending_;
    std::thread worker_thread_;
    std::thread decoder_thread_;
    std::mutex status_mutex_;
    std::string last_status_ = "starting";
    std::uint64_t last_status_request_ = 0;
    std::wstring last_status_path_;
    std::string last_status_error_;
    std::chrono::steady_clock::time_point last_status_write_{};
    std::uint64_t play_request_ = 0;
    std::size_t play_cursor_ = 0;
};

float SoftLimit(float value) noexcept;

}  // namespace cs2music
