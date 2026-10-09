#include "selfdrive/ui/qt/offroad/openblt_update_widget.h"

#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QGridLayout>
#include <QLabel>
#include <QComboBox>
#include <QProgressBar>
#include <QTextEdit>
#include <QPushButton>
#include <QGroupBox>
#include <QTimer>
#include <QMessageBox>

#include "common/params.h"
#include "common/util.h"
#include "selfdrive/ui/qt/util.h"
#include "selfdrive/ui/qt/widgets/input.h"

// Implementation of the OpenBLTUpdateWidget class

namespace openblt {

struct UpdateInfo {
    std::string version;
    std::string release_notes;
    size_t file_size;
    std::string device_type;
    bool is_available;
};

class OpenBLTService {
public:
    OpenBLTService() {}
    ~OpenBLTService() {}
    
    bool checkForUpdates() { 
        // Simulate update check
        return true; 
    }
    
    bool startUpdate(const std::string& device_type) {
        // Simulate update start
        return true;
    }
    
    bool cancelUpdate() {
        // Simulate update cancel
        return true;
    }
    
    bool rollback() {
        // Simulate rollback
        return true;
    }
    
    std::vector<std::string> getAvailableDevices() {
        return {"TC275", "STM32", "NXP"};
    }
    
    UpdateInfo getUpdateInfo() {
        UpdateInfo info;
        info.version = "1.2.3";
        info.release_notes = "Bug fixes and improvements";
        info.file_size = 1024 * 1024; // 1MB
        info.device_type = "TC275";
        info.is_available = false;
        return info;
    }
    
    int getProgress() { return 0; }
    std::string getStatus() { return "idle"; }
};

} // namespace openblt

OpenBLTUpdateWidget::OpenBLTUpdateWidget(QWidget* parent)
    : QWidget(parent)
    , service(std::make_unique<openblt::OpenBLTService>())
    , update_in_progress(false) {
    setupUI();
    setupService();
    startStatusTimer();
}

OpenBLTUpdateWidget::~OpenBLTUpdateWidget() = default;

void OpenBLTUpdateWidget::setupUI() {
    QVBoxLayout* main_layout = new QVBoxLayout(this);
    
    // Device selection group
    QGroupBox* device_group = new QGroupBox("Device Selection");
    QHBoxLayout* device_layout = new QHBoxLayout(device_group);
    
    device_combo = new QComboBox();
    device_layout->addWidget(new QLabel("Device:"));
    device_layout->addWidget(device_combo, 1);
    device_status_label = new QLabel("Status: Unknown");
    device_layout->addWidget(device_status_label);
    
    main_layout->addWidget(device_group);
    
    // Update information group
    QGroupBox* info_group = new QGroupBox("Update Information");
    QGridLayout* info_layout = new QGridLayout(info_group);
    
    current_version_label = new QLabel("Current: Unknown");
    update_version_label = new QLabel("Available: None");
    file_size_label = new QLabel("Size: 0 MB");
    
    info_layout->addWidget(current_version_label, 0, 0);
    info_layout->addWidget(update_version_label, 0, 1);
    info_layout->addWidget(file_size_label, 1, 0);
    
    release_notes_text = new QTextEdit();
    release_notes_text->setReadOnly(true);
    release_notes_text->setMaximumHeight(100);
    release_notes_text->setPlaceholderText("Release notes will appear here...");
    info_layout->addWidget(release_notes_text, 2, 0, 1, 2);
    
    main_layout->addWidget(info_group);
    
    // Progress group
    QGroupBox* progress_group = new QGroupBox("Update Progress");
    QVBoxLayout* progress_layout = new QVBoxLayout(progress_group);
    
    progress_bar = new QProgressBar();
    progress_bar->setRange(0, 100);
    progress_layout->addWidget(progress_bar);
    
    progress_label = new QLabel("Ready");
    progress_layout->addWidget(progress_label);
    
    main_layout->addWidget(progress_group);
    
    // Control buttons
    QHBoxLayout* button_layout = new QHBoxLayout();
    
    check_updates_button = new QPushButton("Check for Updates");
    start_update_button = new QPushButton("Start Update");
    cancel_update_button = new QPushButton("Cancel");
    rollback_button = new QPushButton("Rollback");
    
    button_layout->addWidget(check_updates_button);
    button_layout->addWidget(start_update_button);
    button_layout->addWidget(cancel_update_button);
    button_layout->addWidget(rollback_button);
    
    main_layout->addLayout(button_layout);
    
    // Connect signals
    connect(check_updates_button, &QPushButton::clicked, this, &OpenBLTUpdateWidget::onCheckUpdatesClicked);
    connect(start_update_button, &QPushButton::clicked, this, &OpenBLTUpdateWidget::onStartUpdateClicked);
    connect(cancel_update_button, &QPushButton::clicked, this, &OpenBLTUpdateWidget::onCancelUpdateClicked);
    connect(rollback_button, &QPushButton::clicked, this, &OpenBLTUpdateWidget::onRollbackClicked);
    connect(device_combo, QOverload<int>::of(&QComboBox::currentIndexChanged), this, &OpenBLTUpdateWidget::onDeviceSelectionChanged);
    
    // Connect internal signals
    connect(this, &OpenBLTUpdateWidget::updateProgressSignal, this, &OpenBLTUpdateWidget::updateProgress);
    connect(this, &OpenBLTUpdateWidget::deviceListUpdated, this, &OpenBLTUpdateWidget::updateDeviceList);
    connect(this, &OpenBLTUpdateWidget::updateInfoAvailable, this, &OpenBLTUpdateWidget::updateUpdateInfo);
    connect(this, &OpenBLTUpdateWidget::errorOccurred, this, &OpenBLTUpdateWidget::showError);
    connect(this, &OpenBLTUpdateWidget::successOccurred, this, &OpenBLTUpdateWidget::showSuccess);
}

void OpenBLTUpdateWidget::setupService() {
    updateDeviceList(service->getAvailableDevices());
    updateUpdateInfo(service->getUpdateInfo());
}

void OpenBLTUpdateWidget::startStatusTimer() {
    status_timer = new QTimer(this);
    connect(status_timer, &QTimer::timeout, this, &OpenBLTUpdateWidget::updateUIStatus);
    status_timer->start(1000); // Update every second
}

void OpenBLTUpdateWidget::updateUIStatus() {
    // Update current version from params
    Params params;
    std::string current_version = params.get("OpenBLTFirmwareVersion");
    if (!current_version.empty()) {
        current_version_label->setText(QString("Current: %1").arg(QString::fromStdString(current_version)));
    }
    
    // Update button states
    bool update_available = params.getBool("OpenBLTUpdateAvailable");
    bool is_update_in_progress = (params.get("OpenBLTState") != "idle");
    
    check_updates_button->setEnabled(!is_update_in_progress);
    start_update_button->setEnabled(update_available && !is_update_in_progress);
    cancel_update_button->setEnabled(is_update_in_progress);
    rollback_button->setEnabled(!is_update_in_progress && !update_available);
    
    // Update progress
    if (update_in_progress) {
        int progress = service->getProgress();
        progress_bar->setValue(progress);
        progress_label->setText(QString("Updating... %1%").arg(progress));
    } else {
        progress_bar->setValue(0);
        progress_label->setText("Ready");
    }
}

void OpenBLTUpdateWidget::updateProgress(int percentage, const QString& status, const QString& details) {
    progress_bar->setValue(percentage);
    progress_label->setText(status);
    if (!details.isEmpty()) {
        release_notes_text->append(details);
    }
}

void OpenBLTUpdateWidget::updateDeviceList(const std::vector<std::string>& devices) {
    device_combo->clear();
    for (const auto& device : devices) {
        device_combo->addItem(QString::fromStdString(device));
    }
    if (!devices.empty()) {
        selected_device = devices[0];
    }
}

void OpenBLTUpdateWidget::updateUpdateInfo(const openblt::UpdateInfo& info) {
    update_version_label->setText(QString("Available: %1").arg(QString::fromStdString(info.version)));
    file_size_label->setText(QString("Size: %1 MB").arg(info.file_size / (1024.0 * 1024.0), 0, 'f', 1));
    release_notes_text->setPlainText(QString::fromStdString(info.release_notes));
}

void OpenBLTUpdateWidget::showError(const QString& error) {
    QMessageBox::critical(this, "OpenBLT Update Error", error);
}

void OpenBLTUpdateWidget::showSuccess(const QString& message) {
    QMessageBox::information(this, "OpenBLT Update Success", message);
}

void OpenBLTUpdateWidget::onCheckUpdatesClicked() {
    check_updates_button->setEnabled(false);
    if (service->checkForUpdates()) {
        updateUpdateInfo(service->getUpdateInfo());
        showSuccess("Update check completed successfully!");
    } else {
        showError("Failed to check for updates.");
    }
    check_updates_button->setEnabled(true);
}

void OpenBLTUpdateWidget::onStartUpdateClicked() {
    if (!selected_device.empty()) {
        update_in_progress = true;
        start_update_button->setEnabled(false);
        if (service->startUpdate(selected_device)) {
            showSuccess("Update started successfully!");
        } else {
            showError("Failed to start update.");
            update_in_progress = false;
        }
        start_update_button->setEnabled(true);
    }
}

void OpenBLTUpdateWidget::onCancelUpdateClicked() {
    if (service->cancelUpdate()) {
        update_in_progress = false;
        showSuccess("Update cancelled successfully!");
    } else {
        showError("Failed to cancel update.");
    }
}

void OpenBLTUpdateWidget::onRollbackClicked() {
    if (QMessageBox::question(this, "Confirm Rollback", 
                              "Are you sure you want to rollback to the previous firmware version?",
                              QMessageBox::Yes | QMessageBox::No) == QMessageBox::Yes) {
        if (service->rollback()) {
            showSuccess("Rollback completed successfully!");
        } else {
            showError("Failed to rollback firmware.");
        }
    }
}

void OpenBLTUpdateWidget::onDeviceSelectionChanged() {
    selected_device = device_combo->currentText().toStdString();
    device_status_label->setText(QString("Status: %1").arg(formatDeviceStatus(selected_device)));
}

QString OpenBLTUpdateWidget::formatUpdateSize(size_t bytes) {
    if (bytes < 1024) return QString("%1 B").arg(bytes);
    if (bytes < 1024 * 1024) return QString("%1 KB").arg(bytes / 1024.0, 0, 'f', 1);
    return QString("%1 MB").arg(bytes / (1024.0 * 1024.0), 0, 'f', 1);
}

QString OpenBLTUpdateWidget::formatDeviceStatus(const std::string& device_type) {
    if (device_type == "TC275") return "Ready";
    if (device_type == "STM32") return "Ready";
    if (device_type == "NXP") return "Ready";
    return "Unknown";
}

QString OpenBLTUpdateWidget::getUpdateButtonText() {
    Params params;
    if (params.getBool("OpenBLTUpdateAvailable")) {
        return "Update Available";
    }
    return "Up to Date";
}

bool OpenBLTUpdateWidget::isUpdateAvailable() const {
    Params params;
    return params.getBool("OpenBLTUpdateAvailable");
}

bool OpenBLTUpdateWidget::isUpdateInProgress() const {
    return update_in_progress;
}

bool OpenBLTUpdateWidget::canStartUpdate() const {
    return isUpdateAvailable() && !isUpdateInProgress();
}

bool OpenBLTUpdateWidget::canRollback() const {
    return !isUpdateInProgress() && !isUpdateAvailable();
}

// Factory function implementation
QWidget* createOpenBLTUpdateWidget(QWidget* parent) {
    return new OpenBLTUpdateWidget(parent);
}