import { test } from "node:test";
import assert from "node:assert/strict";
import { verifyPassword } from "better-auth/crypto";
import { hashPassword } from "./create-user.mjs";

test("hash verifies with Better Auth", async () => {
  const hash = hashPassword("correct horse ✓");
  assert.match(hash, /^[0-9a-f]{32}:[0-9a-f]{128}$/);
  assert.equal(await verifyPassword({ hash, password: "correct horse ✓" }), true);
  assert.equal(await verifyPassword({ hash, password: "wrong" }), false);
});
