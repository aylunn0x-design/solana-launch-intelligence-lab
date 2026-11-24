import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/layout/sidebar";

export const metadata: Metadata = {
  title: {
    default: "Solana Launch Intelligence Lab",
    template: "%s · Solana Launch Intelligence Lab",
  },
  description:
    "Research platform studying the lifecycle of newly launched Solana tokens using historical on-chain market data.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a href="#main" className="skip-link">
          Skip to main content
        </a>
        {/*
          Block on mobile, row only from lg up. The sidebar's mobile header is
          a sibling of <main>; in a flex row it would sit *beside* main and eat
          horizontal space, pushing the page wider than the viewport. As a
          block it spans the full width and the fixed drawer stays out of flow.
        */}
        <div className="min-h-screen lg:flex">
          <Sidebar />
          <main id="main" className="min-w-0 flex-1 px-4 py-6 sm:px-6 lg:px-8">
            <div className="mx-auto max-w-[1400px]">{children}</div>
          </main>
        </div>
      </body>
    </html>
  );
}
