/*
 * VoicesProfile_items_inner.h
 *
 * Generated from schemas/rotunda-profile.openapi.yaml.
 * Do not edit by hand.
 */

#ifndef ROTUNDA_PROFILE_VoicesProfile_items_inner_H_
#define ROTUNDA_PROFILE_VoicesProfile_items_inner_H_

#include <cstdint>
#include <optional>
#include <string>

#include "json.hpp"

namespace rotundacfg {

class VoicesProfile_items_inner {
 public:
  VoicesProfile_items_inner() = default;
  explicit VoicesProfile_items_inner(const nlohmann::json& json) { fromJson(json); }

  bool fromJson(const nlohmann::json& json) {
    if (!json.is_object()) {
      return false;
    }
    m_IsSet = true;
    if (json.contains("isLocalService") && !json.at("isLocalService").is_null()) {
      const auto& value = json.at("isLocalService");
      if (!value.is_boolean()) {
        return false;
      }
      m_isLocalService = value.get<bool>();
    }
    if (json.contains("isDefault") && !json.at("isDefault").is_null()) {
      const auto& value = json.at("isDefault");
      if (!value.is_boolean()) {
        return false;
      }
      m_isDefault = value.get<bool>();
    }
    if (json.contains("voiceUri") && !json.at("voiceUri").is_null()) {
      const auto& value = json.at("voiceUri");
      if (!value.is_string()) {
        return false;
      }
      m_voiceUri = value.get<std::string>();
    }
    if (json.contains("name") && !json.at("name").is_null()) {
      const auto& value = json.at("name");
      if (!value.is_string()) {
        return false;
      }
      m_name = value.get<std::string>();
    }
    if (json.contains("lang") && !json.at("lang").is_null()) {
      const auto& value = json.at("lang");
      if (!value.is_string()) {
        return false;
      }
      m_lang = value.get<std::string>();
    }
    return true;
  }

  nlohmann::json toJson() const {
    nlohmann::json json = nlohmann::json::object();
    if (m_isLocalService.has_value()) {
      json["isLocalService"] = *m_isLocalService;
    }
    if (m_isDefault.has_value()) {
      json["isDefault"] = *m_isDefault;
    }
    if (m_voiceUri.has_value()) {
      json["voiceUri"] = *m_voiceUri;
    }
    if (m_name.has_value()) {
      json["name"] = *m_name;
    }
    if (m_lang.has_value()) {
      json["lang"] = *m_lang;
    }
    return json;
  }

  bool isSet() const { return m_IsSet; }

  bool isLocalServiceIsSet() const { return m_isLocalService.has_value(); }
  std::optional<bool> isIsLocalService() const {
    return m_isLocalService;
  }
  void setIsLocalService(const bool& value) {
    m_isLocalService = value;
  }

  bool isDefaultIsSet() const { return m_isDefault.has_value(); }
  std::optional<bool> isIsDefault() const {
    return m_isDefault;
  }
  void setIsDefault(const bool& value) {
    m_isDefault = value;
  }

  bool voiceUriIsSet() const { return m_voiceUri.has_value(); }
  std::optional<std::string> getVoiceUri() const {
    return m_voiceUri;
  }
  void setVoiceUri(const std::string& value) {
    m_voiceUri = value;
  }

  bool nameIsSet() const { return m_name.has_value(); }
  std::optional<std::string> getName() const {
    return m_name;
  }
  void setName(const std::string& value) {
    m_name = value;
  }

  bool langIsSet() const { return m_lang.has_value(); }
  std::optional<std::string> getLang() const {
    return m_lang;
  }
  void setLang(const std::string& value) {
    m_lang = value;
  }

 private:
  bool m_IsSet = false;
  std::optional<bool> m_isLocalService;
  std::optional<bool> m_isDefault;
  std::optional<std::string> m_voiceUri;
  std::optional<std::string> m_name;
  std::optional<std::string> m_lang;
};

}  // namespace rotundacfg

#endif  // ROTUNDA_PROFILE_VoicesProfile_items_inner_H_

