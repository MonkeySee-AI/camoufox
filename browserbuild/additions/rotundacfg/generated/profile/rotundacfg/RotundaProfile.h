/*
 * RotundaProfile.h
 *
 * Generated from schemas/rotunda-profile.openapi.yaml.
 * Do not edit by hand.
 */

#ifndef ROTUNDA_PROFILE_RotundaProfile_H_
#define ROTUNDA_PROFILE_RotundaProfile_H_

#include <cstdint>
#include <optional>
#include <string>

#include "json.hpp"
#include "NavigatorProfile.h"
#include "ScreenProfile.h"
#include "WindowProfile.h"
#include "DocumentProfile.h"
#include "HeaderProfile.h"
#include "WebRtcProfile.h"
#include "BatteryProfile.h"
#include "FontsProfile.h"
#include "AudioProfile.h"
#include "GeolocationProfile.h"
#include "LocaleProfile.h"
#include "HumanizeProfile.h"
#include "AudioContextProfile.h"
#include "VoicesProfile.h"
#include "MediaDevicesProfile.h"

namespace rotundacfg {

class RotundaProfile {
 public:
  RotundaProfile() = default;
  explicit RotundaProfile(const nlohmann::json& json) { fromJson(json); }

  bool fromJson(const nlohmann::json& json) {
    if (!json.is_object()) {
      return false;
    }
    m_IsSet = true;
    if (json.contains("navigator") && !json.at("navigator").is_null()) {
      NavigatorProfile value;
      if (!value.fromJson(json.at("navigator"))) {
        return false;
      }
      m_navigator = value;
    }
    if (json.contains("screen") && !json.at("screen").is_null()) {
      ScreenProfile value;
      if (!value.fromJson(json.at("screen"))) {
        return false;
      }
      m_screen = value;
    }
    if (json.contains("window") && !json.at("window").is_null()) {
      WindowProfile value;
      if (!value.fromJson(json.at("window"))) {
        return false;
      }
      m_window = value;
    }
    if (json.contains("document") && !json.at("document").is_null()) {
      DocumentProfile value;
      if (!value.fromJson(json.at("document"))) {
        return false;
      }
      m_document = value;
    }
    if (json.contains("headers") && !json.at("headers").is_null()) {
      HeaderProfile value;
      if (!value.fromJson(json.at("headers"))) {
        return false;
      }
      m_headers = value;
    }
    if (json.contains("webrtc") && !json.at("webrtc").is_null()) {
      WebRtcProfile value;
      if (!value.fromJson(json.at("webrtc"))) {
        return false;
      }
      m_webrtc = value;
    }
    if (json.contains("pdfViewerEnabled") && !json.at("pdfViewerEnabled").is_null()) {
      const auto& value = json.at("pdfViewerEnabled");
      if (!value.is_boolean()) {
        return false;
      }
      m_pdfViewerEnabled = value.get<bool>();
    }
    if (json.contains("battery") && !json.at("battery").is_null()) {
      BatteryProfile value;
      if (!value.fromJson(json.at("battery"))) {
        return false;
      }
      m_battery = value;
    }
    if (json.contains("fonts") && !json.at("fonts").is_null()) {
      FontsProfile value;
      if (!value.fromJson(json.at("fonts"))) {
        return false;
      }
      m_fonts = value;
    }
    if (json.contains("audio") && !json.at("audio").is_null()) {
      AudioProfile value;
      if (!value.fromJson(json.at("audio"))) {
        return false;
      }
      m_audio = value;
    }
    if (json.contains("geolocation") && !json.at("geolocation").is_null()) {
      GeolocationProfile value;
      if (!value.fromJson(json.at("geolocation"))) {
        return false;
      }
      m_geolocation = value;
    }
    if (json.contains("timezone") && !json.at("timezone").is_null()) {
      const auto& value = json.at("timezone");
      if (!value.is_string()) {
        return false;
      }
      m_timezone = value.get<std::string>();
    }
    if (json.contains("locale") && !json.at("locale").is_null()) {
      LocaleProfile value;
      if (!value.fromJson(json.at("locale"))) {
        return false;
      }
      m_locale = value;
    }
    if (json.contains("humanize") && !json.at("humanize").is_null()) {
      HumanizeProfile value;
      if (!value.fromJson(json.at("humanize"))) {
        return false;
      }
      m_humanize = value;
    }
    if (json.contains("showcursor") && !json.at("showcursor").is_null()) {
      const auto& value = json.at("showcursor");
      if (!value.is_boolean()) {
        return false;
      }
      m_showcursor = value.get<bool>();
    }
    if (json.contains("audioContext") && !json.at("audioContext").is_null()) {
      AudioContextProfile value;
      if (!value.fromJson(json.at("audioContext"))) {
        return false;
      }
      m_audioContext = value;
    }
    if (json.contains("voices") && !json.at("voices").is_null()) {
      VoicesProfile value;
      if (!value.fromJson(json.at("voices"))) {
        return false;
      }
      m_voices = value;
    }
    if (json.contains("mediaDevices") && !json.at("mediaDevices").is_null()) {
      MediaDevicesProfile value;
      if (!value.fromJson(json.at("mediaDevices"))) {
        return false;
      }
      m_mediaDevices = value;
    }
    if (json.contains("allowMainWorld") && !json.at("allowMainWorld").is_null()) {
      const auto& value = json.at("allowMainWorld");
      if (!value.is_boolean()) {
        return false;
      }
      m_allowMainWorld = value.get<bool>();
    }
    if (json.contains("forceScopeAccess") && !json.at("forceScopeAccess").is_null()) {
      const auto& value = json.at("forceScopeAccess");
      if (!value.is_boolean()) {
        return false;
      }
      m_forceScopeAccess = value.get<bool>();
    }
    if (json.contains("disableTheming") && !json.at("disableTheming").is_null()) {
      const auto& value = json.at("disableTheming");
      if (!value.is_boolean()) {
        return false;
      }
      m_disableTheming = value.get<bool>();
    }
    if (json.contains("memorysaver") && !json.at("memorysaver").is_null()) {
      const auto& value = json.at("memorysaver");
      if (!value.is_boolean()) {
        return false;
      }
      m_memorysaver = value.get<bool>();
    }
    if (json.contains("addons") && !json.at("addons").is_null()) {
      m_addons = json.at("addons");
    }
    if (json.contains("certificatePaths") && !json.at("certificatePaths").is_null()) {
      m_certificatePaths = json.at("certificatePaths");
    }
    if (json.contains("certificates") && !json.at("certificates").is_null()) {
      m_certificates = json.at("certificates");
    }
    if (json.contains("debug") && !json.at("debug").is_null()) {
      const auto& value = json.at("debug");
      if (!value.is_boolean()) {
        return false;
      }
      m_debug = value.get<bool>();
    }
    return true;
  }

  nlohmann::json toJson() const {
    nlohmann::json json = nlohmann::json::object();
    if (m_navigator.has_value()) {
      json["navigator"] = m_navigator->toJson();
    }
    if (m_screen.has_value()) {
      json["screen"] = m_screen->toJson();
    }
    if (m_window.has_value()) {
      json["window"] = m_window->toJson();
    }
    if (m_document.has_value()) {
      json["document"] = m_document->toJson();
    }
    if (m_headers.has_value()) {
      json["headers"] = m_headers->toJson();
    }
    if (m_webrtc.has_value()) {
      json["webrtc"] = m_webrtc->toJson();
    }
    if (m_pdfViewerEnabled.has_value()) {
      json["pdfViewerEnabled"] = *m_pdfViewerEnabled;
    }
    if (m_battery.has_value()) {
      json["battery"] = m_battery->toJson();
    }
    if (m_fonts.has_value()) {
      json["fonts"] = m_fonts->toJson();
    }
    if (m_audio.has_value()) {
      json["audio"] = m_audio->toJson();
    }
    if (m_geolocation.has_value()) {
      json["geolocation"] = m_geolocation->toJson();
    }
    if (m_timezone.has_value()) {
      json["timezone"] = *m_timezone;
    }
    if (m_locale.has_value()) {
      json["locale"] = m_locale->toJson();
    }
    if (m_humanize.has_value()) {
      json["humanize"] = m_humanize->toJson();
    }
    if (m_showcursor.has_value()) {
      json["showcursor"] = *m_showcursor;
    }
    if (m_audioContext.has_value()) {
      json["audioContext"] = m_audioContext->toJson();
    }
    if (m_voices.has_value()) {
      json["voices"] = m_voices->toJson();
    }
    if (m_mediaDevices.has_value()) {
      json["mediaDevices"] = m_mediaDevices->toJson();
    }
    if (m_allowMainWorld.has_value()) {
      json["allowMainWorld"] = *m_allowMainWorld;
    }
    if (m_forceScopeAccess.has_value()) {
      json["forceScopeAccess"] = *m_forceScopeAccess;
    }
    if (m_disableTheming.has_value()) {
      json["disableTheming"] = *m_disableTheming;
    }
    if (m_memorysaver.has_value()) {
      json["memorysaver"] = *m_memorysaver;
    }
    if (m_addons.has_value()) {
      json["addons"] = *m_addons;
    }
    if (m_certificatePaths.has_value()) {
      json["certificatePaths"] = *m_certificatePaths;
    }
    if (m_certificates.has_value()) {
      json["certificates"] = *m_certificates;
    }
    if (m_debug.has_value()) {
      json["debug"] = *m_debug;
    }
    return json;
  }

  bool isSet() const { return m_IsSet; }

  bool navigatorIsSet() const { return m_navigator.has_value(); }
  std::optional<NavigatorProfile> getNavigator() const {
    return m_navigator;
  }
  void setNavigator(const NavigatorProfile& value) {
    m_navigator = value;
  }

  bool screenIsSet() const { return m_screen.has_value(); }
  std::optional<ScreenProfile> getScreen() const {
    return m_screen;
  }
  void setScreen(const ScreenProfile& value) {
    m_screen = value;
  }

  bool windowIsSet() const { return m_window.has_value(); }
  std::optional<WindowProfile> getWindow() const {
    return m_window;
  }
  void setWindow(const WindowProfile& value) {
    m_window = value;
  }

  bool documentIsSet() const { return m_document.has_value(); }
  std::optional<DocumentProfile> getDocument() const {
    return m_document;
  }
  void setDocument(const DocumentProfile& value) {
    m_document = value;
  }

  bool headersIsSet() const { return m_headers.has_value(); }
  std::optional<HeaderProfile> getHeaders() const {
    return m_headers;
  }
  void setHeaders(const HeaderProfile& value) {
    m_headers = value;
  }

  bool webrtcIsSet() const { return m_webrtc.has_value(); }
  std::optional<WebRtcProfile> getWebrtc() const {
    return m_webrtc;
  }
  void setWebrtc(const WebRtcProfile& value) {
    m_webrtc = value;
  }

  bool pdfViewerEnabledIsSet() const { return m_pdfViewerEnabled.has_value(); }
  std::optional<bool> isPdfViewerEnabled() const {
    return m_pdfViewerEnabled;
  }
  void setPdfViewerEnabled(const bool& value) {
    m_pdfViewerEnabled = value;
  }

  bool batteryIsSet() const { return m_battery.has_value(); }
  std::optional<BatteryProfile> getBattery() const {
    return m_battery;
  }
  void setBattery(const BatteryProfile& value) {
    m_battery = value;
  }

  bool fontsIsSet() const { return m_fonts.has_value(); }
  std::optional<FontsProfile> getFonts() const {
    return m_fonts;
  }
  void setFonts(const FontsProfile& value) {
    m_fonts = value;
  }

  bool audioIsSet() const { return m_audio.has_value(); }
  std::optional<AudioProfile> getAudio() const {
    return m_audio;
  }
  void setAudio(const AudioProfile& value) {
    m_audio = value;
  }

  bool geolocationIsSet() const { return m_geolocation.has_value(); }
  std::optional<GeolocationProfile> getGeolocation() const {
    return m_geolocation;
  }
  void setGeolocation(const GeolocationProfile& value) {
    m_geolocation = value;
  }

  bool timezoneIsSet() const { return m_timezone.has_value(); }
  std::optional<std::string> getTimezone() const {
    return m_timezone;
  }
  void setTimezone(const std::string& value) {
    m_timezone = value;
  }

  bool localeIsSet() const { return m_locale.has_value(); }
  std::optional<LocaleProfile> getLocale() const {
    return m_locale;
  }
  void setLocale(const LocaleProfile& value) {
    m_locale = value;
  }

  bool humanizeIsSet() const { return m_humanize.has_value(); }
  std::optional<HumanizeProfile> getHumanize() const {
    return m_humanize;
  }
  void setHumanize(const HumanizeProfile& value) {
    m_humanize = value;
  }

  bool showcursorIsSet() const { return m_showcursor.has_value(); }
  std::optional<bool> isShowcursor() const {
    return m_showcursor;
  }
  void setShowcursor(const bool& value) {
    m_showcursor = value;
  }

  bool audioContextIsSet() const { return m_audioContext.has_value(); }
  std::optional<AudioContextProfile> getAudioContext() const {
    return m_audioContext;
  }
  void setAudioContext(const AudioContextProfile& value) {
    m_audioContext = value;
  }

  bool voicesIsSet() const { return m_voices.has_value(); }
  std::optional<VoicesProfile> getVoices() const {
    return m_voices;
  }
  void setVoices(const VoicesProfile& value) {
    m_voices = value;
  }

  bool mediaDevicesIsSet() const { return m_mediaDevices.has_value(); }
  std::optional<MediaDevicesProfile> getMediaDevices() const {
    return m_mediaDevices;
  }
  void setMediaDevices(const MediaDevicesProfile& value) {
    m_mediaDevices = value;
  }

  bool allowMainWorldIsSet() const { return m_allowMainWorld.has_value(); }
  std::optional<bool> isAllowMainWorld() const {
    return m_allowMainWorld;
  }
  void setAllowMainWorld(const bool& value) {
    m_allowMainWorld = value;
  }

  bool forceScopeAccessIsSet() const { return m_forceScopeAccess.has_value(); }
  std::optional<bool> isForceScopeAccess() const {
    return m_forceScopeAccess;
  }
  void setForceScopeAccess(const bool& value) {
    m_forceScopeAccess = value;
  }

  bool disableThemingIsSet() const { return m_disableTheming.has_value(); }
  std::optional<bool> isDisableTheming() const {
    return m_disableTheming;
  }
  void setDisableTheming(const bool& value) {
    m_disableTheming = value;
  }

  bool memorysaverIsSet() const { return m_memorysaver.has_value(); }
  std::optional<bool> isMemorysaver() const {
    return m_memorysaver;
  }
  void setMemorysaver(const bool& value) {
    m_memorysaver = value;
  }

  bool addonsIsSet() const { return m_addons.has_value(); }
  std::optional<nlohmann::json> getAddons() const {
    return m_addons;
  }
  void setAddons(const nlohmann::json& value) {
    m_addons = value;
  }

  bool certificatePathsIsSet() const { return m_certificatePaths.has_value(); }
  std::optional<nlohmann::json> getCertificatePaths() const {
    return m_certificatePaths;
  }
  void setCertificatePaths(const nlohmann::json& value) {
    m_certificatePaths = value;
  }

  bool certificatesIsSet() const { return m_certificates.has_value(); }
  std::optional<nlohmann::json> getCertificates() const {
    return m_certificates;
  }
  void setCertificates(const nlohmann::json& value) {
    m_certificates = value;
  }

  bool debugIsSet() const { return m_debug.has_value(); }
  std::optional<bool> isDebug() const {
    return m_debug;
  }
  void setDebug(const bool& value) {
    m_debug = value;
  }

 private:
  bool m_IsSet = false;
  std::optional<NavigatorProfile> m_navigator;
  std::optional<ScreenProfile> m_screen;
  std::optional<WindowProfile> m_window;
  std::optional<DocumentProfile> m_document;
  std::optional<HeaderProfile> m_headers;
  std::optional<WebRtcProfile> m_webrtc;
  std::optional<bool> m_pdfViewerEnabled;
  std::optional<BatteryProfile> m_battery;
  std::optional<FontsProfile> m_fonts;
  std::optional<AudioProfile> m_audio;
  std::optional<GeolocationProfile> m_geolocation;
  std::optional<std::string> m_timezone;
  std::optional<LocaleProfile> m_locale;
  std::optional<HumanizeProfile> m_humanize;
  std::optional<bool> m_showcursor;
  std::optional<AudioContextProfile> m_audioContext;
  std::optional<VoicesProfile> m_voices;
  std::optional<MediaDevicesProfile> m_mediaDevices;
  std::optional<bool> m_allowMainWorld;
  std::optional<bool> m_forceScopeAccess;
  std::optional<bool> m_disableTheming;
  std::optional<bool> m_memorysaver;
  std::optional<nlohmann::json> m_addons;
  std::optional<nlohmann::json> m_certificatePaths;
  std::optional<nlohmann::json> m_certificates;
  std::optional<bool> m_debug;
};

}  // namespace rotundacfg

#endif  // ROTUNDA_PROFILE_RotundaProfile_H_
