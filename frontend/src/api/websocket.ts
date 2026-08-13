import { getAuthToken } from "./client";
import {
  normalizeBackendUrls,
  websocketBaseUrl,
} from "@/config/desktopRuntime";

export type MessageListener = (
  event: string,
  data: unknown,
  requestId?: string,
) => void;

export type CloseListener = (event: CloseEvent) => void;
export type OpenListener = () => void;

/**
 * 管理单个会话的 WebSocket 连接。
 *
 * 职责：
 * - 按 conversationId 建立/维护 WebSocket 长连接
 * - 自动心跳（30s ping/pong）
 * - 断线自动重连（指数退避，最大 30s）
 * - 事件订阅/分发
 */
class ConversationWS {
  private ws: WebSocket | null = null;

  private listeners = new Set<MessageListener>();

  private closeListeners = new Set<CloseListener>();

  private openListeners = new Set<OpenListener>();

  private pingTimer: number | null = null;

  private reconnectTimer: number | null = null;

  private reconnectDelay = 1000;

  private closed = false;

  constructor(private conversationId: string) {}

  get readyState(): number {
    return this.ws?.readyState ?? WebSocket.CLOSED;
  }

  /**
   * 建立 WebSocket 连接。
   */
  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        resolve();
        return;
      }

      this.closed = false;
      const token = getAuthToken();
      const url = `${websocketBaseUrl()}/ws/conversations/${this.conversationId}?token=${token}`;

      try {
        this.ws = new WebSocket(url);
      } catch (err) {
        reject(err instanceof Error ? err : new Error("WebSocket 连接失败"));
        return;
      }

      this.ws.onopen = () => {
        this.reconnectDelay = 1000;
        this.startPing();
        this.openListeners.forEach((fn) => fn());
        resolve();
      };

      this.ws.onmessage = (e) => {
        let msg: { event?: string; data?: unknown; request_id?: string };
        try {
          msg = JSON.parse(e.data);
        } catch {
          return;
        }
        if (msg.event !== undefined) {
          const data = normalizeBackendUrls(msg.data);
          this.listeners.forEach((fn) => fn(msg.event!, data, msg.request_id));
        }
      };

      this.ws.onclose = (event) => {
        this.stopPing();
        this.closeListeners.forEach((fn) => fn(event));
        if (!this.closed) {
          this.scheduleReconnect();
        }
      };

      this.ws.onerror = () => {
        reject(new Error("WebSocket 连接失败"));
      };
    });
  }

  /**
   * 发送消息。
   */
  send(event: string, data: unknown, requestId?: string): void {
    if (this.ws?.readyState !== WebSocket.OPEN) {
      return;
    }
    const payload: Record<string, unknown> = { event, data };
    if (requestId !== undefined) {
      payload.request_id = requestId;
    }
    this.ws.send(JSON.stringify(payload));
  }

  /**
   * 订阅消息事件。返回取消订阅函数。
   */
  onMessage(listener: MessageListener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  onClose(listener: CloseListener): () => void {
    this.closeListeners.add(listener);
    return () => {
      this.closeListeners.delete(listener);
    };
  }

  onOpen(listener: OpenListener): () => void {
    this.openListeners.add(listener);
    return () => {
      this.openListeners.delete(listener);
    };
  }

  /**
   * 主动断开连接（不再重连）。
   */
  disconnect(): void {
    this.closed = true;
    this.stopPing();
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
  }

  private startPing(): void {
    this.pingTimer = window.setInterval(() => {
      this.send("ping", {});
    }, 30000);
  }

  private stopPing(): void {
    if (this.pingTimer !== null) {
      window.clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null) return;

    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect().catch(() => {
        /* 重连失败，继续等待下一次 */
      });
    }, this.reconnectDelay);

    this.reconnectDelay = Math.min(this.reconnectDelay * 2, 30000);
  }
}

const pool = new Map<string, ConversationWS>();

/**
 * 获取或创建指定会话的 WebSocket 连接。
 */
export function getConversationWS(conversationId: string): ConversationWS {
  if (!pool.has(conversationId)) {
    pool.set(conversationId, new ConversationWS(conversationId));
  }
  return pool.get(conversationId)!;
}

/**
 * 断开指定会话的 WebSocket 连接并从池中移除。
 */
export function disconnectConversationWS(conversationId: string): void {
  const conn = pool.get(conversationId);
  if (conn) {
    conn.disconnect();
    pool.delete(conversationId);
  }
}
