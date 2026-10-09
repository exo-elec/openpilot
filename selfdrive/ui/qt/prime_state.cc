#include "selfdrive/ui/qt/prime_state.h"

#include <QTimer>

#include "common/params.h"

PrimeState::PrimeState(QObject* parent) : QObject(parent) {
  const char *env_prime_type = std::getenv("PRIME_TYPE");
  auto type = env_prime_type ? env_prime_type : Params().get("PrimeType");

  if (!type.empty()) {
    prime_type = static_cast<PrimeState::Type>(std::atoi(type.c_str()));
  }

  // EOP: No cloud API calls. Emit initial state only.
  QTimer::singleShot(1, [this]() { emit changed(prime_type); });
}

void PrimeState::setType(PrimeState::Type type) {
  if (type != prime_type) {
    prime_type = type;
    Params().put("PrimeType", std::to_string(prime_type));
    emit changed(prime_type);
  }
}
