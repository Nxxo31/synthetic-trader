"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export default function Strategies() {
  const [strategies, setStrategies] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedStrategy, setSelectedStrategy] = useState<string | null>(null);
  const [backtestData, setBacktestData] = useState<any>(null);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await api.getStrategies();
        setStrategies(data);
      } catch (err) {
        console.error("Failed to load strategies:", err);
        setError("Failed to load strategies");
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  const renderEmpty = <p className="text-center py-8 text-muted-foreground">No strategies found</p>;
  const renderLoading = <p className="text-center py-8 text-muted-foreground">Loading backtest data...</p>;
  const renderError = (
    <p className="text-center py-8 text-muted-foreground">{error}</p>
  );
  const renderNoSelect = (
    <p className="text-center py-8 text-muted-foreground">
      Choose a strategy from the list to see its backtest results.
    </p>
  );

  if (loading) return renderLoading;

  if (error) return <>{error}</>;

  if (strategies.length === 0) return renderEmpty;

  return (
    <div className="min-h-[calc(100vh-4rem)] p-6">
      <h1 className="text-2xl font-bold">Strategies</h1>
      <p className="text-muted-foreground mt-2">
        Overview of all available trading strategies with their latest backtest performance.
      </p>
      <div className="grid gap-4">
        {strategies.map((s) => (
          <Button
            key={s.name}
            variant={selectedStrategy === s.name ? "default" : "outline"}
            size="default"
            onClick={() => setSelectedStrategy(s.name)}
            className="text-left"
          >
            <div className="flex items-center justify-between">
              <div className="flex-1">
                <div className="font-medium">{s.display || s.name}</div>
                <p className="text-xs text-muted-foreground mt-1">
                  {s.description || ""}
                </p>
              </div>
              <div className="flex items-center space-x-2 text-xs">
                {s.enabled && (
                  <Badge variant="secondary" className="text-xs">
                    Active
                  </Badge>
                )}
                {s.latest_backtest && (
                  <Badge
                    variant={s.latest_backtest.win_rate >= 0.6 ? "secondary" : "outline"}
                    className="text-xs"
                  >
                    {Math.round(s.latest_backtest.win_rate * 100)}% WR
                  </Badge>
                )}
              </div>
            </div>
          </Button>
        ))}
      </div>
      {selectedStrategy ? (
        <div className="grid gap-4 sm:grid-cols-3">
          {backtestData && !backtestData.error ? (
            <div className="grid gap-2">
              <div>
                <p className="text-xs font-medium text-muted-foreground">Total Trades</p>
                <p className="font-bold">{backtestData.total_trades}</p>
              </div>
              <div>
                <p className="text-xs font-medium text-muted-foreground">Win Rate</p>
                <p className="font-bold">
                  {(backtestData.win_rate * 100).toFixed(1)}%
                </p>
              </div>
              <div>
                <p className="text-xs font-medium text-muted-foreground">Sharpe Ratio</p>
                <p className="font-bold">
                  {backtestData.sharpe_ratio?.toFixed(2) ?? "0.00"}
                </p>
              </div>
              <div>
                <p className="text-xs font-medium text-muted-foreground">Total P/L</p>
                <p className="font-bold">
                  ${backtestData.total_pnl?.toFixed(2) ?? "0.00"}
                </p>
              </div>
            </div>
          ) : backtestData && backtestData.error ? (
            <p className="text-center py-8 text-muted-foreground">
              {backtestData.error}
            </p>
          ) : (
            <p className="text-center py-8 text-muted-foreground">
              Loading backtest data...
            </p>
          )}
        </div>
      ) : (
        <p className="text-center py-8 text-muted-foreground">
          Select a strategy from the list to see its backtest results.
        </p>
      )}
    </div>
  );
}
