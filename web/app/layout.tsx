import type { Metadata } from "next";
import { I18nProvider } from "@/lib/i18n";
import { ThemeProvider, themeScript } from "@/lib/theme";
import "./globals.css";

export const metadata: Metadata = {
  title: "Mas",
  description: "Your server, mapped.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Applied before paint to avoid a flash of the wrong theme. */}
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body><I18nProvider><ThemeProvider>{children}</ThemeProvider></I18nProvider></body>
    </html>
  );
}
