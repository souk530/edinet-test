import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://souk530.github.io/edinet-test/"),
  title: "EDINET Lakehouse Manual",
  description: "Databricksで複数データソースをBronzeからSilverへ整理する図解マニュアル",
  openGraph: {
    title: "EDINET Lakehouse Manual",
    description: "点在するデータを、説明できるSilverへ。",
    type: "website",
    images: ["og-edinet-lakehouse.png"],
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ja">
      <body>{children}</body>
    </html>
  );
}
