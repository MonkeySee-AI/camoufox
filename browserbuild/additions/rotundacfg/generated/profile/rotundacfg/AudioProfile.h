/*
 * AudioProfile.h
 *
 * Generated from schemas/rotunda-profile.openapi.yaml.
 * Do not edit by hand.
 */

#ifndef ROTUNDA_PROFILE_AudioProfile_H_
#define ROTUNDA_PROFILE_AudioProfile_H_

#include <cstdint>
#include <optional>
#include <string>

#include "json.hpp"

namespace rotundacfg {

class AudioProfile {
 public:
  AudioProfile() = default;
  explicit AudioProfile(const nlohmann::json& json) { fromJson(json); }

  bool fromJson(const nlohmann::json& json) {
    if (!json.is_object()) {
      return false;
    }
    m_IsSet = true;
    if (json.contains("seed") && !json.at("seed").is_null()) {
      const auto& value = json.at("seed");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_seed = value.get<int32_t>();
    }
    return true;
  }

  nlohmann::json toJson() const {
    nlohmann::json json = nlohmann::json::object();
    if (m_seed.has_value()) {
      json["seed"] = *m_seed;
    }
    return json;
  }

  bool isSet() const { return m_IsSet; }

  bool seedIsSet() const { return m_seed.has_value(); }
  std::optional<int32_t> getSeed() const {
    return m_seed;
  }
  void setSeed(const int32_t& value) {
    m_seed = value;
  }

 private:
  bool m_IsSet = false;
  std::optional<int32_t> m_seed;
};

}  // namespace rotundacfg

#endif  // ROTUNDA_PROFILE_AudioProfile_H_

