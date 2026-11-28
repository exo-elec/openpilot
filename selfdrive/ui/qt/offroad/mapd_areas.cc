#include "selfdrive/ui/qt/offroad/mapd_areas.h"

#include <algorithm>

#include <QDateTime>
#include <QFile>
#include <QHBoxLayout>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QPushButton>
#include <QSet>
#include <QVBoxLayout>

#include "selfdrive/ui/qt/util.h"
#include "selfdrive/ui/qt/widgets/scrollview.h"
#include "selfdrive/ui/ui.h"

// ********** menu and schedule **********

QList<MapdArea> mapdMenu(const QString &builtin_path, const QString &override_path) {
  // mapd reads the override when it exists and falls back to its built-in menu only when the file is absent.
  QFile file(QFile::exists(override_path) ? override_path : builtin_path);
  QList<MapdArea> areas;
  if (!file.open(QIODevice::ReadOnly)) return areas;
  const QJsonObject menu = QJsonDocument::fromJson(file.readAll()).object();

  for (const QString group : {"nation", "us_state"}) {
    QList<MapdArea> entries;
    const QJsonObject locations = menu.value(group).toObject();
    for (auto it = locations.begin(); it != locations.end(); ++it) {
      const QJsonObject location = it.value().toObject();
      if (!location.value("submenu").toString().isEmpty()) continue;  // the United States: pick its states
      const QString name = location.value("full_name").toString(it.key());
      entries.push_back({group + "." + it.key(), name, group == "us_state"});
    }
    std::sort(entries.begin(), entries.end(), [](const MapdArea &a, const MapdArea &b) {
      return QString::localeAwareCompare(a.name, b.name) < 0;
    });
    areas += entries;
  }
  return areas;
}

bool mapdRefreshDue(int mode, qint64 last_download, qint64 now) {
  qint64 period = 0;
  if (mode == MAPD_UPDATE_WEEKLY) period = 7 * 24 * 3600;
  else if (mode == MAPD_UPDATE_MONTHLY) period = 30 * 24 * 3600;
  else return false;
  return last_download <= 0 || now - last_download >= period;
}

QString mapdAreasSummary(const QStringList &paths, const QList<MapdArea> &menu) {
  if (paths.isEmpty()) return "";
  if (paths.size() > 2) return QObject::tr("%n areas", "", paths.size());
  QStringList names;
  for (const QString &path : paths) {
    auto it = std::find_if(menu.begin(), menu.end(), [&](const MapdArea &a) { return a.path == path; });
    names << (it != menu.end() ? it->name : path);
  }
  return names.join(", ");
}

// ********** downloader **********

MapdDownloader::MapdDownloader(QObject *parent) : QObject(parent) {
  timer = new QTimer(this);
  timer->setInterval(500);
  QObject::connect(timer, &QTimer::timeout, this, &MapdDownloader::poll);
}

void MapdDownloader::download(const QString &areas) {
  if (areas.isEmpty()) return;
  if (!pm) pm = std::make_unique<PubMaster>(std::vector<const char *>{"mapdIn"});
  MessageBuilder msg;
  auto in = msg.initEvent().initMapdIn();
  in.setType(cereal::MapdInputType::DOWNLOAD);
  in.setStr(areas.toStdString());
  pm->send("mapdIn", msg);
  requested_ = true;
  requested_at_ = QDateTime::currentMSecsSinceEpoch();
  updateTimer();
  emit changed();
}

void MapdDownloader::cancel() {
  if (!pm) pm = std::make_unique<PubMaster>(std::vector<const char *>{"mapdIn"});
  MessageBuilder msg;
  msg.initEvent().initMapdIn().setType(cereal::MapdInputType::CANCEL_DOWNLOAD);
  pm->send("mapdIn", msg);
}

void MapdDownloader::setWatching(bool watching) {
  watching_ = watching;
  updateTimer();
}

void MapdDownloader::checkSchedule(bool network_ok) {
  if (active_ || requested_ || !network_ok || !params.getBool("MapdEnabled")) return;
  const QString areas = QString::fromStdString(params.get("MapdAreas"));
  const int mode = std::atoi(params.get("MapdAutoUpdate").c_str());
  const qint64 last = std::atoll(params.get("MapdLastDownload").c_str());
  if (!areas.isEmpty() && mapdRefreshDue(mode, last, QDateTime::currentSecsSinceEpoch())) {
    download(areas);
  }
}

void MapdDownloader::updateTimer() {
  // Listen only while someone looks or a download runs: nothing is read while driving with the page closed.
  const bool need = watching_ || requested_ || active_;
  if (need && !sm) {
    sm = std::make_unique<SubMaster>(std::vector<const char *>{"mapdExtendedOut"});
    seen_ = false;
  }
  if (need && !timer->isActive()) timer->start();
  if (!need) {
    timer->stop();
    sm.reset();
  }
}

void MapdDownloader::poll() {
  sm->update(0);
  if (requested_ && !active_ && QDateTime::currentMSecsSinceEpoch() - requested_at_ > 30000) {
    requested_ = false;
    updateTimer();
    emit changed();
    if (!sm) return;
  }
  if (!sm->updated("mapdExtendedOut")) return;
  seen_ = true;
  const auto p = (*sm)["mapdExtendedOut"].getMapdExtendedOut().getDownloadProgress();
  const bool was_active = active_;
  active_ = p.getActive();
  downloaded_ = p.getDownloadedFiles();
  total_ = p.getTotalFiles();
  if (active_) requested_ = false;
  if (was_active && !active_ && !p.getCancelled()) {
    params.put("MapdLastDownload", std::to_string(QDateTime::currentSecsSinceEpoch()));
  }
  updateTimer();
  emit changed();
}

// ********** area picker **********

MapdAreasDialog::MapdAreasDialog(const QList<MapdArea> &menu, const QStringList &current, QWidget *parent)
    : DialogBase(parent), selection(current) {
  // Sizes are comma's, for a 1080 px tall screen; shorter screens (the EOP boards' 600 px) get the same layout,
  // smaller, so the list keeps room for its rows.
  const double k = std::clamp(parent->window()->height() / 1080.0, 0.5, 1.0);
  auto px = [k](int n) { return int(n * k); };

  QFrame *container = new QFrame(this);
  container->setStyleSheet(QString(R"(
    QFrame { background-color: #1B1B1B; }
    QPushButton { height: %1; font-size: %2px; }
    #confirm_btn { background-color: #465BEA; }
    #confirm_btn:pressed { background-color: #3049F4; }
  )").arg(px(160)).arg(px(55)));
  QVBoxLayout *main_layout = new QVBoxLayout(container);
  main_layout->setContentsMargins(px(55), px(50), px(55), px(50));

  QHBoxLayout *header = new QHBoxLayout;
  QLabel *title = new QLabel(tr("Offline Map Areas"), this);
  title->setStyleSheet(QString("font-size: %1px; font-weight: 500;").arg(px(70)));
  header->addWidget(title, 1, Qt::AlignLeft);
  QLabel *count = new QLabel(this);
  count->setStyleSheet(QString("font-size: %1px; color: #AAAAAA;").arg(px(50)));
  header->addWidget(count, 0, Qt::AlignRight | Qt::AlignVCenter);
  main_layout->addLayout(header);
  main_layout->addSpacing(px(25));

  QWidget *list = new QWidget(this);
  QVBoxLayout *list_layout = new QVBoxLayout(list);
  list_layout->setSpacing(px(20));
  list->setStyleSheet(QString(R"(
    QPushButton {
      height: %1;
      padding: 0px %2px;
      text-align: left;
      font-size: %3px;
      font-weight: 300;
      border-radius: 10px;
      background-color: #4F4F4F;
    }
    QPushButton:checked { background-color: #465BEA; }
    QLabel { font-size: %4px; font-weight: 500; color: #AAAAAA; padding: %5px 0px %6px %6px; }
  )").arg(px(135)).arg(px(50)).arg(px(55)).arg(px(50)).arg(px(30)).arg(px(10)));

  auto update_count = [=]() {
    count->setText(selection.isEmpty() ? tr("None selected") : tr("%n selected", "", selection.size()));
  };
  // what is already chosen comes first, so the list opens on it; then every country, then the U.S. states
  auto add_area = [=](const MapdArea &area) {
    QPushButton *btn = new QPushButton(area.name, list);
    btn->setCheckable(true);
    btn->setChecked(current.contains(area.path));
    QObject::connect(btn, &QPushButton::toggled, [=](bool checked) {
      selection.removeAll(area.path);
      if (checked) selection << area.path;
      update_count();
    });
    list_layout->addWidget(btn);
  };
  if (!current.isEmpty()) {
    list_layout->addWidget(new QLabel(tr("Selected"), list));
    for (const MapdArea &area : menu) {
      if (current.contains(area.path)) add_area(area);
    }
  }
  bool us_header = false;
  list_layout->addWidget(new QLabel(tr("Countries"), list));
  for (const MapdArea &area : menu) {
    if (current.contains(area.path)) continue;
    if (area.us_state && !us_header) {
      list_layout->addWidget(new QLabel(tr("U.S. States"), list));
      us_header = true;
    }
    add_area(area);
  }
  list_layout->addStretch(1);
  update_count();

  ScrollView *scroll = new ScrollView(list, this);
  scroll->setVerticalScrollBarPolicy(Qt::ScrollBarAsNeeded);
  main_layout->addWidget(scroll);
  main_layout->addSpacing(px(35));

  QHBoxLayout *buttons = new QHBoxLayout;
  buttons->setSpacing(px(50));
  QPushButton *cancel_btn = new QPushButton(tr("Cancel"));
  QPushButton *confirm_btn = new QPushButton(tr("Save"));
  confirm_btn->setObjectName("confirm_btn");
  QObject::connect(cancel_btn, &QPushButton::clicked, this, &MapdAreasDialog::reject);
  QObject::connect(confirm_btn, &QPushButton::clicked, this, &MapdAreasDialog::accept);
  buttons->addWidget(cancel_btn);
  buttons->addWidget(confirm_btn);
  main_layout->addLayout(buttons);

  QVBoxLayout *outer = new QVBoxLayout(this);
  outer->setContentsMargins(px(50), px(50), px(50), px(50));
  outer->addWidget(container);
}

QStringList MapdAreasDialog::getSelection(const QList<MapdArea> &menu, const QStringList &current, QWidget *parent) {
  MapdAreasDialog d(menu, current, parent);
  return d.exec() ? d.selection : current;
}

// ********** settings rows **********

MapdAreaRows::MapdAreaRows(ListWidget *list_, const QString &title_prefix) : QObject(list_), list(list_) {
  menu = mapdMenu("../../third_party/mapd/download_menu.json");  // the UI runs in selfdrive/ui
  mapd = new MapdDownloader(this);

  areas_btn = new ButtonControl(title_prefix + tr("Map Areas"), tr("SELECT"),
      tr("Countries and U.S. states to keep on the device. Map data works offline once downloaded."));
  connect(areas_btn, &ButtonControl::clicked, [=]() {
    const QStringList current = QString::fromStdString(params.get("MapdAreas")).split(",", QString::SkipEmptyParts);
    const QStringList selected = MapdAreasDialog::getSelection(menu, current, list);
    if (selected != current) {
      params.put("MapdAreas", selected.join(",").toStdString());
      params.remove("MapdLastDownload");  // the new set has not been downloaded yet
      refresh();
    }
  });
  list->addItem(areas_btn);

  download_btn = new ButtonControl(title_prefix + tr("Download Maps"), tr("DOWNLOAD"),
      tr("Download the selected areas now. Large download; Wi-Fi recommended."));
  connect(download_btn, &ButtonControl::clicked, [=]() {
    if (mapd->active()) mapd->cancel();
    else mapd->download(QString::fromStdString(params.get("MapdAreas")));
  });
  list->addItem(download_btn);

  list->addItem(new ButtonParamControl("MapdAutoUpdate", title_prefix + tr("Keep Maps Updated"),
      tr("Download the selected areas again on this schedule, while parked on Wi-Fi or Ethernet."),
      "", {tr("Off"), tr("Weekly"), tr("Monthly")}));

  connect(mapd, &MapdDownloader::changed, this, &MapdAreaRows::refresh);
  ParamWatcher *watch = new ParamWatcher(this);
  watch->addParam("MapdEnabled");
  connect(watch, &ParamWatcher::paramChanged, this, &MapdAreaRows::refresh);

  // Scheduled refresh: when the car is parked, and hourly while it stays parked.
  connect(uiState(), &UIState::offroadTransition, [=](bool offroad) { if (offroad) checkSchedule(); });
  QTimer *schedule = new QTimer(this);
  connect(schedule, &QTimer::timeout, this, &MapdAreaRows::checkSchedule);
  schedule->start(3600 * 1000);

  list->installEventFilter(this);  // read mapd's progress only while the list is on screen
  refresh();
}

bool MapdAreaRows::eventFilter(QObject *obj, QEvent *event) {
  if (obj == list && (event->type() == QEvent::Show || event->type() == QEvent::Hide)) {
    mapd->setWatching(event->type() == QEvent::Show);
    refresh();
  }
  return QObject::eventFilter(obj, event);
}

void MapdAreaRows::checkSchedule() {
  if (uiState()->scene.started) return;
  const auto net = (*uiState()->sm)["deviceState"].getDeviceState().getNetworkType();
  mapd->checkSchedule(net == cereal::DeviceState::NetworkType::WIFI || net == cereal::DeviceState::NetworkType::ETHERNET);
}

void MapdAreaRows::refresh() {
  const QStringList areas = QString::fromStdString(params.get("MapdAreas")).split(",", QString::SkipEmptyParts);
  const QString summary = mapdAreasSummary(areas, menu);
  areas_btn->setValue(summary.isEmpty() ? tr("None") : summary);

  const bool enabled = params.getBool("MapdEnabled");
  QString status;
  if (mapd->waiting()) {
    status = tr("Waiting for mapd");
  } else if (mapd->active()) {
    status = tr("%1 / %2 files").arg(mapd->downloadedFiles()).arg(mapd->totalFiles());
  } else if (!enabled) {
    status = tr("Map Data is off");
  } else if (areas.isEmpty()) {
    status = tr("No areas selected");
  } else if (const qint64 last = std::atoll(params.get("MapdLastDownload").c_str()); last > 0) {
    status = tr("Updated %1").arg(QDateTime::fromSecsSinceEpoch(last).date().toString(Qt::ISODate));
  } else {
    status = tr("Not downloaded");
  }
  download_btn->setValue(status);
  download_btn->setText(mapd->active() ? tr("CANCEL") : tr("DOWNLOAD"));
  download_btn->setEnabled(mapd->active() || (enabled && !areas.isEmpty()));
}
