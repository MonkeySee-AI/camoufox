/*
 * NavigatorProfile.h
 *
 * Generated from schemas/rotunda-profile.openapi.yaml.
 * Do not edit by hand.
 */

#ifndef ROTUNDA_PROFILE_NavigatorProfile_H_
#define ROTUNDA_PROFILE_NavigatorProfile_H_

#include <cstdint>
#include <optional>
#include <string>

#include "json.hpp"

namespace rotundacfg {

class NavigatorProfile {
 public:
  NavigatorProfile() = default;
  explicit NavigatorProfile(const nlohmann::json& json) { fromJson(json); }

  bool fromJson(const nlohmann::json& json) {
    if (!json.is_object()) {
      return false;
    }
    m_IsSet = true;
    if (json.contains("userAgent") && !json.at("userAgent").is_null()) {
      const auto& value = json.at("userAgent");
      if (!value.is_string()) {
        return false;
      }
      m_userAgent = value.get<std::string>();
    }
    if (json.contains("doNotTrack") && !json.at("doNotTrack").is_null()) {
      const auto& value = json.at("doNotTrack");
      if (!value.is_string()) {
        return false;
      }
      m_doNotTrack = value.get<std::string>();
    }
    if (json.contains("appCodeName") && !json.at("appCodeName").is_null()) {
      const auto& value = json.at("appCodeName");
      if (!value.is_string()) {
        return false;
      }
      m_appCodeName = value.get<std::string>();
    }
    if (json.contains("appName") && !json.at("appName").is_null()) {
      const auto& value = json.at("appName");
      if (!value.is_string()) {
        return false;
      }
      m_appName = value.get<std::string>();
    }
    if (json.contains("appVersion") && !json.at("appVersion").is_null()) {
      const auto& value = json.at("appVersion");
      if (!value.is_string()) {
        return false;
      }
      m_appVersion = value.get<std::string>();
    }
    if (json.contains("oscpu") && !json.at("oscpu").is_null()) {
      const auto& value = json.at("oscpu");
      if (!value.is_string()) {
        return false;
      }
      m_oscpu = value.get<std::string>();
    }
    if (json.contains("language") && !json.at("language").is_null()) {
      const auto& value = json.at("language");
      if (!value.is_string()) {
        return false;
      }
      m_language = value.get<std::string>();
    }
    if (json.contains("languages") && !json.at("languages").is_null()) {
      m_languages = json.at("languages");
    }
    if (json.contains("platform") && !json.at("platform").is_null()) {
      const auto& value = json.at("platform");
      if (!value.is_string()) {
        return false;
      }
      m_platform = value.get<std::string>();
    }
    if (json.contains("hardwareConcurrency") && !json.at("hardwareConcurrency").is_null()) {
      const auto& value = json.at("hardwareConcurrency");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_hardwareConcurrency = value.get<int32_t>();
    }
    if (json.contains("product") && !json.at("product").is_null()) {
      const auto& value = json.at("product");
      if (!value.is_string()) {
        return false;
      }
      m_product = value.get<std::string>();
    }
    if (json.contains("productSub") && !json.at("productSub").is_null()) {
      const auto& value = json.at("productSub");
      if (!value.is_string()) {
        return false;
      }
      m_productSub = value.get<std::string>();
    }
    if (json.contains("maxTouchPoints") && !json.at("maxTouchPoints").is_null()) {
      const auto& value = json.at("maxTouchPoints");
      if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
      }
      m_maxTouchPoints = value.get<int32_t>();
    }
    if (json.contains("cookieEnabled") && !json.at("cookieEnabled").is_null()) {
      const auto& value = json.at("cookieEnabled");
      if (!value.is_boolean()) {
        return false;
      }
      m_cookieEnabled = value.get<bool>();
    }
    if (json.contains("globalPrivacyControl") && !json.at("globalPrivacyControl").is_null()) {
      const auto& value = json.at("globalPrivacyControl");
      if (!value.is_boolean()) {
        return false;
      }
      m_globalPrivacyControl = value.get<bool>();
    }
    if (json.contains("buildID") && !json.at("buildID").is_null()) {
      const auto& value = json.at("buildID");
      if (!value.is_string()) {
        return false;
      }
      m_buildID = value.get<std::string>();
    }
    if (json.contains("onLine") && !json.at("onLine").is_null()) {
      const auto& value = json.at("onLine");
      if (!value.is_boolean()) {
        return false;
      }
      m_onLine = value.get<bool>();
    }
    return true;
  }

  nlohmann::json toJson() const {
    nlohmann::json json = nlohmann::json::object();
    if (m_userAgent.has_value()) {
      json["userAgent"] = *m_userAgent;
    }
    if (m_doNotTrack.has_value()) {
      json["doNotTrack"] = *m_doNotTrack;
    }
    if (m_appCodeName.has_value()) {
      json["appCodeName"] = *m_appCodeName;
    }
    if (m_appName.has_value()) {
      json["appName"] = *m_appName;
    }
    if (m_appVersion.has_value()) {
      json["appVersion"] = *m_appVersion;
    }
    if (m_oscpu.has_value()) {
      json["oscpu"] = *m_oscpu;
    }
    if (m_language.has_value()) {
      json["language"] = *m_language;
    }
    if (m_languages.has_value()) {
      json["languages"] = *m_languages;
    }
    if (m_platform.has_value()) {
      json["platform"] = *m_platform;
    }
    if (m_hardwareConcurrency.has_value()) {
      json["hardwareConcurrency"] = *m_hardwareConcurrency;
    }
    if (m_product.has_value()) {
      json["product"] = *m_product;
    }
    if (m_productSub.has_value()) {
      json["productSub"] = *m_productSub;
    }
    if (m_maxTouchPoints.has_value()) {
      json["maxTouchPoints"] = *m_maxTouchPoints;
    }
    if (m_cookieEnabled.has_value()) {
      json["cookieEnabled"] = *m_cookieEnabled;
    }
    if (m_globalPrivacyControl.has_value()) {
      json["globalPrivacyControl"] = *m_globalPrivacyControl;
    }
    if (m_buildID.has_value()) {
      json["buildID"] = *m_buildID;
    }
    if (m_onLine.has_value()) {
      json["onLine"] = *m_onLine;
    }
    return json;
  }

  bool isSet() const { return m_IsSet; }

  bool userAgentIsSet() const { return m_userAgent.has_value(); }
  std::optional<std::string> getUserAgent() const {
    return m_userAgent;
  }
  void setUserAgent(const std::string& value) {
    m_userAgent = value;
  }

  bool doNotTrackIsSet() const { return m_doNotTrack.has_value(); }
  std::optional<std::string> getDoNotTrack() const {
    return m_doNotTrack;
  }
  void setDoNotTrack(const std::string& value) {
    m_doNotTrack = value;
  }

  bool appCodeNameIsSet() const { return m_appCodeName.has_value(); }
  std::optional<std::string> getAppCodeName() const {
    return m_appCodeName;
  }
  void setAppCodeName(const std::string& value) {
    m_appCodeName = value;
  }

  bool appNameIsSet() const { return m_appName.has_value(); }
  std::optional<std::string> getAppName() const {
    return m_appName;
  }
  void setAppName(const std::string& value) {
    m_appName = value;
  }

  bool appVersionIsSet() const { return m_appVersion.has_value(); }
  std::optional<std::string> getAppVersion() const {
    return m_appVersion;
  }
  void setAppVersion(const std::string& value) {
    m_appVersion = value;
  }

  bool oscpuIsSet() const { return m_oscpu.has_value(); }
  std::optional<std::string> getOscpu() const {
    return m_oscpu;
  }
  void setOscpu(const std::string& value) {
    m_oscpu = value;
  }

  bool languageIsSet() const { return m_language.has_value(); }
  std::optional<std::string> getLanguage() const {
    return m_language;
  }
  void setLanguage(const std::string& value) {
    m_language = value;
  }

  bool languagesIsSet() const { return m_languages.has_value(); }
  std::optional<nlohmann::json> getLanguages() const {
    return m_languages;
  }
  void setLanguages(const nlohmann::json& value) {
    m_languages = value;
  }

  bool platformIsSet() const { return m_platform.has_value(); }
  std::optional<std::string> getPlatform() const {
    return m_platform;
  }
  void setPlatform(const std::string& value) {
    m_platform = value;
  }

  bool hardwareConcurrencyIsSet() const { return m_hardwareConcurrency.has_value(); }
  std::optional<int32_t> getHardwareConcurrency() const {
    return m_hardwareConcurrency;
  }
  void setHardwareConcurrency(const int32_t& value) {
    m_hardwareConcurrency = value;
  }

  bool productIsSet() const { return m_product.has_value(); }
  std::optional<std::string> getProduct() const {
    return m_product;
  }
  void setProduct(const std::string& value) {
    m_product = value;
  }

  bool productSubIsSet() const { return m_productSub.has_value(); }
  std::optional<std::string> getProductSub() const {
    return m_productSub;
  }
  void setProductSub(const std::string& value) {
    m_productSub = value;
  }

  bool maxTouchPointsIsSet() const { return m_maxTouchPoints.has_value(); }
  std::optional<int32_t> getMaxTouchPoints() const {
    return m_maxTouchPoints;
  }
  void setMaxTouchPoints(const int32_t& value) {
    m_maxTouchPoints = value;
  }

  bool cookieEnabledIsSet() const { return m_cookieEnabled.has_value(); }
  std::optional<bool> isCookieEnabled() const {
    return m_cookieEnabled;
  }
  void setCookieEnabled(const bool& value) {
    m_cookieEnabled = value;
  }

  bool globalPrivacyControlIsSet() const { return m_globalPrivacyControl.has_value(); }
  std::optional<bool> isGlobalPrivacyControl() const {
    return m_globalPrivacyControl;
  }
  void setGlobalPrivacyControl(const bool& value) {
    m_globalPrivacyControl = value;
  }

  bool buildIDIsSet() const { return m_buildID.has_value(); }
  std::optional<std::string> getBuildID() const {
    return m_buildID;
  }
  void setBuildID(const std::string& value) {
    m_buildID = value;
  }

  bool onLineIsSet() const { return m_onLine.has_value(); }
  std::optional<bool> isOnLine() const {
    return m_onLine;
  }
  void setOnLine(const bool& value) {
    m_onLine = value;
  }

 private:
  bool m_IsSet = false;
  std::optional<std::string> m_userAgent;
  std::optional<std::string> m_doNotTrack;
  std::optional<std::string> m_appCodeName;
  std::optional<std::string> m_appName;
  std::optional<std::string> m_appVersion;
  std::optional<std::string> m_oscpu;
  std::optional<std::string> m_language;
  std::optional<nlohmann::json> m_languages;
  std::optional<std::string> m_platform;
  std::optional<int32_t> m_hardwareConcurrency;
  std::optional<std::string> m_product;
  std::optional<std::string> m_productSub;
  std::optional<int32_t> m_maxTouchPoints;
  std::optional<bool> m_cookieEnabled;
  std::optional<bool> m_globalPrivacyControl;
  std::optional<std::string> m_buildID;
  std::optional<bool> m_onLine;
};

}  // namespace rotundacfg

#endif  // ROTUNDA_PROFILE_NavigatorProfile_H_

