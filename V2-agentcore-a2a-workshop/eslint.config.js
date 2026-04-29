import globals from "globals";
import js from "@eslint/js";
import ts from "@typescript-eslint/eslint-plugin";
import tsParser from "@typescript-eslint/parser";
import { globalIgnores } from "eslint/config";

export default [
  // Global ignores for all files
  globalIgnores([
    ".venv/",
    "**/dist/",
    "**/cdk.out/",
    "node_modules/",
    ".ruff_cache/",
    ".husky/",
    ".vscode/",
    "**/.venv/",
  ]),
  {
    ignores: ["**/dist/", "**/cdk.out/", "node_modules/", "**/.venv/"],
  },

  // Base ESLint recommended rules for all files
  js.configs.recommended,

  // Configuration for JavaScript files
  {
    files: ["**/*.js", "**/*.mjs", "**/*.cjs"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: {
        ...globals.browser,
        ...globals.es2021,
      },
    },
    rules: {
      "no-console": "warn",
    },
  },

  // Configuration for TypeScript files in tools (Node.js environment)
  {
    files: ["tools/**/*.ts", "tools/**/*.tsx"],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        project: ["./tsconfig.json", "./tools/*/tsconfig.json"],
        ecmaVersion: "latest",
        sourceType: "module",
      },
      globals: {
        ...globals.node,
        ...globals.es2021,
      },
    },
    plugins: {
      "@typescript-eslint": ts,
    },
    rules: {
      ...ts.configs.recommended.rules,
      "@typescript-eslint/no-unused-vars": "warn",
    },
  },
  // Configuration for TypeScript files in packages (likely browser/universal)
  {
    files: ["packages/**/*.ts", "packages/**/*.tsx"],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        project: ["./tsconfig.json", "./packages/*/tsconfig.json"],
        ecmaVersion: "latest",
        sourceType: "module",
      },
      globals: {
        ...globals.browser,
        ...globals.es2021,
      },
    },
    plugins: {
      "@typescript-eslint": ts,
    },
    rules: {
      ...ts.configs.recommended.rules,
      "@typescript-eslint/no-unused-vars": "warn",
    },
  },
];
