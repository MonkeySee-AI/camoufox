/*
 * FontsProfile.h
 *
 * Generated from schemas/rotunda-profile.openapi.yaml.
 * Do not edit by hand.
 */

#ifndef ROTUNDA_PROFILE_FontsProfile_H_
#define ROTUNDA_PROFILE_FontsProfile_H_

#include <cstdint>
#include <optional>
#include <string>

#include "json.hpp"

namespace rotundacfg {

class FontsProfile {
 public:
  FontsProfile() = default;
  explicit FontsProfile(const nlohmann::json& json) { fromJson(json); }

  bool fromJson(const nlohmann::json& json) {
    if (!json.is_object()) {
      return false;
    }
    m_IsSet = true;
    if (json.contains("families") && !json.at("families").is_null()) {
      m_families = json.at("families");
    }
    if (json.contains("spacingSeed") && !json.at("spacingSeed").is_null()) {
      const auto& value = json.at("spacingSeed");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_spacingSeed = value.get<int32_t>();
    }
    return true;
  }

  nlohmann::json toJson() const {
    nlohmann::json json = nlohmann::json::object();
    if (m_families.has_value()) {
      json["families"] = *m_families;
    }
    if (m_spacingSeed.has_value()) {
      json["spacingSeed"] = *m_spacingSeed;
    }
    return json;
  }

  bool isSet() const { return m_IsSet; }

  bool familiesIsSet() const { return m_families.has_value(); }
  std::optional<nlohmann::json> getFamilies() const {
    return m_families;
  }
  void setFamilies(const nlohmann::json& value) {
    m_families = value;
  }

  bool spacingSeedIsSet() const { return m_spacingSeed.has_value(); }
  std::optional<int32_t> getSpacingSeed() const {
    return m_spacingSeed;
  }
  void setSpacingSeed(const int32_t& value) {
    m_spacingSeed = value;
  }

 private:
  bool m_IsSet = false;
  std::optional<nlohmann::json> m_families;
  std::optional<int32_t> m_spacingSeed;
};

}  // namespace rotundacfg

#endif  // ROTUNDA_PROFILE_FontsProfile_H_

