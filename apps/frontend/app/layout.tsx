import type { Metadata } from "next"
import "./globals.css"
import { GeistSans } from "geist/font/sans"
import { ThemeProvider } from "@/components/theme-provider"

export const metadata: Metadata = {
  title: "InteraMed",
  description: "Interações medicamentosas descritas nas bulas profissionais publicadas pela Anvisa",
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="pt-BR" suppressHydrationWarning><body className={GeistSans.className}><ThemeProvider>{children}</ThemeProvider></body></html>
}
