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

QString styleSheet(std::initializer_list<Component> components) {
  QStringList parts = {readSheet("base"), readSheet("dark_automotive")};
  for (Component c : components) {
    parts << readSheet(componentName(c));
  }
  parts.removeAll(QString());
  return parts.join('\n');
}

}  // namespace theme
