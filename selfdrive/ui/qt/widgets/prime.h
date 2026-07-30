#pragma once

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

private:
  QStackedWidget *mainLayout;
};
