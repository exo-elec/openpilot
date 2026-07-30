#include "selfdrive/ui/qt/offroad/firehose.h"
#include "selfdrive/ui/ui.h"
#include "selfdrive/ui/qt/offroad/settings.h"

#include <QLabel>
#include <QVBoxLayout>
#include <QFrame>

FirehosePanel::FirehosePanel(SettingsWindow *parent) : QWidget((QWidget*)parent) {
  QVBoxLayout *layout = new QVBoxLayout(this);
  layout->setContentsMargins(40, 40, 40, 40);
  layout->setSpacing(20);

  QLabel *title = new QLabel(tr("Firehose Mode"));
  title->setStyleSheet("font-size: 55px; font-weight: 500;");
  layout->addWidget(title, 0, Qt::AlignCenter);

  QFrame *content = new QFrame();
  content->setStyleSheet("background-color: #292929; border-radius: 15px; padding: 20px;");
  QVBoxLayout *content_layout = new QVBoxLayout(content);
  content_layout->setSpacing(20);

  QLabel *description = new QLabel(tr(
    "EOP: Firehose Mode is not available.\n\n"
    "This is an offline-first system. No driving data is uploaded to external servers."
  ));
  description->setStyleSheet("font-size: 26px; padding-bottom: 12px;");
  description->setWordWrap(true);
  content_layout->addWidget(description);

  layout->addWidget(content, 1);
}

void FirehosePanel::refresh() {
  // EOP: No-op
}
