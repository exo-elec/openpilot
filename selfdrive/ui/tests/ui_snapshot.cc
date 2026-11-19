// Render the settings UI to a PNG without a display, a car or a route: the quick check loop for UI work
// (change -> build -> snapshot -> look). Uses the real SettingsWindow and panels, at the device's 2160x1080.
//
//   QT_QPA_PLATFORM=offscreen selfdrive/ui/tests/ui_snapshot --panel Features --out /tmp/features.png
//     --full          the whole panel, as tall as its content, instead of the first screen
//     --find TEXT     scroll the panel so the row with this title is on screen
//     --click TEXT    press the button with this text (e.g. SELECT), then snapshot the dialog it opens
//
// Params are read as usual, so run it under OPENPILOT_PREFIX to show a prepared state without touching the device's.

#include <QAbstractButton>
#include <QApplication>
#include <QCommandLineParser>
#include <QDialog>
#include <QDir>
#include <QFontDatabase>
#include <QLabel>
#include <QScrollArea>
#include <QStackedWidget>
#include <QTimer>
#include <QVBoxLayout>

#include "selfdrive/ui/qt/offroad/settings.h"
#include "selfdrive/ui/qt/qt_window.h"

static QAbstractButton *findButton(QWidget *root, const QString &text) {
  for (auto *b : root->findChildren<QAbstractButton *>()) {
    if (b->text() == text && b->isVisible()) return b;
  }
  return nullptr;
}

int main(int argc, char *argv[]) {
  // under OPENPILOT_PREFIX, messaging lives in /dev/shm/<prefix>: create it so the UI's sockets can open
  if (const char *prefix = getenv("OPENPILOT_PREFIX"); prefix && *prefix) QDir().mkpath(QString("/dev/shm/%1").arg(prefix));
  QApplication app(argc, argv);
  QCommandLineParser parser;
  parser.addHelpOption();
  parser.addOption({"panel", "settings panel to open (its sidebar name)", "name", "Device"});
  parser.addOption({"out", "PNG to write", "path", "ui_snapshot.png"});
  parser.addOption({"full", "the whole panel, as tall as its content"});
  parser.addOption({"find", "scroll to the row with this title", "text"});
  parser.addOption({"click", "press this button, then snapshot the dialog it opens", "text"});
  parser.process(app);
  const QString out = QDir::current().absoluteFilePath(parser.value("out"));

  // same working directory and fonts as the real UI (selfdrive/ui)
  QDir::setCurrent(QCoreApplication::applicationDirPath() + "/..");
  for (const char *font : {"Inter-Black", "Inter-Bold", "Inter-ExtraBold", "Inter-ExtraLight", "Inter-Medium",
                           "Inter-Regular", "Inter-SemiBold", "Inter-Thin", "JetBrainsMono-Medium"}) {
    QFontDatabase::addApplicationFont(QString("../assets/fonts/%1.ttf").arg(font));
  }

  QWidget root;
  root.setStyleSheet("* { font-family: Inter; outline: none; } QWidget#root { background-color: black; }");
  root.setObjectName("root");
  QVBoxLayout *layout = new QVBoxLayout(&root);
  layout->setContentsMargins(0, 0, 0, 0);
  SettingsWindow *settings = new SettingsWindow(&root);
  layout->addWidget(settings);
  root.setFixedSize(DEVICE_SCREEN_SIZE);
  root.show();

  QAbstractButton *nav = findButton(settings, parser.value("panel"));
  if (!nav) {
    qCritical() << "no panel named" << parser.value("panel");
    return 1;
  }
  nav->click();
  app.processEvents();

  auto *stack = settings->findChild<QStackedWidget *>();
  auto *scroll = qobject_cast<QScrollArea *>(stack->currentWidget());

  if (parser.isSet("full") && scroll) {
    const int content = scroll->widget()->sizeHint().height() + 2 * 50;  // panel + the window's margins
    root.setFixedSize(DEVICE_SCREEN_SIZE.width(), std::max(DEVICE_SCREEN_SIZE.height(), content));
    app.processEvents();
  }
  if (parser.isSet("find") && scroll) {
    QWidget *row = nullptr;
    for (auto *w : scroll->widget()->findChildren<QWidget *>()) {  // a row's title is a QLabel or, in controls, a button
      auto *label = qobject_cast<QLabel *>(w);
      auto *button = qobject_cast<QAbstractButton *>(w);
      if ((label && label->text() == parser.value("find")) || (button && button->text() == parser.value("find"))) row = w;
    }
    if (!row) {
      qCritical() << "no row titled" << parser.value("find");
      return 1;
    }
    scroll->ensureWidgetVisible(row, 0, 300);
    app.processEvents();
  }

  int ret = 0;
  auto save = [&](QWidget *w) {
    if (!w->grab().save(out)) {
      qCritical() << "could not write" << out;
      ret = 1;
    } else {
      qInfo().noquote() << out;
    }
  };

  if (parser.isSet("click")) {
    QAbstractButton *btn = findButton(settings, parser.value("click"));
    if (!btn) {
      qCritical() << "no button" << parser.value("click");
      return 1;
    }
    // the button may open a modal dialog (exec): snapshot it from inside that dialog's event loop, then close it
    QTimer::singleShot(300, [&]() {
      QWidget *dialog = QApplication::activeModalWidget();
      for (QWidget *w : QApplication::topLevelWidgets()) {
        if (!dialog && w != &root && w->isVisible() && qobject_cast<QDialog *>(w)) dialog = w;
      }
      if (!dialog) qWarning() << "the button opened no dialog; saving the page";
      if (dialog) dialog->setFixedSize(DEVICE_SCREEN_SIZE);
      app.processEvents();
      save(dialog ? dialog : &root);
      if (dialog) dialog->close();
      app.quit();
    });
    QTimer::singleShot(0, btn, &QAbstractButton::click);
    app.exec();
  } else {
    save(&root);
  }
  return ret;
}
