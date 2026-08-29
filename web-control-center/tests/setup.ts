import "@testing-library/jest-dom/vitest";
import "fake-indexeddb/auto";
import { webcrypto } from "node:crypto";

Object.defineProperty(globalThis, "crypto", { value: webcrypto, configurable: true });
if (typeof Element !== "undefined") {
  Object.defineProperty(Element.prototype, "scrollIntoView", { value: () => undefined, configurable: true });
}
