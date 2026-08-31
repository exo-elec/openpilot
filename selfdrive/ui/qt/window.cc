#include "selfdrive/ui/qt/window.h"

#include <QFontDatabase>
#include <QHBoxLayout>
#include <QPainter>

#include "selfdrive/ui/qt/qt_window.h"
#include "system/hardware/hw.h"

MainWindow::MainWindow(QWidget *parent) : QWidget(parent) {
  QHBoxLayout *main_layout = new QHBoxLayout(this);
  main_layout->setContentsMargins(0, 0, 0, 0);
  main_layout->setSpacing(0);

  // Everything that used to be MainWindow's direct QStackedLayout content
  // now lives inside stack_wrapper instead, so that widget -- not
  // MainWindow itself -- is what telemetry sits beside. The QHBoxLayout
  // gives stack_wrapper exactly (MainWindow's width - telemetry's width),
  // which by construction is always the ExoPilot 01M baseline
  // (deviceScreenSize() in qt_window.h defines MainWindow's width as
  // exactly EOP_01M_WIDTH + telemetry's width) -- so stack_layout keeps
  // forcing its children to fill that baseline exactly, on every platform,
  // with no widget inside it (including settingsWindow/onboardingWindow)
  // needing to know ExoPilot 02M exists. This depends on telemetry's
  // reserved width surviving even while it's hidden offroad -- see
  // setRetainSizeWhenHidden() below, without which QHBoxLayout collapses a
  // hidden widget to zero width and hands stack_wrapper the full window
  // instead. See nagaspilot/docs/TELEMETRY_PANEL.md.
  QWidget *stack_wrapper = new QWidget(this);
  main_layout->addWidget(stack_wrapper, 1);
  stack_layout = new QStackedLayout(stack_wrapper);
  stack_layout->setContentsMargins(0, 0, 0, 0);

  homeWindow = new HomeWindow(this);
  stack_layout->addWidget(homeWindow);
  QObject::connect(homeWindow, &HomeWindow::openSettings, this, &MainWindow::openSettings);
  QObject::connect(homeWindow, &HomeWindow::closeSettings, this, &MainWindow::closeSettings);

  settingsWindow = new SettingsWindow(this);
  stack_layout->addWidget(settingsWindow);
  QObject::connect(settingsWindow, &SettingsWindow::closeSettings, this, &MainWindow::closeSettings);
  QObject::connect(settingsWindow, &SettingsWindow::reviewTrainingGuide, [=]() {
    onboardingWindow->showTrainingGuide();
    stack_layout->setCurrentWidget(onboardingWindow);
  });
  onboardingWindow = new OnboardingWindow(this);
  stack_layout->addWidget(onboardingWindow);
  QObject::connect(onboardingWindow, &OnboardingWindow::onboardingDone, [=]() {
    stack_layout->setCurrentWidget(homeWindow);
  });
  if (!onboardingWindow->completed()) {
    stack_layout->setCurrentWidget(onboardingWindow);
  }

  const int telemetryWidth = getTelemetryPanelWidth();
  if (telemetryWidth > 0) {
    telemetry = new TelemetryPanel(this);
    telemetry->setFixedWidth(telemetryWidth);
    // QBoxLayout treats a hidden widget as zero-size by default
    // (QWidgetItem::isEmpty() == widget->isHidden(), unless this is set) --
    // without it, stack_wrapper above would get the *entire* MainWindow
    // width instead of the ExoPilot 01M baseline for as long as telemetry
    // is hidden offroad, silently reintroducing the "offroad screens
    // stretch on 02M" bug this feature already fixed once, just now
    // limited to (most of) the time the car is parked instead of always.
    QSizePolicy sp = telemetry->sizePolicy();
    sp.setRetainSizeWhenHidden(true);
    telemetry->setSizePolicy(sp);
    telemetry->setVisible(false);  // onroad-only; see the offroadTransition connection below
    main_layout->addWidget(telemetry);
  }

  QObject::connect(uiState(), &UIState::uiUpdate, this, &MainWindow::updateState);
  QObject::connect(uiState(), &UIState::offroadTransition, [=](bool offroad) {
    if (telemetry) telemetry->setVisible(!offroad);
    if (!offroad) {
      closeSettings();
    }
  });
  QObject::connect(device(), &Device::interactiveTimeout, [=]() {
    if (stack_layout->currentWidget() == settingsWindow) {
      closeSettings();
    }
  });

  // load fonts
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-Black.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-Bold.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-ExtraBold.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-ExtraLight.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-Medium.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-Regular.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-SemiBold.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-Thin.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/JetBrainsMono-Medium.ttf");

  // no outline to prevent the focus rectangle
  setStyleSheet(R"(
    * {
      font-family: Inter;
      outline: none;
    }
  )");
  setAttribute(Qt::WA_NoSystemBackground);
}

void MainWindow::updateState(const UIState &s) {
  if (telemetry) telemetry->updateState(s);
}

void MainWindow::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.fillRect(rect(), Qt::black);
}

void MainWindow::openSettings(int index, const QString &param) {
  stack_layout->setCurrentWidget(settingsWindow);
  settingsWindow->setCurrentPanel(index, param);
}

void MainWindow::closeSettings() {
  stack_layout->setCurrentWidget(homeWindow);

  if (uiState()->scene.started) {
    homeWindow->showSidebar(false);
  }
}

bool MainWindow::eventFilter(QObject *obj, QEvent *event) {
  bool ignore = false;
  switch (event->type()) {
    case QEvent::TouchBegin:
    case QEvent::TouchUpdate:
    case QEvent::TouchEnd:
    case QEvent::MouseButtonPress:
    case QEvent::MouseMove: {
      // ignore events when device is awakened by resetInteractiveTimeout
      ignore = !device()->isAwake();
      device()->resetInteractiveTimeout();
      break;
    }
    default:
      break;
  }
  return ignore;
}
