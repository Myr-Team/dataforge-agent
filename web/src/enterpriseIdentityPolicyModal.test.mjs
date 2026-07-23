import assert from "node:assert/strict";
import test from "node:test";

import { domainsToDraft } from "./governanceCenterModel.js";

test("policy modal draft serializes only normalized domains", () => {
  assert.equal(domainsToDraft(["corp.example", "team.corp.example"]), "corp.example, team.corp.example");
  assert.equal(domainsToDraft(["invalid", null, "CORP.EXAMPLE"]), "corp.example");
});
