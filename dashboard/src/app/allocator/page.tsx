"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { AllocationPieChart } from "@/components/charts/index";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { formatCurrency } from "@/lib/utils";

export default function Allocator() {
  const [config, setConfig] = useState<any>(null);
  const [allocation, setAllocation] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const loadData = async () => {
      setLoading(true);
      setError(null);
      try {
        const [configData, allocationData] = await Promise.all([
          api.getAllocatorConfig(),
          api.getAllocation(),
        ]);
        setConfig(configData);
        setAllocation(allocationData);
      } catch (err) {
        console.error("Failed to load allocator data:", err);
        setError("Failed to load allocator data");
      } finally {
        setLoading(false);
      }
    };

    loadData();
    const interval = setInterval(loadData, 15_000); // refresh every 15s
    return () => clearInterval(interval);
  }, [refresh]);

  const handleUpdateConfig = async (e: React.FormEvent) => {
    e.preventDefault();
    // In a real app, we'd get values from form inputs
    // For now, just toggle between two presets
    try {
      const newConfig = config.reserva_pct === 80 ? { reserva_pct: 75 } : { reserva_pct: 80 };
      const updated = await api.updateAllocatorConfig(newConfig);
      setConfig(updated);
    } catch (err) {
      console.error("Failed to update allocator config:", err);
      setError("Failed to update configuration");
    }
  };

  if (loading) {
    return (
      <div className="min-h-[calc(100vh-4rem)] p-6">
        <div className="flex items-center justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
          <span className="ml-3">Loading allocator data...</span>
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

  // Default values if data not available
  const configData = config ?? {
    data_available: false,
    capital_total: 10000,
    reserva: 8000,
    superavit_diario: 200,
    superavit_disponible: 200,
    superavit_usado: 0,
    reserva_pct: 80,
    superavit_diario_pct: 20,
    live_balance: 9792.38,
    live_pnl: -207.62,
    trades_today: 1,
    return_pct: -2.08,
    is_active: true,
    rebalance_daily: true,
    min_surplus: 50,
  };

  const allocationData = allocation ?? {
    capital_total: configData.capital_total,
    reserva: Math.round(configData.capital_total * (configData.reserva_pct / 100)),
    superavit_diario: configData.superavit_diario,
    superavit_disponible: configData.superavit_disponible,
    superavit_usado: configData.superavit_usado,
    reserva_pct: configData.reserva_pct,
    superavit_diario_pct: configData.superavit_diario_pct,
    live_balance: configData.live_balance,
    live_pnl: configData.live_pnl,
    trades_today: configData.trades_today,
    return_pct: configData.return_pct,
    is_active: configData.is_active,
    data_available: configData.data_available,
  };

  return (
    <div className="min-h-[calc(100vh-4rem)] p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold">Capital Allocator</h1>
        <p className="text-muted-foreground mt-2">
          Visualize and manage capital allocation between reserve (80%) and daily surplus (20%).
        </p>
      </div>

      {/* Allocation Overview Cards */}
      <div className="grid gap-6 mb-6 sm:grid-cols-2 lg:grid-cols-3">
        {/* Total Capital */}
        <div className="bg-card rounded-lg border border-border p-6">
          <h3 className="text-sm font-medium text-muted-foreground mb-2">Total Capital</h3>
          <div className="text-2xl font-bold tabular-nums">
            ${formatCurrency(allocationData.capital_total)}
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            Starting balance for allocation
          </p>
        </div>

        {/* Reserve (80%) */}
        <div className="bg-card rounded-lg border border-border p-6">
          <h3 className="text-sm font-medium text-muted-foreground mb-2">Reserve (80%)</h3>
          <div className="text-2xl font-bold tabular-nums">
            ${formatCurrency(allocationData.reserva)}
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            Protected capital for trading
          </p>
        </div>

        {/* Daily Surplus (20%) */}
        <div className="bg-card rounded-lg border border-border p-6">
          <h3 className="text-sm font-medium text-muted-foreground mb-2">Daily Surplus (20%)</h3>
          <div className="text-2xl font-bold tabular-nums">
            ${formatCurrency(allocationData.superavit_diario)}
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            Available for daily trading
          </p>
        </div>
      </div>

      {/* Allocation Chart */}
      <div className="bg-card rounded-lg border border-border p-6">
        <div className="flex justify-between items-start mb-4">
          <h2 className="text-lg font-semibold">Current Allocation</h2>
          <Button 
            variant="outline" 
            size="sm" 
            onClick={handleUpdateConfig}
          >
            Adjust Allocation
          </Button>
        </div>
        <div className="h-96">
          {allocationData.data_available ? (
            <AllocationPieChart 
              allocation={{ 
                reserve: allocationData.reserva, 
                surplus: allocationData.superavit_disponible 
              }} 
            />
          ) : (
            <div className="flex h-full items-center justify-center text-muted-foreground">
              Allocation data not available
            </div>
          )}
        </div>
      </div>

      {/* Surplus Utilization */}
      <div className="bg-card rounded-lg border border-border p-6">
        <div className="flex justify-between items-start mb-4">
          <h2 className="text-lg font-semibold">Surplus Utilization</h2>
        </div>
        <div className="space-y-4">
          <div className="flex items-center justify-between text-sm">
            <span>Available Surplus</span>
            <span className="font-mono tabular-nums">
              ${formatCurrency(allocationData.superavit_disponible)}
            </span>
          </div>
          <div className="flex items-center justify-between text-sm">
            <span>Used Surplus</span>
            <span className="font-mono tabular-nums">
              ${formatCurrency(allocationData.superavit_usado)}
            </span>
          </div>
          <div className="flex items-center justify-between text-sm">
            <span>Utilization Rate</span>
            <span className="font-mono tabular-nums">
              {allocationData.superavit_disponible > 0 
                ? ((allocationData.superavit_usado / allocationData.superavit_disponible) * 100).toFixed(1) + "%"
                : "0%"}
            </span>
          </div>
        </div>
      </div>

      {/* Live Trading Stats */}
      <div className="bg-card rounded-lg border border-border p-6">
        <div className="flex justify-between items-start mb-4">
          <h2 className="text-lg font-semibold">Live Trading Status</h2>
        </div>
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <p className="text-xs font-medium text-muted-foreground">Live Balance</p>
              <p className="text-lg font-bold tabular-nums">
                ${formatCurrency(allocationData.live_balance)}
              </p>
            </div>
            <div>
              <p className="text-xs font-medium text-muted-foreground">Live P/L</p>
              <p className={`text-lg font-bold tabular-nums ${allocationData.live_pnl >= 0 ? "text-success" : "text-destructive"}`}>
                {allocationData.live_pnl >= 0 ? "+" : ""}${formatCurrency(Math.abs(allocationData.live_pnl))}
              </p>
            </div>
            <div>
              <p className="text-xs font-medium text-muted-foreground">Trades Today</p>
              <p className="text-lg font-bold tabular-nums">
                {allocationData.trades_today}
              </p>
            </div>
            <div>
              <p className="text-xs font-medium text-muted-foreground">Return %</p>
              <p className={`text-lg font-bold tabular-nums ${allocationData.return_pct >= 0 ? "text-success" : "text-destructive"}`}>
                {allocationData.return_pct >= 0 ? "+" : ""}${allocationData.return_pct.toFixed(2)}%
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}