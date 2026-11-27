import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { SystemTheme } from "@/components/assistant/SystemTheme";
import "./globals.css";

export const metadata: Metadata = { title: "Executive Assistant" };
export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <SystemTheme />
        {children}
      </body>
    </html>
  );
}
