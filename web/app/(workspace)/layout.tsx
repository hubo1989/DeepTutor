import WorkspaceSidebar from "@/components/sidebar/WorkspaceSidebar";
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
          <div className="flex h-screen overflow-hidden">
            <WorkspaceSidebar />
            <div className="flex min-w-0 flex-1 flex-col overflow-hidden bg-[var(--background)]">
              <TrialStatusBanner />
              <main className="min-h-0 flex-1 overflow-hidden">
                <CapabilityGate>{children}</CapabilityGate>
              </main>
            </div>
          </div>
        </UnifiedChatProvider>
      </CapabilityAccessProvider>
    </CommercialAccessProvider>
  );
}
