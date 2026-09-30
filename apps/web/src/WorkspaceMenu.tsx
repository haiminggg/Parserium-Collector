import { Loader, Menu, Text, UnstyledButton } from "@mantine/core";
import { useState } from "react";

import {
  switchWorkspace,
  type AuthenticatedSession,
  type WorkspaceSummary,
} from "./api/session";

interface WorkspaceMenuProps {
  session: AuthenticatedSession;
  onSwitched: (session: AuthenticatedSession) => void | Promise<void>;
}

export function WorkspaceMenu({ session, onSwitched }: WorkspaceMenuProps) {
  const [opened, setOpened] = useState(false);
  const [pendingWorkspaceId, setPendingWorkspaceId] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  async function selectWorkspace(workspace: WorkspaceSummary) {
    if (workspace.id === session.workspace.id || pendingWorkspaceId !== null) return;
    setPendingWorkspaceId(workspace.id);
    setFailed(false);
    try {
      const switched = await switchWorkspace(session.csrf_token, workspace.id);
      await onSwitched(switched);
    } catch {
      setFailed(true);
    } finally {
      setPendingWorkspaceId(null);
    }
  }

  if (session.workspaces.length === 1) {
    return (
      <div className="workspace-control workspace-control-static">
        <Text component="span" className="workspace-control-label">
          {session.workspace.name}
        </Text>
      </div>
    );
  }

  return (
    <div className="workspace-control">
      <Menu
        opened={opened}
        onChange={setOpened}
        position="bottom-end"
        width={260}
        shadow="sm"
        withinPortal
      >
        <Menu.Target>
          <UnstyledButton
            className="workspace-menu-trigger"
            aria-label={`Workspace: ${session.workspace.name}`}
          >
            <span className="workspace-control-label">{session.workspace.name}</span>
            {pendingWorkspaceId ? (
              <Loader size={13} aria-label="Switching workspace" />
            ) : (
              <span className="workspace-menu-caret" aria-hidden="true">
                ▾
              </span>
            )}
          </UnstyledButton>
        </Menu.Target>
        <Menu.Dropdown className="workspace-menu-dropdown">
          <Menu.Label>Workspaces</Menu.Label>
          {session.workspaces.map((workspace) => {
            const active = workspace.id === session.workspace.id;
            return (
              <UnstyledButton
                key={workspace.id}
                className="workspace-menu-item"
                role="menuitemradio"
                aria-checked={active}
                disabled={pendingWorkspaceId !== null}
                data-menu-item
                data-active={active || undefined}
                onClick={() => {
                  setOpened(false);
                  void selectWorkspace(workspace);
                }}
              >
                <span>
                  <span className="workspace-menu-item-name">{workspace.name}</span>
                  <span className="workspace-menu-item-role">{workspace.role}</span>
                </span>
                {active ? <span aria-hidden="true">✓</span> : null}
              </UnstyledButton>
            );
          })}
        </Menu.Dropdown>
      </Menu>
      {failed ? (
        <Text role="alert" className="workspace-menu-error">
          Workspace could not be switched.
        </Text>
      ) : null}
    </div>
  );
}
