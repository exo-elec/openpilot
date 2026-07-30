#pragma once

#include <QWidget>

// Forward declarations
class SettingsWindow;

class FirehosePanel : public QWidget {
  Q_OBJECT
public:
  explicit FirehosePanel(SettingsWindow *parent);

private slots:
  void refresh();
};
