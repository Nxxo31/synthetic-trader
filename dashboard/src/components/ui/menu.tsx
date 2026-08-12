"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

/* -------------------------------------------------------------------------- */
/*                               Menu Primitives                              */
/* -------------------------------------------------------------------------- */

const Menu = React.forwardRef<
  React.HTMLAttributes<HTMLDivElement>,
  HTMLDivElement
>(({ className, children, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("relative inline-block text-left", className)}
    {...props}
  >
    <div className="relative z-50">{children}</div>
  </div>
));
Menu.displayName = "Menu";

const MenuTrigger = React.forwardRef<
  React.HTMLAttributes<HTMLButtonElement>,
  HTMLButtonElement
>(({ className, children, ...props }, ref) => (
  <button
    ref={ref}
    className={cn(
      "inline-flex items-center rounded-md border border-transparent bg-white px-3 py-2 text-sm font-medium text-white hover:bg-white/80 focus:outline-none focus:ring-2 focus:ring-white focus:ring-offset-2",
      className
    )}
    {...props}
    aria-haspopup="true"
  >
    {children}
  </button>
));
MenuTrigger.displayName = "MenuTrigger";

const MenuContent = React.forwardRef<
  React.HTMLAttributes<HTMLDivElement> & {
    sideOffset?: number;
    align?: "start" | "center" | "end";
  },
  HTMLDivElement
>(({ className, sideOffset = 4, align = "start", children, ...props }, ref) => {
  const [side, setSide] = React.useState<"left" | "right">("right");
  const [alignX, setAlignX] = React.useState<"start" | "center" | "end">(
    align
  );
  return (
    <div
      ref={ref}
      role="menu"
      aria-orientation="vertical"
      aria-label="menu"
      className={cn(
        "fixed left-0 z-50 mt-2 max-h-[var--radar-menu-content-max-height] min-w-[var--radar-menu-content-min-width] w-[var--radar-menu-content-width] rounded-md border bg-popover text-popover-foreground shadow-md pointer-events-auto",
        {
          "[&>*:not(:first-child)]": "border-t",
          "[&>*:not(:last-child)]": "border-b",
          "data-[state=open]:animate-in": "",
          "data-[state=closed]:animate-out": "",
          "data-[state=closed]:fade-out-0": "",
          "data-[state=open]:fade-in-0": "",
          "data-[state=closed]:zoom-out-95": "",
          "data-[state=open]:zoom-in-95": "",
          "data-[side=bottom]:animate-in": "",
          "data-[side=bottom]:animate-out": "",
          "data-[side=bottom]:fade-in-0": "",
          "data-[side=bottom]:fade-out-0": "",
          "data-[side=bottom]:zoom-in-95": "",
          "data-[side=bottom]:zoom-out-95": "",
          "data-[side=left]:animate-in": "",
          "data-[side=left]:animate-out": "",
          "data-[side=left]:fade-in-0": "",
          "data-[side=left]:fade-out-0": "",
          "data-[side=left]:zoom-in-95": "",
          "data-[side=left]:zoom-out-95": "",
          "data-[side=right]:animate-in": "",
          "data-[side=right]:animate-out": "",
          "data-[side=right]:fade-in-0": "",
          "data-[side=right]:fade-out-0": "",
          "data-[side=right]:zoom-in-95": "",
          "data-[side=right]:zoom-out-95": "",
          "data-[align=start]:translate-x-0": side === "right",
          "data-[align=center]-translate-x-1/2": side === "right",
          "data-[align=end]-translate-x-full": side === "right",
          "data-[align=start]-translate-x-full": side === "left",
          "data-[align=center]-translate-x-1/2": side === "left",
          "data-[align=end]:translate-x-0": side === "left",
        },
        className
      )}
      {...props}
    >
      <div className="py-1">{children}</div>
      {/* TODO: add arrow */}
    </div>
  );
});
MenuContent.displayName = "MenuContent";

const MenuItem = React.forwardRef<
  React.HTMLAttributes<HTMLDivElement>,
  HTMLDivElement
>(({ className, disabled, ...props }, ref) => (
  <div
    ref={ref}
    role="menuitem"
    tabindex={disabled ? -1 : 0}
    aria-disabled={disabled}
    className={cn(
      "relative flex cursor-default select-none items-center rounded-sm px-2 py-1.5 text-sm outline-none",
      "focus:bg-primary focus:text-primary-foreground",
      "disabled:pointer-events-none disabled:opacity-50",
      className
    )}
    {...props}
  />
));
MenuItem.displayName = "MenuItem";

/* -------------------------------------------------------------------------- */
/*                                 Menu Export                                */
/* -------------------------------------------------------------------------- */

export { Menu, MenuItem, MenuContent, MenuTrigger };