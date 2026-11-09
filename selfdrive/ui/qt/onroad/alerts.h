#pragma once

#include <QWidget>

#include "selfdrive/ui/ui.h"

class OnroadAlerts : public QWidget {
  Q_OBJECT

public:
  OnroadAlerts(QWidget *parent = 0) : QWidget(parent) {}
  void updateState(const UIState &s);
  void clear();

protected:
  struct Alert {
    QString text1;
    QString text2;
    QString type;
    cereal::SelfdriveState::AlertSize size;
    cereal::SelfdriveState::AlertStatus status;

    bool equal(const Alert &other) const {
      return text1 == other.text1 && text2 == other.text2 && type == other.type;
    }
  };

  const QMap<cereal::SelfdriveState::AlertStatus, QColor> alert_colors = {
    {cereal::SelfdriveState::AlertStatus::NORMAL, theme::ALERT_NORMAL},
    {cereal::SelfdriveState::AlertStatus::USER_PROMPT, theme::ALERT_WARNING},
    {cereal::SelfdriveState::AlertStatus::CRITICAL, theme::ALERT_CRITICAL},
  };

  void paintEvent(QPaintEvent*) override;
  OnroadAlerts::Alert getAlert(const SubMaster &sm, uint64_t started_frame);

  QColor bg;
  Alert alert = {};
};
