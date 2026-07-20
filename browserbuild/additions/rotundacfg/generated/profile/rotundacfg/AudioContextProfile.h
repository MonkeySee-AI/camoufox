/*
 * AudioContextProfile.h
 *
 * Generated from schemas/rotunda-profile.openapi.yaml.
 * Do not edit by hand.
 */

#ifndef ROTUNDA_PROFILE_AudioContextProfile_H_
#define ROTUNDA_PROFILE_AudioContextProfile_H_

#include <cstdint>
#include <optional>
#include <string>

#include "json.hpp"

namespace rotundacfg {

class AudioContextProfile {
 public:
  AudioContextProfile() = default;
  explicit AudioContextProfile(const nlohmann::json& json) { fromJson(json); }

  bool fromJson(const nlohmann::json& json) {
    if (!json.is_object()) {
      return false;
    }
    m_IsSet = true;
    if (json.contains("sampleRate") && !json.at("sampleRate").is_null()) {
      const auto& value = json.at("sampleRate");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_sampleRate = value.get<int32_t>();
    }
    if (json.contains("outputLatency") && !json.at("outputLatency").is_null()) {
      const auto& value = json.at("outputLatency");
      if (!value.is_number()) {
        return false;
      }
      m_outputLatency = value.get<double>();
    }
    if (json.contains("maxChannelCount") && !json.at("maxChannelCount").is_null()) {
      const auto& value = json.at("maxChannelCount");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_maxChannelCount = value.get<int32_t>();
    }
    return true;
  }

  nlohmann::json toJson() const {
    nlohmann::json json = nlohmann::json::object();
    if (m_sampleRate.has_value()) {
      json["sampleRate"] = *m_sampleRate;
    }
    if (m_outputLatency.has_value()) {
      json["outputLatency"] = *m_outputLatency;
    }
    if (m_maxChannelCount.has_value()) {
      json["maxChannelCount"] = *m_maxChannelCount;
    }
    return json;
  }

  bool isSet() const { return m_IsSet; }

  bool sampleRateIsSet() const { return m_sampleRate.has_value(); }
  std::optional<int32_t> getSampleRate() const {
    return m_sampleRate;
  }
  void setSampleRate(const int32_t& value) {
    m_sampleRate = value;
  }

  bool outputLatencyIsSet() const { return m_outputLatency.has_value(); }
  std::optional<double> getOutputLatency() const {
    return m_outputLatency;
  }
  void setOutputLatency(const double& value) {
    m_outputLatency = value;
  }

  bool maxChannelCountIsSet() const { return m_maxChannelCount.has_value(); }
  std::optional<int32_t> getMaxChannelCount() const {
    return m_maxChannelCount;
  }
  void setMaxChannelCount(const int32_t& value) {
    m_maxChannelCount = value;
  }

 private:
  bool m_IsSet = false;
  std::optional<int32_t> m_sampleRate;
  std::optional<double> m_outputLatency;
  std::optional<int32_t> m_maxChannelCount;
};

}  // namespace rotundacfg

#endif  // ROTUNDA_PROFILE_AudioContextProfile_H_

