import type { Metadata } from "next";
import { JourneyProfileProvider } from "@/components/journey-profile";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Roamline | Travel with intention",
  description: "Personalized travel discovery and itinerary planning.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <header className="site-header">
          <Link className="brand" href="/" aria-label="Roamline home">
            <span aria-hidden="true">↗</span> roamline
          </Link>
          <span className="header-note">
            A little further. A little more you.
          </span>
        </header>
        <JourneyProfileProvider>{children}</JourneyProfileProvider>
        <footer>
          <span>Roamline</span>
          <span>Made for the way you want to travel.</span>
          <span>Foundation · Work in progress</span>
        </footer>
      </body>
    </html>
  );
}
