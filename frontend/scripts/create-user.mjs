// NextGenSoft fork: sign-up is disabled, so admins create accounts here.
// Usage (inside the frontend container, cwd /app):
//   node scripts/create-user.mjs <email> <name> <password> [--admin]
// Re-running for an existing email resets the password.
import { randomBytes, randomUUID, scryptSync } from "node:crypto";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

// Same format as better-auth/crypto hashPassword (1.6.x).
export function hashPassword(password) {
  const salt = randomBytes(16).toString("hex");
  const key = scryptSync(password.normalize("NFKC"), salt, 64, {
    N: 16384, r: 16, p: 1, maxmem: 128 * 16384 * 16 * 2,
  });
  return `${salt}:${key.toString("hex")}`;
}

async function main([email, name, password, flag]) {
  if (!email || !name || !password || password.length < 8) {
    console.error("usage: create-user.mjs <email> <name> <password(min 8)> [--admin]");
    process.exit(2);
  }
  const { PrismaClient } = createRequire(import.meta.url)("../src/generated/prisma");
  const prisma = new PrismaClient();
  const now = new Date();
  const user = await prisma.user.upsert({
    where: { email: email.toLowerCase() },
    update: { name, ...(flag === "--admin" && { is_admin: true }) },
    create: { email: email.toLowerCase(), name, emailVerified: true, is_admin: flag === "--admin" },
  });
  const hash = hashPassword(password);
  const existing = await prisma.account.findFirst({ where: { userId: user.id, providerId: "credential" } });
  if (existing) {
    await prisma.account.update({ where: { id: existing.id }, data: { password: hash, updatedAt: now } });
  } else {
    await prisma.account.create({
      data: { id: randomUUID(), accountId: user.id, providerId: "credential", userId: user.id, password: hash, createdAt: now, updatedAt: now },
    });
  }
  console.log(`ok ${user.email} admin=${user.is_admin}`);
  await prisma.$disconnect();
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main(process.argv.slice(2));
}
