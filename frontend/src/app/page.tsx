"use client";

import { useState, useEffect, useRef } from "react";
import styles from "./page.module.css";

type MessageRole = "user" | "assistant";

interface Message {
  role: MessageRole;
  content: string;
  toolCalls?: { name: string; input: Record<string, unknown> }[];
}

export default function Home() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [toolActivity, setToolActivity] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    checkAuth();
    // Handle OAuth redirect params
    const params = new URLSearchParams(window.location.search);
    if (params.get("authed") === "1") {
      setAuthenticated(true);
      window.history.replaceState({}, "", "/");
    }
    if (params.get("error")) {
      console.error("Auth error:", params.get("error"));
      window.history.replaceState({}, "", "/");
    }
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, toolActivity]);

  async function checkAuth() {
    const res = await fetch("/api/auth/status");
    const data = await res.json();
    setAuthenticated(data.authenticated);
  }

  async function handleLogout() {
    await fetch("/api/auth/logout", { method: "POST" });
    setAuthenticated(false);
    setMessages([]);
  }

  async function handleSend() {
    if (!input.trim() || streaming) return;

    const userMessage: Message = { role: "user", content: input.trim() };
    const newMessages = [...messages, userMessage];
    setMessages(newMessages);
    setInput("");
    setStreaming(true);
    setToolActivity(null);

    // Build conversation history for the API
    const apiMessages = newMessages.map((m) => ({
      role: m.role,
      content: m.content,
    }));

    let assistantText = "";
    const assistantMessage: Message = { role: "assistant", content: "" };
    setMessages([...newMessages, assistantMessage]);

    try {
      const res = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: apiMessages }),
      });

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const text = decoder.decode(value);
        const lines = text.split("\n");

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const data = line.slice(6);
          if (data === "[DONE]") break;

          try {
            const chunk = JSON.parse(data);

            if (chunk.type === "text_delta") {
              assistantText += chunk.content;
              setMessages((prev) => {
                const updated = [...prev];
                updated[updated.length - 1] = {
                  role: "assistant",
                  content: assistantText,
                };
                return updated;
              });
            } else if (chunk.type === "tool_use") {
              setToolActivity(`🔧 Calling ${chunk.name}…`);
            } else if (chunk.type === "tool_result") {
              setToolActivity(
                chunk.is_error
                  ? `❌ ${chunk.name} failed`
                  : `✅ ${chunk.name} done`
              );
              setTimeout(() => setToolActivity(null), 1500);
            } else if (chunk.type === "error") {
              assistantText = `Error: ${chunk.content}`;
              setMessages((prev) => {
                const updated = [...prev];
                updated[updated.length - 1] = {
                  role: "assistant",
                  content: assistantText,
                };
                return updated;
              });
            }
          } catch {
            // skip malformed chunks
          }
        }
      }
    } catch (err) {
      console.error("Stream error:", err);
      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = {
          role: "assistant",
          content: "Something went wrong. Please try again.",
        };
        return updated;
      });
    } finally {
      setStreaming(false);
      setToolActivity(null);
    }
  }

  if (authenticated === null) {
    return (
      <main className={styles.main}>
        <div className={styles.loading}>Connecting…</div>
      </main>
    );
  }

  return (
    <main className={styles.main}>
      <header className={styles.header}>
        <div className={styles.headerLeft}>
          <span className={styles.logo}>✉</span>
          <h1 className={styles.title}>Gmail Agent</h1>
        </div>
        {authenticated && (
          <button className={styles.logoutBtn} onClick={handleLogout}>
            Disconnect
          </button>
        )}
      </header>

      {!authenticated ? (
        <div className={styles.connectScreen}>
          <div className={styles.connectCard}>
            <div className={styles.connectIcon}>✉</div>
            <h2 className={styles.connectTitle}>Connect your Gmail</h2>
            <p className={styles.connectDesc}>
              Authorize read access and draft creation. No emails will ever be sent.
            </p>
            <a href="/api/auth/login" className={styles.connectBtn}>
              Connect Gmail
            </a>
          </div>
        </div>
      ) : (
        <div className={styles.chatContainer}>
          <div className={styles.messages}>
            {messages.length === 0 && (
  <div className={styles.emptyState}>
    <p>Ask me about your emails.</p>
  </div>
)}
<div className={styles.suggestions}>
  {[
    "Summarize my unread emails from this week",
    "List my Gmail labels",
    "Draft a reply to the last email from my boss",
  ].map((s) => (
    <button
      key={s}
      className={styles.suggestion}
      onClick={() => setInput(s)}
    >
      {s}
    </button>
  ))}
</div>

            {messages.map((msg, i) => (
              <div
                key={i}
                className={`${styles.message} ${
                  msg.role === "user" ? styles.userMessage : styles.assistantMessage
                }`}
              >
                <div className={styles.bubble}>{msg.content}</div>
              </div>
            ))}

            {toolActivity && (
              <div className={styles.toolActivity}>{toolActivity}</div>
            )}

            <div ref={bottomRef} />
          </div>

          <div className={styles.inputRow}>
            <input
              className={styles.input}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleSend()}
              placeholder="Ask about your emails…"
              disabled={streaming}
            />
            <button
              className={styles.sendBtn}
              onClick={handleSend}
              disabled={streaming || !input.trim()}
            >
              {streaming ? "…" : "Send"}
            </button>
          </div>
        </div>
      )}
    </main>
  );
}
