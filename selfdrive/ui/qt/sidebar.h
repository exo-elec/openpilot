#pragma once

#include <memory>

#include <QFrame>
#include <QMap>

#include "selfdrive/ui/ui.h"
#include "selfdrive/ui/qt/network/networking.h"

typedef QPair<QPair<QString, QString>, QColor> ItemStatus;
Q_DECLARE_METATYPE(ItemStatus);

class Sidebar : public QFrame {
  Q_OBJECT
  Q_PROPERTY(ItemStatus pandaStatus MEMBER panda_status NOTIFY valueChanged);
  Q_PROPERTY(ItemStatus tempStatus MEMBER temp_status NOTIFY valueChanged);
  Q_PROPERTY(ItemStatus bleStatus MEMBER ble_status NOTIFY valueChanged);
  Q_PROPERTY(ItemStatus wifiStatus MEMBER wifi_status NOTIFY valueChanged);
  Q_PROPERTY(QString netType MEMBER net_type NOTIFY valueChanged);
  Q_PROPERTY(int netStrength MEMBER net_strength NOTIFY valueChanged);
  Q_PROPERTY(bool recordingAudio MEMBER recording_audio NOTIFY valueChanged);

public:
  explicit Sidebar(QWidget* parent = 0);

signals:
  void openSettings(int index = 0, const QString &param = "");
  void valueChanged();

public slots:
  void offroadTransition(bool offroad);
  void updateState(const UIState &s);

protected:
  void paintEvent(QPaintEvent *event) override;
  void mousePressEvent(QMouseEvent *event) override;
  void mouseReleaseEvent(QMouseEvent *event) override;
  void drawMetric(QPainter &p, const QPair<QString, QString> &label, QColor c, int y);

  QPixmap home_img, flag_img, settings_img, mic_img, link_img;
  bool onroad, recording_audio, flag_pressed, settings_pressed, mic_indicator_pressed;
  const QMap<cereal::DeviceState::NetworkType, QString> network_type = {
    {cereal::DeviceState::NetworkType::NONE, tr("--")},
    {cereal::DeviceState::NetworkType::WIFI, tr("Wi-Fi")},
    {cereal::DeviceState::NetworkType::ETHERNET, tr("ETH")},
    {cereal::DeviceState::NetworkType::CELL2_G, tr("2G")},
    {cereal::DeviceState::NetworkType::CELL3_G, tr("3G")},
    {cereal::DeviceState::NetworkType::CELL4_G, tr("LTE")},
    {cereal::DeviceState::NetworkType::CELL5_G, tr("5G")}
  };

  // Resized for 600px height / ~260px width
  const QRect home_btn = QRect(40, 520, 180, 75);
  const QRect settings_btn = QRect(30, 15, 200, 80);
  const QRect mic_indicator_btn = QRect(140, 115, 55, 28);
  const QColor good_color = QColor(255, 255, 255);
  const QColor warning_color = QColor(218, 202, 37);
  const QColor danger_color = QColor(201, 34, 49);

  ItemStatus panda_status, temp_status, ble_status, wifi_status;
  QString net_type;
  int net_strength = 0;

private:
  std::unique_ptr<PubMaster> pm;
  Networking *networking = nullptr;
};
