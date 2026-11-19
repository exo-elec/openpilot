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

#include "selfdrive/ui/qt/widgets/scrollview.h"

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
  QFrame *container = new QFrame(this);
  container->setStyleSheet(R"(
    QFrame { background-color: #1B1B1B; }
    #confirm_btn { background-color: #465BEA; }
    #confirm_btn:pressed { background-color: #3049F4; }
  )");
  QVBoxLayout *main_layout = new QVBoxLayout(container);
  main_layout->setContentsMargins(55, 50, 55, 50);

  QHBoxLayout *header = new QHBoxLayout;
  QLabel *title = new QLabel(tr("Offline Map Areas"), this);
  title->setStyleSheet("font-size: 70px; font-weight: 500;");
  header->addWidget(title, 1, Qt::AlignLeft);
  QLabel *count = new QLabel(this);
  count->setStyleSheet("font-size: 50px; color: #AAAAAA;");
  header->addWidget(count, 0, Qt::AlignRight | Qt::AlignVCenter);
  main_layout->addLayout(header);
  main_layout->addSpacing(25);

  QWidget *list = new QWidget(this);
  QVBoxLayout *list_layout = new QVBoxLayout(list);
  list_layout->setSpacing(20);
  list->setStyleSheet(R"(
    QPushButton {
      height: 135;
      padding: 0px 50px;
      text-align: left;
      font-size: 55px;
      font-weight: 300;
      border-radius: 10px;
      background-color: #4F4F4F;
    }
    QPushButton:checked { background-color: #465BEA; }
    QLabel { font-size: 50px; font-weight: 500; color: #AAAAAA; padding: 30px 0px 10px 10px; }
  )");

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
  main_layout->addSpacing(35);

  QHBoxLayout *buttons = new QHBoxLayout;
  buttons->setSpacing(50);
  QPushButton *cancel_btn = new QPushButton(tr("Cancel"));
  QPushButton *confirm_btn = new QPushButton(tr("Save"));
  confirm_btn->setObjectName("confirm_btn");
  QObject::connect(cancel_btn, &QPushButton::clicked, this, &MapdAreasDialog::reject);
  QObject::connect(confirm_btn, &QPushButton::clicked, this, &MapdAreasDialog::accept);
  buttons->addWidget(cancel_btn);
  buttons->addWidget(confirm_btn);
  main_layout->addLayout(buttons);

  QVBoxLayout *outer = new QVBoxLayout(this);
  outer->setContentsMargins(50, 50, 50, 50);
  outer->addWidget(container);
}

QStringList MapdAreasDialog::getSelection(const QList<MapdArea> &menu, const QStringList &current, QWidget *parent) {
  MapdAreasDialog d(menu, current, parent);
  return d.exec() ? d.selection : current;
}
