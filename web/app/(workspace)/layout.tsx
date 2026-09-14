import WorkspaceSidebar from "@/components/sidebar/WorkspaceSidebar";
import AppShell from "@/components/layout/AppShell";
import { CapabilityAccessProvider } from "@/components/access/CapabilityAccessContext";
import CapabilityGate from "@/components/access/CapabilityGate";
import { UnifiedChatProvider } from "@/context/UnifiedChatContext";
import { ReadingProvider } from "@/context/ReadingContext";
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
          {/* Above the page on purpose: sending the first message navigates
              /home → /home/<id>, which remounts the page. The open document
              must not die with it. */}
          <ReadingProvider>
            <AppShell sidebar={<WorkspaceSidebar />}>
              <TrialStatusBanner />
              <CapabilityGate>{children}</CapabilityGate>
            </AppShell>
          </ReadingProvider>
        </UnifiedChatProvider>
      </CapabilityAccessProvider>
    </CommercialAccessProvider>
  );
}
