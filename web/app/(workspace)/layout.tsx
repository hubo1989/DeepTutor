import WorkspaceSidebar from "@/components/sidebar/WorkspaceSidebar";
import AppShell from "@/components/layout/AppShell";
import { CapabilityAccessProvider } from "@/components/access/CapabilityAccessContext";
import CapabilityGate from "@/components/access/CapabilityGate";
import { UnifiedChatProvider } from "@/context/UnifiedChatContext";
import {
  CommercialAccessProvider,
  TrialStatusBanner,
} from "@/features/commercial";

export default function WorkspaceLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <CommercialAccessProvider>
      <CapabilityAccessProvider>
        <UnifiedChatProvider>
          <AppShell sidebar={<WorkspaceSidebar />}>
            <TrialStatusBanner />
            <CapabilityGate>{children}</CapabilityGate>
          </AppShell>
        </UnifiedChatProvider>
      </CapabilityAccessProvider>
    </CommercialAccessProvider>
  );
}
