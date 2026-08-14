/*
 * WindowProfile.h
 *
 * Generated from schemas/rotunda-profile.openapi.yaml.
 * Do not edit by hand.
 */

#ifndef ROTUNDA_PROFILE_WindowProfile_H_
#define ROTUNDA_PROFILE_WindowProfile_H_

#include <cstdint>
#include <optional>
#include <string>

#include "json.hpp"
#include "HistoryProfile.h"

namespace rotundacfg {

class WindowProfile {
 public:
  WindowProfile() = default;
  explicit WindowProfile(const nlohmann::json& json) { fromJson(json); }

  bool fromJson(const nlohmann::json& json) {
    if (!json.is_object()) {
      return false;
    }
    m_IsSet = true;
    if (json.contains("scrollMinX") && !json.at("scrollMinX").is_null()) {
      const auto& value = json.at("scrollMinX");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_scrollMinX = value.get<int32_t>();
    }
    if (json.contains("scrollMinY") && !json.at("scrollMinY").is_null()) {
      const auto& value = json.at("scrollMinY");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_scrollMinY = value.get<int32_t>();
    }
    if (json.contains("scrollMaxX") && !json.at("scrollMaxX").is_null()) {
      const auto& value = json.at("scrollMaxX");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_scrollMaxX = value.get<int32_t>();
    }
    if (json.contains("scrollMaxY") && !json.at("scrollMaxY").is_null()) {
      const auto& value = json.at("scrollMaxY");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_scrollMaxY = value.get<int32_t>();
    }
    if (json.contains("outerHeight") && !json.at("outerHeight").is_null()) {
      const auto& value = json.at("outerHeight");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_outerHeight = value.get<int32_t>();
    }
    if (json.contains("outerWidth") && !json.at("outerWidth").is_null()) {
      const auto& value = json.at("outerWidth");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_outerWidth = value.get<int32_t>();
    }
    if (json.contains("innerHeight") && !json.at("innerHeight").is_null()) {
      const auto& value = json.at("innerHeight");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_innerHeight = value.get<int32_t>();
    }
    if (json.contains("innerWidth") && !json.at("innerWidth").is_null()) {
      const auto& value = json.at("innerWidth");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_innerWidth = value.get<int32_t>();
    }
    if (json.contains("screenX") && !json.at("screenX").is_null()) {
      const auto& value = json.at("screenX");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_screenX = value.get<int32_t>();
    }
    if (json.contains("screenY") && !json.at("screenY").is_null()) {
      const auto& value = json.at("screenY");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_screenY = value.get<int32_t>();
    }
    if (json.contains("history") && !json.at("history").is_null()) {
      HistoryProfile value;
      if (!value.fromJson(json.at("history"))) {
        return false;
      }
      m_history = value;
    }
    if (json.contains("devicePixelRatio") && !json.at("devicePixelRatio").is_null()) {
      const auto& value = json.at("devicePixelRatio");
      if (!value.is_number()) {
        return false;
      }
      m_devicePixelRatio = value.get<double>();
    }
    return true;
  }

  nlohmann::json toJson() const {
    nlohmann::json json = nlohmann::json::object();
    if (m_scrollMinX.has_value()) {
      json["scrollMinX"] = *m_scrollMinX;
    }
    if (m_scrollMinY.has_value()) {
      json["scrollMinY"] = *m_scrollMinY;
    }
    if (m_scrollMaxX.has_value()) {
      json["scrollMaxX"] = *m_scrollMaxX;
    }
    if (m_scrollMaxY.has_value()) {
      json["scrollMaxY"] = *m_scrollMaxY;
    }
    if (m_outerHeight.has_value()) {
      json["outerHeight"] = *m_outerHeight;
    }
    if (m_outerWidth.has_value()) {
      json["outerWidth"] = *m_outerWidth;
    }
    if (m_innerHeight.has_value()) {
      json["innerHeight"] = *m_innerHeight;
    }
    if (m_innerWidth.has_value()) {
      json["innerWidth"] = *m_innerWidth;
    }
    if (m_screenX.has_value()) {
      json["screenX"] = *m_screenX;
    }
    if (m_screenY.has_value()) {
      json["screenY"] = *m_screenY;
    }
    if (m_history.has_value()) {
      json["history"] = m_history->toJson();
    }
    if (m_devicePixelRatio.has_value()) {
      json["devicePixelRatio"] = *m_devicePixelRatio;
    }
    return json;
  }

  bool isSet() const { return m_IsSet; }

  bool scrollMinXIsSet() const { return m_scrollMinX.has_value(); }
  std::optional<int32_t> getScrollMinX() const {
    return m_scrollMinX;
  }
  void setScrollMinX(const int32_t& value) {
    m_scrollMinX = value;
  }

  bool scrollMinYIsSet() const { return m_scrollMinY.has_value(); }
  std::optional<int32_t> getScrollMinY() const {
    return m_scrollMinY;
  }
  void setScrollMinY(const int32_t& value) {
    m_scrollMinY = value;
  }

  bool scrollMaxXIsSet() const { return m_scrollMaxX.has_value(); }
  std::optional<int32_t> getScrollMaxX() const {
    return m_scrollMaxX;
  }
  void setScrollMaxX(const int32_t& value) {
    m_scrollMaxX = value;
  }

  bool scrollMaxYIsSet() const { return m_scrollMaxY.has_value(); }
  std::optional<int32_t> getScrollMaxY() const {
    return m_scrollMaxY;
  }
  void setScrollMaxY(const int32_t& value) {
    m_scrollMaxY = value;
  }

  bool outerHeightIsSet() const { return m_outerHeight.has_value(); }
  std::optional<int32_t> getOuterHeight() const {
    return m_outerHeight;
  }
  void setOuterHeight(const int32_t& value) {
    m_outerHeight = value;
  }

  bool outerWidthIsSet() const { return m_outerWidth.has_value(); }
  std::optional<int32_t> getOuterWidth() const {
    return m_outerWidth;
  }
  void setOuterWidth(const int32_t& value) {
    m_outerWidth = value;
  }

  bool innerHeightIsSet() const { return m_innerHeight.has_value(); }
  std::optional<int32_t> getInnerHeight() const {
    return m_innerHeight;
  }
  void setInnerHeight(const int32_t& value) {
    m_innerHeight = value;
  }

  bool innerWidthIsSet() const { return m_innerWidth.has_value(); }
  std::optional<int32_t> getInnerWidth() const {
    return m_innerWidth;
  }
  void setInnerWidth(const int32_t& value) {
    m_innerWidth = value;
  }

  bool screenXIsSet() const { return m_screenX.has_value(); }
  std::optional<int32_t> getScreenX() const {
    return m_screenX;
  }
  void setScreenX(const int32_t& value) {
    m_screenX = value;
  }

  bool screenYIsSet() const { return m_screenY.has_value(); }
  std::optional<int32_t> getScreenY() const {
    return m_screenY;
  }
  void setScreenY(const int32_t& value) {
    m_screenY = value;
  }

  bool historyIsSet() const { return m_history.has_value(); }
  std::optional<HistoryProfile> getHistory() const {
    return m_history;
  }
  void setHistory(const HistoryProfile& value) {
    m_history = value;
  }

  bool devicePixelRatioIsSet() const { return m_devicePixelRatio.has_value(); }
  std::optional<double> getDevicePixelRatio() const {
    return m_devicePixelRatio;
  }
  void setDevicePixelRatio(const double& value) {
    m_devicePixelRatio = value;
  }

 private:
  bool m_IsSet = false;
  std::optional<int32_t> m_scrollMinX;
  std::optional<int32_t> m_scrollMinY;
  std::optional<int32_t> m_scrollMaxX;
  std::optional<int32_t> m_scrollMaxY;
  std::optional<int32_t> m_outerHeight;
  std::optional<int32_t> m_outerWidth;
  std::optional<int32_t> m_innerHeight;
  std::optional<int32_t> m_innerWidth;
  std::optional<int32_t> m_screenX;
  std::optional<int32_t> m_screenY;
  std::optional<HistoryProfile> m_history;
  std::optional<double> m_devicePixelRatio;
};

}  // namespace rotundacfg

#endif  // ROTUNDA_PROFILE_WindowProfile_H_

