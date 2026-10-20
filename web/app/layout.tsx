import type { Metadata } from "next";
import { I18nProvider } from "@/lib/i18n";
import "./globals.css";

export const metadata: Metadata = {
  title: "Mas",
  description: "Your server, mapped.",
  robots: { index: false, follow: false },
};

// Applied before paint to avoid a flash of the wrong theme.
const themeScript = `try{var t=localStorage.getItem("mas-theme")||"dark";var d=t==="dark"||(t==="system"&&matchMedia("(prefers-color-scheme: dark)").matches);document.documentElement.classList.toggle("dark",d)}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body><I18nProvider>{children}</I18nProvider></body>
    </html>
  );
}
