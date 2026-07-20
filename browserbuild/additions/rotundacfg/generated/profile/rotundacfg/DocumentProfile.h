/*
 * DocumentProfile.h
 *
 * Generated from schemas/rotunda-profile.openapi.yaml.
 * Do not edit by hand.
 */

#ifndef ROTUNDA_PROFILE_DocumentProfile_H_
#define ROTUNDA_PROFILE_DocumentProfile_H_

#include <cstdint>
#include <optional>
#include <string>

#include "json.hpp"
#include "DocumentBodyProfile.h"

namespace rotundacfg {

class DocumentProfile {
 public:
  DocumentProfile() = default;
  explicit DocumentProfile(const nlohmann::json& json) { fromJson(json); }

  bool fromJson(const nlohmann::json& json) {
    if (!json.is_object()) {
      return false;
    }
    m_IsSet = true;
    if (json.contains("body") && !json.at("body").is_null()) {
      DocumentBodyProfile value;
      if (!value.fromJson(json.at("body"))) {
        return false;
      }
      m_body = value;
    }
    return true;
  }

  nlohmann::json toJson() const {
    nlohmann::json json = nlohmann::json::object();
    if (m_body.has_value()) {
      json["body"] = m_body->toJson();
    }
    return json;
  }

  bool isSet() const { return m_IsSet; }

  bool bodyIsSet() const { return m_body.has_value(); }
  std::optional<DocumentBodyProfile> getBody() const {
    return m_body;
  }
  void setBody(const DocumentBodyProfile& value) {
    m_body = value;
  }

 private:
  bool m_IsSet = false;
  std::optional<DocumentBodyProfile> m_body;
};

}  // namespace rotundacfg

#endif  // ROTUNDA_PROFILE_DocumentProfile_H_

