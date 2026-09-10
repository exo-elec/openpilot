#pragma once

// OverlayCameraWidget — camera overlay for the Qt onroad UI. Shown full
// screen: side cameras on a single blinker, rear in reverse.
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

  // Which screen edge this camera looks out of. A bar blinks on that edge
  // while the overlay is up, so a full-screen image is never ambiguous
  // about which camera the driver is looking through.
  enum class SourceEdge { None, Left, Right, Bottom };

  void setCornerRadius(int radius);
  void setBorderColor(const QColor &color);
  void setBorderWidth(int width);
  void setSourceEdge(SourceEdge edge);

protected:
  void paintEvent(QPaintEvent *event) override;

private:
  void vipcThread();
  void updateFrame(VisionBuf *buf);
  void drawSourceEdge(QPainter &p);

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
  SourceEdge source_edge_ = SourceEdge::None;
};
