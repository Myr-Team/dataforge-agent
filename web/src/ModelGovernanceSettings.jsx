import React, { useState } from "react";
import { Bot, KeyRound, ShieldCheck } from "lucide-react";

import { IdentityAccessPage } from "./IdentityAccessPage.jsx";
import { ModelRoutingPage } from "./ModelRoutingPage.jsx";
import { ProviderConnectionsPage } from "./ProviderConnectionsPage.jsx";

const TABS = [
  { id: "agents", label: "Agent 模型", icon: Bot },
  { id: "providers", label: "模型提供商", icon: KeyRound },
  { id: "identity", label: "身份与访问", icon: ShieldCheck },
];

export function ModelGovernanceSettings({ workspaceId = "" }) {
  const [tab, setTab] = useState("agents");
  return (
    <div className="model-governance-settings" data-testid="model-governance-settings">
      <nav className="model-governance-tabs" aria-label="模型与身份设置">
        {TABS.map((item) => {
          const Icon = item.icon;
          return (
            <button
              type="button"
              key={item.id}
              className={tab === item.id ? "active" : ""}
              aria-current={tab === item.id ? "page" : undefined}
              onClick={() => setTab(item.id)}
            >
              <Icon size={15} />
              {item.label}
            </button>
          );
        })}
      </nav>
      <div className="model-governance-body">
        {tab === "agents" ? <ModelRoutingPage workspaceId={workspaceId} embedded /> : null}
        {tab === "providers" ? <ProviderConnectionsPage /> : null}
        {tab === "identity" ? <IdentityAccessPage workspaceId={workspaceId} /> : null}
      </div>
    </div>
  );
}
