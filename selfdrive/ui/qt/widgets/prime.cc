#include "selfdrive/ui/qt/widgets/prime.h"

#include <QLabel>
#include <QStackedWidget>
#include <QVBoxLayout>

PrimeUserWidget::PrimeUserWidget(QWidget* parent) : QFrame(parent) {
  setObjectName("primeWidget");
  QVBoxLayout *mainLayout = new QVBoxLayout(this);
  mainLayout->setContentsMargins(30, 20, 30, 20);
  mainLayout->setSpacing(20);

  QLabel *subscribed = new QLabel(tr("✓ OFFLINE"));
  subscribed->setStyleSheet("font-size: 41px; font-weight: bold; color: #86FF4E;");
  mainLayout->addWidget(subscribed);

  QLabel *eopLabel = new QLabel(tr("EnhancedOpenPilot"));
  eopLabel->setStyleSheet("font-size: 45px; font-weight: bold;");
  mainLayout->addWidget(eopLabel);

  QLabel *desc = new QLabel(tr("All processing is local. No cloud account required."));
  desc->setStyleSheet("font-size: 45px; font-weight: light; color: #CCCCCC;");
  desc->setWordWrap(true);
  mainLayout->addWidget(desc);

  setStyleSheet(R"(
    PrimeUserWidget {
      border-radius: 10px;
      background-color: #333333;
    }
  )");
}

PrimeAdWidget::PrimeAdWidget(QWidget* parent) : QFrame(parent) {
  QVBoxLayout *main_layout = new QVBoxLayout(this);
  main_layout->setContentsMargins(30, 20, 30, 20);
  main_layout->setSpacing(20);

  QLabel *title = new QLabel(tr("EnhancedOpenPilot"));
  title->setStyleSheet("font-size: 45px; font-weight: bold;");
  main_layout->addWidget(title, 0, Qt::AlignTop);

  QLabel *desc = new QLabel(tr("EOP is ready to use. No cloud account or subscription required."));
  desc->setStyleSheet("font-size: 30px; font-weight: light; color: white;");
  desc->setWordWrap(true);
  main_layout->addWidget(desc, 0, Qt::AlignTop);

  main_layout->addStretch();

  setStyleSheet(R"(
    PrimeAdWidget {
      border-radius: 10px;
      background-color: #333333;
    }
  )");
}

SetupWidget::SetupWidget(QWidget* parent) : QFrame(parent) {
  mainLayout = new QStackedWidget;

  // EOP setup prompt (no pairing)
  QFrame* eopSetup = new QFrame;
  eopSetup->setObjectName("primeWidget");
  QVBoxLayout* setupLayout = new QVBoxLayout(eopSetup);
  setupLayout->setSpacing(38);
  setupLayout->setContentsMargins(30, 20, 30, 20);

  QLabel* title = new QLabel(tr("EOP Setup"));
  title->setStyleSheet("font-size: 45px; font-weight: bold;");
  setupLayout->addWidget(title);

  QLabel* description = new QLabel(tr("EnhancedOpenPilot (EOP) is ready to use. No cloud account required."));
  description->setWordWrap(true);
  description->setStyleSheet("font-size: 30px; font-weight: light;");
  setupLayout->addWidget(description);

  QLabel* desc2 = new QLabel(tr("All AI processing is local via Hailo-8. No data leaves your device."));
  desc2->setWordWrap(true);
  desc2->setStyleSheet("font-size: 40px; font-weight: light; color: #AAAAAA;");
  setupLayout->addWidget(desc2);

  setupLayout->addStretch();

  mainLayout->addWidget(eopSetup);

  QVBoxLayout *outer_layout = new QVBoxLayout(this);
  outer_layout->setContentsMargins(0, 0, 0, 0);
  outer_layout->addWidget(mainLayout);

  setStyleSheet(R"(
    #primeWidget {
      border-radius: 10px;
      background-color: #333333;
    }
  )");

  // Retain size while hidden
  QSizePolicy sp_retain = sizePolicy();
  sp_retain.setRetainSizeWhenHidden(true);
  setSizePolicy(sp_retain);
}
