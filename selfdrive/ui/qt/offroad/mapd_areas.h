#pragma once

#include <memory>

#include <QList>
#include <QString>
#include <QStringList>
#include <QTimer>

#include "cereal/messaging/messaging.h"
#include "common/params.h"
#include "selfdrive/ui/qt/widgets/controls.h"
#include "selfdrive/ui/qt/widgets/input.h"

// Offline map areas for mapd (third_party/mapd): which countries / US states to keep, downloading them, and refreshing
// them on a schedule. mapd does the download; this only sends mapdIn and reads mapdExtendedOut.

struct MapdArea {
  QString path;   // mapd download menu path: "nation.KR", "us_state.OH"
  QString name;   // "South Korea"
  bool us_state;
};

// mapd's download menu as mapd itself reads it: /data/openpilot/mapd_download_menu.json when present, else the menu
// built into mapd (vendored unchanged as third_party/mapd/download_menu.json). Entries with a submenu (the United
// States) are listed by their submenu instead. Sorted by name, countries first.
QList<MapdArea> mapdMenu(const QString &builtin_path, const QString &override_path = "/data/openpilot/mapd_download_menu.json");

enum MapdAutoUpdate { MAPD_UPDATE_OFF = 0, MAPD_UPDATE_WEEKLY = 1, MAPD_UPDATE_MONTHLY = 2 };

// A scheduled refresh is due: never downloaded, or older than the period. last_download and now are unix seconds.
bool mapdRefreshDue(int mode, qint64 last_download, qint64 now);

// "South Korea", "South Korea, Japan", "4 areas"; "" for none.
QString mapdAreasSummary(const QStringList &paths, const QList<MapdArea> &menu);

class MapdDownloader : public QObject {
  Q_OBJECT

public:
  explicit MapdDownloader(QObject *parent = nullptr);
  void download(const QString &areas);  // comma-separated menu paths, as MapdAreas
  void cancel();
  void setWatching(bool watching);      // read progress while the settings page shows it
  void checkSchedule(bool network_ok);  // offroad: start a refresh of MapdAreas when MapdAutoUpdate says it is due

  bool active() const { return active_; }
  bool waiting() const { return requested_ && !active_; }  // sent, mapd has not started it yet
  bool responding() const { return seen_; }  // mapd has published since we started listening
  int downloadedFiles() const { return downloaded_; }
  int totalFiles() const { return total_; }

signals:
  void changed();

private:
  void poll();
  void updateTimer();

  Params params;
  std::unique_ptr<PubMaster> pm;
  std::unique_ptr<SubMaster> sm;
  QTimer *timer;
  bool watching_ = false, requested_ = false, active_ = false, seen_ = false;
  int downloaded_ = 0, total_ = 0;
  qint64 requested_at_ = 0;  // ms; a request mapd never picks up (not running yet) expires so it can be sent again
};

// Checklist of mapd's areas. Returns the chosen paths, or the current ones unchanged when cancelled.
class MapdAreasDialog : public DialogBase {
  Q_OBJECT

public:
  MapdAreasDialog(const QList<MapdArea> &menu, const QStringList &current, QWidget *parent);
  static QStringList getSelection(const QList<MapdArea> &menu, const QStringList &current, QWidget *parent);
  QStringList selection;
};

// The map-area rows for a settings list: Map Areas (picker), Download Maps (progress / cancel) and Keep Maps
// Updated (MapdAutoUpdate), plus the scheduled refresh when parked. One implementation for every panel that shows
// map settings: construct it where the rows belong in the list. It reads mapd only while the list is on screen or
// a download runs.
class MapdAreaRows : public QObject {
  Q_OBJECT

public:
  // title_prefix goes before each row title, to match the panel's naming (e.g. "OSM · ").
  MapdAreaRows(ListWidget *list, const QString &title_prefix = "");

protected:
  bool eventFilter(QObject *obj, QEvent *event) override;

private:
  void refresh();
  void checkSchedule();

  Params params;
  ListWidget *list;
  QList<MapdArea> menu;
  MapdDownloader *mapd;
  ButtonControl *areas_btn;
  ButtonControl *download_btn;
};
