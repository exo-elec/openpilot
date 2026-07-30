#include "selfdrive/ui/qt/widgets/overlay_camera.h"

#include <algorithm>
#include <cmath>

#include "common/swaglog.h"

OverlayCameraWidget::OverlayCameraWidget(const std::string &server_name,
                                         VisionStreamType type,
                                         QWidget *parent)
    : QWidget(parent), server_name_(server_name), stream_type_(type) {
  setAttribute(Qt::WA_OpaquePaintEvent, false);
  setAttribute(Qt::WA_TransparentForMouseEvents, true);
}

OverlayCameraWidget::~OverlayCameraWidget() {
  stop();
}

void OverlayCameraWidget::start() {
  if (running_.exchange(true)) return;
  exit_flag_ = false;
  vipc_thread_ = std::thread(&OverlayCameraWidget::vipcThread, this);
}

void OverlayCameraWidget::stop() {
  if (!running_.exchange(false)) return;
  exit_flag_ = true;
  if (vipc_thread_.joinable()) {
    vipc_thread_.join();
  }
}

void OverlayCameraWidget::setCornerRadius(int radius) {
  corner_radius_ = radius;
}

void OverlayCameraWidget::setBorderColor(const QColor &color) {
  border_color_ = color;
}

void OverlayCameraWidget::setBorderWidth(int width) {
  border_width_ = width;
}

void OverlayCameraWidget::vipcThread() {
  VisionIpcClient client(server_name_, stream_type_, false);
  while (!exit_flag_) {
    if (!client.connected) {
      if (client.connect(false)) {
        qDebug("OverlayCameraWidget: connected to %s stream %d",
               server_name_.c_str(), static_cast<int>(stream_type_));
      }
    }
    if (client.connected) {
      VisionBuf *buf = client.recv(nullptr, 50);
      if (buf) {
        updateFrame(buf);
      }
    } else {
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
  }
}

void OverlayCameraWidget::updateFrame(VisionBuf *buf) {
  if (!buf || !buf->addr || buf->width <= 0 || buf->height <= 0) return;

  QImage img(static_cast<int>(buf->width), static_cast<int>(buf->height), QImage::Format_RGB888);

  if (buf->y != nullptr && buf->uv != nullptr) {
    // NV12 → RGB
    const uint8_t *y_plane = buf->y;
    const uint8_t *uv_plane = buf->uv;
    int w = static_cast<int>(buf->width);
    int h = static_cast<int>(buf->height);
    int stride = static_cast<int>(buf->stride);

    for (int y = 0; y < h; ++y) {
      uint8_t *rgb_row = img.scanLine(y);
      for (int x = 0; x < w; ++x) {
        int Y = y_plane[y * stride + x];
        int uv_x = (x / 2) * 2;
        int uv_y = (y / 2);
        int uv_idx = uv_y * stride + uv_x;
        int U = uv_plane[uv_idx];
        int V = uv_plane[uv_idx + 1];

        int R = static_cast<int>(Y + 1.402f * (V - 128));
        int G = static_cast<int>(Y - 0.344136f * (U - 128) - 0.714136f * (V - 128));
        int B = static_cast<int>(Y + 1.772f * (U - 128));

        rgb_row[x * 3 + 0] = static_cast<uint8_t>(std::clamp(R, 0, 255));
        rgb_row[x * 3 + 1] = static_cast<uint8_t>(std::clamp(G, 0, 255));
        rgb_row[x * 3 + 2] = static_cast<uint8_t>(std::clamp(B, 0, 255));
      }
    }
  } else {
    // BGR → RGB
    const uint8_t *bgr = static_cast<const uint8_t *>(buf->addr);
    for (int y = 0; y < static_cast<int>(buf->height); ++y) {
      const uint8_t *bgr_row = bgr + y * static_cast<int>(buf->stride);
      uint8_t *rgb_row = img.scanLine(y);
      for (int x = 0; x < static_cast<int>(buf->width); ++x) {
        rgb_row[x * 3 + 0] = bgr_row[x * 3 + 2];  // R
        rgb_row[x * 3 + 1] = bgr_row[x * 3 + 1];  // G
        rgb_row[x * 3 + 2] = bgr_row[x * 3 + 0];  // B
      }
    }
  }

  std::unique_lock<std::mutex> lock(frame_lock_);
  frame_image_ = img.copy();
  frame_ready_ = true;
  QMetaObject::invokeMethod(this, "update", Qt::QueuedConnection);
}

void OverlayCameraWidget::paintEvent(QPaintEvent *event) {
  (void)event;
  QPainter p(this);
  p.setRenderHint(QPainter::Antialiasing);
  p.setRenderHint(QPainter::SmoothPixmapTransform);

  QRect r = rect();
  QPainterPath path;
  path.addRoundedRect(r, corner_radius_, corner_radius_);
  p.setClipPath(path);

  std::unique_lock<std::mutex> lock(frame_lock_);
  if (frame_ready_) {
    QImage scaled = frame_image_.scaled(r.size(), Qt::KeepAspectRatioByExpanding, Qt::SmoothTransformation);
    QRect target_rect(
      r.center().x() - scaled.width() / 2,
      r.center().y() - scaled.height() / 2,
      scaled.width(), scaled.height()
    );
    p.drawImage(target_rect, scaled);
  } else {
    p.fillRect(r, QColor(20, 20, 20, 200));
  }
  lock.unlock();

  if (border_width_ > 0) {
    QPen pen(border_color_);
    pen.setWidth(border_width_);
    p.setPen(pen);
    p.setBrush(Qt::NoBrush);
    p.drawRoundedRect(r.adjusted(border_width_ / 2, border_width_ / 2,
                                 -border_width_ / 2, -border_width_ / 2),
                      corner_radius_, corner_radius_);
  }
}
