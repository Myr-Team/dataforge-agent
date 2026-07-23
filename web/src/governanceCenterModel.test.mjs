import assert from "node:assert/strict";
import test from "node:test";

import { normalizeDomainDraft, resolveLineageScope } from "./governanceCenterModel.js";

test("lineage scope comes from backend capability", () => {
  assert.equal(resolveLineageScope({ sections: { lineage: { scope: "self" } } }), "self");
  assert.equal(resolveLineageScope({ sections: { lineage: { scope: "workspace" } } }), "workspace");
  assert.equal(resolveLineageScope({ sections: { lineage: { scope: "unsafe" } } }), "self");
});

test("domain draft keeps valid unique domains", () => {
  assert.deepEqual(normalizeDomainDraft("CORP.EXAMPLE, corp.example, invalid, team.corp.example"), ["corp.example", "team.corp.example"]);
});
