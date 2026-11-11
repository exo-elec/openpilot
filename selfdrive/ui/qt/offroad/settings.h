#pragma once

#include <map>
#include <string>

#include <QButtonGroup>
#include <QFrame>
#include <QLabel>
#include <QPushButton>
#include <QStackedWidget>
#include <QTimer>
#include <QWidget>

#include "selfdrive/ui/ui.h"
#include "selfdrive/ui/qt/util.h"
#include "selfdrive/ui/qt/widgets/controls.h"

// ********** settings window + top-level panels **********
class SettingsWindow : public QFrame {
  Q_OBJECT

public:
  explicit SettingsWindow(QWidget *parent = 0);
  void setCurrentPanel(int index, const QString &param = "");

protected:
  void showEvent(QShowEvent *event) override;

signals:
  void closeSettings();
  void reviewTrainingGuide();

  void expandToggleDescription(const QString &param);
  void scrollToToggle(const QString &param);

private:
  void unlockAdvanced();

  QPushButton *sidebar_alert_widget;
  QWidget *sidebar_widget;
  QButtonGroup *nav_btns;
  QStackedWidget *panel_widget;
  QPushButton *advanced_btn = nullptr;  // hidden until unlocked on Device
};

class DevicePanel : public ListWidget {
  Q_OBJECT
public:
  explicit DevicePanel(SettingsWindow *parent);

signals:
  void reviewTrainingGuide();
  void advancedUnlocked();  // 8 quick taps on the Dongle ID row

protected:
  bool eventFilter(QObject *obj, QEvent *event) override;

private slots:
  void poweroff();
  void reboot();
  void updateCalibDescription();

private:
  void addOfflineMaps();
  void updateMapStatus();
  void updateNetworkInfo();
  void showEvent(QShowEvent *event) override;
  void hideEvent(QHideEvent *event) override;

  Params params;
  ButtonControl *resetCalibBtn;
  ButtonControl *mapRegionBtn = nullptr;
  ButtonControl *mapDownloadBtn = nullptr;
  LabelControl *ipLbl = nullptr;
  LabelControl *wifiLbl = nullptr;
  QTimer *net_timer = nullptr;
  int dongle_taps = 0;
  qint64 last_tap_ms = 0;
};

class TogglesPanel : public ListWidget {
  Q_OBJECT
public:
  explicit TogglesPanel(SettingsWindow *parent);
  void showEvent(QShowEvent *event) override;

public slots:
  void expandToggleDescription(const QString &param);
  void scrollToToggle(const QString &param);

private slots:
  void updateState(const UIState &s);

private:
  Params params;
  std::map<std::string, ParamControl*> toggles;
  ButtonParamControl *long_personality_setting;
  ButtonParamControl *drive_mode_setting;

  void updateToggles();
};

class SoftwarePanel : public ListWidget {
  Q_OBJECT
public:
  explicit SoftwarePanel(QWidget* parent = nullptr);

private:
  void showEvent(QShowEvent *event) override;
  void updateLabels();
  void checkForUpdates();

  bool is_onroad = false;

  QLabel *onroadLbl;
  LabelControl *versionLbl;
  ButtonControl *installBtn;
  ButtonControl *downloadBtn;

  Params params;
  ParamWatcher *fs_watch;
};
