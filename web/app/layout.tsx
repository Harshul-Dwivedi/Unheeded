import type { Metadata } from "next";
import Link from "next/link";
import Header from "@/components/Header";
import Palette from "@/components/Palette";
import { api } from "@/lib/api";
import { date } from "@/lib/format";
import "./globals.css";

export const metadata: Metadata = {
  title: "Unheeded",
  description: "Had the NTSB already warned about this, and was the warning still open on the day it happened?",
};

// applied before first paint so dark mode never flashes
// light is the server default, so a page rendered without this script (Next's 404 in dev) still has the palette
const THEME = `try{if(localStorage.getItem('unheeded-theme')==='dark')document.documentElement.classList.replace('t-light','t-dark')}catch(e){}`;

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const health = await api<{ data_date: number }>("/api/health").catch(() => null);
  return (
    <html lang="en" className="t-light" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME }} />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Newsreader:opsz,wght@6..72,400;6..72,500;6..72,600&family=Public+Sans:wght@400;500;600&display=swap"
        />
      </head>
      <body>
        <Header />
        {children}
        <footer className="ft">
          <div className="wrap ftin">
            <span>Data: NTSB CAROL export · {health ? date(health.data_date) : "unavailable"} · US aviation only</span>
            <Link href="/method">Method and sources</Link>
          </div>
        </footer>
        <Palette />
      </body>
    </html>
  );
}
