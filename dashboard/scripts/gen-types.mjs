// Generates src/types/*.ts from ../schemas/*.json — run `npm run gen:types`, never edit the output.
import { compileFromFile } from "json-schema-to-typescript";
import { mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

const schemasDir = fileURLToPath(new URL("../../schemas/", import.meta.url));
const outDir = fileURLToPath(new URL("../src/types/", import.meta.url));
mkdirSync(outDir, { recursive: true });

for (const file of readdirSync(schemasDir).filter((f) => f.endsWith(".json")).sort()) {
  const ts = await compileFromFile(join(schemasDir, file), {
    cwd: schemasDir,
    bannerComment: `/* Generated from schemas/${file} by \`npm run gen:types\`. Do not edit. */`,
    style: { singleQuote: false },
    unreachableDefinitions: true,
  });
  const name = file.replace(/-\d+\.\d+\.json$/, ".ts");
  writeFileSync(join(outDir, name), ts);
  console.log(`schemas/${file} -> src/types/${name}`);
}
