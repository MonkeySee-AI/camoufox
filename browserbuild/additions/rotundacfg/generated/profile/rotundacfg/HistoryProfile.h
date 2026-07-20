/*
 * HistoryProfile.h
 *
 * Generated from schemas/rotunda-profile.openapi.yaml.
 * Do not edit by hand.
 */

#ifndef ROTUNDA_PROFILE_HistoryProfile_H_
#define ROTUNDA_PROFILE_HistoryProfile_H_

#include <cstdint>
#include <optional>
#include <string>

#include "json.hpp"

namespace rotundacfg {

class HistoryProfile {
 public:
  HistoryProfile() = default;
  explicit HistoryProfile(const nlohmann::json& json) { fromJson(json); }

  bool fromJson(const nlohmann::json& json) {
    if (!json.is_object()) {
      return false;
    }
    m_IsSet = true;
    if (json.contains("length") && !json.at("length").is_null()) {
      const auto& value = json.at("length");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_length = value.get<int32_t>();
    }
    return true;
  }

  nlohmann::json toJson() const {
    nlohmann::json json = nlohmann::json::object();
    if (m_length.has_value()) {
      json["length"] = *m_length;
    }
    return json;
  }

  bool isSet() const { return m_IsSet; }

  bool lengthIsSet() const { return m_length.has_value(); }
  std::optional<int32_t> getLength() const {
    return m_length;
  }
  void setLength(const int32_t& value) {
    m_length = value;
  }

 private:
  bool m_IsSet = false;
  std::optional<int32_t> m_length;
};

}  // namespace rotundacfg

#endif  // ROTUNDA_PROFILE_HistoryProfile_H_

