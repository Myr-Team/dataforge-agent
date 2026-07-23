import React, { useEffect, useState } from "react";
import { Loader2, X } from "lucide-react";

import { domainsToDraft, normalizeDomainDraft } from "./governanceCenterModel.js";

export function EnterpriseIdentityPolicyModal({ open, initialDomains = [], busy = false, error = "", onSave = async () => {}, onClose = () => {} }) {
  const [draft, setDraft] = useState("");

  useEffect(() => {
    if (open) setDraft(domainsToDraft(initialDomains));
  }, [open, initialDomains]);

  if (!open) return null;
  const domains = normalizeDomainDraft(draft);
  return (
    <div className="governance-modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="governance-modal" role="dialog" aria-modal="true" aria-labelledby="enterprise-identity-policy-title" onMouseDown={(event) => event.stopPropagation()}>
        <header className="governance-modal-head">
          <div>
            <p className="governance-kicker">Enterprise identity</p>
            <h2 id="enterprise-identity-policy-title">企业身份展示</h2>
            <p>仅已验证且邮箱域名匹配的活跃成员，会在成员目录中展示姓名与企业邮箱。</p>
          </div>
          <button className="icon-button" type="button" title="关闭" aria-label="关闭" onClick={onClose} disabled={busy}>
            <X size={18} />
          </button>
        </header>
        <label className="governance-field">
          <span>允许展示的企业邮箱域名</span>
          <textarea
            aria-label="企业邮箱域名"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="corp.example, team.corp.example"
            rows={4}
            disabled={busy}
          />
          <small>{domains.length ? `将保存 ${domains.length} 个有效域名。` : "留空将继续以匿名标识展示所有成员。"}</small>
        </label>
        {error ? <p className="governance-form-error" role="alert">{error}</p> : null}
        <footer className="governance-modal-actions">
          <button className="ghost-button" type="button" onClick={onClose} disabled={busy}>取消</button>
          <button className="primary-button" type="button" onClick={() => onSave(domains)} disabled={busy}>
            {busy ? <Loader2 className="spin" size={16} /> : null}
            保存策略
          </button>
        </footer>
      </section>
    </div>
  );
}
