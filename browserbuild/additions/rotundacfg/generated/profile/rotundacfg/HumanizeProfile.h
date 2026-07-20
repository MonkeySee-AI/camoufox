/*
 * HumanizeProfile.h
 *
 * Generated from schemas/rotunda-profile.openapi.yaml.
 * Do not edit by hand.
 */

#ifndef ROTUNDA_PROFILE_HumanizeProfile_H_
#define ROTUNDA_PROFILE_HumanizeProfile_H_

#include <cstdint>
#include <optional>
#include <string>

#include "json.hpp"

namespace rotundacfg {

class HumanizeProfile {
 public:
  HumanizeProfile() = default;
  explicit HumanizeProfile(const nlohmann::json& json) { fromJson(json); }

  bool fromJson(const nlohmann::json& json) {
    if (!json.is_object()) {
      return false;
    }
    m_IsSet = true;
    if (json.contains("enabled") && !json.at("enabled").is_null()) {
      const auto& value = json.at("enabled");
      if (!value.is_boolean()) {
        return false;
      }
      m_enabled = value.get<bool>();
    }
    if (json.contains("maxTime") && !json.at("maxTime").is_null()) {
      const auto& value = json.at("maxTime");
      if (!value.is_number()) {
        return false;
      }
      m_maxTime = value.get<double>();
    }
    if (json.contains("minTime") && !json.at("minTime").is_null()) {
      const auto& value = json.at("minTime");
      if (!value.is_number()) {
        return false;
      }
      m_minTime = value.get<double>();
    }
    if (json.contains("mouseModelPath") && !json.at("mouseModelPath").is_null()) {
      const auto& value = json.at("mouseModelPath");
      if (!value.is_string()) {
        return false;
      }
      m_mouseModelPath = value.get<std::string>();
    }
    if (json.contains("keyboardModelPath") && !json.at("keyboardModelPath").is_null()) {
      const auto& value = json.at("keyboardModelPath");
      if (!value.is_string()) {
        return false;
      }
      m_keyboardModelPath = value.get<std::string>();
    }
    if (json.contains("mouseMaxSteps") && !json.at("mouseMaxSteps").is_null()) {
      const auto& value = json.at("mouseMaxSteps");
      if (!value.is_number_integer()) {
        return false;
      }
      m_mouseMaxSteps = value.get<int32_t>();
    }
    if (json.contains("mouseClickThreshold") && !json.at("mouseClickThreshold").is_null()) {
      const auto& value = json.at("mouseClickThreshold");
      if (!value.is_number()) {
        return false;
      }
      m_mouseClickThreshold = value.get<double>();
    }
    if (json.contains("mouseMinDtMs") && !json.at("mouseMinDtMs").is_null()) {
      const auto& value = json.at("mouseMinDtMs");
      if (!value.is_number()) {
        return false;
      }
      m_mouseMinDtMs = value.get<double>();
    }
    if (json.contains("mousePathCurveSigma") && !json.at("mousePathCurveSigma").is_null()) {
      const auto& value = json.at("mousePathCurveSigma");
      if (!value.is_number()) {
        return false;
      }
      m_mousePathCurveSigma = value.get<double>();
    }
    if (json.contains("keyboardSampleTypos") && !json.at("keyboardSampleTypos").is_null()) {
      const auto& value = json.at("keyboardSampleTypos");
      if (!value.is_boolean()) {
        return false;
      }
      m_keyboardSampleTypos = value.get<bool>();
    }
    if (json.contains("keyboardTimingJitterSigma") && !json.at("keyboardTimingJitterSigma").is_null()) {
      const auto& value = json.at("keyboardTimingJitterSigma");
      if (!value.is_number()) {
        return false;
      }
      m_keyboardTimingJitterSigma = value.get<double>();
    }
    if (json.contains("keyboardTimingTemperature") && !json.at("keyboardTimingTemperature").is_null()) {
      const auto& value = json.at("keyboardTimingTemperature");
      if (!value.is_number()) {
        return false;
      }
      m_keyboardTimingTemperature = value.get<double>();
    }
    if (json.contains("keyboardPauseProbability") && !json.at("keyboardPauseProbability").is_null()) {
      const auto& value = json.at("keyboardPauseProbability");
      if (!value.is_number()) {
        return false;
      }
      m_keyboardPauseProbability = value.get<double>();
    }
    if (json.contains("keyboardPauseMeanMs") && !json.at("keyboardPauseMeanMs").is_null()) {
      const auto& value = json.at("keyboardPauseMeanMs");
      if (!value.is_number()) {
        return false;
      }
      m_keyboardPauseMeanMs = value.get<double>();
    }
    return true;
  }

  nlohmann::json toJson() const {
    nlohmann::json json = nlohmann::json::object();
    if (m_enabled.has_value()) {
      json["enabled"] = *m_enabled;
    }
    if (m_maxTime.has_value()) {
      json["maxTime"] = *m_maxTime;
    }
    if (m_minTime.has_value()) {
      json["minTime"] = *m_minTime;
    }
    if (m_mouseModelPath.has_value()) {
      json["mouseModelPath"] = *m_mouseModelPath;
    }
    if (m_keyboardModelPath.has_value()) {
      json["keyboardModelPath"] = *m_keyboardModelPath;
    }
    if (m_mouseMaxSteps.has_value()) {
      json["mouseMaxSteps"] = *m_mouseMaxSteps;
    }
    if (m_mouseClickThreshold.has_value()) {
      json["mouseClickThreshold"] = *m_mouseClickThreshold;
    }
    if (m_mouseMinDtMs.has_value()) {
      json["mouseMinDtMs"] = *m_mouseMinDtMs;
    }
    if (m_mousePathCurveSigma.has_value()) {
      json["mousePathCurveSigma"] = *m_mousePathCurveSigma;
    }
    if (m_keyboardSampleTypos.has_value()) {
      json["keyboardSampleTypos"] = *m_keyboardSampleTypos;
    }
    if (m_keyboardTimingJitterSigma.has_value()) {
      json["keyboardTimingJitterSigma"] = *m_keyboardTimingJitterSigma;
    }
    if (m_keyboardTimingTemperature.has_value()) {
      json["keyboardTimingTemperature"] = *m_keyboardTimingTemperature;
    }
    if (m_keyboardPauseProbability.has_value()) {
      json["keyboardPauseProbability"] = *m_keyboardPauseProbability;
    }
    if (m_keyboardPauseMeanMs.has_value()) {
      json["keyboardPauseMeanMs"] = *m_keyboardPauseMeanMs;
    }
    return json;
  }

  bool isSet() const { return m_IsSet; }

  bool enabledIsSet() const { return m_enabled.has_value(); }
  std::optional<bool> isEnabled() const {
    return m_enabled;
  }
  void setEnabled(const bool& value) {
    m_enabled = value;
  }

  bool maxTimeIsSet() const { return m_maxTime.has_value(); }
  std::optional<double> getMaxTime() const {
    return m_maxTime;
  }
  void setMaxTime(const double& value) {
    m_maxTime = value;
  }

  bool minTimeIsSet() const { return m_minTime.has_value(); }
  std::optional<double> getMinTime() const {
    return m_minTime;
  }
  void setMinTime(const double& value) {
    m_minTime = value;
  }

  bool mouseModelPathIsSet() const { return m_mouseModelPath.has_value(); }
  std::optional<std::string> getMouseModelPath() const {
    return m_mouseModelPath;
  }
  void setMouseModelPath(const std::string& value) {
    m_mouseModelPath = value;
  }

  bool keyboardModelPathIsSet() const { return m_keyboardModelPath.has_value(); }
  std::optional<std::string> getKeyboardModelPath() const {
    return m_keyboardModelPath;
  }
  void setKeyboardModelPath(const std::string& value) {
    m_keyboardModelPath = value;
  }

  bool mouseMaxStepsIsSet() const { return m_mouseMaxSteps.has_value(); }
  std::optional<int32_t> getMouseMaxSteps() const {
    return m_mouseMaxSteps;
  }
  void setMouseMaxSteps(const int32_t& value) {
    m_mouseMaxSteps = value;
  }

  bool mouseClickThresholdIsSet() const { return m_mouseClickThreshold.has_value(); }
  std::optional<double> getMouseClickThreshold() const {
    return m_mouseClickThreshold;
  }
  void setMouseClickThreshold(const double& value) {
    m_mouseClickThreshold = value;
  }

  bool mouseMinDtMsIsSet() const { return m_mouseMinDtMs.has_value(); }
  std::optional<double> getMouseMinDtMs() const {
    return m_mouseMinDtMs;
  }
  void setMouseMinDtMs(const double& value) {
    m_mouseMinDtMs = value;
  }

  bool mousePathCurveSigmaIsSet() const { return m_mousePathCurveSigma.has_value(); }
  std::optional<double> getMousePathCurveSigma() const {
    return m_mousePathCurveSigma;
  }
  void setMousePathCurveSigma(const double& value) {
    m_mousePathCurveSigma = value;
  }

  bool keyboardSampleTyposIsSet() const { return m_keyboardSampleTypos.has_value(); }
  std::optional<bool> isKeyboardSampleTypos() const {
    return m_keyboardSampleTypos;
  }
  void setKeyboardSampleTypos(const bool& value) {
    m_keyboardSampleTypos = value;
  }

  bool keyboardTimingJitterSigmaIsSet() const { return m_keyboardTimingJitterSigma.has_value(); }
  std::optional<double> getKeyboardTimingJitterSigma() const {
    return m_keyboardTimingJitterSigma;
  }
  void setKeyboardTimingJitterSigma(const double& value) {
    m_keyboardTimingJitterSigma = value;
  }

  bool keyboardTimingTemperatureIsSet() const { return m_keyboardTimingTemperature.has_value(); }
  std::optional<double> getKeyboardTimingTemperature() const {
    return m_keyboardTimingTemperature;
  }
  void setKeyboardTimingTemperature(const double& value) {
    m_keyboardTimingTemperature = value;
  }

  bool keyboardPauseProbabilityIsSet() const { return m_keyboardPauseProbability.has_value(); }
  std::optional<double> getKeyboardPauseProbability() const {
    return m_keyboardPauseProbability;
  }
  void setKeyboardPauseProbability(const double& value) {
    m_keyboardPauseProbability = value;
  }

  bool keyboardPauseMeanMsIsSet() const { return m_keyboardPauseMeanMs.has_value(); }
  std::optional<double> getKeyboardPauseMeanMs() const {
    return m_keyboardPauseMeanMs;
  }
  void setKeyboardPauseMeanMs(const double& value) {
    m_keyboardPauseMeanMs = value;
  }

 private:
  bool m_IsSet = false;
  std::optional<bool> m_enabled;
  std::optional<double> m_maxTime;
  std::optional<double> m_minTime;
  std::optional<std::string> m_mouseModelPath;
  std::optional<std::string> m_keyboardModelPath;
  std::optional<int32_t> m_mouseMaxSteps;
  std::optional<double> m_mouseClickThreshold;
  std::optional<double> m_mouseMinDtMs;
  std::optional<double> m_mousePathCurveSigma;
  std::optional<bool> m_keyboardSampleTypos;
  std::optional<double> m_keyboardTimingJitterSigma;
  std::optional<double> m_keyboardTimingTemperature;
  std::optional<double> m_keyboardPauseProbability;
  std::optional<double> m_keyboardPauseMeanMs;
};

}  // namespace rotundacfg

#endif  // ROTUNDA_PROFILE_HumanizeProfile_H_
