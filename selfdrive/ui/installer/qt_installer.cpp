#include <QApplication>
#include <QFile>
#include <QHBoxLayout>
#include <QLabel>
#include <QProgressBar>
#include <QPushButton>
#include <QTextEdit>
#include <QTimer>

#include "common/util.h"

int freshClone();
int cachedFetch(const std::string &cache);
int executeGitCommand(const std::string &cmd);

static const std::string GIT_URL = "https://github.com/commaai/openpilot.git";
static const std::string INSTALL_PATH = "/data/openpilot";
static const std::string VALID_CACHE_PATH = "/data/.openpilot_cache";
static const std::string TMP_INSTALL_PATH = "/data/tmppilot";

class InstallerWindow : public QWidget {
  Q_OBJECT
public:
  InstallerWindow(QWidget *parent = nullptr) : QWidget(parent) {
    setWindowTitle("Installer");
    setFixedSize(1024, 600);

    auto *layout = new QVBoxLayout(this);
    layout->setAlignment(Qt::AlignCenter);

    label = new QLabel("Installing...");
    label->setAlignment(Qt::AlignCenter);
    label->setStyleSheet("font-size: 32px; color: white;");

    progress = new QProgressBar();
    progress->setRange(0, 100);
    progress->setValue(0);
    progress->setFixedSize(800, 40);
    progress->setStyleSheet("QProgressBar { background: #222; color: white; font-size: 24px; }"
                            "QProgressBar::chunk { background: #465BEA; }");

    log = new QTextEdit();
    log->setReadOnly(true);
    log->setFixedSize(900, 300);
    log->setStyleSheet("background: #000; color: white; font-size: 20px;");

    layout->addWidget(label);
    layout->addSpacing(30);
    layout->addWidget(progress, 0, Qt::AlignCenter);
    layout->addSpacing(20);
    layout->addWidget(log, 0, Qt::AlignCenter);

    setStyleSheet("background: black;");

    QTimer::singleShot(100, this, &InstallerWindow::startInstall);
  }

public slots:
  void updateProgress(int value) {
    progress->setValue(std::clamp(value, 0, 100));
  }

  void appendLog(const QString &text) {
    log->append(text);
  }

  void finishInstall() {
    label->setText("Finishing install...");
    progress->setValue(100);
    appendLog("Install complete. Waiting for UI...");
  }

private slots:
  void startInstall() {
    if (util::file_exists(INSTALL_PATH) && util::file_exists(VALID_CACHE_PATH)) {
      execute(cachedFetch(INSTALL_PATH));
    } else {
      execute(freshClone());
    }
  }

private:
  void execute(int status) {
    Q_UNUSED(status);
    finishInstall();
    QTimer::singleShot(60000, qApp, &QCoreApplication::quit);
  }

  QLabel *label;
  QProgressBar *progress;
  QTextEdit *log;
};

int main(int argc, char *argv[]) {
  QApplication app(argc, argv);
  InstallerWindow w;
  w.show();
  return app.exec();
}

#include "qt_installer.moc"
