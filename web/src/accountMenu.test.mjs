import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function loadAccountViewModel() {
  const source = await readFile(new URL("./components.jsx", import.meta.url), "utf8");
  const start = source.indexOf("export function accountViewModel");
  const end = source.indexOf("function NotificationBell", start);
  assert.ok(start >= 0 && end > start, "components must export accountViewModel before NotificationBell");
  return import(`data:text/javascript;base64,${Buffer.from(source.slice(start, end)).toString("base64")}`);
}

test("derives account identity from the signed-in Easy Auth principal", async () => {
  const { accountViewModel } = await loadAccountViewModel();
  const model = accountViewModel({ name: "Fu Zihao", email: "fuzihao@gdjiuyun.onmicrosoft.com" }, "authenticated");

  assert.equal(model.initial, "F");
  assert.equal(model.name, "Fu Zihao");
  assert.equal(model.email, "fuzihao@gdjiuyun.onmicrosoft.com");
  assert.equal(model.authLabel, "已通过 Microsoft Entra 登录");
});

test("keeps the account menu truthful when a local preview has no identity claims", async () => {
  const { accountViewModel } = await loadAccountViewModel();
  const model = accountViewModel({}, "local");

  assert.equal(model.initial, "U");
  assert.equal(model.name, "当前账户");
  assert.equal(model.email, "账号信息暂不可用");
  assert.equal(model.authLabel, "本地预览身份");
});
