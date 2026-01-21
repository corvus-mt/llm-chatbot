const statusText = document.getElementById("status-text");
const chatForm = document.getElementById("chat-form");
const messageInput = document.getElementById("message-input");
const sendButton = document.getElementById("send-button");
const clearButton = document.getElementById("clear-button");
const chatList = document.getElementById("chat-list");
const emptyState = document.getElementById("empty-state");

let history = [];

function setStatus(message) {
  statusText.textContent = message;
}

function renderHistory() {
  chatList.textContent = "";
  history.forEach((message) => {
    const item = document.createElement("div");
    item.className =
      message.role === "user" ? "message message--user" : "message message--assistant";

    const roleLabel = document.createElement("span");
    roleLabel.className = "message-role";
    roleLabel.textContent = message.role === "user" ? "You" : "Assistant";

    const content = document.createElement("p");
    content.className = "message-text";
    content.textContent = message.content;

    item.append(roleLabel, content);
    chatList.append(item);
  });
  emptyState.hidden = history.length > 0;
  chatList.scrollTop = chatList.scrollHeight;
}

async function loadHistory() {
  setStatus("Loading history...");
  try {
    const response = await fetch("/api/history");
    if (!response.ok) {
      throw new Error("History request failed");
    }
    const data = await response.json();
    history = data.messages || [];
    renderHistory();
    setStatus("Ready to chat.");
  } catch (error) {
    setStatus("Could not load history.");
  }
}

async function sendMessage(message) {
  setStatus("Sending...");
  sendButton.disabled = true;

  history = history.concat([{ role: "user", content: message }]);
  renderHistory();

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(errorText || "Request failed");
    }

    const data = await response.json();
    history = history.concat([{ role: "assistant", content: data.reply }]);
    renderHistory();
    setStatus("Reply received.");
  } catch (error) {
    setStatus("Server error.");
  } finally {
    sendButton.disabled = false;
  }
}

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message) {
    setStatus("Enter a message first.");
    return;
  }
  messageInput.value = "";
  sendMessage(message);
});

clearButton.addEventListener("click", async () => {
  setStatus("Clearing...");
  try {
    const response = await fetch("/api/clear", { method: "POST" });
    if (!response.ok) {
      throw new Error("Clear request failed");
    }
    history = [];
    renderHistory();
    setStatus("History cleared.");
  } catch (error) {
    setStatus("Could not clear history.");
  }
});

loadHistory();
