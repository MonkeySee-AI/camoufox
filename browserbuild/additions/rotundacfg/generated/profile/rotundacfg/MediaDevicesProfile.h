/*
 * MediaDevicesProfile.h
 *
 * Generated from schemas/rotunda-profile.openapi.yaml.
 * Do not edit by hand.
 */

#ifndef ROTUNDA_PROFILE_MediaDevicesProfile_H_
#define ROTUNDA_PROFILE_MediaDevicesProfile_H_

#include <cstdint>
#include <optional>
#include <string>

#include "json.hpp"

namespace rotundacfg {

class MediaDevicesProfile {
 public:
  MediaDevicesProfile() = default;
  explicit MediaDevicesProfile(const nlohmann::json& json) { fromJson(json); }

  bool fromJson(const nlohmann::json& json) {
    if (!json.is_object()) {
      return false;
    }
    m_IsSet = true;
    if (json.contains("micros") && !json.at("micros").is_null()) {
      const auto& value = json.at("micros");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_micros = value.get<int32_t>();
    }
    if (json.contains("webcams") && !json.at("webcams").is_null()) {
      const auto& value = json.at("webcams");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_webcams = value.get<int32_t>();
    }
    if (json.contains("speakers") && !json.at("speakers").is_null()) {
      const auto& value = json.at("speakers");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_speakers = value.get<int32_t>();
    }
    if (json.contains("enabled") && !json.at("enabled").is_null()) {
      const auto& value = json.at("enabled");
      if (!value.is_boolean()) {
        return false;
      }
      m_enabled = value.get<bool>();
    }
    return true;
  }

  nlohmann::json toJson() const {
    nlohmann::json json = nlohmann::json::object();
    if (m_micros.has_value()) {
      json["micros"] = *m_micros;
    }
    if (m_webcams.has_value()) {
      json["webcams"] = *m_webcams;
    }
    if (m_speakers.has_value()) {
      json["speakers"] = *m_speakers;
    }
    if (m_enabled.has_value()) {
      json["enabled"] = *m_enabled;
    }
    return json;
  }

  bool isSet() const { return m_IsSet; }

  bool microsIsSet() const { return m_micros.has_value(); }
  std::optional<int32_t> getMicros() const {
    return m_micros;
  }
  void setMicros(const int32_t& value) {
    m_micros = value;
  }

  bool webcamsIsSet() const { return m_webcams.has_value(); }
  std::optional<int32_t> getWebcams() const {
    return m_webcams;
  }
  void setWebcams(const int32_t& value) {
    m_webcams = value;
  }

  bool speakersIsSet() const { return m_speakers.has_value(); }
  std::optional<int32_t> getSpeakers() const {
    return m_speakers;
  }
  void setSpeakers(const int32_t& value) {
    m_speakers = value;
  }

  bool enabledIsSet() const { return m_enabled.has_value(); }
  std::optional<bool> isEnabled() const {
    return m_enabled;
  }
  void setEnabled(const bool& value) {
    m_enabled = value;
  }

 private:
  bool m_IsSet = false;
  std::optional<int32_t> m_micros;
  std::optional<int32_t> m_webcams;
  std::optional<int32_t> m_speakers;
  std::optional<bool> m_enabled;
};

}  // namespace rotundacfg

#endif  // ROTUNDA_PROFILE_MediaDevicesProfile_H_

