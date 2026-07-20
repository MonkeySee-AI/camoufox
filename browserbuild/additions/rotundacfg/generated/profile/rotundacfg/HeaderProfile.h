/*
 * HeaderProfile.h
 *
 * Generated from schemas/rotunda-profile.openapi.yaml.
 * Do not edit by hand.
 */

#ifndef ROTUNDA_PROFILE_HeaderProfile_H_
#define ROTUNDA_PROFILE_HeaderProfile_H_

#include <cstdint>
#include <optional>
#include <string>

#include "json.hpp"

namespace rotundacfg {

class HeaderProfile {
 public:
  HeaderProfile() = default;
  explicit HeaderProfile(const nlohmann::json& json) { fromJson(json); }

  bool fromJson(const nlohmann::json& json) {
    if (!json.is_object()) {
      return false;
    }
    m_IsSet = true;
    if (json.contains("User-Agent") && !json.at("User-Agent").is_null()) {
      const auto& value = json.at("User-Agent");
      if (!value.is_string()) {
        return false;
      }
      m_userAgent = value.get<std::string>();
    }
    if (json.contains("Accept-Language") && !json.at("Accept-Language").is_null()) {
      const auto& value = json.at("Accept-Language");
      if (!value.is_string()) {
        return false;
      }
      m_acceptLanguage = value.get<std::string>();
    }
    if (json.contains("Accept-Encoding") && !json.at("Accept-Encoding").is_null()) {
      const auto& value = json.at("Accept-Encoding");
      if (!value.is_string()) {
        return false;
      }
      m_acceptEncoding = value.get<std::string>();
    }
    return true;
  }

  nlohmann::json toJson() const {
    nlohmann::json json = nlohmann::json::object();
    if (m_userAgent.has_value()) {
      json["User-Agent"] = *m_userAgent;
    }
    if (m_acceptLanguage.has_value()) {
      json["Accept-Language"] = *m_acceptLanguage;
    }
    if (m_acceptEncoding.has_value()) {
      json["Accept-Encoding"] = *m_acceptEncoding;
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

  bool acceptLanguageIsSet() const { return m_acceptLanguage.has_value(); }
  std::optional<std::string> getAcceptLanguage() const {
    return m_acceptLanguage;
  }
  void setAcceptLanguage(const std::string& value) {
    m_acceptLanguage = value;
  }

  bool acceptEncodingIsSet() const { return m_acceptEncoding.has_value(); }
  std::optional<std::string> getAcceptEncoding() const {
    return m_acceptEncoding;
  }
  void setAcceptEncoding(const std::string& value) {
    m_acceptEncoding = value;
  }

 private:
  bool m_IsSet = false;
  std::optional<std::string> m_userAgent;
  std::optional<std::string> m_acceptLanguage;
  std::optional<std::string> m_acceptEncoding;
};

}  // namespace rotundacfg

#endif  // ROTUNDA_PROFILE_HeaderProfile_H_

