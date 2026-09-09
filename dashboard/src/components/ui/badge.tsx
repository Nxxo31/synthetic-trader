"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

interface BadgeProps extends React.HTMLAttributes<HTMLElement> {
  variant?: "default" | "secondary" | "destructive" | "outline";
  className?: string;
}

export function Badge({
  className = "",
  variant = "default",
  children,
}: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
        {
          // variants
          default: "bg-primary text-primary-foreground",
          secondary: "bg-secondary text-secondary-foreground",
          destructive: "bg-destructive text-destructive-foreground",
          outline: "border border-input hover:bg-accent hover:text-accent-foreground",
        }[variant],
        className
      )}
    >
      {children}
    </span>
  );
}