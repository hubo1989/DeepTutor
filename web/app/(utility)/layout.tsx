import UtilitySidebar from "@/components/sidebar/UtilitySidebar";
import AppShell from "@/components/layout/AppShell";
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
        <AppShell sidebar={<UtilitySidebar />}>
          <TrialStatusBanner />
          <CapabilityGate>{children}</CapabilityGate>
        </AppShell>
      </CapabilityAccessProvider>
    </CommercialAccessProvider>
  );
}
