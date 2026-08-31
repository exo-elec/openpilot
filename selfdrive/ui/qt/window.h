#pragma once

#include <QStackedLayout>
#include <QWidget>

#include "selfdrive/ui/qt/home.h"
#include "selfdrive/ui/qt/offroad/onboarding.h"
#include "selfdrive/ui/qt/offroad/settings.h"

class MainWindow : public QWidget {
  Q_OBJECT

public:
  explicit MainWindow(QWidget *parent = 0);

private:
  bool eventFilter(QObject *obj, QEvent *event) override;
  void openSettings(int index = 0, const QString &param = "");
  void closeSettings();

  QStackedLayout *main_layout;
  HomeWindow *homeWindow;
  SettingsWindow *settingsWindow;
  OnboardingWindow *onboardingWindow;
  // QStackedLayout forces its current widget to fill the full MainWindow
  // rect regardless of size policy/maximumSize -- on ExoPilot 02M that rect
  // is wider than settingsWindow/onboardingWindow were designed for. These
  // wrappers hold them at the ExoPilot 01M baseline width (left-aligned,
  // stretch on the right) instead of letting them stretch; on 01M/PC the
  // wrapper's target width already equals the full rect, so it's a no-op.
  // See window.cc and nagaspilot/docs/TELEMETRY_PANEL.md.
  QWidget *settingsWrapper;
  QWidget *onboardingWrapper;
};
