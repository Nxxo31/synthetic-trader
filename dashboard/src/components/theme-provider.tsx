"use client";
import { useLiveData } from "@/hooks/use-live-data";

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const { state } = useLiveData();
  return (
    <div className={state?.mode === "paper" ? "dark" : ""}>
      {children}
    </div>
  );
}
