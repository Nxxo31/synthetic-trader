"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EquityCurve, DrawdownChart } from "@/components/charts/index";
import { cn } from "@/lib/utils";
import { formatCurrency, formatPct, formatNumber } from "@/lib/utils";

export default function Backtest() {
  const [backtests, setBacktests] = useState<any[]>([]);
  const [selectedBacktest, setSelectedBacktest] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [replayStatus, setReplayStatus] = useState<string | null>(null);
  const [isReplaying, setIsReplaying] = useState(false);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const loadBacktests = async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await api.getBacktestResults();
        setBacktests(data);
        // Auto-select the first backtest if none selected
        if (!selectedBacktest && data.length > 0) {
          setSelectedBacktest(data[0]);
        }
      } catch (err) {
        console.error("Failed to load backtests:", err);
        setError("Failed to load backtest results");
      } finally {
        setLoading(false);
      }
    };

    loadBacktests();
    const interval = setInterval(loadBacktests, 60_000); // refresh every minute
    return () => clearInterval(interval);
  }, [refresh, selectedBacktest]);

  const handleSelectBacktest = async (backtest: any) => {
    setSelectedBacktest(backtest);
    setReplayStatus(null);
    setIsReplaying(false);
  };

  const handleReplay = async () => {
    if (!selectedBacktest) return;
    
    setIsReplaying(true);
    setReplayStatus("Starting replay...");
    
    try {
      // Trigger the replay via API
      const result = await api.postBacktestReplay(selectedBacktest.filename);
      setReplayStatus("Replay completed!");
      setIsReplaying(false);
      
      // Optionally reload the backtest data to see updated results
      setTimeout(() => {
        setSelectedBacktest(result);
      }, 2000);
    } catch (err) {
      console.error("Failed to replay backtest:", err);
      setReplayStatus("Replay failed");
      setIsReplaying(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-[calc(100vh-4rem)] p-6">
        <div className="flex items-center justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
          <span className="ml-3">Loading backtests...</span>
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
        <h1 className="text-2xl font-bold">Backtest Replay</h1>
        <p className="text-muted-foreground mt-2">
          Review and replay historical backtest results to analyze strategy performance.
        </p>
      </div>

      <div className="grid gap-6">
        {/* Backtests List */}
        <div className="w-full lg:w-1/3">
          <div className="bg-card rounded-lg border border-border p-6 h-fit">
            <h2 className="text-lg font-semibold mb-4">Available Backtests</h2>
            {backtests.length === 0 ? (
              <p className="text-center py-8 text-muted-foreground">No backtests found</p>
            ) : (
              <div className="space-y-3">
                {backtests.map((bt) => (
                  <Button
                    key={bt.filename || bt.id}
                    variant={selectedBacktest && selectedBacktest.filename === bt.filename ? "default" : "outline"}
                    size="full"
                    onClick={() => handleSelectBacktest(bt)}
                    className="text-left"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex-1">
                        <div className="font-medium">
                          {bt.strategy || "Unknown"} ({bt.symbol || "Unknown"})
                        </div>
                        <p className="text-xs text-muted-foreground mt-1">
                          {new Date(bt.timestamp || bt.created_at || 0).toLocaleDateString()} • 
                          {bt.total_trades} trades
                        </p>
                      </div>
                      <div className="flex items-center space-x-2 text-xs">
                        <Badge 
                          variant={bt.win_rate >= 0.6 ? "secondary" : bt.win_rate >= 0.4 ? "outline" : "destructive"}
                        >
                          {formatPct(bt.win_rate)}
                        </Badge>
                        <Badge 
                          variant={bt.sharpe_ratio >= 1 ? "secondary" : bt.sharpe_ratio >= 0.5 ? "outline" : "destructive"}
                        >
                          SR: {bt.sharpe_ratio?.toFixed(2) ?? "0.00"}
                        </Badge>
                      </div>
                    </div>
                  </Button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Selected Backtest Detail */}
        <div className="lg:w-2/3 space-y-6">
          {!selectedBacktest ? (
            <div className="bg-card rounded-lg border border-border p-6">
              <h2 className="text-lg font-semibold mb-4">Select a Backtest</h2>
              <p className="text-muted-foreground">
                Choose a backtest from the list to view its detailed results and equity curve.
              </p>
            </div>
          ) : (
            <>
              <div className="bg-card rounded-lg border border-border p-6">
                <div className="flex justify-between items-start mb-4">
                  <h2 className="text-lg font-semibold">
                    {selectedBacktest.strategy} ({selectedBacktest.symbol})
                  </h2>
                  <div className="flex items-center space-x-2">
                    {isReplaying && (
                      <Button variant="outline" size="sm" disabled>
                        Replaying...
                      </Button>
                    )}
                    {!isReplaying && (
                      <Button 
                        variant={replayStatus ? "secondary" : "default"} 
                        size="sm" 
                        onClick={handleReplay}
                        disabled={isReplaying}
                      >
                        {replayStatus ? "Replay Again" : "Replay Backtest"}
                      </Button>
                    )}
                  </div>
                </div>
                
                {replayStatus && (
                  <div className="mb-4 p-3 rounded-lg text-xs text-center">
                    {replayStatus.includes("completed") 
                      ? "bg-secondary/20 border border-secondary/50 text-secondary"
                      : replayStatus.includes("failed")
                        ? "bg-destructive/20 border border-destructive/50 text-destructive"
                        : "bg-accent/20 border border-accent/50 text-accent"}
                    >
                      {replayStatus}
                    </div>
                  )}
                
                {/* Performance Metrics */}
                <div className="grid gap-4 sm:grid-cols-4">
                  <div className="bg-border/50 p-4 rounded-lg">
                    <p className="text-xs font-medium text-muted-foreground">Total Trades</p>
                    <p className="text-2xl font-bold tabular-nums">
                      {selectedBacktest.total_trades}
                    </p>
                  </div>
                  <div className="bg-border/50 p-4 rounded-lg">
                    <p className="text-xs font-medium text-muted-foreground">Win Rate</p>
                    <p className="text-2xl font-bold tabular-nums">
                      {formatPct(selectedBacktest.win_rate)}
                    </p>
                  </div>
                  <div className="bg-border/50 p-4 rounded-lg">
                    <p className="text-xs font-medium text-muted-foreground">Sharpe Ratio</p>
                    <p className="text-2xl font-bold tabular-nums">
                      {selectedBacktest.sharpe_ratio?.toFixed(2) ?? "0.00"}
                    </p>
                  </div>
                  <div className="bg-border/50 p-4 rounded-lg">
                    <p className="text-xs font-medium text-muted-foreground">Max Drawdown</p>
                    <p className="text-2xl font-bold tabular-nums">
                      {formatPct(selectedBacktest.max_drawdown ?? 0)}
                    </p>
                  </div>
                  <div className="bg-border/50 p-4 rounded-lg">
                    <p className="text-xs font-medium text-muted-foreground">Total P/L</p>
                    <p className="text-2xl font-bold tabular-nums">
                      {formatCurrency(selectedBacktest.total_pnl ?? 0)}
                    </p>
                  </div>
                  <div className="bg-border/50 p-4 rounded-lg">
                    <p className="text-xs font-medium text-muted-foreground">Profit Factor</p>
                    <p className="text-2xl font-bold tabular-nums">
                      {selectedBacktest.profit_factor?.toFixed(2) ?? "0.00"}
                    </p>
                  </div>
                  <div className="bg-border/50 p-4 rounded-lg">
                    <p className="text-xs font-medium text-muted-foreground">Expectancy</p>
                    <p className="text-2xl font-bold tabular-nums">
                      {formatCurrency(selectedBacktest.expectancy ?? 0)}
                    </p>
                  </div>
                  <div className="bg-border/50 p-4 rounded-lg">
                    <p className="text-xs font-medium text-muted-foreground">Avg P/L</p>
                    <p className="text-2xl font-bold tabular-nums">
                      {formatCurrency(selectedBacktest.avg_pnl ?? 0)}
                    </p>
                  </div>
                </div>

                {/* Gate Status */}
                {selectedBacktest.gate_failures && selectedBacktest.gate_failures.length > 0 && (
                  <div className="mt-4 p-4 bg-destructive/10 border border-destructive/20 rounded-lg">
                    <p className="font-medium text-destructive mb-2">Gate Status: FAILED</p>
                    <p className="text-sm text-destructive">
                      {selectedBacktest.gate_failures.map((f: string) => `• ${f}`).join("\n")}
                    </p>
                  </div>
                )}
                {!selectedBacktest.gate_failures || selectedBacktest.gate_failures.length === 0 && (
                  <div className="mt-4 p-4 bg-secondary/10 border border-secondary/20 rounded-lg">
                    <p className="font-medium text-secondary mb-2">Gate Status: PASSED</p>
                    <p className="text-sm text-secondary">
                      All risk management gates passed
                    </p>
                  </div>
                )}
              </div>
              
              {/* Equity Curve Chart */}
              <div className="bg-card rounded-lg border border-border p-6">
                <div className="flex justify-between items-start mb-4">
                  <h2 className="text-lg font-semibold">Equity Curve</h2>
                </div>
                {selectedBacktest.equity_curve && selectedBacktest.equity_curve.length > 0 ? (
                  <div className="h-96">
                    {/* Convert equity curve to format for chart */}
                    <EquityCurve 
                      data={selectedBacktest.equity_curve.map((value: number, index: number) => ({
                        timestamp: new Date(Date.now() - (selectedBacktest.equity_curve.length - index - 1) * 60_000).toISOString(), // approximate timestamps
                        value: value
                      }))}
                    />
                  </div>
                ) : (
                  <div className="h-96 flex items-center justify-center text-muted-foreground">
                    No equity curve data available
                  </div>
                )}
              </div>

              {/* Drawdown Chart */}
              <div className="bg-card rounded-lg border border-border p-6">
                <div className="flex justify-between items-start mb-4">
                  <h2 className="text-lg font-semibold">Drawdown Analysis</h2>
                </div>
                {selectedBacktest.equity_curve && selectedBacktest.equity_curve.length > 0 ? (
                  <div className="h-64">
                    {/* Calculate drawdown from equity curve */}
                    <DrawdownChart 
                      data={selectedBacktest.equity_curve.map((value: number, index: number) => {
                        const peak = Math.max(...selectedBacktest.equity_curve.slice(0, index + 1));
                        const drawdown = ((value - peak) / peak) * 100;
                        return {
                          timestamp: new Date(Date.now() - (selectedBacktest.equity_curve.length - index - 1) * 60_000).toISOString(),
                          drawdown: drawdown
                        };
                      })}
                    />
                  </div>
                ) : (
                  <div className="h-64 flex items-center justify-center text-muted-foreground">
                    No drawdown data available
                  </div>
                )}
              </div>

              {/* Recent Trades Table */}
              <div className="bg-card rounded-lg border border-border p-6">
                <div className="flex justify-between items-start mb-4">
                  <h2 className="text-lg font-semibold">Recent Trades</h2>
                  <Button variant="outline" size="sm">
                    Show all {selectedBacktest.trades?.length ?? 0} trades
                  </Button>
                </div>
                {selectedBacktest.trades && selectedBacktest.trades.length > 0 ? (
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
                        {selectedBacktest.trades.slice(0, 10).map((trade: any, idx: number) => (
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
                        {selectedBacktest.trades.length > 10 && (
                          <tr>
                            <td colSpan="6" className="px-4 py-3 text-center text-xs text-muted-foreground">
                              {selectedBacktest.trades.length - 10} more trades...
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="text-center py-8 text-muted-foreground">
                    No trade data available
                  </p>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}