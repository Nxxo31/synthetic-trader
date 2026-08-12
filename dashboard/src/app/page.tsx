"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useLiveData } from "@/hooks/use-live-data";
import { EquityCurve } from "@/components/charts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export default function Dashboard() {
  const { state, equity, trade } = useLiveData();
  const [apiBalance, setApiBalance] = useState<any>(null);
  const [positions, setPositions] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const loadData = async () => {
      setLoading(true);
      try {
        const [balanceData, positionsData] = await Promise.all([
          api.getBalance(),
          api.getPositions(),
        ]);
        setApiBalance(balanceData);
        setPositions(positionsData);
      } catch (err) {
        console.error("Failed to load dashboard data:", err);
      } finally {
        setLoading(false);
      }
    };

    loadData();
    const interval = setInterval(loadData, 30_000); // refresh every 30s
    return () => clearInterval(interval);
  }, [refresh]);

  // Fallback to live WebSocket data if API not available
  const balance = apiBalance?.balance ?? state?.balance ?? 0;
  const pnl = apiBalance?.pnl ?? state?.pnl ?? 0;
  const equityValue = apiBalance?.equity ?? (state?.balance ?? 0) + (state?.pnl ?? 0);
  const tradesToday = apiBalance?.trades_today ?? state?.trades_today ?? 0;
  const isHalted = apiBalance?.is_halted ?? state?.is_halted ?? false;

  // Calculate 24h change (simplified: use P/L as % of starting balance)
  const startingBalance = 10000; // default paper trading start
  const change24h = startingBalance > 0 ? (pnl / startingBalance) * 100 : 0;

  return (
    <main className="space-y-8 p-6">
      <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
        {/* Balance Card */}
        <div className="bg-card rounded-lg border border-border p-6">
          <h3 className="text-sm font-medium text-muted-foreground mb-2">Balance</h3>
          <div className="text-2xl font-bold tabular-nums">
            ${balance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            Paper trading • {state?.symbol ?? "R_100"}
          </p>
        </div>

        {/* Equity Card */}
        <div className="bg-card rounded-lg border border-border p-6">
          <h3 className="text-sm font-medium text-muted-foreground mb-2">Equity</h3>
          <div className="text-2xl font-bold tabular-nums">
            ${equityValue.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            Starting: $10,000.00
          </p>
        </div>

        {/* P/L Card */}
        <div className="bg-card rounded-lg border border-border p-6">
          <h3 className="text-sm font-medium text-muted-foreground mb-2">P/L Today</h3>
          <div className={`text-2xl font-bold tabular-nums ${pnl >= 0 ? "text-success" : "text-destructive"}`}>
            {pnl >= 0 ? "+" : ""}${pnl.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <div className="flex items-center text-xs mt-2">
            <Badge 
              variant={pnl >= 0 ? "secondary" : "destructive"}
              className="ml-2"
            >
              {change24h >= 0 ? `+${change24h.toFixed(2)}%` : `${change24h.toFixed(2)}%`}
            </Badge>
            <span className="ml-2">24h change</span>
          </div>
        </div>

        {/* Status Card */}
        <div className="bg-card rounded-lg border border-border p-6">
          <h3 className="text-sm font-medium text-muted-foreground mb-2">Status</h3>
          <div className="space-y-3">
            <div className="flex items-center">
              <div className="flex h-5 w-5 items-center justify-center rounded bg-primary/20 text-primary flex-shrink-0">
                🤖
              </div>
              <div className="flex-1 space-y-0.5">
                <p className="text-sm font-medium">Bot Status</p>
                <p className="text-xs text-muted-foreground">
                  {isHalted ? "Halted" : state?.mode === "paper" ? "Paper Trading" : "Live"}
                </p>
              </div>
            </div>
            <div className="flex items-center">
              <div className="flex h-5 w-5 items-center justify-center rounded bg-primary/20 text-primary flex-shrink-0">
                📊
              </div>
              <div className="flex-1 space-y-0.5">
                <p className="text-sm font-medium">Trades Today</p>
                <p className="text-xs text-muted-foreground tabular-nums">
                  {tradesToday}
                </p>
              </div>
            </div>
            <div className="flex items-center">
              <div className="flex h-5 w-5 items-center justify-center rounded bg-primary/20 text-primary flex-shrink-0">
                ⚡
              </div>
              <div className="flex-1 space-y-0.5">
                <p className="text-sm font-medium">Last Update</p>
                <p className="text-xs text-muted-foreground" id="last-update-time">
                  {state ? new Date(state.last_update).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : "--:--"}
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Positions Table */}
      <section className="bg-card rounded-lg border border-border p-6">
        <div className="flex justify-between items-start mb-4">
          <h2 className="text-lg font-semibold">Active Positions</h2>
          <Button variant="outline" size="sm" onClick={() => setRefresh(r => r + 1)}>
            Refresh
          </Button>
        </div>
        
        {loading && !positions.length ? (
          <p className="text-center py-8 text-muted-foreground">Loading positions...</p>
        ) : positions.length === 0 ? (
          <p className="text-center py-8 text-muted-foreground">No active positions</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead>
                <tr className="border-b">
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">
                    Time
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">
                    Direction
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">
                    Entry
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">
                    Size
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">
                    P/L
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">
                    Status
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {positions.slice(0, 10).map((pos, idx) => (
                  <tr key={idx} className="hover:bg-accent/5">
                    <td className="px-4 py-3 text-xs tabular-nums">
                      {new Date(pos.timestamp || Number(pos.entry_time) * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </td>
                    <td className="px-4 py-3 text-xs capitalize">
                      <Badge 
                        variant={pos.direction === "LONG" ? "secondary" : "destructive"}
                      >
                        {pos.direction}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-xs tabular-nums">
                      ${pos.entry_price?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    <td className="px-4 py-3 text-xs tabular-nums">
                      ${(pos.stake ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    <td className="px-4 py-3 text-xs tabular-nums">
                      {pos.pnl >= 0 ? `+${pos.pnl.toFixed(2)}` : `${pos.pnl.toFixed(2)}`}
                    </td>
                    <td className="px-4 py-3 text-xs capitalize">
                      <Badge variant="outline">
                        {pos.status || pos.exit_reason || "OPEN"}
                      </Badge>
                    </td>
                  </tr>
                ))}
                {positions.length > 10 && (
                  <tr>
                    <td colSpan="6" className="px-4 py-3 text-center text-xs text-muted-foreground">
                      {positions.length - 10} more positions...
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Equity Chart */}
      <section className="bg-card rounded-lg border border-border p-6">
        <div className="flex justify-between items-start mb-4">
          <h2 className="text-lg font-semibold">Equity Curve (24h)</h2>
        </div>
        {/* Use WebSocket equity data if available, else placeholder */}
        {equity ? (
          <EquityCurve 
            data={[
              { 
                timestamp: new Date().toISOString(), 
                value: equity.equity 
              }
            ]} 
          />
        ) : (
          <div className="h-96 flex items-center justify-center text-muted-foreground">
            Loading equity data...
          </div>
        )}
      </section>
    </main>
  );
}