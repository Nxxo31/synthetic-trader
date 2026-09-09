"use client";
import { useCallback, useEffect, useRef, useState } from "react";

interface LiveState {
  type: "state";
  data: {
    mode: string;
    symbol: string;
    balance: number;
    pnl: number;
    trades_today: number;
    is_halted: boolean;
    circuit_breaker: {
      consecutive_losses: number;
      last_loss_time: string | null;
      is_halted: boolean;
      halt_reason: string;
      halt_until: string | null;
      cooldown_minutes: number;
      today: string;
    };
    last_update: string;
  };
  ts: string;
}

interface EquityUpdate {
  type: "equity_update";
  data: {
    timestamp: string;
    equity: number;
    pnl: number;
  };
  ts: string;
}

interface TradeMessage {
  type: "trade";
  data: {
    timestamp: string;
    direction: string;
    entry_price: number;
    stop_loss: number;
    take_profit: number;
    stake: number;
    confidence: number;
    score: number;
    contract_id: string;
    exit_price: number;
    pnl: number;
    exit_reason: string;
    duration_seconds: number;
    status: string;
  };
  ts: string;
}

interface Ping {
  type: "ping";
  ts: string;
}

interface ReplayStart {
  type: "replay_start";
  source: string;
  equity_points: number;
  trades: number;
  ts: string;
}

interface ReplayEquity {
  type: "equity";
  index: number;
  value: number;
  total: number;
  ts: string;
}

interface ReplayTrade {
  type: "replay_trade";
  trade: {
    entry_time: number;
    exit_time: number;
    direction: string;
    entry_price: number;
    exit_price: number;
    stop_loss: number;
    take_profit: number;
    pnl: number;
    pnl_pct: number;
    duration: number;
    win: boolean;
    exit_reason: string;
  };
  index: number;
  total: number;
  ts: string;
}

interface ReplayComplete {
  type: "replay_complete";
  ts: string;
}

type WSMessage =
  | LiveState
  | EquityUpdate
  | TradeMessage
  | Ping
  | ReplayStart
  | ReplayEquity
  | ReplayTrade
  | ReplayComplete;

interface LiveData {
  state: LiveState["data"] | null;
  equity: EquityUpdate["data"] | null;
  trade: TradeMessage["data"] | null;
  replay: {
    active: boolean;
    source: string;
    equityIndex: number;
    tradeIndex: number;
    equityTotal: number;
    tradeTotal: number;
    equityCurve: number[];
    trades: any[];
  } | null;
}

export function useLiveData() {
  const [data, setData] = useState<LiveData>({
    state: null,
    equity: null,
    trade: null,
    replay: null,
  });
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeout = useRef<NodeJS.Timeout | null>(null);
  const heartbeatTimeout = useRef<NodeJS.Timeout | null>(null);

  const wsUrl =
    process.env.NEXT_PUBLIC_WS_URL?.replace(/^http/, "ws") || "ws://localhost:8001/ws/live-data";

  const connect = useCallback(() => {
    // Close existing connection
    if (wsRef.current) {
      wsRef.current.close();
    }

    wsRef.current = new WebSocket(wsUrl);

    wsRef.current.onopen = () => {
      console.log("[WS] Connected to live data");
      if (reconnectTimeout.current) {
        clearTimeout(reconnectTimeout.current);
        reconnectTimeout.current = null;
      }
      // Start heartbeat
      if (heartbeatTimeout.current) {
        clearTimeout(heartbeatTimeout.current);
      }
      heartbeatTimeout.current = setTimeout(() => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({ type: "ping" }));
          if (heartbeatTimeout.current) {
            clearTimeout(heartbeatTimeout.current);
          }
          heartbeatTimeout.current = setTimeout(() => {
            /* ping every 10s */
          }, 10_000);
        }
      }, 10_000);
    };

    wsRef.current.onmessage = (event) => {
      try {
        const msg: WSMessage = JSON.parse(event.data);
        setData((prev) => {
          let next = { ...prev };
          switch (msg.type) {
            case "state":
              next.state = msg.data;
              break;
            case "equity_update":
              next.equity = msg.data;
              break;
            case "trade":
              next.trade = msg.data;
              break;
            case "replay_start":
              next.replay = {
                active: true,
                source: msg.source,
                equityIndex: 0,
                tradeIndex: 0,
                equityTotal: msg.equity_points,
                tradeTotal: msg.trades,
                equityCurve: [],
                trades: [],
              };
              break;
            case "equity":
              if (prev.replay) {
                next.replay = {
                  ...prev.replay,
                  equityCurve: [...(prev.replay.equityCurve || []), msg.value],
                  equityIndex: msg.index + 1,
                };
              }
              break;
            case "replay_trade":
              if (prev.replay) {
                next.replay = {
                  ...prev.replay,
                  trades: [...(prev.replay.trades || []), msg.trade],
                  tradeIndex: msg.index + 1,
                };
              }
              break;
            case "replay_complete":
              // keep replay data but mark inactive
              if (prev.replay) {
                next.replay = { ...prev.replay, active: false };
              }
              break;
            default:
              // ping or unknown -> ignore
              break;
          }
          return next;
        });
      } catch (e) {
        console.error("[WS] Failed to parse message", event.data, e);
      }
    };

    wsRef.current.onclose = () => {
      console.log("[WS] Disconnected");
      if (heartbeatTimeout.current) {
        clearTimeout(heartbeatTimeout.current);
        heartbeatTimeout.current = null;
      }
      // Attempt reconnect after 2s
      if (!reconnectTimeout.current) {
        reconnectTimeout.current = setTimeout(connect, 2000);
      }
    };

    wsRef.current.onerror = (err) => {
      console.error("[WS] Error", err);
    };
  }, [wsUrl]);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimeout.current) {
        clearTimeout(reconnectTimeout.current);
        reconnectTimeout.current = null;
      }
      if (heartbeatTimeout.current) {
        clearTimeout(heartbeatTimeout.current);
        heartbeatTimeout.current = null;
      }
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [connect]);

  return data;
}
