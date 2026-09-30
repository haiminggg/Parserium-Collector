import { AppShell, Text, UnstyledButton } from "@mantine/core";
import { type ReactNode, useRef } from "react";

import { BrandMark } from "./auth/BrandMark";
import { useWorkspaceEntrance } from "./workspace/useWorkspaceEntrance";
import "./workspace/workspace.css";

export interface ParseriumNavigationItem {
  destination: string;
  label: string;
}

export interface ParseriumShellProps {
  children: ReactNode;
  destination: string;
  headerActions: ReactNode;
  mainLabel?: string;
  navigation: readonly ParseriumNavigationItem[];
  onDestinationChange: (destination: string) => void;
}

export function ParseriumShell({
  children,
  destination,
  headerActions,
  mainLabel = "Document discovery console",
  navigation,
  onDestinationChange,
}: ParseriumShellProps) {
  const root = useRef<HTMLDivElement>(null);
  useWorkspaceEntrance(root);

  return (
    <AppShell
      ref={root}
      className="parserium-shell workspace-shell"
      header={{ height: { base: 128, sm: 112, lg: 80 } }}
      padding={0}
    >
      <a className="workspace-skip" href="#workspace-content">
        Skip to workspace
      </a>
      <AppShell.Header className="parserium-header">
        <div className="header-content">
          <div className="parserium-brand">
            <span className="parserium-brand-mark" aria-hidden="true">
              <BrandMark />
            </span>
            <Text component="span" className="parserium-wordmark">
              Parserium
            </Text>
          </div>

          <nav className="dashboard-navigation" aria-label="Primary navigation">
            {navigation.map((item) => {
              const active = destination === item.destination;
              return (
                <UnstyledButton
                  key={item.destination}
                  className="dashboard-navigation-button"
                  data-active={active || undefined}
                  aria-current={active ? "page" : undefined}
                  onClick={() => onDestinationChange(item.destination)}
                >
                  {item.label}
                  {active ? (
                    <span className="dashboard-navigation-underline" aria-hidden="true" />
                  ) : null}
                </UnstyledButton>
              );
            })}
          </nav>

          <div className="header-actions">{headerActions}</div>
        </div>
      </AppShell.Header>

      <AppShell.Main id="workspace-content" tabIndex={-1} aria-label={mainLabel}>
        {children}
      </AppShell.Main>
    </AppShell>
  );
}
