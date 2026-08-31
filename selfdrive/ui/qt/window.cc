#include "selfdrive/ui/qt/window.h"

#include <QFontDatabase>
#include <QHBoxLayout>

#include "selfdrive/ui/qt/qt_window.h"
#include "system/hardware/hw.h"

namespace {
// Wraps `content` in a QHBoxLayout that holds it at the ExoPilot 01M
// baseline width (left-aligned, stretch fills any extra width) instead of
// whatever the caller's QStackedLayout would otherwise force it to.
QWidget *wrapAtBaselineWidth(QWidget *content, QWidget *parent) {
  // Fixed, not just maximum: a plain QWidget defaults to a Preferred
  // horizontal size policy, so QHBoxLayout would size it to its own
  // sizeHint() (whatever that happens to be, unconstrained below 1024) and
  // hand the rest to the trailing stretch -- not what we want on any
  // platform. Pin it exactly, matching the full width QStackedLayout used
  // to force on it before this wrapper existed.
  content->setFixedWidth(EOP_01M_WIDTH);
  QWidget *wrapper = new QWidget(parent);
  wrapper->setAttribute(Qt::WA_StyledBackground);  // plain QWidget needs this for stylesheet backgrounds to paint
  wrapper->setStyleSheet("background-color: black;");  // matches OffroadHome's own background
  QHBoxLayout *layout = new QHBoxLayout(wrapper);
  layout->setContentsMargins(0, 0, 0, 0);
  layout->setSpacing(0);
  layout->addWidget(content);
  layout->addStretch();
  return wrapper;
}
}  // namespace

MainWindow::MainWindow(QWidget *parent) : QWidget(parent) {
  main_layout = new QStackedLayout(this);
  main_layout->setMargin(0);

  homeWindow = new HomeWindow(this);
  main_layout->addWidget(homeWindow);
  QObject::connect(homeWindow, &HomeWindow::openSettings, this, &MainWindow::openSettings);
  QObject::connect(homeWindow, &HomeWindow::closeSettings, this, &MainWindow::closeSettings);

  settingsWindow = new SettingsWindow(this);
  settingsWrapper = wrapAtBaselineWidth(settingsWindow, this);
  main_layout->addWidget(settingsWrapper);
  QObject::connect(settingsWindow, &SettingsWindow::closeSettings, this, &MainWindow::closeSettings);
  QObject::connect(settingsWindow, &SettingsWindow::reviewTrainingGuide, [=]() {
    onboardingWindow->showTrainingGuide();
    main_layout->setCurrentWidget(onboardingWrapper);
  });
  onboardingWindow = new OnboardingWindow(this);
  onboardingWrapper = wrapAtBaselineWidth(onboardingWindow, this);
  main_layout->addWidget(onboardingWrapper);
  QObject::connect(onboardingWindow, &OnboardingWindow::onboardingDone, [=]() {
    main_layout->setCurrentWidget(homeWindow);
  });
  if (!onboardingWindow->completed()) {
    main_layout->setCurrentWidget(onboardingWrapper);
  }

  QObject::connect(uiState(), &UIState::offroadTransition, [=](bool offroad) {
    if (!offroad) {
      closeSettings();
    }
  });
  QObject::connect(device(), &Device::interactiveTimeout, [=]() {
    if (main_layout->currentWidget() == settingsWrapper) {
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

void MainWindow::openSettings(int index, const QString &param) {
  main_layout->setCurrentWidget(settingsWrapper);
  settingsWindow->setCurrentPanel(index, param);
}

void MainWindow::closeSettings() {
  main_layout->setCurrentWidget(homeWindow);

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
