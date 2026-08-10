import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { memberDirectoryViewModel } from "./governanceViewModel.js";

test("trusted member display is used before a pseudonym", () => {
  const [member] = memberDirectoryViewModel([{
    subject_label: "member_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    identity_visibility: "verified_enterprise",
    display: { name: "Ava", email: "ava@corp.example" },
    role: "owner",
  }]);

  assert.equal(member.label, "Ava");
  assert.equal(member.detail, "ava@corp.example");
  assert.match(member.subjectLabel, /^member_aaaaaaaa/);
});

test("pseudonymous members discard unsafe display fields", () => {
  const [member] = memberDirectoryViewModel([{
    subject_label: "member_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    identity_visibility: "pseudonymous",
    display: { name: "Unsafe", email: "unsafe@outside.example" },
  }]);

  assert.equal(member.label, member.subjectLabel);
  assert.equal(member.detail, "");
  assert.ok(!JSON.stringify(member).includes("outside.example"));
});

test("settings member rows render verified enterprise identity and expose its policy", async () => {
  const source = await readFile(new URL("./components.jsx", import.meta.url), "utf8");

  assert.match(source, /<b>\{m\.label\}<\/b>/);
  assert.match(source, /m\.detail \|\| "未验证企业身份"/);
  assert.match(source, /EnterpriseIdentityPolicyModal/);
  assert.match(source, /企业身份展示/);
  assert.doesNotMatch(source, /<b>\{m\.subjectLabel\}<\/b>\s*\n\s*<em>服务端安全成员标签<\/em>/);
});
