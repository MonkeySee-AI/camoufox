/*
 * GeolocationProfile.h
 *
 * Generated from schemas/rotunda-profile.openapi.yaml.
 * Do not edit by hand.
 */

#ifndef ROTUNDA_PROFILE_GeolocationProfile_H_
#define ROTUNDA_PROFILE_GeolocationProfile_H_

#include <cstdint>
#include <optional>
#include <string>

#include "json.hpp"

namespace rotundacfg {

class GeolocationProfile {
 public:
  GeolocationProfile() = default;
  explicit GeolocationProfile(const nlohmann::json& json) { fromJson(json); }

  bool fromJson(const nlohmann::json& json) {
    if (!json.is_object()) {
      return false;
    }
    m_IsSet = true;
    if (json.contains("latitude") && !json.at("latitude").is_null()) {
      const auto& value = json.at("latitude");
      if (!value.is_number()) {
        return false;
      }
      m_latitude = value.get<double>();
    }
    if (json.contains("longitude") && !json.at("longitude").is_null()) {
      const auto& value = json.at("longitude");
      if (!value.is_number()) {
        return false;
      }
      m_longitude = value.get<double>();
    }
    if (json.contains("accuracy") && !json.at("accuracy").is_null()) {
      const auto& value = json.at("accuracy");
      if (!value.is_number()) {
        return false;
      }
      m_accuracy = value.get<double>();
    }
    return true;
  }

  nlohmann::json toJson() const {
    nlohmann::json json = nlohmann::json::object();
    if (m_latitude.has_value()) {
      json["latitude"] = *m_latitude;
    }
    if (m_longitude.has_value()) {
      json["longitude"] = *m_longitude;
    }
    if (m_accuracy.has_value()) {
      json["accuracy"] = *m_accuracy;
    }
    return json;
  }

  bool isSet() const { return m_IsSet; }

  bool latitudeIsSet() const { return m_latitude.has_value(); }
  std::optional<double> getLatitude() const {
    return m_latitude;
  }
  void setLatitude(const double& value) {
    m_latitude = value;
  }

  bool longitudeIsSet() const { return m_longitude.has_value(); }
  std::optional<double> getLongitude() const {
    return m_longitude;
  }
  void setLongitude(const double& value) {
    m_longitude = value;
  }

  bool accuracyIsSet() const { return m_accuracy.has_value(); }
  std::optional<double> getAccuracy() const {
    return m_accuracy;
  }
  void setAccuracy(const double& value) {
    m_accuracy = value;
  }

 private:
  bool m_IsSet = false;
  std::optional<double> m_latitude;
  std::optional<double> m_longitude;
  std::optional<double> m_accuracy;
};

}  // namespace rotundacfg

#endif  // ROTUNDA_PROFILE_GeolocationProfile_H_

