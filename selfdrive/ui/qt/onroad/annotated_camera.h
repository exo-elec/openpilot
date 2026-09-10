#pragma once

#include <QVBoxLayout>
#include <memory>
#include "selfdrive/ui/qt/onroad/blind_spot_indicator.h"
#include "selfdrive/ui/qt/onroad/hud.h"
#include "selfdrive/ui/qt/onroad/buttons.h"
#include "selfdrive/ui/qt/onroad/model.h"
#include "selfdrive/ui/qt/widgets/cameraview.h"

class AnnotatedCameraWidget : public CameraWidget {
  Q_OBJECT

public:
  explicit AnnotatedCameraWidget(VisionStreamType type, QWidget* parent = 0);
  void updateState(const UIState &s);

private:
  QVBoxLayout *main_layout;
  ExperimentalButton *experimental_btn;
  HudRenderer hud;
  ModelRenderer model;
  // Edge blind-spot bands, drawn over the camera feed. Not in main_layout
  // -- it covers the whole view rather than taking space in it, so it is
  // sized manually on every resize.
  BlindSpotIndicator *blind_spot = nullptr;
  std::unique_ptr<PubMaster> pm;

  int skip_frame_count = 0;
  bool wide_cam_requested = false;

protected:
  void paintGL() override;
  void initializeGL() override;
  void showEvent(QShowEvent *event) override;
  void resizeEvent(QResizeEvent *event) override;
  mat4 calcFrameMatrix() override;

  double prev_draw_t = 0;
  FirstOrderFilter fps_filter;
};
