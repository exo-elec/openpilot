#pragma once

// OverlayCameraWidget — Picture-in-Picture camera overlay for Qt onroad UI.
// Supports both NV12 (rear camera) and BGR (side cameras) VisionIPC streams.
// Renders via QPainter (NOT OpenGL) so it can be composited on top of
// AnnotatedCameraWidget without GL context conflicts.

#include <atomic>
#include <chrono>
#include <mutex>
#include <thread>

#include <QImage>
#include <QPainter>
#include <QPainterPath>
#include <QWidget>

#include "msgq/visionipc/visionipc_client.h"
#include "msgq/visionipc/visionbuf.h"

class OverlayCameraWidget : public QWidget {
  Q_OBJECT

public:
  explicit OverlayCameraWidget(const std::string &server_name,
                               VisionStreamType type,
                               QWidget *parent = nullptr);
  ~OverlayCameraWidget();

  void start();
  void stop();

  void setCornerRadius(int radius);
  void setBorderColor(const QColor &color);
  void setBorderWidth(int width);

protected:
  void paintEvent(QPaintEvent *event) override;

private:
  void vipcThread();
  void updateFrame(VisionBuf *buf);

  std::string server_name_;
  VisionStreamType stream_type_;

  std::thread vipc_thread_;
  std::atomic<bool> exit_flag_{false};
  std::atomic<bool> running_{false};

  std::mutex frame_lock_;
  QImage frame_image_;
  bool frame_ready_ = false;

  int corner_radius_ = 8;
  QColor border_color_ = QColor(255, 255, 255, 200);
  int border_width_ = 2;
};
