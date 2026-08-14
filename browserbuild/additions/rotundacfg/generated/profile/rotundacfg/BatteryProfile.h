/*
 * BatteryProfile.h
 *
 * Generated from schemas/rotunda-profile.openapi.yaml.
 * Do not edit by hand.
 */

#ifndef ROTUNDA_PROFILE_BatteryProfile_H_
#define ROTUNDA_PROFILE_BatteryProfile_H_

#include <cstdint>
#include <optional>
#include <string>

#include "json.hpp"

namespace rotundacfg {

class BatteryProfile {
 public:
  BatteryProfile() = default;
  explicit BatteryProfile(const nlohmann::json& json) { fromJson(json); }

  bool fromJson(const nlohmann::json& json) {
    if (!json.is_object()) {
      return false;
    }
    m_IsSet = true;
    if (json.contains("charging") && !json.at("charging").is_null()) {
      const auto& value = json.at("charging");
      if (!value.is_boolean()) {
        return false;
      }
      m_charging = value.get<bool>();
    }
    if (json.contains("chargingTime") && !json.at("chargingTime").is_null()) {
      const auto& value = json.at("chargingTime");
      if (!value.is_number()) {
        return false;
      }
      m_chargingTime = value.get<double>();
    }
    if (json.contains("dischargingTime") && !json.at("dischargingTime").is_null()) {
      const auto& value = json.at("dischargingTime");
      if (!value.is_number()) {
        return false;
      }
      m_dischargingTime = value.get<double>();
    }
    if (json.contains("level") && !json.at("level").is_null()) {
      const auto& value = json.at("level");
      if (!value.is_number()) {
        return false;
      }
      m_level = value.get<double>();
    }
    return true;
  }

  nlohmann::json toJson() const {
    nlohmann::json json = nlohmann::json::object();
    if (m_charging.has_value()) {
      json["charging"] = *m_charging;
    }
    if (m_chargingTime.has_value()) {
      json["chargingTime"] = *m_chargingTime;
    }
    if (m_dischargingTime.has_value()) {
      json["dischargingTime"] = *m_dischargingTime;
    }
    if (m_level.has_value()) {
      json["level"] = *m_level;
    }
    return json;
  }

  bool isSet() const { return m_IsSet; }

  bool chargingIsSet() const { return m_charging.has_value(); }
  std::optional<bool> isCharging() const {
    return m_charging;
  }
  void setCharging(const bool& value) {
    m_charging = value;
  }

  bool chargingTimeIsSet() const { return m_chargingTime.has_value(); }
  std::optional<double> getChargingTime() const {
    return m_chargingTime;
  }
  void setChargingTime(const double& value) {
    m_chargingTime = value;
  }

  bool dischargingTimeIsSet() const { return m_dischargingTime.has_value(); }
  std::optional<double> getDischargingTime() const {
    return m_dischargingTime;
  }
  void setDischargingTime(const double& value) {
    m_dischargingTime = value;
  }

  bool levelIsSet() const { return m_level.has_value(); }
  std::optional<double> getLevel() const {
    return m_level;
  }
  void setLevel(const double& value) {
    m_level = value;
  }

 private:
  bool m_IsSet = false;
  std::optional<bool> m_charging;
  std::optional<double> m_chargingTime;
  std::optional<double> m_dischargingTime;
  std::optional<double> m_level;
};

}  // namespace rotundacfg

#endif  // ROTUNDA_PROFILE_BatteryProfile_H_

