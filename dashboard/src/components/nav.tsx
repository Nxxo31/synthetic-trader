"use client";

import * as React from "react";
import Link from "next/link";
import { cn } from "@/lib/utils";
import { useLiveData } from "@/hooks/use-live-data";
import { Badge } from "@/components/ui/badge";

export function Nav() {
  const { state } = useLiveData();

  return (
    <nav className="flex h-14 items-center justify-between px-4 bg-border/50 backdrop-blur border-b border-border">
      <div className="flex items-center space-x-6">
        <Link href="/" className="flex items-center space-x-3 text-lg font-semibold">
          <span className="text-primary">Synthetic</span>
          <span className="text-muted-foreground">Trader</span>
        </Link>

        <div className="hidden md:flex space-x-4">
          <Link href="/" className="text-sm font-medium hover:text-primary transition-colors">
            Dashboard
          </Link>
          <Link href="/strategies" className="text-sm font-medium hover:text-primary transition-colors">
            Strategies
          </Link>
          <Link href="/allocator" className="text-sm font-medium hover:text-primary transition-colors">
            Allocator
          </Link>
          <Link href="/backtest" className="text-sm font-medium hover:text-primary transition-colors">
            Backtest
          </Link>
          <Link href="/settings" className="text-sm font-medium hover:text-primary transition-colors">
            Settings
          </Link>
        </div>
      </div>

      <div className="flex items-center space-x-3">
        <div className="relative inline-block text-sm">
          <button
            className={cn(
              "inline-flex items-center rounded-md border border-input bg-background px-3 py-2 text-sm hover:bg-accent hover:text-accent-foreground",
              "focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
            )}
          >
            <span className="tabular-nums" id="nav-balance">
              {state ? `$${state.balance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "$0.00"}
            </span>
          </button>
          <div className="absolute left-0 mt-2 w-56 p-2 bg-border/80 backdrop-blur border border-border rounded-md z-50 hidden md:block">
            <div className="space-y-1 text-sm">
              <div className="flex items-center space-x-2">
                <span className="flex h-5 w-5 items-center justify-center rounded bg-primary/20 text-primary">
                  💰
                </span>
                <span>Balance: {state ? `$${state.balance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "--"}</span>
              </div>
              <div className="flex items-center space-x-2">
                <span className="flex h-5 w-5 items-center justify-center rounded bg-primary/20 text-primary">
                  📈
                </span>
                <span>P/L: {state ? state.pnl >= 0 ? `+${state.pnl.toFixed(2)}` : `${state.pnl.toFixed(2)}` : "--"}</span>
              </div>
              <div className="flex items-center space-x-2">
                <span className="flex h-5 w-5 items-center justify-center rounded bg-primary/20 text-primary">
                  📊
                </span>
                <span>Trades: {state ? state.trades_today : "--"}</span>
              </div>
            </div>
          </div>
        </div>

        <button
          className="p-1 rounded-md bg-background hover:bg-accent/20"
          aria-label="Theme toggle"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            className="h-4 w-4"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"
            />
          </svg>
        </button>
      </div>
    </nav>
  );
}