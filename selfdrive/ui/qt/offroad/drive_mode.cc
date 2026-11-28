#include "selfdrive/ui/qt/offroad/drive_mode.h"

#include <cstdlib>

#include <QHBoxLayout>
#include <QPainter>
#include <QPainterPath>

#include "selfdrive/ui/qt/theme.h"

QString DriveModeButton::modeName(int m) {
  switch (m) {
    case 0: return tr("Highway (Rule)");
    case 1: return tr("Urban (AI)");
    default: return tr("Dynamic");
  }
}

DriveModeButton::DriveModeButton(QWidget *parent) : QPushButton(parent) {
  connect(this, &QPushButton::clicked, [=]() { emit openSettings(0, "DrivingPanel"); });

  setFixedHeight(52);
  QHBoxLayout *main_layout = new QHBoxLayout(this);
  main_layout->setContentsMargins(horizontal_padding, 0, horizontal_padding, 0);

  title_label = new QLabel(tr("DRIVE MODE"));
  mode_label = new QLabel;
  main_layout->addWidget(title_label, 1, Qt::AlignLeft | Qt::AlignVCenter);
  main_layout->addWidget(mode_label, 0, Qt::AlignRight | Qt::AlignVCenter);

  setStyleSheet(R"(
    QPushButton { border: none; }
    QLabel { font-size: 20px; color: #ffffff; background: transparent; }
  )");
  mode_label->setStyleSheet("font-weight: 600;");
}

void DriveModeButton::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.setPen(Qt::NoPen);
  p.setRenderHint(QPainter::Antialiasing);

  QPainterPath path;
  path.addRoundedRect(rect(), 10, 10);
  QColor bg = theme::PRIMARY;
  if (isDown()) bg = bg.lighter(125);
  p.fillPath(path, bg);
}

void DriveModeButton::showEvent(QShowEvent *event) {
  const std::string v = params.get("DlonMode");
  mode = v.empty() ? 2 : std::atoi(v.c_str());
  mode_label->setText(modeName(mode));
}
