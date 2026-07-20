/*
 * VoicesProfile.h
 *
 * Generated from schemas/rotunda-profile.openapi.yaml.
 * Do not edit by hand.
 */

#ifndef ROTUNDA_PROFILE_VoicesProfile_H_
#define ROTUNDA_PROFILE_VoicesProfile_H_

#include <cstdint>
#include <optional>
#include <string>

#include "json.hpp"

namespace rotundacfg {

class VoicesProfile {
 public:
  VoicesProfile() = default;
  explicit VoicesProfile(const nlohmann::json& json) { fromJson(json); }

  bool fromJson(const nlohmann::json& json) {
    if (!json.is_object()) {
      return false;
    }
    m_IsSet = true;
    if (json.contains("items") && !json.at("items").is_null()) {
      m_items = json.at("items");
    }
    if (json.contains("blockIfNotDefined") && !json.at("blockIfNotDefined").is_null()) {
      const auto& value = json.at("blockIfNotDefined");
      if (!value.is_boolean()) {
        return false;
      }
      m_blockIfNotDefined = value.get<bool>();
    }
    if (json.contains("fakeCompletion") && !json.at("fakeCompletion").is_null()) {
      const auto& value = json.at("fakeCompletion");
      if (!value.is_boolean()) {
        return false;
      }
      m_fakeCompletion = value.get<bool>();
    }
    if (json.contains("fakeCompletionCharsPerSecond") && !json.at("fakeCompletionCharsPerSecond").is_null()) {
      const auto& value = json.at("fakeCompletionCharsPerSecond");
      if (!value.is_number()) {
        return false;
      }
      m_fakeCompletionCharsPerSecond = value.get<double>();
    }
    return true;
  }

  nlohmann::json toJson() const {
    nlohmann::json json = nlohmann::json::object();
    if (m_items.has_value()) {
      json["items"] = *m_items;
    }
    if (m_blockIfNotDefined.has_value()) {
      json["blockIfNotDefined"] = *m_blockIfNotDefined;
    }
    if (m_fakeCompletion.has_value()) {
      json["fakeCompletion"] = *m_fakeCompletion;
    }
    if (m_fakeCompletionCharsPerSecond.has_value()) {
      json["fakeCompletionCharsPerSecond"] = *m_fakeCompletionCharsPerSecond;
    }
    return json;
  }

  bool isSet() const { return m_IsSet; }

  bool itemsIsSet() const { return m_items.has_value(); }
  std::optional<nlohmann::json> getItems() const {
    return m_items;
  }
  void setItems(const nlohmann::json& value) {
    m_items = value;
  }

  bool blockIfNotDefinedIsSet() const { return m_blockIfNotDefined.has_value(); }
  std::optional<bool> isBlockIfNotDefined() const {
    return m_blockIfNotDefined;
  }
  void setBlockIfNotDefined(const bool& value) {
    m_blockIfNotDefined = value;
  }

  bool fakeCompletionIsSet() const { return m_fakeCompletion.has_value(); }
  std::optional<bool> isFakeCompletion() const {
    return m_fakeCompletion;
  }
  void setFakeCompletion(const bool& value) {
    m_fakeCompletion = value;
  }

  bool fakeCompletionCharsPerSecondIsSet() const { return m_fakeCompletionCharsPerSecond.has_value(); }
  std::optional<double> getFakeCompletionCharsPerSecond() const {
    return m_fakeCompletionCharsPerSecond;
  }
  void setFakeCompletionCharsPerSecond(const double& value) {
    m_fakeCompletionCharsPerSecond = value;
  }

 private:
  bool m_IsSet = false;
  std::optional<nlohmann::json> m_items;
  std::optional<bool> m_blockIfNotDefined;
  std::optional<bool> m_fakeCompletion;
  std::optional<double> m_fakeCompletionCharsPerSecond;
};

}  // namespace rotundacfg

#endif  // ROTUNDA_PROFILE_VoicesProfile_H_

