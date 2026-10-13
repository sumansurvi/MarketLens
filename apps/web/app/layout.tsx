import type { Metadata } from "next";

import { StatusProvider } from "@/components/status-provider";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "MarketLens", template: "%s · MarketLens" },
  description:
    "Ask a question, get a research brief you can verify. Research only — no trading, no brokerage access, no personalized recommendations.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  // suppressHydrationWarning: browser extensions (Grammarly, password managers, translators) add
  // attributes to <html> and <body> before React loads. It covers only these two elements' own
  // attributes; mismatches anywhere inside the app are still reported.
  return (
    <html lang="en" className="h-full antialiased" suppressHydrationWarning>
      <body className="h-full" suppressHydrationWarning>
        <StatusProvider>{children}</StatusProvider>
      </body>
    </html>
  );
}
