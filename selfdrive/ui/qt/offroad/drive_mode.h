#pragma once

#include <QLabel>
#include <QPushButton>

#include "common/params.h"

// Offroad home card showing the DLON Drive Mode (DlonMode: Highway /
// Urban / Dynamic). Tapping it opens Settings -> Driving.
class DriveModeButton : public QPushButton {
  Q_OBJECT

public:
  explicit DriveModeButton(QWidget* parent = 0);
  static QString modeName(int mode);

signals:
  void openSettings(int index = 0, const QString &toggle = "");

private:
  void showEvent(QShowEvent *event) override;

  Params params;
  int mode = 2;
  int horizontal_padding = 20;
  QLabel *title_label;
  QLabel *mode_label;

protected:
  void paintEvent(QPaintEvent *event) override;
};
