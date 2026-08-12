import "./globals.css";
import type { Metadata } from "next";
import { Nav } from "@/components/nav";
import { useLiveData } from "@/hooks/use-live-data";

export const metadata: Metadata = {
  title: "Synthetic Trader Dashboard",
  description: "Real-time trading dashboard for synthetic indices",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { state } = useLiveData();
  
  return (
    <html lang="en" className={state?.mode === "paper" ? "dark" : ""}>
      <body className="antialiased">
        <Nav />
        <main className="min-h-[calc(100vh-4rem)]">{children}</main>
      </body>
    </html>
  );
}