#pragma once

// NGP-owned widgets. Kept out of selfdrive/ui/qt/widgets/controls.h so that file
// stays identical to upstream and UI rebases only touch NGP files.

#include <algorithm>
#include <cstdlib>
#include <string>

#include <QDoubleSpinBox>
#include <QSpinBox>

#include "selfdrive/ui/qt/widgets/controls.h"

class ParamSpinBoxControl : public AbstractControl {
  Q_OBJECT
public:
  ParamSpinBoxControl(const QString &param, const QString &title, const QString &desc, const QString &icon,
                      int min, int max, int step, const QString &suffix = "", const QString &placeholder = "",
                      int default_value = 0) : AbstractControl(title, desc, icon) {
    key = param.toStdString();
    spin = new QSpinBox(this);
    spin->setRange(min, max);
    spin->setSingleStep(step);
    spin->setSuffix(suffix);
    spin->setSpecialValueText(placeholder);
    spin->setAlignment(Qt::AlignRight);
    spin->setStyleSheet(R"(
      QSpinBox {
        background-color: #393939;
        border-radius: 10px;
        padding: 5px 15px;
        font-size: 32px;
        color: #E4E4E4;
      }
    )");
    hlayout->addWidget(spin);

    std::string param_val = params.get(key);
    int value = param_val.empty() ? default_value : atoi(param_val.c_str());
    spin->setValue(std::clamp(value, min, max));

    QObject::connect(spin, QOverload<int>::of(&QSpinBox::valueChanged), [=](int v) {
      params.put(key, std::to_string(v));
    });
  }

  void refresh() {
    int value = atoi(params.get(key).c_str());
    spin->setValue(value);
  }

  void showEvent(QShowEvent *event) override {
    refresh();
  }

private:
  std::string key;
  Params params;
  QSpinBox *spin;
};

class ParamDoubleSpinBoxControl : public AbstractControl {
  Q_OBJECT
public:
  ParamDoubleSpinBoxControl(const QString &param, const QString &title, const QString &desc, const QString &icon,
                            double min, double max, double step, const QString &suffix = "", const QString &placeholder = "",
                            double default_value = 0.0) : AbstractControl(title, desc, icon) {
    key = param.toStdString();
    spin = new QDoubleSpinBox(this);
    spin->setRange(min, max);
    spin->setSingleStep(step);
    spin->setSuffix(suffix);
    spin->setDecimals(1);
    spin->setSpecialValueText(placeholder);
    spin->setAlignment(Qt::AlignRight);
    spin->setStyleSheet(R"(
      QDoubleSpinBox {
        background-color: #393939;
        border-radius: 10px;
        padding: 5px 15px;
        font-size: 32px;
        color: #E4E4E4;
      }
    )");
    hlayout->addWidget(spin);

    std::string param_val = params.get(key);
    double value = param_val.empty() ? default_value : atof(param_val.c_str());
    spin->setValue(std::clamp(value, min, max));

    QObject::connect(spin, QOverload<double>::of(&QDoubleSpinBox::valueChanged), [=](double v) {
      params.put(key, QString::number(v, 'f', 1).toStdString());
    });
  }

  void refresh() {
    double value = atof(params.get(key).c_str());
    spin->setValue(value);
  }

  void showEvent(QShowEvent *event) override {
    refresh();
  }

private:
  std::string key;
  Params params;
  QDoubleSpinBox *spin;
};
