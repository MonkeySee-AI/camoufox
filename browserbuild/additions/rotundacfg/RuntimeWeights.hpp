#pragma once

#include <cstddef>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

#include "json.hpp"

namespace rotundacfg {

struct RuntimeTensor {
  std::vector<size_t> shape;
  std::vector<float> values;

  size_t size() const { return values.size(); }
  size_t dim(size_t index) const { return index < shape.size() ? shape[index] : 0; }
};

class RuntimeWeights {
 public:
  static std::optional<RuntimeWeights> Load(const std::string& path);
  static std::optional<std::string> ResolveBundledModelPath(
      const std::string& fileName);

  const RuntimeTensor* get(const std::string& name) const;
  std::optional<std::string> metadata(const std::string& key) const;
  std::optional<nlohmann::json> rotundaMetadata() const;

 private:
  std::unordered_map<std::string, RuntimeTensor> m_tensors;
  std::unordered_map<std::string, std::string> m_metadata;
};

}  // namespace rotundacfg
