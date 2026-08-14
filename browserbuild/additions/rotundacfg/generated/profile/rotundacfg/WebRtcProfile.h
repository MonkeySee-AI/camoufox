/*
 * WebRtcProfile.h
 *
 * Generated from schemas/rotunda-profile.openapi.yaml.
 * Do not edit by hand.
 */

#ifndef ROTUNDA_PROFILE_WebRtcProfile_H_
#define ROTUNDA_PROFILE_WebRtcProfile_H_

#include <cstdint>
#include <optional>
#include <string>

#include "json.hpp"

namespace rotundacfg {

class WebRtcProfile {
 public:
  WebRtcProfile() = default;
  explicit WebRtcProfile(const nlohmann::json& json) { fromJson(json); }

  bool fromJson(const nlohmann::json& json) {
    if (!json.is_object()) {
      return false;
    }
    m_IsSet = true;
    if (json.contains("ipv4") && !json.at("ipv4").is_null()) {
      const auto& value = json.at("ipv4");
      if (!value.is_string()) {
        return false;
      }
      m_ipv4 = value.get<std::string>();
    }
    if (json.contains("ipv6") && !json.at("ipv6").is_null()) {
      const auto& value = json.at("ipv6");
      if (!value.is_string()) {
        return false;
      }
      m_ipv6 = value.get<std::string>();
    }
    if (json.contains("localipv4") && !json.at("localipv4").is_null()) {
      const auto& value = json.at("localipv4");
      if (!value.is_string()) {
        return false;
      }
      m_localipv4 = value.get<std::string>();
    }
    if (json.contains("localipv6") && !json.at("localipv6").is_null()) {
      const auto& value = json.at("localipv6");
      if (!value.is_string()) {
        return false;
      }
      m_localipv6 = value.get<std::string>();
    }
    return true;
  }

  nlohmann::json toJson() const {
    nlohmann::json json = nlohmann::json::object();
    if (m_ipv4.has_value()) {
      json["ipv4"] = *m_ipv4;
    }
    if (m_ipv6.has_value()) {
      json["ipv6"] = *m_ipv6;
    }
    if (m_localipv4.has_value()) {
      json["localipv4"] = *m_localipv4;
    }
    if (m_localipv6.has_value()) {
      json["localipv6"] = *m_localipv6;
    }
    return json;
  }

  bool isSet() const { return m_IsSet; }

  bool ipv4IsSet() const { return m_ipv4.has_value(); }
  std::optional<std::string> getIpv4() const {
    return m_ipv4;
  }
  void setIpv4(const std::string& value) {
    m_ipv4 = value;
  }

  bool ipv6IsSet() const { return m_ipv6.has_value(); }
  std::optional<std::string> getIpv6() const {
    return m_ipv6;
  }
  void setIpv6(const std::string& value) {
    m_ipv6 = value;
  }

  bool localipv4IsSet() const { return m_localipv4.has_value(); }
  std::optional<std::string> getLocalipv4() const {
    return m_localipv4;
  }
  void setLocalipv4(const std::string& value) {
    m_localipv4 = value;
  }

  bool localipv6IsSet() const { return m_localipv6.has_value(); }
  std::optional<std::string> getLocalipv6() const {
    return m_localipv6;
  }
  void setLocalipv6(const std::string& value) {
    m_localipv6 = value;
  }

 private:
  bool m_IsSet = false;
  std::optional<std::string> m_ipv4;
  std::optional<std::string> m_ipv6;
  std::optional<std::string> m_localipv4;
  std::optional<std::string> m_localipv6;
};

}  // namespace rotundacfg

#endif  // ROTUNDA_PROFILE_WebRtcProfile_H_

