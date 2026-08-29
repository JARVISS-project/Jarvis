export type DeviceCredential = { deviceId: string; token: string };

export interface CredentialVault {
  hasCredential(): Promise<boolean>;
  useCredential<T>(consumer: (credential: DeviceCredential) => T | Promise<T>): Promise<T | undefined>;
  save(deviceId: string, token: string): Promise<void>;
  clear(): Promise<void>;
}

type StoredCredential = { deviceId: string; iv: ArrayBuffer; ciphertext: ArrayBuffer };
const DB_NAME = "jarvis-control-center-credentials";
const STORE = "vault";
const KEY_ID = "non-extractable-aes-key";
const CREDENTIAL_ID = "remote-credential";

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error("Secure credential storage is unavailable."));
  });
}

async function transaction<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, mode);
    const request = run(tx.objectStore(STORE));
    let result: T;
    request.onsuccess = () => { result = request.result; };
    tx.oncomplete = () => { db.close(); resolve(result); };
    tx.onerror = () => { db.close(); reject(new Error("Secure credential storage failed.")); };
    tx.onabort = () => { db.close(); reject(new Error("Secure credential storage failed.")); };
  });
}

async function getOrCreateKey(): Promise<CryptoKey> {
  const existing = await transaction<CryptoKey | undefined>("readonly", (store) => store.get(KEY_ID));
  if (existing) return existing;
  const key = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  await transaction("readwrite", (store) => store.put(key, KEY_ID));
  return key;
}

export class IndexedDbCredentialVault implements CredentialVault {
  async hasCredential(): Promise<boolean> {
    return Boolean(await transaction<StoredCredential | undefined>("readonly", (store) => store.get(CREDENTIAL_ID)));
  }

  async useCredential<T>(consumer: (credential: DeviceCredential) => T | Promise<T>): Promise<T | undefined> {
    const stored = await transaction<StoredCredential | undefined>("readonly", (store) => store.get(CREDENTIAL_ID));
    if (!stored) return undefined;
    try {
      const key = await transaction<CryptoKey>("readonly", (store) => store.get(KEY_ID));
      if (!key) throw new Error();
      const plaintext = await crypto.subtle.decrypt({ name: "AES-GCM", iv: stored.iv }, key, stored.ciphertext);
      const token = new TextDecoder().decode(plaintext);
      return await consumer({ deviceId: stored.deviceId, token });
    } catch {
      await this.clear();
      throw new Error("Stored device credentials could not be decrypted and were removed.");
    }
  }

  async save(deviceId: string, token: string): Promise<void> {
    const key = await getOrCreateKey();
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ciphertext = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, new TextEncoder().encode(token));
    const stored: StoredCredential = { deviceId, iv: iv.buffer, ciphertext };
    await transaction("readwrite", (store) => store.put(stored, CREDENTIAL_ID));
  }

  async clear(): Promise<void> {
    await transaction("readwrite", (store) => store.delete(CREDENTIAL_ID));
  }
}
