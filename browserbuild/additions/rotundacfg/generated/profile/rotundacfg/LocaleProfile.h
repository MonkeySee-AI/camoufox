/*
 * LocaleProfile.h
 *
 * Generated from schemas/rotunda-profile.openapi.yaml.
 * Do not edit by hand.
 */

#ifndef ROTUNDA_PROFILE_LocaleProfile_H_
#define ROTUNDA_PROFILE_LocaleProfile_H_

#include <cstdint>
#include <optional>
#include <string>

#include "json.hpp"

namespace rotundacfg {

class LocaleProfile {
 public:
  LocaleProfile() = default;
  explicit LocaleProfile(const nlohmann::json& json) { fromJson(json); }

  bool fromJson(const nlohmann::json& json) {
    if (!json.is_object()) {
      return false;
    }
    m_IsSet = true;
    if (json.contains("language") && !json.at("language").is_null()) {
      const auto& value = json.at("language");
      if (!value.is_string()) {
        return false;
      }
      m_language = value.get<std::string>();
    }
    if (json.contains("region") && !json.at("region").is_null()) {
      const auto& value = json.at("region");
      if (!value.is_string()) {
        return false;
      }
      m_region = value.get<std::string>();
    }
    if (json.contains("script") && !json.at("script").is_null()) {
      const auto& value = json.at("script");
      if (!value.is_string()) {
        return false;
      }
      m_script = value.get<std::string>();
    }
    if (json.contains("all") && !json.at("all").is_null()) {
      const auto& value = json.at("all");
      if (!value.is_string()) {
        return false;
      }
      m_all = value.get<std::string>();
    }
    return true;
  }

  nlohmann::json toJson() const {
    nlohmann::json json = nlohmann::json::object();
    if (m_language.has_value()) {
      json["language"] = *m_language;
    }
    if (m_region.has_value()) {
      json["region"] = *m_region;
    }
    if (m_script.has_value()) {
      json["script"] = *m_script;
    }
    if (m_all.has_value()) {
      json["all"] = *m_all;
    }
    return json;
  }

  bool isSet() const { return m_IsSet; }

  bool languageIsSet() const { return m_language.has_value(); }
  std::optional<std::string> getLanguage() const {
    return m_language;
  }
  void setLanguage(const std::string& value) {
    m_language = value;
  }

  bool regionIsSet() const { return m_region.has_value(); }
  std::optional<std::string> getRegion() const {
    return m_region;
  }
  void setRegion(const std::string& value) {
    m_region = value;
  }

  bool scriptIsSet() const { return m_script.has_value(); }
  std::optional<std::string> getScript() const {
    return m_script;
  }
  void setScript(const std::string& value) {
    m_script = value;
  }

  bool allIsSet() const { return m_all.has_value(); }
  std::optional<std::string> getAll() const {
    return m_all;
  }
  void setAll(const std::string& value) {
    m_all = value;
  }

 private:
  bool m_IsSet = false;
  std::optional<std::string> m_language;
  std::optional<std::string> m_region;
  std::optional<std::string> m_script;
  std::optional<std::string> m_all;
};

}  // namespace rotundacfg

#endif  // ROTUNDA_PROFILE_LocaleProfile_H_

