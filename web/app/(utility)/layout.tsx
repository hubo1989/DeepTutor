import UtilitySidebar from "@/components/sidebar/UtilitySidebar";
import { CapabilityAccessProvider } from "@/components/access/CapabilityAccessContext";
import CapabilityGate from "@/components/access/CapabilityGate";
import {
  CommercialAccessProvider,
  TrialStatusBanner,
} from "@/features/commercial";

export default function UtilityLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <CommercialAccessProvider>
      <CapabilityAccessProvider>
        <div className="flex h-screen overflow-hidden">
          <UtilitySidebar />
          <div className="flex min-w-0 flex-1 flex-col overflow-hidden bg-[var(--background)]">
            <TrialStatusBanner />
            <main className="min-h-0 flex-1 overflow-hidden">
              <CapabilityGate>{children}</CapabilityGate>
            </main>
          </div>
        </div>
      </CapabilityAccessProvider>
    </CommercialAccessProvider>
  );
}
