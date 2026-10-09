#pragma once

#include <QWidget>
#include <QLabel>
#include <QPushButton>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QScrollArea>
#include <QFrame>
#include <QTimer>
#include <deque>

#include "selfdrive/ui/ui.h"

/**
 * AssistantCard - AI Assistant Conversation UI
 * 
 * Displays conversation history with the AI assistant.
 * Shows wake word status and current listening state.
 */

struct ChatMessage {
  enum Type { User, Assistant, System };
  Type type;
  QString text;
  qint64 timestamp;
  bool error = false;
};

class ChatBubble : public QFrame {
  Q_OBJECT

public:
  explicit ChatBubble(const ChatMessage& msg, QWidget* parent = nullptr);

private:
  void setupUI(const ChatMessage& msg);
  QString formatTimestamp(qint64 timestamp);
};

class AssistantCard : public QWidget {
  Q_OBJECT

public:
  explicit AssistantCard(QWidget* parent = nullptr);
  void updateState(const UIState& s);

private slots:
  void onClearClicked();
  void onMuteClicked();
  void updateListeningAnimation();

private:
  void setupUI();
  void addMessage(const ChatMessage& msg);
  void clearHistory();
  void setListening(bool listening);
  void setProcessing(bool processing);
  void setSpeaking(bool speaking);

  // UI Elements
  QLabel* title_label;
  QLabel* status_icon;
  QLabel* status_text;
  QPushButton* mute_btn;
  QPushButton* clear_btn;
  QScrollArea* scroll_area;
  QWidget* chat_container;
  QVBoxLayout* chat_layout;
  
  // Layouts
  QVBoxLayout* main_layout;
  QHBoxLayout* header_layout;
  
  // State
  std::deque<ChatMessage> message_history;
  bool is_listening = false;
  bool is_processing = false;
  bool is_speaking = false;
  bool is_muted = false;
  int animation_frame = 0;
  QTimer* animation_timer;
  
  // Constants
  const int CARD_WIDTH = 450;
  const int MAX_HISTORY = 50;
  
  // Animation frames for listening indicator
  const QStringList LISTENING_FRAMES = {"◉", "◎", "◉"};
};
