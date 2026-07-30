#include "selfdrive/ui/qt/api.h"

#include <openssl/pem.h>
#include <openssl/rsa.h>

#include <QApplication>
#include <QCryptographicHash>
#include <QDateTime>
#include <QDebug>
#include <QJsonDocument>
#include <QNetworkRequest>

#include <memory>
#include <string>

#include "common/util.h"
#include "system/hardware/hw.h"
#include "selfdrive/ui/qt/util.h"

namespace ExoApi {

RSA *get_rsa_private_key() {
  static std::unique_ptr<RSA, decltype(&RSA_free)> rsa_private(nullptr, RSA_free);
  if (!rsa_private) {
    // EOP: No comma API / RSA key
    FILE *fp = nullptr; // fopen(Path::rsa_file().c_str(), "rb");
    if (!fp) {
      qDebug() << "No RSA private key found, please run manager.py or registration.py";
      return nullptr;
    }
    rsa_private.reset(PEM_read_RSAPrivateKey(fp, NULL, NULL, NULL));
    fclose(fp);
  }
  return rsa_private.get();
}

QByteArray rsa_sign(const QByteArray &data) {
  RSA *rsa_private = get_rsa_private_key();
  if (!rsa_private) return {};

  QByteArray sig(RSA_size(rsa_private), Qt::Uninitialized);
  unsigned int sig_len;
  int ret = RSA_sign(NID_sha256, (unsigned char*)data.data(), data.size(), (unsigned char*)sig.data(), &sig_len, rsa_private);
  assert(ret == 1);
  assert(sig.size() == sig_len);
  return sig;
}

QString create_jwt(const QJsonObject &payloads, int expiry) {
  // EOP: No cloud JWT needed. Return empty string.
  Q_UNUSED(payloads)
  Q_UNUSED(expiry)
  return "";
}

}  // namespace ExoApi

HttpRequest::HttpRequest(QObject *parent, bool create_jwt, int timeout) : create_jwt(create_jwt), QObject(parent) {
  networkTimer = new QTimer(this);
  networkTimer->setSingleShot(true);
  networkTimer->setInterval(timeout);
  connect(networkTimer, &QTimer::timeout, this, &HttpRequest::requestTimeout);
}

bool HttpRequest::active() const {
  return reply != nullptr;
}

bool HttpRequest::timeout() const {
  return reply && reply->error() == QNetworkReply::OperationCanceledError;
}

void HttpRequest::sendRequest(const QString &requestURL, const HttpRequest::Method method) {
  // EOP: No cloud requests. Immediately emit failure.
  Q_UNUSED(requestURL)
  Q_UNUSED(method)
  QTimer::singleShot(0, [this]() {
    emit requestDone("EOP: Cloud requests disabled", false, QNetworkReply::HostNotFoundError);
  });
}

void HttpRequest::requestTimeout() {
  reply->abort();
}

void HttpRequest::requestFinished() {
  networkTimer->stop();

  if (reply->error() == QNetworkReply::NoError) {
    emit requestDone(reply->readAll(), true, reply->error());
  } else {
    QString error;
    if (reply->error() == QNetworkReply::OperationCanceledError) {
      nam()->clearAccessCache();
      nam()->clearConnectionCache();
      error = "Request timed out";
    } else {
      error = reply->errorString();
    }
    emit requestDone(error, false, reply->error());
  }

  reply->deleteLater();
  reply = nullptr;
}

QNetworkAccessManager *HttpRequest::nam() {
  static QNetworkAccessManager *networkAccessManager = new QNetworkAccessManager(qApp);
  return networkAccessManager;
}
