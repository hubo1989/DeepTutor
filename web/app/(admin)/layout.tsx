export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <div className="min-h-dvh bg-[var(--background)]">{children}</div>;
}
