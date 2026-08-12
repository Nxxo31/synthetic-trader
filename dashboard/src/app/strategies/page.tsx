"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export default function Strategies() {
  const [strategies, setStrategies] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedStrategy, setSelectedStrategy] = useState<string | null>(null);
  const [backtestData, setBacktestData] = useState<any>(null);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const loadStrategies = async () => {
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

    loadStrategies();
    const interval = setInterval(loadStrategies, 60_000); // refresh every minute
    return () => clearInterval(interval);
  }, [refresh]);

  const handleSelectStrategy = async (name: string) => {
    setSelectedStrategy(name);
    setBacktestData(null);
    try {
      const data = await api.getLatestBacktest();
      setBacktestData(data);
    } catch (err) {
      // Try to get specific backtest by strategy
      try {
        const results = await api.getBacktestResults();
        const strategyResults = results.filter((r: any) => r.strategy === name);
        if (strategyResults.length > 0) {
          // Get the most recent
          const latest = strategyResults.reduce((prev: any, curr: any) => 
            new Date(curr.timestamp || 0) > new Date(prev.timestamp || 0) ? curr : prev
          );
          setBacktestData(latest);
        }
      } catch (err2) {
        console.error("Failed to load backtest for strategy:", err2);
        setBacktestData({ error: "No backtest data available" });
      }
    }
  };

  if (loading) {
    return (
      <div className="min-h-[calc(100vh-4rem)] p-6">
        <div className="flex items-center justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
          <span className="ml-3">Loading strategies...</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-[calc(100vh-4rem)] p-6">
        <div className="bg-destructive/10 border border-destructive/20 rounded-lg p-6 text-destructive">
          {error}
          <Button variant="outline" size="sm" onClick={() => setRefresh(r => r + 1)} className="mt-4">
            Retry
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-[calc(100vh-4rem)] p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold">Strategies</h1>
        <p className="text-muted-foreground mt-2">
          Overview of all available trading strategies with their latest backtest performance.
        </p>
      </div>

      <div className="grid gap-6">
        {/* Strategies List */}
        <div className="w-full lg:w-1/3">
          <div className="bg-card rounded-lg border border-border p-6 h-fit">
            <h2 className="text-lg font-semibold mb-4">Available Strategies</h2>
            {strategies.length === 0 ? (
              <p className="text-center py-8 text-muted-foreground">No strategies found</p>
            ) : (
              <div className="space-y-3">
                {strategies.map((strategy) => (
                  <Button
                    key={strategy.name}
                    variant={selectedStrategy === strategy.name ? "default" : "outline"}
                    size="full"
                    onClick={() => handleSelectStrategy(strategy.name)}
                    className="text-left"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex-1">
                        <div className="font-medium">{strategy.display || strategy.name}</div>
                        <p className="text-xs text-muted-foreground mt-1">
                          {strategy.description || ""}
                        </p>
                      </div>
                      <div className="flex items-center space-x-2 text-xs">
                        {strategy.enabled && (
                          <Badge variant="secondary" size="xs">
                            Active
                          </Badge>
                        )}
                        {strategy.latest_backtest && (
                          <>
                            <Badge 
                              variant={strategy.latest_backtest.win_rate >= 0.6 ? "secondary" : "outline"}
                              size="xs"
                            >
                              {Math.round(strategy.latest_backtest.win_rate * 100)}% WR
                            </Badge>
                            <Badge 
                              variant={strategy.latest_backtest.sharpe_ratio >= 1 ? "secondary" : "outline"}
                              size="xs"
                            >
                              SR: {strategy.latest_backtest.sharpe_ratio.toFixed(2)}
                            </Badge>
                          </>
                        )}
                      </div>
                    </div>
                  </Button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Strategy Detail / Backtest */}
        <div className="lg:w-2/3 space-y-6">
          {!selectedStrategy ? (
            <div className="bg-card rounded-lg border border-border p-6">
              <h2 className="text-lg font-semibold mb-4">Select a Strategy</h2>
              <p className="text-muted-foreground">
                Choose a strategy from the list to view its detailed backtest results and performance metrics.
              </p>
            </div>
          ) : (
            <>
              <div className="bg-card rounded-lg border border-border p-6">
                <div className="flex justify-between items-start mb-4">
                  <h2 className="text-lg font-semibold">
                    {selectedStrategy} Performance
                  </h2>
                  <div className="flex items-center space-x-2">
                    <Button 
                      variant="outline" 
                      size="sm" 
                      onClick={() => setSelectedStrategy(null)}
                    >
                      Clear Selection
                    </Button>
                  </div>
                </div>
                
                {backtestData && !backtestData.error ? (
                  <>
                    <div className="grid gap-4 sm:grid-cols-3">
                      <div className="bg-border/50 p-4 rounded-lg">
                        <p className="text-xs font-medium text-muted-foreground">Total Trades</p>
                        <p className="text-2xl font-bold tabular-nums">
                          {backtestData.total_trades}
                        </p>
                      </div>
                      <div className="bg-border/50 p-4 rounded-lg">
                        <p className="text-xs font-medium text-muted-foreground">Win Rate</p>
                        <p className="text-2xl font-bold tabular-nums">
                          {(backtestData.win_rate * 100).toFixed(1)}%
                        </p>
                      </div>
                      <div className="bg-border/50 p-4 rounded-lg">
                        <p className="text-xs font-medium text-muted-foreground">Sharpe Ratio</p>
                        <p className="text-2xl font-bold tabular-nums">
                          {backtestData.sharpe_ratio?.toFixed(2) ?? "0.00"}
                        </p>
                      </div>
                      <div className="bg-border/50 p-4 rounded-lg">
                        <p className="text-xs font-medium text-muted-foreground">Total P/L</p>
                        <p className="text-2xl font-bold tabular-nums">
                          ${backtestData.total_pnl?.toFixed(2) ?? "0.00"}
                        </p>
                      </div>
                      <div className="bg-border/50 p-4 rounded-lg">
                        <p className="text-xs font-medium text-muted-foreground">Max Drawdown</p>
                        <p className="text-2xl font-bold tabular-nums">
                          {(backtestData.max_drawdown ?? 0) >= 0 ? 
                            `-${(backtestData.max_drawdown ?? 0).toFixed(2)}%` : 
                            `${(backtestData.max_drawdown ?? 0).toFixed(2)}%`}
                        </p>
                      </div>
                      <div className="bg-border/50 p-4 rounded-lg">
                        <p className="text-xs font-medium text-muted-foreground">Profit Factor</p>
                        <p className="text-2xl font-bold tabular-nums">
                          {backtestData.profit_factor?.toFixed(2) ?? "0.00"}
                        </p>
                      </div>
                    </div>

                    <div className="mt-6">
                      <h3 className="text-lg font-semibold mb-3">Equity Curve</h3>
                      {/* Convert equity curve to format for chart */}
                      {backtestData.equity_curve && backtestData.equity_curve.length > 0 ? (
                        <div className="h-96">
                          {/* We'll need to import the EquityCurve component or recreate it inline */}
                          <div className="text-center py-8 text-muted-foreground">
                            Equity chart would be displayed here
                          </div>
                        </div>
                      ) : (
                        <p className="text-center py-8 text-muted-foreground">
                          No equity curve data available
                        </p>
                      )}
                    </div>
                  </>
                ) : backtestData && backtestData.error ? (
                  <div className="text-center py-8 text-muted-foreground">
                    {backtestData.error}
                  </div>
                ) : (
                  <div className="text-center py-8 text-muted-foreground">
                    Loading backtest data...
                  </div>
                )}
              </div>
              
              {/* Trades Table */}
              {backtestData && !backtestData.error && backtestData.trades && backtestData.trades.length > 0 && (
                <div className="bg-card rounded-lg border border-border p-6">
                  <div className="flex justify-between items-start mb-4">
                    <h2 className="text-lg font-semibold">Recent Trades</h2>
                    <Button variant="outline" size="sm">
                      Show all {backtestData.trades.length} trades
                    </Button>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm text-left">
                      <thead>
                        <tr className="border-b">
                          <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">
                            Time
                          </th>
                          <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">
                            Dir
                          </th>
                          <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">
                            Entry
                          </th>
                          <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">
                            Exit
                          </th>
                          <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">
                            P/L
                          </th>
                          <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">
                            Result
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y">
                        {backtestData.trades.slice(0, 10).map((trade: any, idx: number) => (
                          <tr key={idx} className="hover:bg-accent/5">
                            <td className="px-4 py-3 text-xs tabular-nums">
                              {new Date(trade.timestamp || trade.entry_time * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                            </td>
                            <td className="px-4 py-3 text-xs capitalize">
                              <Badge 
                                variant={trade.direction === "LONG" ? "secondary" : "destructive"}
                              >
                                {trade.direction}
                              </Badge>
                            </td>
                            <td className="px-4 py-3 text-xs tabular-nums">
                              ${trade.entry_price?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                            </td>
                            <td className="px-4 py-3 text-xs tabular-nums">
                              ${trade.exit_price?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                            </td>
                            <td className="px-4 py-3 text-xs tabular-nums">
                              {trade.pnl >= 0 ? `+${trade.pnl.toFixed(2)}` : `${trade.pnl.toFixed(2)}`}
                            </td>
                            <td className="px-4 py-3 text-xs capitalize">
                              <Badge 
                                variant={trade.win ? "secondary" : "destructive"}
                              >
                                {trade.win ? "WIN" : "LOSS"}
                              </Badge>
                            </td>
                          </tr>
                        ))}
                        {backtestData.trades.length > 10 && (
                          <tr>
                            <td colSpan="6" className="px-4 py-3 text-center text-xs text-muted-foreground">
                              {backtestData.trades.length - 10} more trades...
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}