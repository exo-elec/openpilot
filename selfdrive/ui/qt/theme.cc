#include "selfdrive/ui/qt/theme.h"

#include <QFile>
#include <QRegularExpression>
#include <QStringList>

namespace theme {

namespace {

// Qt's QSS parser ignores @import, so the directives are stripped and the
// layers are composed explicitly (same rule as styles/style_manager.py).
QString readSheet(const QString &name) {
  QFile f(QString(":/qss/%1.qss").arg(name));
  if (!f.open(QIODevice::ReadOnly | QIODevice::Text)) {
    return {};
  }
  static const QRegularExpression import_re(R"(^\s*@import\s+url\([^)]*\);?\s*$)",
                                            QRegularExpression::MultilineOption);
  return QString::fromUtf8(f.readAll()).remove(import_re);
}

const char *componentName(Component c) {
  switch (c) {
    case Component::ONROAD: return "onroad";
    case Component::OFFROAD: return "offroad";
  }
  return "";
}

}  // namespace

QString controlStyle(Control kind) {
  auto rgb = [](const QColor &c) { return c.name(QColor::HexRgb); };
  auto argb = [](const QColor &c) { return c.name(QColor::HexArgb); };
  const QString text_disabled = argb(QColor(CONTROL_TEXT.red(), CONTROL_TEXT.green(), CONTROL_TEXT.blue(), 0x55));

  if (kind == Control::VALUE) {
    return QString(R"(
      QAbstractSpinBox {
        min-height: %1px; max-height: %1px; min-width: 150px;
        padding: 0 8px 0 16px;
        border: none; border-radius: %2px;
        font-size: %3px; font-weight: 500;
        color: %4; background-color: %5;
      }
      QAbstractSpinBox:disabled { color: %6; }
      QAbstractSpinBox::up-button, QAbstractSpinBox::down-button { width: 40px; }
    )").arg(CONTROL_H).arg(CONTROL_RADIUS).arg(CONTROL_FONT_PX)
       .arg(rgb(CONTROL_TEXT), rgb(CONTROL_BG), text_disabled);
  }

  QColor bg = CONTROL_BG, pressed = CONTROL_BG_PRESSED, fg = CONTROL_TEXT;
  if (kind == Control::PRIMARY) {
    bg = PRIMARY; pressed = PRIMARY.lighter(125); fg = WHITE;
  } else if (kind == Control::DANGER) {
    bg = DANGER; pressed = DANGER.lighter(120); fg = WHITE;
  }

  QString qss = QString(R"(
    QPushButton {
      min-height: %1px; max-height: %1px;
      padding: 0 24px;
      border: none; border-radius: %2px;
      font-size: %3px; font-weight: 500;
      color: %4; background-color: %5;
    }
    QPushButton:pressed { background-color: %6; }
    QPushButton:disabled { color: %7; }
  )").arg(CONTROL_H).arg(CONTROL_RADIUS).arg(CONTROL_FONT_PX)
     .arg(rgb(fg), rgb(bg), rgb(pressed), text_disabled);

  if (kind == Control::SEGMENTED) {
    // The choices sit on their own line under the title (MultiButtonControl),
    // so they keep the full control size.
    QColor checked_off = PRIMARY;
    checked_off.setAlpha(0x99);
    qss += QString(R"(
      QPushButton { padding: 0 16px; }
      QPushButton:checked:enabled { color: %1; background-color: %2; }
      QPushButton:checked:disabled { background-color: %3; }
    )").arg(rgb(WHITE), rgb(PRIMARY), argb(checked_off));
  }
  return qss;
}

QString styleSheet(std::initializer_list<Component> components) {
  QStringList parts = {readSheet("base"), readSheet("dark_automotive")};
  for (Component c : components) {
    parts << readSheet(componentName(c));
  }
  parts.removeAll(QString());
  return parts.join('\n');
}

}  // namespace theme
