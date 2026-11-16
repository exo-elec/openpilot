#include "selfdrive/ui/qt/onroad/navigation.h"

#include <algorithm>
#include <cmath>

#include <QPainter>
#include <QPainterPath>

#include "common/params.h"
#include "common/timing.h"
#include "selfdrive/ui/qt/util.h"

// Values from upstream openpilot (selfdrive/navigation/renderer.py and selfdrive/ui/mici/onroad/navigation.py).
// Lengths are mici's times SCALE: comma 3's onroad view is about 2.2 times the comma four's.
namespace {
constexpr int SIZE = 256;                  // renderer.SIZE
constexpr double EGO_Y = SIZE / 2 + 64.0;  // renderer.EGO: the ego three quarters down the frame
constexpr int ROUTE_INTENSITY = 128;       // renderer.ROUTE_INTENSITY
constexpr double SCALE = 2.2;
constexpr double STALE_SECONDS = 3;
constexpr double MARKER_MARGIN = 18;       // pixels of the frame kept below the ego
constexpr double CONNECTION_RETRY_INTERVAL = 0.2;
constexpr double GPS_FRESH_SECONDS = 2;    // helpers.latest_gps_source
const QColor BACKGROUND(7, 13, 18), ACCENT(155, 230, 185), MUTED(162, 175, 185);

int s(double v) { return std::lround(v * SCALE); }
double now() { return millis_since_boot() / 1000.0; }

bool fresh_fix(const SubMaster &sm, const char *name) {
  if (!sm.valid(name) || sm.rcv_frame(name) == 0) return false;
  const auto event = sm[name];
  const bool fix = std::string(name) == "gpsLocation" ? event.getGpsLocation().getHasFix() : event.getGpsLocationExternal().getHasFix();
  return fix && (nanos_since_boot() - sm.rcv_time(name)) * 1e-9 < GPS_FRESH_SECONDS;
}
}  // namespace

NavigationPanel::NavigationPanel(QWidget *parent) : QWidget(parent) {
  setFixedWidth(s(261));  // mici PANEL_WIDTH
  // Recolor the model's grayscale frame for people: dim the white roads and make the grey route pop (as mici's MAP_COLORS).
  map = QImage(SIZE, SIZE, QImage::Format_Indexed8);
  QVector<QRgb> colors(256);
  for (int i = 0; i < 256; ++i) {
    const int g = std::lround(i * 0.45);
    colors[i] = (i >= ROUTE_INTENSITY - 1 && i <= ROUTE_INTENSITY + 1) ? qRgb(72, 235, 132) : qRgb(g, g, g);
  }
  map.setColorTable(colors);
}

bool NavigationPanel::wanted() {
  Params params;
  return params.getBool("NavigationEnabled") && !params.get("NavDestination").empty();
}

void NavigationPanel::receiveFrame() {
  if (!client) client = std::make_unique<VisionIpcClient>("mapsd", VISION_STREAM_MAP, true);
  if (!client->is_connected()) {
    if (now() - last_connect < CONNECTION_RETRY_INTERVAL) return;
    last_connect = now();
    if (!client->connect(false) || client->num_buffers == 0) return;
  }
  VisionIpcBufExtra extra = {};
  VisionBuf *buf = client->recv(&extra, 0);
  if (buf == nullptr) return;
  for (int y = 0; y < SIZE; ++y) {
    memcpy(map.scanLine(y), (const uint8_t *)buf->addr + y * buf->stride, SIZE);
  }
  // Without a usable map, mapsd publishes black frames with a zero timestamp.
  frame_time = extra.timestamp_sof ? now() : 0.0;
}

bool NavigationPanel::routing() const { return has_route; }

bool NavigationPanel::mapReady() const { return frame_time > 0 && now() - frame_time < STALE_SECONDS; }

QString NavigationPanel::formatDistance(float meters) const {
  // m / ft under 1000 m / 300 m, km / mi beyond (mici format_distance)
  if (is_metric) {
    return meters < 1000 ? QString("%1 m").arg(std::lround(meters / 10) * 10) : QString("%1 km").arg(meters / 1000, 0, 'f', 1);
  }
  return meters < 300 ? QString("%1 ft").arg(std::lround(meters * 3.28084 / 50) * 50)
                      : QString("%1 mi").arg(meters / 1609.344, 0, 'f', 1);
}

void NavigationPanel::updateState(const UIState &st) {
  const SubMaster &sm = *(st.sm);
  is_metric = st.scene.is_metric;
  gps_fix = fresh_fix(sm, "gpsLocation") || fresh_fix(sm, "gpsLocationExternal");
  has_route = sm.valid("navInstruction") && sm.alive("navInstruction");
  if (has_route) {
    const auto instruction = sm["navInstruction"].getNavInstruction();
    modifier = QString::fromStdString(instruction.getManeuverModifier());
    maneuver_distance = std::max(0.f, instruction.getManeuverDistance());
    const float seconds = std::max(0.f, instruction.getTimeRemaining());
    eta = QString("%1 min / %2").arg(std::max(1L, std::lround(seconds / 60))).arg(formatDistance(std::max(0.f, instruction.getDistanceRemaining())));
  }
  receiveFrame();
  update();
}

void NavigationPanel::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.setRenderHint(QPainter::Antialiasing);
  p.fillRect(rect(), BACKGROUND);

  if (routing()) {
    // The maneuver arrow on a 1.4x grid, centered in the header's icon box (mici's shape and stroke).
    const int flip = modifier.contains("left") ? -1 : 1;
    std::vector<QPointF> shape;
    if (modifier.isEmpty() || modifier == "straight") {
      shape = {{0, 14}, {0, -12}, {-6, -5}, {0, -12}, {6, -5}};
    } else {
      shape = {{-flip * 9., 14}, {-flip * 9., -4}, {-flip * 2., -9}, {flip * 9., -9}, {flip * 3., -15}, {flip * 9., -9}, {flip * 3., -3}};
    }
    QPainterPath path;
    for (size_t i = 0; i < shape.size(); ++i) {
      const QPointF pt((31 + 1.4 * shape[i].x()) * SCALE, (30 + 1.4 * shape[i].y()) * SCALE);
      i == 0 ? path.moveTo(pt) : path.lineTo(pt);
    }
    p.setPen(QPen(ACCENT, 7 * SCALE, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
    p.drawPath(path);
    p.setPen(Qt::white);
    p.setFont(InterFont(s(34), QFont::Bold));
    p.drawText(QRect(s(64), s(11), width() - s(72), s(40)), Qt::AlignLeft | Qt::AlignTop, formatDistance(maneuver_distance));
  } else {
    p.setPen(Qt::white);
    p.setFont(InterFont(s(26), QFont::Bold));
    p.drawText(QRect(s(12), s(18), width() - s(24), s(30)), Qt::AlignLeft | Qt::AlignTop, tr("Waiting for route"));
  }

  const QRect map_rect(s(8), s(61), width() - s(16), height() - s(111));
  p.fillRect(map_rect, Qt::black);
  if (mapReady()) {
    // A crop of the model's frame, centered horizontally with the ego near the bottom, drawn at SCALE.
    const int w = std::min<int>(map_rect.width() / SCALE, SIZE), h = std::min<int>(map_rect.height() / SCALE, SIZE);
    const QRect source((SIZE - w) / 2, std::min<int>(SIZE - h, std::max<int>(0, EGO_Y + MARKER_MARGIN - h)), w, h);
    const QRect target(map_rect.x() + (map_rect.width() - s(w) ) / 2, map_rect.bottom() + 1 - s(h), s(w), s(h));
    p.setRenderHint(QPainter::SmoothPixmapTransform, false);
    p.drawImage(target, map, source);
  } else {
    // What the map is waiting on, in place of the map.
    const std::pair<QString, bool> statuses[] = {{tr("GPS fix"), gps_fix}, {tr("Route"), routing()}, {tr("Map frames"), mapReady()}};
    p.setFont(InterFont(s(22), QFont::Medium));
    for (int i = 0; i < 3; ++i) {
      p.setPen(statuses[i].second ? ACCENT : MUTED);
      p.drawText(QRect(map_rect.x() + s(10), map_rect.y() + s(12 + i * 28), map_rect.width() - s(20), s(26)), Qt::AlignLeft | Qt::AlignTop,
                 QString("%1: %2").arg(statuses[i].first, statuses[i].second ? tr("yes") : tr("no")));
    }
  }

  if (routing()) {
    p.setPen(Qt::white);
    p.setFont(InterFont(s(34), QFont::Bold));
    p.drawText(QRect(s(9), height() - s(45), width() - s(18), s(40)), Qt::AlignLeft | Qt::AlignVCenter, eta);
  }
}
