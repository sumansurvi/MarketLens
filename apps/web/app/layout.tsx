import type { Metadata } from "next";

import { StatusProvider } from "@/components/status-provider";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "MarketLens", template: "%s · MarketLens" },
  description:
    "Ask a question, get a research brief you can verify. Research only — no trading, no brokerage access, no personalized recommendations.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="h-full">
        <StatusProvider>{children}</StatusProvider>
      </body>
    </html>
  );
}
