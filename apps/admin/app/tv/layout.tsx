/**
 * TV Mode layout — fullscreen, no sidebar, dark background.
 * This is a nested layout under the root layout, so we must NOT
 * re-declare <html> or <body> tags (the root layout owns those).
 */
export default function TVLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-[#0A1628] text-white min-h-screen overflow-hidden">
      {children}
    </div>
  );
}
