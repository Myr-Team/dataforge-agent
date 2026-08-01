import React from "react";
import { CheckCircle2, Info, ShieldCheck } from "lucide-react";


function CapabilityList({ icon: Icon, title, items }) {
  if (!Array.isArray(items) || !items.length) return null;
  return (
    <section className="finops-decision-capability-group">
      <header>
        <Icon size={15} aria-hidden="true" />
        <b>{title}</b>
      </header>
      <ul>
        {items.map((item, index) => <li key={`${title}-${index}`}>{item}</li>)}
      </ul>
    </section>
  );
}


export function FinOpsCapabilityNote({ capability = {} }) {
  const platformConfirmed = Array.isArray(capability?.platformConfirmed)
    ? capability.platformConfirmed
    : [];
  const businessVerification = Array.isArray(capability?.businessVerification)
    ? capability.businessVerification
    : [];
  const governanceBoundary = Array.isArray(capability?.governanceBoundary)
    ? capability.governanceBoundary
    : [];
  if (!platformConfirmed.length && !businessVerification.length && !governanceBoundary.length) {
    return null;
  }
  return (
    <details className="finops-decision-capability">
      <summary>
        <Info size={15} aria-hidden="true" />
        <span>
          <b>能力与口径说明</b>
          <small>区分平台事实、业务验证与治理边界</small>
        </span>
      </summary>
      <div className="finops-decision-capability-body">
        <CapabilityList
          icon={CheckCircle2}
          title="平台自动确认"
          items={platformConfirmed}
        />
        <CapabilityList
          icon={Info}
          title="业务侧补充验证"
          items={businessVerification}
        />
        <CapabilityList
          icon={ShieldCheck}
          title="治理边界"
          items={governanceBoundary}
        />
      </div>
    </details>
  );
}
