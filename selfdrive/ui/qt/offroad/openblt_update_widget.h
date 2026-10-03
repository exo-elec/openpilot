#pragma once

#include <QWidget>
#include <QTimer>
#include <memory>
#include <string>
#include <vector>

QT_BEGIN_NAMESPACE
class QComboBox;
class QLabel;
class QProgressBar;
class QTextEdit;
class QPushButton;
class QGroupBox;
QT_END_NAMESPACE

namespace openblt {
class OpenBLTService;
struct UpdateInfo;
}

class OpenBLTUpdateWidget : public QWidget {
    Q_OBJECT

public:
    explicit OpenBLTUpdateWidget(QWidget* parent = nullptr);
    ~OpenBLTUpdateWidget();

    // UI event handlers (called from Qt UI)
public slots:
    void onCheckUpdatesClicked();
    void onStartUpdateClicked();
    void onCancelUpdateClicked();
    void onRollbackClicked();
    void onDeviceSelectionChanged();

    // UI update methods (called to update UI)
    void updateProgress(int percentage, const QString& status, const QString& details);
    void updateDeviceList(const std::vector<std::string>& devices);
    void updateUpdateInfo(const openblt::UpdateInfo& info);
    void showError(const QString& error);
    void showSuccess(const QString& message);

    // UI state queries
    bool isUpdateAvailable() const;
    bool isUpdateInProgress() const;
    bool canStartUpdate() const;
    bool canRollback() const;

signals:
    void updateProgressSignal(int percentage, const QString& status, const QString& details);
    void deviceListUpdated(const std::vector<std::string>& devices);
    void updateInfoAvailable(const openblt::UpdateInfo& info);
    void errorOccurred(const QString& error);
    void successOccurred(const QString& message);

private:
    void setupUI();
    void setupService();
    void startStatusTimer();
    void updateUIStatus();
    QString formatUpdateSize(size_t bytes);
    QString formatDeviceStatus(const std::string& device_type);
    QString getUpdateButtonText();

    // Core components
    std::unique_ptr<openblt::OpenBLTService> service;
    QTimer* status_timer;

    // UI elements
    QComboBox* device_combo;
    QLabel* device_status_label;
    QLabel* current_version_label;
    QLabel* update_version_label;
    QLabel* file_size_label;
    QTextEdit* release_notes_text;
    QProgressBar* progress_bar;
    QLabel* progress_label;
    QPushButton* check_updates_button;
    QPushButton* start_update_button;
    QPushButton* cancel_update_button;
    QPushButton* rollback_button;

    // State
    std::string selected_device;
    bool update_in_progress;
};

// Factory function to create the widget
QWidget* createOpenBLTUpdateWidget(QWidget* parent = nullptr);