/*
Helper to extract values from the ROTUNDA_CONFIG_PATH runtime profile.
Written by daijro.
*/

#pragma once

#include "generated/profile/rotundacfg/RotundaProfile.h"
#include "json.hpp"
#include "mozilla/glue/Debug.h"

#include <algorithm>
#include <cctype>
#include <codecvt>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <memory>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <vector>

#ifdef _WIN32
#  include <windows.h>
#endif

namespace MaskConfig {

inline std::optional<std::string> get_env_utf8(const std::string& name) {
#ifdef _WIN32
  std::wstring wName(name.begin(), name.end());
  DWORD size = GetEnvironmentVariableW(wName.c_str(), nullptr, 0);
  if (size == 0) return std::nullopt;

  std::vector<wchar_t> buffer(size);
  GetEnvironmentVariableW(wName.c_str(), buffer.data(), size);
  std::wstring wValue(buffer.data());

  std::wstring_convert<std::codecvt_utf8_utf16<wchar_t>> converter;
  return converter.to_bytes(wValue);
#else
  const char* value = std::getenv(name.c_str());
  if (!value) return std::nullopt;
  return std::string(value);
#endif
}

inline const nlohmann::json& GetJson() {
  static std::once_flag initFlag;
  static nlohmann::json jsonConfig;

  std::call_once(initFlag, []() {
    auto configPath = get_env_utf8("ROTUNDA_CONFIG_PATH");
    if (!configPath || configPath->empty()) {
      jsonConfig = nlohmann::json{};
      return;
    }

    std::ifstream configFile(*configPath, std::ios::in | std::ios::binary);
    if (!configFile) {
      printf_stderr("ERROR: Could not open ROTUNDA_CONFIG_PATH: %s\n",
                    configPath->c_str());
      jsonConfig = nlohmann::json{};
      return;
    }

    std::ostringstream buffer;
    buffer << configFile.rdbuf();
    std::string jsonString = buffer.str();

    if (!nlohmann::json::accept(jsonString)) {
      printf_stderr("ERROR: Invalid JSON passed to ROTUNDA_CONFIG_PATH: %s\n",
                    configPath->c_str());
      jsonConfig = nlohmann::json{};
      return;
    }

    jsonConfig = nlohmann::json::parse(jsonString);
  });

  return jsonConfig;
}

inline const rotundacfg::RotundaProfile& Profile() {
  static std::once_flag initFlag;
  static rotundacfg::RotundaProfile profile;

  std::call_once(initFlag, []() {
    if (!profile.fromJson(GetJson())) {
      printf_stderr("ERROR: ROTUNDA_CONFIG_PATH does not match RotundaProfile\n");
      profile = rotundacfg::RotundaProfile();
    }
  });

  return profile;
}

inline std::vector<std::string> JsonStringList(
    const std::optional<nlohmann::json>& value) {
  std::vector<std::string> result;
  if (!value || !value->is_array()) return result;
  for (const auto& item : *value) {
    result.push_back(item.get<std::string>());
  }
  return result;
}

inline std::vector<std::string> JsonStringListLower(
    const std::optional<nlohmann::json>& value) {
  std::vector<std::string> result = JsonStringList(value);
  for (auto& str : result) {
    std::transform(str.begin(), str.end(), str.begin(),
                   [](unsigned char c) { return std::tolower(c); });
  }
  return result;
}

inline std::optional<nlohmann::json> GetValue(const std::string& path) {
  const auto& data = GetJson();
  const nlohmann::json* cursor = &data;

  size_t start = 0;
  while (start < path.size()) {
    size_t end = path.find('.', start);
    std::string part =
        path.substr(start, end == std::string::npos ? std::string::npos
                                                    : end - start);
    if (part.empty()) return std::nullopt;
    if (!cursor->is_object() || !cursor->contains(part)) return std::nullopt;
    cursor = &((*cursor)[part]);
    if (end == std::string::npos) return *cursor;
    start = end + 1;
  }

  return std::nullopt;
}

inline std::optional<std::string> GetString(const std::string& key) {
  auto value = GetValue(key);
  if (!value) return std::nullopt;
  return value->get<std::string>();
}

inline std::vector<std::string> GetStringList(const std::string& key) {
  std::vector<std::string> result;
  auto value = GetValue(key);
  if (!value || !value->is_array()) return {};
  for (const auto& item : *value) {
    result.push_back(item.get<std::string>());
  }
  return result;
}

inline std::vector<std::string> GetStringListLower(const std::string& key) {
  std::vector<std::string> result = GetStringList(key);
  for (auto& str : result) {
    std::transform(str.begin(), str.end(), str.begin(),
                   [](unsigned char c) { return std::tolower(c); });
  }
  return result;
}

template <typename T>
inline std::optional<T> GetUintImpl(const std::string& key) {
  auto value = GetValue(key);
  if (!value) return std::nullopt;
  if (value->is_number_unsigned()) return value->get<T>();
  printf_stderr("ERROR: Value for key '%s' is not an unsigned integer\n",
                key.c_str());
  return std::nullopt;
}

inline std::optional<uint64_t> GetUint64(const std::string& key) {
  return GetUintImpl<uint64_t>(key);
}

inline std::optional<uint32_t> GetUint32(const std::string& key) {
  return GetUintImpl<uint32_t>(key);
}

inline std::optional<int32_t> GetInt32(const std::string& key) {
  auto value = GetValue(key);
  if (!value) return std::nullopt;
  if (value->is_number_integer()) return value->get<int32_t>();
  printf_stderr("ERROR: Value for key '%s' is not an integer\n", key.c_str());
  return std::nullopt;
}

inline std::optional<double> GetDouble(const std::string& key) {
  auto value = GetValue(key);
  if (!value) return std::nullopt;
  if (value->is_number_float()) return value->get<double>();
  if (value->is_number_unsigned() || value->is_number_integer())
    return static_cast<double>(value->get<int64_t>());
  printf_stderr("ERROR: Value for key '%s' is not a double\n", key.c_str());
  return std::nullopt;
}

inline std::optional<bool> GetBool(const std::string& key) {
  auto value = GetValue(key);
  if (!value) return std::nullopt;
  if (value->is_boolean()) return value->get<bool>();
  printf_stderr("ERROR: Value for key '%s' is not a boolean\n", key.c_str());
  return std::nullopt;
}

}  // namespace MaskConfig
