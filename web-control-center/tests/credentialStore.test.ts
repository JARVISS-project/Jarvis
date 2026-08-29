import { beforeEach, describe, expect, it } from "vitest";
import { IndexedDbCredentialVault } from "../src/credentialStore";

describe("encrypted credential vault", () => {
  beforeEach(async () => {
    await new Promise<void>((resolve) => { const request = indexedDB.deleteDatabase("jarvis-control-center-credentials"); request.onsuccess = () => resolve(); request.onerror = () => resolve(); });
  });

  it("stores an encrypted token behind a non-extractable key", async () => {
    const vault = new IndexedDbCredentialVault();
    await vault.save("dev-1", "top-secret-token");
    let recovered = "";
    await vault.useCredential((credential) => { recovered = credential.token; });
    expect(recovered).toBe("top-secret-token");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);

    const db = await new Promise<IDBDatabase>((resolve) => { const request = indexedDB.open("jarvis-control-center-credentials"); request.onsuccess = () => resolve(request.result); });
    const tx = db.transaction("vault", "readonly");
    const key = await new Promise<CryptoKey>((resolve) => { const request = tx.objectStore("vault").get("non-extractable-aes-key"); request.onsuccess = () => resolve(request.result); });
    const stored = await new Promise<Record<string, unknown>>((resolve) => { const request = tx.objectStore("vault").get("remote-credential"); request.onsuccess = () => resolve(request.result); });
    expect(key.extractable).toBe(false);
    expect(JSON.stringify(stored)).not.toContain("top-secret-token");
    db.close();
  });
});
