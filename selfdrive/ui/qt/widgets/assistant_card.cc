#include "selfdrive/ui/qt/widgets/assistant_card.h"

#include <QDateTime>
#include <QDebug>
#include <QScrollBar>

// ChatBubble implementation
ChatBubble::ChatBubble(const ChatMessage& msg, QWidget* parent) : QFrame(parent) {
  setupUI(msg);
}

void ChatBubble::setupUI(const ChatMessage& msg) {
  setFrameShape(QFrame::NoFrame);
  
  QHBoxLayout* layout = new QHBoxLayout(this);
  layout->setSpacing(10);
  layout->setContentsMargins(10, 5, 10, 5);
  
  // Style based on message type
  QString bg_color;
  QString text_color = "#ffffff";
  Qt::Alignment alignment;
  
  switch (msg.type) {
    case ChatMessage::User:
      bg_color = "#4a90d9";
      alignment = Qt::AlignRight;
      break;
    case ChatMessage::Assistant:
      bg_color = "#2a2a2a";
      alignment = Qt::AlignLeft;
      break;
    case ChatMessage::System:
      bg_color = "#fbbf24";
      text_color = "#1a1a1a";
      alignment = Qt::AlignCenter;
      break;
  }
  
  setStyleSheet(QString(R"(
    ChatBubble {
      background-color: %1;
      border-radius: 12px;
    }
    QLabel {
      color: %2;
      font-size: 14px;
      padding: 8px;
    }
  )").arg(bg_color, text_color));
  
  // Message text
  QLabel* text_label = new QLabel(msg.text);
  text_label->setWordWrap(true);
  text_label->setMaximumWidth(350);
  
  // Timestamp
  QLabel* time_label = new QLabel(formatTimestamp(msg.timestamp));
  time_label->setStyleSheet("font-size: 10px; color: #888888; padding: 2px;");
  
  QVBoxLayout* text_layout = new QVBoxLayout();
  text_layout->addWidget(text_label);
  text_layout->addWidget(time_label, 0, alignment);
  
  if (msg.type == ChatMessage::User) {
    layout->addStretch();
    layout->addLayout(text_layout);
  } else if (msg.type == ChatMessage::Assistant) {
    layout->addLayout(text_layout);
    layout->addStretch();
  } else {
    layout->addStretch();
    layout->addLayout(text_layout);
    layout->addStretch();
  }
}

QString ChatBubble::formatTimestamp(qint64 timestamp) {
  QDateTime dt = QDateTime::fromMSecsSinceEpoch(timestamp);
  return dt.toString("hh:mm");
}

// AssistantCard implementation
AssistantCard::AssistantCard(QWidget* parent) : QWidget(parent) {
  setupUI();
  
  animation_timer = new QTimer(this);
  connect(animation_timer, &QTimer::timeout, this, &AssistantCard::updateListeningAnimation);
}

void AssistantCard::setupUI() {
  setFixedWidth(CARD_WIDTH);
  setStyleSheet(R"(
    AssistantCard {
      background-color: #1a1a1a;
      border-radius: 12px;
    }
    QLabel {
      color: #ffffff;
      font-family: "Inter";
    }
    QPushButton {
      background-color: #2a2a2a;
      color: white;
      border: none;
      border-radius: 6px;
      padding: 8px 16px;
      font-size: 12px;
    }
    QPushButton:hover {
      background-color: #3a3a3a;
    }
    QScrollArea {
      border: none;
      background-color: transparent;
    }
  )");
  
  main_layout = new QVBoxLayout(this);
  main_layout->setSpacing(10);
  main_layout->setContentsMargins(15, 15, 15, 15);
  
  // Header
  header_layout = new QHBoxLayout();
  
  title_label = new QLabel("AI Assistant");
  title_label->setStyleSheet("font-size: 16px; font-weight: 600;");
  header_layout->addWidget(title_label);
  
  header_layout->addStretch();
  
  status_icon = new QLabel("◉");
  status_icon->setStyleSheet("font-size: 14px; color: #4ade80;");
  header_layout->addWidget(status_icon);
  
  status_text = new QLabel("Ready");
  status_text->setStyleSheet("font-size: 12px; color: #888888;");
  header_layout->addWidget(status_text);
  
  main_layout->addLayout(header_layout);
  
  // Chat area
  scroll_area = new QScrollArea();
  scroll_area->setWidgetResizable(true);
  scroll_area->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
  scroll_area->setVerticalScrollBarPolicy(Qt::ScrollBarAsNeeded);
  scroll_area->setMaximumHeight(300);
  
  chat_container = new QWidget();
  chat_layout = new QVBoxLayout(chat_container);
  chat_layout->setSpacing(8);
  chat_layout->setContentsMargins(5, 5, 5, 5);
  chat_layout->addStretch();
  
  scroll_area->setWidget(chat_container);
  main_layout->addWidget(scroll_area);
  
  // Control buttons
  QHBoxLayout* button_layout = new QHBoxLayout();
  
  mute_btn = new QPushButton("Mute");
  mute_btn->setCheckable(true);
  connect(mute_btn, &QPushButton::clicked, this, &AssistantCard::onMuteClicked);
  button_layout->addWidget(mute_btn);
  
  button_layout->addStretch();
  
  clear_btn = new QPushButton("Clear");
  connect(clear_btn, &QPushButton::clicked, this, &AssistantCard::onClearClicked);
  button_layout->addWidget(clear_btn);
  
  main_layout->addLayout(button_layout);
  
  // Add initial system message
  ChatMessage welcome_msg{
    ChatMessage::System,
    "Say 'Hey ExoPilot' or press the voice button to start",
    QDateTime::currentMSecsSinceEpoch()
  };
  addMessage(welcome_msg);
}

void AssistantCard::updateState(const UIState& s) {
  // Parse voiceStatus
  // TODO: Implement based on actual cereal message structure
  
  // Example state updates:
  // setListening(s.scene.wakeWordDetected);
  // setProcessing(s.scene.isProcessing);
  // setSpeaking(s.scene.isSpeaking);
}

void AssistantCard::addMessage(const ChatMessage& msg) {
  // Remove old messages if exceeding max
  while (message_history.size() >= MAX_HISTORY) {
    message_history.pop_front();
    // Remove first widget from layout (before the stretch)
    QLayoutItem* item = chat_layout->takeAt(0);
    if (item && item->widget()) {
      delete item->widget();
    }
    delete item;
  }
  
  message_history.push_back(msg);
  
  // Add bubble to layout (before the stretch)
  ChatBubble* bubble = new ChatBubble(msg, this);
  chat_layout->insertWidget(chat_layout->count() - 1, bubble);
  
  // Scroll to bottom
  QScrollBar* scrollbar = scroll_area->verticalScrollBar();
  scrollbar->setValue(scrollbar->maximum());
}

void AssistantCard::clearHistory() {
  message_history.clear();
  
  // Remove all widgets except stretch
  while (chat_layout->count() > 1) {
    QLayoutItem* item = chat_layout->takeAt(0);
    if (item && item->widget()) {
      delete item->widget();
    }
    delete item;
  }
  
  // Add welcome message back
  ChatMessage welcome_msg{
    ChatMessage::System,
    "Conversation cleared. Say 'Hey ExoPilot' to start.",
    QDateTime::currentMSecsSinceEpoch()
  };
  addMessage(welcome_msg);
}

void AssistantCard::setListening(bool listening) {
  is_listening = listening;
  
  if (listening) {
    status_text->setText("Listening...");
    status_text->setStyleSheet("font-size: 12px; color: #fbbf24;");
    animation_timer->start(500);
  } else {
    animation_timer->stop();
    if (!is_processing && !is_speaking) {
      status_text->setText("Ready");
      status_text->setStyleSheet("font-size: 12px; color: #888888;");
      status_icon->setText("◉");
      status_icon->setStyleSheet("font-size: 14px; color: #4ade80;");
    }
  }
}

void AssistantCard::setProcessing(bool processing) {
  is_processing = processing;
  
  if (processing) {
    status_text->setText("Thinking...");
    status_text->setStyleSheet("font-size: 12px; color: #4a90d9;");
    status_icon->setText("◌");
    status_icon->setStyleSheet("font-size: 14px; color: #4a90d9;");
  } else if (!is_listening && !is_speaking) {
    status_text->setText("Ready");
    status_text->setStyleSheet("font-size: 12px; color: #888888;");
  }
}

void AssistantCard::setSpeaking(bool speaking) {
  is_speaking = speaking;
  
  if (speaking) {
    status_text->setText("Speaking...");
    status_text->setStyleSheet("font-size: 12px; color: #4ade80;");
    status_icon->setText("◉");
    status_icon->setStyleSheet("font-size: 14px; color: #4ade80;");
  } else if (!is_listening && !is_processing) {
    status_text->setText("Ready");
    status_text->setStyleSheet("font-size: 12px; color: #888888;");
  }
}

void AssistantCard::updateListeningAnimation() {
  animation_frame = (animation_frame + 1) % LISTENING_FRAMES.size();
  status_icon->setText(LISTENING_FRAMES[animation_frame]);
}

void AssistantCard::onClearClicked() {
  clearHistory();
}

void AssistantCard::onMuteClicked() {
  is_muted = mute_btn->isChecked();
  mute_btn->setText(is_muted ? "Unmute" : "Mute");
  
  // TODO: Send mute command to audiod
  qDebug() << "Assistant:" << (is_muted ? "Muted" : "Unmuted");
}
