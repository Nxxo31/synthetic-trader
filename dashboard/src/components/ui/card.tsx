"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

interface CardProps extends React.HTMLAttributes<HTMLElement> {
  className?: string;
  title?: string;
  footer?: React.ReactNode;
}

export function Card({
  className = "",
  title,
  children,
  footer,
}: CardProps) {
  return (
    <div className={cn("rounded-lg border bg-card text-card-foreground shadow-sm", className)}>
      {title && <div className="px-6 py-3 font-medium">{title}</div>}
      <div className="px-6 pt-0 pb-6">{children}</div>
      {footer && (
        <div className="flex items-center px-6 pt-0 pb-3 text-xs text-muted-foreground">
          {footer}
        </div>
      )}
    </div>
  );
}