import { useEffect, useState } from "react";
import type { ClientSnapshot, RemoteClient } from "../remoteClient";

export function useRemoteClient(client: RemoteClient): ClientSnapshot {
  const [snapshot, setSnapshot] = useState(() => client.getSnapshot());
  useEffect(() => client.subscribe(setSnapshot), [client]);
  useEffect(() => { void client.initialize(); }, [client]);
  return snapshot;
}
