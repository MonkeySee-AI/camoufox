/*
 * DocumentBodyProfile.h
 *
 * Generated from schemas/rotunda-profile.openapi.yaml.
 * Do not edit by hand.
 */

#ifndef ROTUNDA_PROFILE_DocumentBodyProfile_H_
#define ROTUNDA_PROFILE_DocumentBodyProfile_H_

#include <cstdint>
#include <optional>
#include <string>

#include "json.hpp"

namespace rotundacfg {

class DocumentBodyProfile {
 public:
  DocumentBodyProfile() = default;
  explicit DocumentBodyProfile(const nlohmann::json& json) { fromJson(json); }

  bool fromJson(const nlohmann::json& json) {
    if (!json.is_object()) {
      return false;
    }
    m_IsSet = true;
    if (json.contains("clientWidth") && !json.at("clientWidth").is_null()) {
      const auto& value = json.at("clientWidth");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_clientWidth = value.get<int32_t>();
    }
    if (json.contains("clientHeight") && !json.at("clientHeight").is_null()) {
      const auto& value = json.at("clientHeight");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_clientHeight = value.get<int32_t>();
    }
    if (json.contains("clientTop") && !json.at("clientTop").is_null()) {
      const auto& value = json.at("clientTop");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_clientTop = value.get<int32_t>();
    }
    if (json.contains("clientLeft") && !json.at("clientLeft").is_null()) {
      const auto& value = json.at("clientLeft");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_clientLeft = value.get<int32_t>();
    }
    return true;
  }

  nlohmann::json toJson() const {
    nlohmann::json json = nlohmann::json::object();
    if (m_clientWidth.has_value()) {
      json["clientWidth"] = *m_clientWidth;
    }
    if (m_clientHeight.has_value()) {
      json["clientHeight"] = *m_clientHeight;
    }
    if (m_clientTop.has_value()) {
      json["clientTop"] = *m_clientTop;
    }
    if (m_clientLeft.has_value()) {
      json["clientLeft"] = *m_clientLeft;
    }
    return json;
  }

  bool isSet() const { return m_IsSet; }

  bool clientWidthIsSet() const { return m_clientWidth.has_value(); }
  std::optional<int32_t> getClientWidth() const {
    return m_clientWidth;
  }
  void setClientWidth(const int32_t& value) {
    m_clientWidth = value;
  }

  bool clientHeightIsSet() const { return m_clientHeight.has_value(); }
  std::optional<int32_t> getClientHeight() const {
    return m_clientHeight;
  }
  void setClientHeight(const int32_t& value) {
    m_clientHeight = value;
  }

  bool clientTopIsSet() const { return m_clientTop.has_value(); }
  std::optional<int32_t> getClientTop() const {
    return m_clientTop;
  }
  void setClientTop(const int32_t& value) {
    m_clientTop = value;
  }

  bool clientLeftIsSet() const { return m_clientLeft.has_value(); }
  std::optional<int32_t> getClientLeft() const {
    return m_clientLeft;
  }
  void setClientLeft(const int32_t& value) {
    m_clientLeft = value;
  }

 private:
  bool m_IsSet = false;
  std::optional<int32_t> m_clientWidth;
  std::optional<int32_t> m_clientHeight;
  std::optional<int32_t> m_clientTop;
  std::optional<int32_t> m_clientLeft;
};

}  // namespace rotundacfg

#endif  // ROTUNDA_PROFILE_DocumentBodyProfile_H_

