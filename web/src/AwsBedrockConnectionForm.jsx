import React, { useState } from "react";

const BEDROCK_REGIONS = [
  ["ap-southeast-1", "亚太地区（新加坡）"],
  ["ap-northeast-1", "亚太地区（东京）"],
  ["us-east-1", "美国东部（弗吉尼亚北部）"],
  ["us-west-2", "美国西部（俄勒冈）"],
];

export function AwsBedrockConnectionForm({
  busy = false,
  displayName = "AWS Bedrock",
  region = "ap-southeast-1",
  submitLabel = "保存并测试连接",
  title = "接入 AWS Bedrock",
  description = "凭据仅写入后端安全存储，页面不会保存或再次展示。",
  onSubmit,
}) {
  const [draft, setDraft] = useState({
    displayName,
    region,
    accessKeyId: "",
    secretAccessKey: "",
    sessionToken: "",
  });

  const update = (field) => (event) => {
    const value = event.target.value;
    setDraft((current) => ({ ...current, [field]: value }));
  };

  async function submit(event) {
    event.preventDefault();
    const payload = {
      provider_type: "aws_bedrock",
      display_name: draft.displayName.trim() || "AWS Bedrock",
      region: draft.region,
      access_key_id: draft.accessKeyId.trim(),
      secret_access_key: draft.secretAccessKey,
      session_token: draft.sessionToken || null,
    };
    setDraft((current) => ({
      ...current,
      accessKeyId: "",
      secretAccessKey: "",
      sessionToken: "",
    }));
    await onSubmit(payload);
  }

  return (
    <form className="provider-create-card bedrock-connection-form" onSubmit={submit}>
      <header>
        <div className="provider-mark muted" aria-hidden="true">AWS</div>
        <div>
          <h3>{title}</h3>
          <p>{description}</p>
        </div>
      </header>
      <div className="bedrock-credential-grid">
        <label>
          <span>显示名称</span>
          <input value={draft.displayName} maxLength={120} onChange={update("displayName")} />
        </label>
        <label>
          <span>区域</span>
          <select value={draft.region} onChange={update("region")}>
            {BEDROCK_REGIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label>
          <span>Access Key ID</span>
          <input type="password" autoComplete="new-password" value={draft.accessKeyId} onChange={update("accessKeyId")} required />
        </label>
        <label>
          <span>Secret Access Key</span>
          <input type="password" autoComplete="new-password" value={draft.secretAccessKey} onChange={update("secretAccessKey")} required />
        </label>
        <label>
          <span>Session Token（可选）</span>
          <input type="password" autoComplete="new-password" value={draft.sessionToken} onChange={update("sessionToken")} />
        </label>
        <button className="primary-button" type="submit" disabled={busy}>{submitLabel}</button>
      </div>
    </form>
  );
}
