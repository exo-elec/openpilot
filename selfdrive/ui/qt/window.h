#pragma once

#include <QStackedLayout>
#include <QWidget>

#include "selfdrive/ui/qt/home.h"
#include "selfdrive/ui/qt/offroad/onboarding.h"
#include "selfdrive/ui/qt/offroad/settings.h"
#include "selfdrive/ui/qt/onroad/telemetry_panel.h"

class MainWindow : public QWidget {
  Q_OBJECT

public:
  explicit MainWindow(QWidget *parent = 0);

private:
  bool eventFilter(QObject *obj, QEvent *event) override;
  void openSettings(int index = 0, const QString &param = "");
  void closeSettings();
  // Fills black so the width main_layout reserves for `telemetry` (see
  // below) paints cleanly while telemetry is hidden (offroad) rather than
  // showing undefined content -- MainWindow has WA_NoSystemBackground set,
  // so nothing else erases that area for it.
  void paintEvent(QPaintEvent *event) override;

  // stack_layout (home/settings/onboarding) is nested inside a QHBoxLayout
  // alongside telemetry, rather than telemetry being nested inside
  // HomeWindow -- that keeps stack_layout's own rect at the ExoPilot 01M
  // baseline width in every case (telemetry, when present, is a sibling
  // consuming the extra width, not part of what the stack has to fill), so
  // no widget inside it needs to know ExoPilot 02M exists at all. See
  // nagaspilot/docs/TELEMETRY_PANEL.md.
  QStackedLayout *stack_layout;
  HomeWindow *homeWindow;
  SettingsWindow *settingsWindow;
  OnboardingWindow *onboardingWindow;
  TelemetryPanel *telemetry = nullptr;  // only on ExoPilot 02M (RK3576)

private slots:
  void updateState(const UIState &s);
};
