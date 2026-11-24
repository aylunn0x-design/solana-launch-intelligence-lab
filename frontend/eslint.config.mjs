// eslint-config-next v16 ships native flat config, so no FlatCompat shim is
// needed (and the shim breaks on this version with a circular-structure error).
import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescriptConfig from "eslint-config-next/typescript";

const config = [
  ...coreWebVitals,
  ...typescriptConfig,
  { ignores: [".next/**", "node_modules/**", "coverage/**", "next-env.d.ts"] },
];

export default config;
