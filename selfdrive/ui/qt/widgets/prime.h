#pragma once

#include <QMouseEvent>
#include <QStackedWidget>
#include <QWidget>

// EOP: Prime widgets simplified — no cloud pairing/subscription.
// Classes kept for ABI compatibility with upstream Qt build.

class PrimeUserWidget : public QFrame {
  Q_OBJECT
public:
  explicit PrimeUserWidget(QWidget* parent = 0);
};

class PrimeAdWidget : public QFrame {
  Q_OBJECT
public:
  explicit PrimeAdWidget(QWidget* parent = 0);
};

class SetupWidget : public QFrame {
  Q_OBJECT

public:
  explicit SetupWidget(QWidget* parent = 0);

signals:
  void openSettings(int index = 0, const QString &param = "");

protected:
  // Upstream emitted openSettings() via the embedded WiFiPromptWidget this
  // fork's rewrite removed; this keeps the widget clickable (home.cc still
  // wires openSettings to OffroadHome::openSettings) instead of leaving it
  // inert with no way to ever fire the signal it still declares.
  void mousePressEvent(QMouseEvent *event) override;

private:
  QStackedWidget *mainLayout;
};
