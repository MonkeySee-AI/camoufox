#include "MouseRuntime.hpp"

#include "MaskConfig.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <memory>
#include <mutex>
#include <optional>
#include <random>
#include <utility>

namespace rotundacfg {

namespace {

constexpr int kMoveAction = 0;
constexpr int kLeftClickAction = 1;
constexpr double kPi = 3.14159265358979323846;

double sigmoid(double value) { return 1.0 / (1.0 + std::exp(-value)); }

double logToDt(double value) {
  // The model predicts log(dt + 1). Clamp before expm1 so a bad head value
  // cannot produce a multi-second pause or a negative dispatch interval.
  return std::expm1(std::max(0.0, std::min(value, std::log1p(5000.0))));
}

int endpointStepBudget(double distance, int maxSteps) {
  // Short moves should not spend the full learned budget or they look sticky;
  // long moves get more samples so the model can bend the path naturally.
  double cappedDistance = std::min(std::max(0.0, distance), 400.0);
  double learnedBudget = 8.0 + (2.0 * std::sqrt(cappedDistance));
  double smallMoveBudget = 2.0 + (0.5 * std::sqrt(cappedDistance));
  double blendedBudget = learnedBudget;
  if (cappedDistance < 80.0) {
    blendedBudget = smallMoveBudget;
  } else if (cappedDistance < 160.0) {
    double alpha = (cappedDistance - 80.0) / 80.0;
    blendedBudget = (smallMoveBudget * (1.0 - alpha)) + (learnedBudget * alpha);
  }
  int budget = static_cast<int>(std::round(blendedBudget));
  return std::max(4, std::min(maxSteps, budget));
}

int argmax(const std::vector<double>& values) {
  if (values.empty()) return 0;
  return static_cast<int>(
      std::distance(values.begin(), std::max_element(values.begin(), values.end())));
}

std::pair<double, double> screenPositionFromGoalRelative(
    double startX, double startY, double dstX, double dstY, double along,
    double perp) {
  // The decoder works in a goal-relative frame: `along` moves from start to
  // destination, while `perp` bends sideways along the perpendicular axis.
  double dx = dstX - startX;
  double dy = dstY - startY;
  double distance = std::max(1.0, std::hypot(dx, dy));
  double ux = dx / distance;
  double uy = dy / distance;
  double vx = -uy;
  double vy = ux;
  return {
      startX + distance * ((along * ux) + (perp * vx)),
      startY + distance * ((along * uy) + (perp * vy)),
  };
}

int configInt(const std::string& key, int fallback) {
  if (auto value = MaskConfig::GetInt32(key)) return *value;
  return fallback;
}

double configDouble(const std::string& key, double fallback) {
  if (auto value = MaskConfig::GetDouble(key)) return *value;
  return fallback;
}

}  // namespace

MouseRuntimeModel::MouseRuntimeModel(RuntimeWeights weights)
    : m_weights(std::move(weights)) {
  if (auto metadata = m_weights.rotundaMetadata()) {
    m_metadata = *metadata;
    m_coordinateScale = m_metadata.value("coordinateScale", 1.0);
    m_positionFrame = m_metadata.value("positionFrame", "goal_relative_delta");
    if (m_metadata.contains("modelConfig")) {
      const auto& config = m_metadata["modelConfig"];
      m_hiddenSize = config.value("hidden_size", 0);
      m_layers = config.value("layers", 1);
      m_actionCount = config.value("action_count", 5);
    }
  }
}

std::optional<MouseRuntimeModel> MouseRuntimeModel::Load(
    const std::string& path) {
  auto weights = RuntimeWeights::Load(path);
  if (!weights) return std::nullopt;
  MouseRuntimeModel model(std::move(*weights));
  if (model.m_hiddenSize <= 0 || model.m_layers <= 0 ||
      model.m_actionCount <= 0) {
    return std::nullopt;
  }
  return model;
}

std::vector<double> MouseRuntimeModel::linear(
    const std::vector<double>& input, const std::string& weightName,
    const std::string& biasName) const {
  const RuntimeTensor* weight = m_weights.get(weightName);
  const RuntimeTensor* bias = m_weights.get(biasName);
  if (!weight || !bias || weight->shape.size() != 2) return {};
  size_t outDim = weight->dim(0);
  size_t inDim = weight->dim(1);
  if (input.size() != inDim || bias->size() != outDim) return {};

  std::vector<double> output(outDim, 0.0);
  for (size_t out = 0; out < outDim; ++out) {
    double value = bias->values[out];
    for (size_t in = 0; in < inDim; ++in) {
      value += static_cast<double>(weight->values[out * inDim + in]) * input[in];
    }
    output[out] = value;
  }
  return output;
}

std::vector<double> MouseRuntimeModel::conditionEmbedding(
    const std::vector<double>& condition) const {
  std::vector<double> hidden =
      linear(condition, "condition.0.weight", "condition.0.bias");
  if (hidden.empty()) return {};
  for (double& value : hidden) value = std::tanh(value);
  hidden = linear(hidden, "condition.2.weight", "condition.2.bias");
  if (hidden.empty()) return {};
  for (double& value : hidden) value = std::tanh(value);
  return hidden;
}

std::vector<double> MouseRuntimeModel::gruCell(
    const std::vector<double>& input, const std::vector<double>& hidden,
    int layer) const {
  std::string suffix = "_l" + std::to_string(layer);
  const RuntimeTensor* wih = m_weights.get("gru.weight_ih" + suffix);
  const RuntimeTensor* whh = m_weights.get("gru.weight_hh" + suffix);
  const RuntimeTensor* bih = m_weights.get("gru.bias_ih" + suffix);
  const RuntimeTensor* bhh = m_weights.get("gru.bias_hh" + suffix);
  if (!wih || !whh || !bih || !bhh || wih->shape.size() != 2 ||
      whh->shape.size() != 2) {
    return {};
  }

  const size_t hiddenSize = hidden.size();
  const size_t inputSize = input.size();
  if (wih->dim(0) != hiddenSize * 3 || whh->dim(0) != hiddenSize * 3 ||
      wih->dim(1) != inputSize || whh->dim(1) != hiddenSize ||
      bih->size() != hiddenSize * 3 || bhh->size() != hiddenSize * 3) {
    return {};
  }

  std::vector<double> ih(hiddenSize * 3, 0.0);
  std::vector<double> hh(hiddenSize * 3, 0.0);
  for (size_t gate = 0; gate < hiddenSize * 3; ++gate) {
    double iv = bih->values[gate];
    for (size_t in = 0; in < inputSize; ++in) {
      iv += static_cast<double>(wih->values[gate * inputSize + in]) * input[in];
    }
    ih[gate] = iv;

    double hv = bhh->values[gate];
    for (size_t in = 0; in < hiddenSize; ++in) {
      hv += static_cast<double>(whh->values[gate * hiddenSize + in]) *
            hidden[in];
    }
    hh[gate] = hv;
  }

  std::vector<double> next(hiddenSize, 0.0);
  for (size_t i = 0; i < hiddenSize; ++i) {
    double reset = sigmoid(ih[i] + hh[i]);
    double update = sigmoid(ih[hiddenSize + i] + hh[hiddenSize + i]);
    double candidate =
        std::tanh(ih[2 * hiddenSize + i] + reset * hh[2 * hiddenSize + i]);
    next[i] = (1.0 - update) * candidate + update * hidden[i];
  }
  return next;
}

std::vector<MouseTrajectoryPoint> MouseRuntimeModel::fallback(
    double fromX, double fromY, double toX, double toY, bool clickAtEnd,
    int maxSteps) const {
  // If model loading or decoding fails, fall back to a deterministic straight
  // line with enough samples to preserve downstream timing/dispatch behavior.
  double distance = std::hypot(toX - fromX, toY - fromY);
  int count = std::max(2, std::min(maxSteps, static_cast<int>(std::pow(distance, 0.25) * 20.0)));
  std::vector<MouseTrajectoryPoint> rows;
  rows.reserve(count);
  for (int i = 1; i <= count; ++i) {
    double t = static_cast<double>(i) / count;
    rows.push_back({
        fromX + (toX - fromX) * t,
        fromY + (toY - fromY) * t,
        i == 1 ? 0.0 : 10.0,
        (clickAtEnd && i == count) ? kLeftClickAction : kMoveAction,
    });
  }
  return rows;
}

std::vector<MouseTrajectoryPoint> MouseRuntimeModel::decode(
    double fromX, double fromY, double toX, double toY, bool clickAtEnd,
    int maxSteps, double clickThreshold, double minDtMs,
    double pathCurveSigma, std::uint32_t randomSeed) const {
  return decodeInternal(fromX, fromY, toX, toY, clickAtEnd, maxSteps,
                        clickThreshold, minDtMs, pathCurveSigma, randomSeed,
                        false)
      .plan;
}

MouseRuntimeTrace MouseRuntimeModel::traceDecode(
    double fromX, double fromY, double toX, double toY, bool clickAtEnd,
    int maxSteps, double clickThreshold, double minDtMs,
    double pathCurveSigma, std::uint32_t randomSeed) const {
  return decodeInternal(fromX, fromY, toX, toY, clickAtEnd, maxSteps,
                        clickThreshold, minDtMs, pathCurveSigma, randomSeed,
                        true);
}

MouseRuntimeTrace MouseRuntimeModel::decodeInternal(
    double fromX, double fromY, double toX, double toY, bool clickAtEnd,
    int maxSteps, double clickThreshold, double minDtMs,
    double pathCurveSigma, std::uint32_t randomSeed,
    bool collectTrace) const {
  MouseRuntimeTrace trace;
  auto fallbackTrace = [&](std::vector<MouseTrajectoryPoint> plan) {
    trace.usedFallback = true;
    trace.plan = std::move(plan);
    return trace;
  };

  if (fromX == toX && fromY == toY) {
    trace.plan = {{toX, toY, 0.0,
                   clickAtEnd ? kLeftClickAction : kMoveAction}};
    return trace;
  }

  if (m_positionFrame != "goal_relative_delta") {
    return fallbackTrace(fallback(fromX, fromY, toX, toY, clickAtEnd, maxSteps));
  }

  double dx = toX - fromX;
  double dy = toY - fromY;
  double distance = std::hypot(dx, dy);
  double scale = std::max(1.0, m_coordinateScale);
  // Condition the decoder on absolute endpoints and the delta between them.
  // Values are scaled into the range the model saw during training.
  std::vector<double> condition = {
      fromX / scale, fromY / scale, toX / scale, toY / scale,
      dx / scale,    dy / scale,    distance / scale,
  };
  if (collectTrace) trace.condition = condition;
  std::vector<double> embedding = conditionEmbedding(condition);
  if (embedding.empty()) {
    return fallbackTrace(fallback(fromX, fromY, toX, toY, clickAtEnd, maxSteps));
  }
  if (collectTrace) trace.embedding = embedding;

  std::vector<std::vector<double>> hidden(
      static_cast<size_t>(m_layers), embedding);
  // Feed the previous prediction back into the next step:
  // [raw dt head, along, perp, one-hot action..., initial-marker].
  std::vector<double> previous(3 + static_cast<size_t>(m_actionCount) + 1, 0.0);
  previous[3 + static_cast<size_t>(m_actionCount)] = 1.0;

  double stateAlong = 0.0;
  double statePerp = 0.0;
  int endpointBudget = endpointStepBudget(distance, maxSteps);
  double curveBias = 0.0;
  if (pathCurveSigma > 0.0 && distance > 0.0) {
    // Add one gentle per-path bend instead of independent noise per step. That
    // keeps the generated path smooth while avoiding perfectly straight lines.
    std::mt19937 rng(randomSeed == 0 ? std::random_device{}() : randomSeed);
    std::normal_distribution<double> curve(0.0, std::min(pathCurveSigma, 0.2));
    curveBias = curve(rng);
  }
  std::vector<MouseTrajectoryPoint> rows;

  for (int step = 0; step < maxSteps; ++step) {
    std::vector<double> input = embedding;
    input.insert(input.end(), previous.begin(), previous.end());
    MouseRuntimeTraceStep traceStep;
    if (collectTrace) {
      traceStep.step = step;
      traceStep.previous = previous;
      traceStep.decoderInput = input;
    }
    for (int layer = 0; layer < m_layers; ++layer) {
      std::vector<double> next = gruCell(input, hidden[static_cast<size_t>(layer)], layer);
      if (next.empty()) {
        return fallbackTrace(fallback(fromX, fromY, toX, toY, clickAtEnd, maxSteps));
      }
      hidden[static_cast<size_t>(layer)] = next;
      input = next;
    }

    std::vector<double> dtHead = linear(input, "dt_head.weight", "dt_head.bias");
    std::vector<double> posHead = linear(input, "pos_head.weight", "pos_head.bias");
    std::vector<double> actionHead =
        linear(input, "action_head.weight", "action_head.bias");
    if (dtHead.empty() || posHead.size() != 2 || actionHead.empty()) {
      return fallbackTrace(fallback(fromX, fromY, toX, toY, clickAtEnd, maxSteps));
    }

    int rawActionId = argmax(actionHead);
    int actionId = rawActionId;
    double dtMs = logToDt(dtHead[0]);
    if (!rows.empty()) dtMs = std::max(dtMs, minDtMs);

    // The model proposes progress deltas, but we guide them so the path reaches
    // the endpoint within budget and never moves backward along the route.
    double remainingSteps = std::max(1, endpointBudget - step);
    double minDelta = (1.0 - stateAlong) / remainingSteps;
    double maxDelta = std::max(minDelta, minDelta * 2.0);
    double guidedDelta = std::max({std::min(posHead[0], maxDelta), minDelta, 0.0});
    stateAlong = std::min(1.0, stateAlong + guidedDelta);
    double guidedPerp = statePerp + posHead[1];
    // Sideways motion is allowed most in the middle of the path and pinched to
    // zero at the endpoints, which prevents hooks right before click/drop.
    double envelope = std::max(0.0, 0.35 * std::sin(kPi * std::max(0.0, std::min(1.0, stateAlong))));
    double curveOffset = curveBias * std::sin(kPi * std::max(0.0, std::min(1.0, stateAlong)));
    statePerp = std::max(
        -envelope,
        std::min(envelope, (guidedPerp * (1.0 - 0.25 * stateAlong)) + curveOffset));

    bool terminal = actionId != kMoveAction || stateAlong >= clickThreshold;
    if (terminal) {
      // Whatever the model's last action was, snap the final row onto the exact
      // requested coordinate so JS never has to dispatch a near-miss.
      actionId = clickAtEnd ? kLeftClickAction : kMoveAction;
      stateAlong = 1.0;
      statePerp = 0.0;
    }

    auto [x, y] = screenPositionFromGoalRelative(fromX, fromY, toX, toY,
                                                 stateAlong, statePerp);
    if (terminal) {
      x = toX;
      y = toY;
    }
    rows.push_back({x, y, dtMs, actionId});
    if (collectTrace) {
      traceStep.hidden = input;
      traceStep.dtHead = dtHead;
      traceStep.posHead = posHead;
      traceStep.actionHead = actionHead;
      traceStep.stateAlong = stateAlong;
      traceStep.statePerp = statePerp;
      traceStep.x = x;
      traceStep.y = y;
      traceStep.dtMs = dtMs;
      traceStep.rawAction = rawActionId;
      traceStep.action = actionId;
      traceStep.terminal = terminal;
      trace.steps.push_back(std::move(traceStep));
    }
    if (terminal) break;

    std::fill(previous.begin(), previous.end(), 0.0);
    // Store the raw timing head, not dtMs, because that is the feature shape the
    // recurrent decoder was trained to consume on the next step.
    previous[0] = dtHead[0];
    previous[1] = stateAlong;
    previous[2] = statePerp;
    previous[3 + static_cast<size_t>(std::max(0, std::min(actionId, m_actionCount - 1)))] = 1.0;
  }

  if (rows.empty() || rows.back().x != toX || rows.back().y != toY) {
    // Belt-and-suspenders endpoint guarantee for max-step exhaustion.
    rows.push_back({toX, toY, minDtMs, clickAtEnd ? kLeftClickAction : kMoveAction});
  }
  trace.plan = std::move(rows);
  return trace;
}

std::vector<MouseTrajectoryPoint> MouseRuntimeModel::GenerateFromConfig(
    double fromX, double fromY, double toX, double toY, bool clickAtEnd) {
  int maxSteps = configInt("humanize.mouseMaxSteps", 128);
  double clickThreshold = configDouble("humanize.mouseClickThreshold", 0.98);
  double minDtMs = configDouble("humanize.mouseMinDtMs", 4.0);
  double pathCurveSigma = configDouble("humanize.mousePathCurveSigma", 0.04);

  static std::once_flag initFlag;
  static std::unique_ptr<MouseRuntimeModel> model;
  std::call_once(initFlag, []() {
    // Model weights are process-global and immutable, so load them once and use
    // config values above to tune decode behavior per call.
    auto path = MaskConfig::GetString("humanize.mouseModelPath");
    if (!path || path->empty()) {
      path = RuntimeWeights::ResolveBundledModelPath("mouse.safetensors");
    }
    if (!path || path->empty()) return;
    auto loaded = MouseRuntimeModel::Load(*path);
    if (loaded) model = std::make_unique<MouseRuntimeModel>(std::move(*loaded));
  });

  if (model) {
    return model->decode(fromX, fromY, toX, toY, clickAtEnd, maxSteps,
                         clickThreshold, minDtMs, pathCurveSigma);
  }
  MouseRuntimeModel fallbackModel{RuntimeWeights()};
  return fallbackModel.fallback(fromX, fromY, toX, toY, clickAtEnd, maxSteps);
}

}  // namespace rotundacfg
